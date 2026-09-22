from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from hmac import compare_digest
import json
import logging
import os
from pathlib import Path
import re
from threading import Event, Lock, Thread
from typing import Annotated, Any, Literal

import firebase_admin
from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from firebase_admin import auth, credentials, firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from backend.system.system_schema import MatchDoc, PlayerKpi, RecordDoc, RecordingDoc, RecordingKpi, Side, TeamRatingSnapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_FILE = PROJECT_ROOT / ".env"
PLAYER_RATING_FIELDS = {
    "scoreRel", "scoreAbs", "score", "jmx", "apx", "apxGrade",
    "tpx", "tpxGrade", "fpx", "fpxGrade", "ratingBasedOn",
}


@dataclass(slots=True)
class BackendError(Exception):
    message: str
    status_code: int = 500
    code: str = "backend_error"


class NotFoundError(BackendError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=404, code="not_found")


class FirebaseUnavailableError(BackendError):
    def __init__(self, message: str = "Firebase Admin is not configured or unavailable") -> None:
        super().__init__(message=message, status_code=503, code="firebase_unavailable")


class AuthenticationError(BackendError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message=message, status_code=401, code="authentication_required")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "didNew Backend"
    app_env: Literal["local", "development", "staging", "production", "test"] = "local"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    backend_reload: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
    )
    firebase_project_id: str | None = None
    firebase_credentials_path: str | None = None
    firebase_credentials_json: str | None = None
    firebase_emulator_host: str | None = None
    swagger_api_key: str | None = None
    jpd_rating_base_url: str = "https://jpd-rating-296087686925.asia-northeast3.run.app"
    jpd_rating_token: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("firebase_credentials_path")
    @classmethod
    def normalize_credentials_path(cls, value: str | None) -> str | None:
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)


_init_lock = Lock()
_readiness_lock = Lock()
_firestore_ready = False
_firestore_probe_attempted = False
logger = logging.getLogger(__name__)


def firebase_is_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if settings.firebase_credentials_path:
        return bool(settings.firebase_project_id and Path(settings.firebase_credentials_path).is_file())
    return bool(settings.firebase_project_id and (
        settings.firebase_credentials_json
        or settings.firebase_emulator_host
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    ))


def firebase_is_initialized() -> bool:
    try:
        firebase_admin.get_app()
    except ValueError:
        return False
    return True


def firestore_is_ready() -> bool:
    return _firestore_ready


def _build_credential(settings: Settings):
    if settings.firebase_credentials_json:
        try:
            return credentials.Certificate(json.loads(settings.firebase_credentials_json))
        except json.JSONDecodeError as exc:
            raise FirebaseUnavailableError("FIREBASE_CREDENTIALS_JSON is not valid JSON") from exc
    if settings.firebase_credentials_path:
        path = Path(settings.firebase_credentials_path)
        if not path.is_file():
            raise FirebaseUnavailableError("Firebase credentials file is missing")
        return credentials.Certificate(str(path))
    return credentials.ApplicationDefault()


def ensure_firebase_app(settings: Settings | None = None):
    settings = settings or get_settings()
    if settings.firebase_emulator_host:
        os.environ["FIRESTORE_EMULATOR_HOST"] = settings.firebase_emulator_host
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass
    with _init_lock:
        try:
            return firebase_admin.get_app()
        except ValueError:
            pass
        options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
        try:
            return firebase_admin.initialize_app(_build_credential(settings), options=options)
        except FirebaseUnavailableError:
            raise
        except Exception as exc:
            raise FirebaseUnavailableError("Firebase Admin initialization failed") from exc


@lru_cache(maxsize=1)
def get_firestore_client() -> Client:
    try:
        return firestore.client(app=ensure_firebase_app())
    except Exception as exc:
        raise FirebaseUnavailableError("Unable to create Firestore client") from exc


def probe_firestore(*, force: bool = False) -> bool:
    global _firestore_ready, _firestore_probe_attempted
    if (_firestore_ready or _firestore_probe_attempted) and not force:
        return _firestore_ready
    with _readiness_lock:
        if (_firestore_ready or _firestore_probe_attempted) and not force:
            return _firestore_ready
        _firestore_probe_attempted = True
        completed = Event()
        error: list[Exception] = []

        def read_one_document() -> None:
            try:
                list(get_firestore_client().collection("matches").limit(1).stream(timeout=5, retry=None))
            except Exception as exc:
                error.append(exc)
            finally:
                completed.set()

        Thread(target=read_one_document, daemon=True).start()
        if not completed.wait(timeout=5):
            logger.warning("Firestore readiness probe timed out")
            return False
        if error:
            logger.warning("Firestore readiness probe failed: %s", type(error[0]).__name__)
            return False
        _firestore_ready = True
        return True


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="allow")
    uid: str
    claims: dict


swagger_key_auth = APIKeyHeader(name="X-Swagger-Key", auto_error=False, scheme_name="SwaggerKey")


def get_current_user(
    request: Request,
    swagger_api_key: Annotated[str | None, Security(swagger_key_auth)] = None,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if settings.app_env == "local" and settings.swagger_api_key and swagger_api_key:
        if compare_digest(swagger_api_key, settings.swagger_api_key):
            return CurrentUser(uid="local-swagger", claims={"auth": "swagger_api_key"})

    # The local DID screen runs in a separate browser origin from uvicorn.
    # Swagger authorization cannot be shared with that tab, and exposing the
    # Swagger secret to a recording device would be worse.  Trust only the
    # configured loopback frontend origins while the backend is explicitly in
    # local mode.  Render/production never enters this branch.
    origin = request.headers.get("Origin")
    if settings.app_env == "local" and origin in settings.cors_origins:
        return CurrentUser(uid="local-did-input", claims={"auth": "local_frontend"})

    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        try:
            token = authorization.removeprefix("Bearer ").strip()
            decoded = auth.verify_id_token(token, app=ensure_firebase_app(settings))
        except Exception as exc:
            raise AuthenticationError("Invalid Firebase login token") from exc
        uid = decoded.get("uid")
        if isinstance(uid, str) and uid:
            return CurrentUser(uid=uid, claims=dict(decoded))

    if settings.app_env == "local" and settings.swagger_api_key:
        raise AuthenticationError("Enter the local Swagger key or sign in through DID INPUT")
    raise AuthenticationError("Authentication is not configured")


RequiredUser = Annotated[CurrentUser, Depends(get_current_user)]


@dataclass(frozen=True, slots=True)
class MatchKpiSource:
    match: MatchDoc
    recording: RecordingDoc
    records: list[tuple[str, RecordDoc]]
    fingerprint: str


def _validate_document(model_type: Any, data: dict[str, Any], *, path: str):
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise BackendError(
            message=f"Stored JPD-DID document does not match its backend schema: {path}",
            status_code=500,
            code="firestore_schema_mismatch",
        ) from exc


class JpdDidData:
    """JPD-DID match reads and recording-level derived writes."""

    def __init__(self) -> None:
        self.db = get_firestore_client()

    def get_match(self, gm_id: str) -> MatchDoc:
        reference = self.db.collection("matches").document(gm_id)
        snapshot = reference.get(retry=None, timeout=10)
        if not snapshot.exists:
            raise NotFoundError(f"Match not found: {gm_id}")
        return _validate_document(MatchDoc, snapshot.to_dict() or {}, path=reference.path)

    def list_matches(
        self,
        *,
        league_id: str | None = None,
        season_id: str | None = None,
        round_number: int | None = None,
        team_id: str | None = None,
        gm_id: str | None = None,
        limit: int = 2000,
    ) -> list[tuple[str, MatchDoc]]:
        if gm_id:
            matches = [(gm_id, self.get_match(gm_id))]
        else:
            matches = [
                (snapshot.id, _validate_document(MatchDoc, snapshot.to_dict() or {}, path=snapshot.reference.path))
                for snapshot in self.db.collection("matches").limit(limit).stream(retry=None, timeout=30)
            ]
        return [
            item for item in matches
            if (league_id is None or item[1].leagueId == league_id)
            and (season_id is None or item[1].seasonId == season_id)
            and (round_number is None or item[1].round == round_number)
            and (team_id is None or team_id in {item[1].homeTeamId, item[1].awayTeamId})
        ]

    def list_matches_for_month(self, year: int, month: int) -> list[tuple[str, MatchDoc]]:
        """Read only fixtures belonging to one calendar month.

        Match dates already exist in a few legacy string formats (YYYYMMDD,
        YYYY-MM-DD and YYYY.MM.DD). Query each format by range instead of
        scanning the whole matches collection and filtering in Python.
        """
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        ranges = (
            (f"{year}{month:02d}01", f"{next_year}{next_month:02d}01"),
            (f"{year}-{month:02d}-01", f"{next_year}-{next_month:02d}-01"),
            (f"{year}.{month:02d}.01", f"{next_year}.{next_month:02d}.01"),
        )
        found: dict[str, MatchDoc] = {}
        collection = self.db.collection("matches")
        for start, end in ranges:
            query = (
                collection
                .where(filter=FieldFilter("date", ">=", start))
                .where(filter=FieldFilter("date", "<", end))
            )
            for snapshot in query.stream(retry=None, timeout=20):
                if snapshot.id in found:
                    continue
                found[snapshot.id] = _validate_document(
                    MatchDoc, snapshot.to_dict() or {}, path=snapshot.reference.path
                )
        return list(found.items())

    def get_documents_by_ids(self, collection_name: str, document_ids: set[str]) -> dict[str, dict[str, Any]]:
        """Batch-read a small set of referenced documents in a few RPCs."""
        ids = sorted(document_id for document_id in document_ids if document_id)
        result: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), 300):
            refs = [self.db.collection(collection_name).document(document_id) for document_id in ids[start:start + 300]]
            for snapshot in self.db.get_all(refs, retry=None, timeout=20):
                if snapshot.exists:
                    result[snapshot.id] = snapshot.to_dict() or {}
        return result

    def get_recording(self, gm_id: str, side: Side) -> RecordingDoc:
        reference = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
        snapshot = reference.get(retry=None, timeout=10)
        if not snapshot.exists:
            raise NotFoundError(f"Recording not found: {gm_id}/{side}")
        return _validate_document(RecordingDoc, snapshot.to_dict() or {}, path=reference.path)

    def get_recording_statuses(self, gm_id: str) -> dict[Side, str | None]:
        """Return raw recording status for H/A without creating any document.

        A side is considered analysis-complete only when its promoted RAW recording
        has status='final'. H1_done may already exist after second-half start, so
        callers must not treat mere document existence as completion.
        """
        return self.get_recording_statuses_many([gm_id]).get(gm_id, {"H": None, "A": None})

    def get_recording_input_states(self, gm_id: str) -> dict[Side, dict[str, Any]]:
        """Read the small H/A recording heads used by the input lobby.

        Besides lifecycle status, the lobby needs the already-recorded team's
        fieldSide so a newly-entered opponent can default to the opposite side.
        No document is created by this read.
        """
        states: dict[Side, dict[str, Any]] = {
            "H": {"rawStatus": None, "completed": False, "fieldSide": None},
            "A": {"rawStatus": None, "completed": False, "fieldSide": None},
        }
        collection = self.db.collection("matches").document(gm_id).collection("recordings")
        refs = [collection.document("H"), collection.document("A")]
        for snapshot in self.db.get_all(refs, retry=None, timeout=20):
            if not snapshot.exists or snapshot.id not in {"H", "A"}:
                continue
            document = snapshot.to_dict() or {}
            raw_status = document.get("status")
            field_side = document.get("fieldSide")
            states[snapshot.id] = {
                "rawStatus": str(raw_status) if raw_status is not None else None,
                "completed": raw_status == "final",
                "fieldSide": field_side if field_side in {"left", "right"} else None,
            }
        return states

    def get_recording_statuses_many(self, gm_ids: list[str]) -> dict[str, dict[Side, str | None]]:
        """Batch-read H/A recording heads for many fixtures.

        This avoids one recordings subcollection stream per schedule row. Only the
        two known recording documents (H and A) are requested for each fixture.
        """
        unique_ids = list(dict.fromkeys(gm_id for gm_id in gm_ids if gm_id))
        statuses: dict[str, dict[Side, str | None]] = {
            gm_id: {"H": None, "A": None} for gm_id in unique_ids
        }
        refs: list[Any] = []
        for gm_id in unique_ids:
            collection = self.db.collection("matches").document(gm_id).collection("recordings")
            refs.extend((collection.document("H"), collection.document("A")))

        for start in range(0, len(refs), 300):
            for snapshot in self.db.get_all(refs[start:start + 300], retry=None, timeout=20):
                if not snapshot.exists or snapshot.id not in {"H", "A"}:
                    continue
                match_ref = snapshot.reference.parent.parent
                if match_ref is None or match_ref.id not in statuses:
                    continue
                document = snapshot.to_dict() or {}
                value = document.get("status")
                statuses[match_ref.id][snapshot.id] = str(value) if value is not None else None
        return statuses

    def list_records(self, gm_id: str, side: Side, *, limit: int = 2000) -> list[tuple[str, RecordDoc]]:
        collection = self.db.collection("matches").document(gm_id).collection("recordings").document(side).collection("records")
        records = [
            (snapshot.id, _validate_document(RecordDoc, snapshot.to_dict() or {}, path=snapshot.reference.path))
            for snapshot in collection.limit(limit).stream(retry=None, timeout=15)
        ]
        return sorted(
            records,
            key=lambda item: (item[1].half, item[1].halfSeconds, item[1].seq, item[1].createdBy),
        )

    def read_match_kpi_source(self, gm_id: str, side: Side) -> MatchKpiSource:
        records = self.list_records(gm_id, side)
        serializable = [
            {"id": record_id, **record.model_dump(mode="json", exclude_none=False)}
            for record_id, record in records
        ]
        return MatchKpiSource(
            match=self.get_match(gm_id),
            recording=self.get_recording(gm_id, side),
            records=records,
            fingerprint=sha256(
                json.dumps(serializable, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )

    def list_player_stats(self, gm_id: str, side: Side) -> dict[str, dict[str, Any]]:
        collection = (
            self.db.collection("matches").document(gm_id)
            .collection("recordings").document(side).collection("playerStats")
        )
        return {
            snapshot.id: snapshot.to_dict() or {}
            for snapshot in collection.stream(retry=None, timeout=15)
        }

    def list_collection_documents(self, collection_name: str, *, limit: int = 2000) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snapshot.id, snapshot.to_dict() or {})
            for snapshot in self.db.collection(collection_name).limit(limit).stream(retry=None, timeout=30)
        ]

    def get_collection_document(self, collection_name: str, document_id: str) -> dict[str, Any] | None:
        snapshot = self.db.collection(collection_name).document(document_id).get(retry=None, timeout=10)
        return (snapshot.to_dict() or {}) if snapshot.exists else None

    def list_team_squad(self, team_id: str, *, limit: int = 60) -> list[tuple[str, dict[str, Any]]]:
        collection = self.db.collection("teams").document(team_id).collection("squad")
        return [
            (snapshot.id, snapshot.to_dict() or {})
            for snapshot in collection.limit(limit).stream(retry=None, timeout=15)
        ]

    @staticmethod
    def _contract_covers_match_date(contract: dict[str, Any], match_date: str) -> bool:
        def date_key(value: object) -> str | None:
            digits = "".join(re.findall(r"\d", str(value or "")))[:8]
            return digits if len(digits) == 8 else None

        target = date_key(match_date)
        if target is None:
            return False
        start = date_key(contract.get("from"))
        end = date_key(contract.get("to"))
        # 0000-00-00 and null mean an open-ended/unknown boundary in legacy data.
        if start and start != "00000000" and start > target:
            return False
        if end and end != "00000000" and end < target:
            return False
        return True

    def list_players_for_team(self, team_id: str, *, match_date: str, league_id: str | None = None, limit: int = 2000) -> list[tuple[str, dict[str, Any]]]:
        """Return players contracted to a team on the selected match date."""
        players: list[tuple[str, dict[str, Any]]] = []
        contracts_by_player: dict[str, dict[str, Any]] = {}
        contracts = self.db.collection_group("contracts").where(filter=FieldFilter("teamId", "==", team_id)).limit(limit)
        for contract_snapshot in contracts.stream(retry=None, timeout=30):
            contract = contract_snapshot.to_dict() or {}
            if not self._contract_covers_match_date(contract, match_date):
                continue
            contract_league = str(contract.get("leagueId") or "")
            if league_id and contract_league and contract_league != league_id:
                continue
            parent = contract_snapshot.reference.parent.parent
            if parent is None:
                continue
            previous = contracts_by_player.get(parent.id)
            if previous is None or str(contract.get("from") or "") > str(previous.get("from") or ""):
                contracts_by_player[parent.id] = contract

        player_documents: dict[str, dict[str, Any]] = {}
        missing_player_ids = [player_id for player_id in contracts_by_player if player_id not in player_documents]
        for start in range(0, len(missing_player_ids), 300):
            references = [self.db.collection("players").document(player_id) for player_id in missing_player_ids[start:start + 300]]
            for player_snapshot in self.db.get_all(references, retry=None, timeout=30):
                if player_snapshot.exists:
                    player_documents[player_snapshot.id] = player_snapshot.to_dict() or {}

        for player_id, contract in contracts_by_player.items():
            player = player_documents.get(player_id)
            if player is None:
                continue
            players.append((player_id, {
                "no": str(contract.get("no") or ""),
                "pos": str(contract.get("pos") or ""),
                "name": str(player.get("name") or player.get("nameKr") or player.get("nameFull") or player.get("nameEn") or player_id),
            }))
        return players

    @staticmethod
    def _input_squad_players(players: list[tuple[str, dict[str, Any]]]) -> list[dict[str, str]]:
        return sorted(
            [
                {
                    "playerId": player_id,
                    "no": str(player.get("no") or ""),
                    "name": str(player.get("name") or player_id),
                    "pos": str(player.get("pos") or ""),
                }
                for player_id, player in players
            ],
            key=lambda player: (player["pos"], player["no"], player["name"]),
        )

    @staticmethod
    def _input_squad_source(match: MatchDoc) -> dict[str, str]:
        return {
            "date": match.date,
            "leagueId": match.leagueId,
            "homeTeamId": match.homeTeamId,
            "awayTeamId": match.awayTeamId,
        }

    def _build_input_squads(self, match: MatchDoc) -> dict[str, list[dict[str, str]]]:
        return {
            side: self._input_squad_players(self.list_players_for_team(
                team_id,
                match_date=match.date,
                league_id=match.leagueId,
            ))
            for side, team_id in (("H", match.homeTeamId), ("A", match.awayTeamId))
        }

    def _input_squads_reference(self, gm_id: str):
        return self.db.collection("matches").document(gm_id).collection("inputSnapshots").document("squads")

    def get_or_create_input_squads(self, gm_id: str, match: MatchDoc) -> tuple[dict[str, list[dict[str, str]]], bool]:
        """Return the immutable-at-input-time roster snapshot for one fixture.

        A contract lookup is deliberately performed only when the snapshot is absent
        or the fixture identity changed. Input devices then make one cheap document
        read instead of scanning every historic team contract on each entry.
        """
        reference = self._input_squads_reference(gm_id)
        existing = reference.get(retry=None, timeout=10)
        document = existing.to_dict() or {}
        source = self._input_squad_source(match)
        home, away = document.get("H"), document.get("A")
        if document.get("source") == source and isinstance(home, list) and isinstance(away, list):
            return {"H": home, "A": away}, True
        return self.refresh_input_squads(gm_id, match), False

    def refresh_input_squads(self, gm_id: str, match: MatchDoc) -> dict[str, list[dict[str, str]]]:
        """Rebuild a fixture roster after its contract data has been corrected."""
        reference = self._input_squads_reference(gm_id)
        existing = reference.get(retry=None, timeout=10)
        squads = self._build_input_squads(match)
        now = utc_now()
        reference.set({
            "schemaVersion": 1,
            "source": self._input_squad_source(match),
            "H": squads["H"],
            "A": squads["A"],
            "createdAt": (existing.to_dict() or {}).get("createdAt", now) if existing.exists else now,
            "updatedAt": now,
        }, retry=None, timeout=30)
        return squads

    @staticmethod
    def _input_draft_id(gm_id: str, side: Side) -> str:
        return f"{gm_id}_{side}"

    def get_input_draft(self, gm_id: str, side: Side) -> dict[str, Any]:
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        snapshot = reference.get(retry=None, timeout=10)
        if not snapshot.exists:
            raise NotFoundError(f"Input draft not found: {gm_id}/{side}")
        return snapshot.to_dict() or {}

    def save_input_draft(
        self,
        gm_id: str,
        side: Side,
        *,
        payload: dict[str, Any],
        client_state: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        now = utc_now()
        current = reference.get(retry=None, timeout=10)
        document = {
            "gmId": gm_id,
            "side": side,
            "payload": payload,
            "clientState": client_state,
            "recorderLevel": payload.get("recorderLevel", "advanced"),
            "status": payload.get("status", "ready"),
            "updatedBy": user_id,
            "updatedAt": now,
            "createdAt": (current.to_dict() or {}).get("createdAt", now) if current.exists else now,
        }
        reference.set(document, retry=None, timeout=20)
        return document

    def delete_input_draft(self, gm_id: str, side: Side) -> None:
        self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side)).delete(retry=None, timeout=20)

    def list_input_drafts(self, *, recorder_level: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        # Drafts are operationally small. Filtering locally avoids requiring a composite index.
        documents = [snapshot.to_dict() or {} for snapshot in self.db.collection("inputDrafts").stream(retry=None, timeout=30)]
        return [
            document for document in documents
            if (recorder_level is None or document.get("recorderLevel") == recorder_level)
            and (status is None or document.get("status") == status)
        ]

    def restore_input_draft_from_raw(self, gm_id: str, side: Side, *, user_id: str) -> dict[str, Any]:
        """Make a new editable draft from an already-final raw recording."""
        recording = self.get_recording(gm_id, side)
        records = self.list_records(gm_id, side)
        recording_ref = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
        cards = [snapshot.to_dict() or {} for snapshot in recording_ref.collection("cards").stream(retry=None, timeout=15)]
        record_fields = {
            "half", "halfSeconds", "seq", "act", "res", "area", "posX", "posY",
            "shootPosX", "shootPosY", "shootDspRange", "isShot", "playerId",
        }
        match = self.get_match(gm_id)
        payload = {
            "gmId": gm_id,
            "side": side,
            "inputMode": recording.inputMode,
            "fieldSide": recording.fieldSide,
            "formationKey": recording.formationKey,
            "homeScore": match.score.home,
            "awayScore": match.score.away,
            "status": "final",
            "halves": {half: {"seconds": values.seconds} for half, values in recording.halves.items() if half in {"H1", "H2"}},
            "lineup": [{"playerId": player_id, **entry.model_dump(mode="python")} for player_id, entry in recording.lineup.items()],
            "records": [{"id": record_id, **{key: value for key, value in record.model_dump(mode="python").items() if key in record_fields}} for record_id, record in records],
            "cards": [{key: value for key, value in card.items() if key in {"playerId", "half", "halfSeconds", "card"}} for card in cards],
            "recorderLevel": "advanced",
            "matchSnapshot": {
                "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
                "date": match.date, "kickoffTime": match.kickoffTime, "stadiumId": match.stadiumId,
                "matchType": match.matchType, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
            },
            "formationChanges": [],
        }
        client_state = {"restoredFromRaw": True}
        return self.save_input_draft(gm_id, side, payload=payload, client_state=client_state, user_id=user_id)

    def replace_recording_raw(
        self,
        gm_id: str,
        side: Side,
        *,
        recording: dict[str, Any],
        records: list[tuple[str, dict[str, Any]]],
        cards: list[tuple[str, dict[str, Any]]],
        score: dict[str, int],
    ) -> None:
        recording_ref = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
        for name in ("records", "cards", "paths", "playerStats"):
            collection = recording_ref.collection(name)
            while True:
                snapshots = list(collection.limit(200).stream(retry=None, timeout=30))
                if not snapshots:
                    break
                batch = self.db.batch()
                for snapshot in snapshots:
                    batch.delete(snapshot.reference)
                batch.commit(retry=None, timeout=30)

        batch = self.db.batch()
        batch.set(recording_ref, recording)
        pending = 1
        for record_id, values in records:
            batch.set(recording_ref.collection("records").document(record_id), values)
            pending += 1
            if pending >= 200:
                batch.commit(retry=None, timeout=30)
                batch = self.db.batch()
                pending = 0
        for card_id, values in cards:
            batch.set(recording_ref.collection("cards").document(card_id), values)
            pending += 1
            if pending >= 200:
                batch.commit(retry=None, timeout=30)
                batch = self.db.batch()
                pending = 0
        if pending:
            batch.commit(retry=None, timeout=30)
        self.db.collection("matches").document(gm_id).update({"score": score, "updatedAt": utc_now()})

    def save_recording_kpi(
        self,
        gm_id: str,
        side: Side,
        *,
        kpi: RecordingKpi,
        kpi_version: int,
        source_fingerprint: str,
        player_kpis: dict[str, PlayerKpi],
        calculated_at: datetime,
    ) -> None:
        recording_ref = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
        batch = self.db.batch()
        batch.update(recording_ref, {
            "kpi": kpi.model_dump(mode="python"),
            "kpiComputedAt": calculated_at,
            "kpiVersion": kpi_version,
            "kpiSourceFingerprint": source_fingerprint,
            "teamRating": None,
            "ratingBasedOn": None,
            "updatedAt": calculated_at,
        })
        for player_id, player_kpi in player_kpis.items():
            player_ref = recording_ref.collection("playerStats").document(player_id)
            batch.set(player_ref, {
                **player_kpi.model_dump(mode="python"),
                "scoreRel": None,
                "scoreAbs": None,
                "score": None,
                "jmx": None,
                "apx": None,
                "apxGrade": None,
                "tpx": None,
                "tpxGrade": None,
                "fpx": None,
                "fpxGrade": None,
                "ratingBasedOn": None,
            }, merge=True)
        batch.commit(retry=None, timeout=20)

    def save_recording_rating(
        self,
        gm_id: str,
        side: Side,
        *,
        team_rating: TeamRatingSnapshot,
        rating_based_on: int,
        player_ratings: list[dict[str, Any]],
        calculated_at: datetime,
    ) -> int:
        recording_ref = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
        batch = self.db.batch()
        batch.update(recording_ref, {
            "teamRating": team_rating.model_dump(mode="python"),
            "ratingBasedOn": rating_based_on,
            "updatedAt": calculated_at,
        })
        saved = 0
        for response in player_ratings:
            player_id = response.get("playerId")
            if not isinstance(player_id, str) or not player_id:
                continue
            fields = {key: value for key, value in response.items() if key in PLAYER_RATING_FIELDS}
            fields["ratingBasedOn"] = rating_based_on
            batch.set(recording_ref.collection("playerStats").document(player_id), fields, merge=True)
            saved += 1
        batch.commit(retry=None, timeout=20)
        return saved


def round_key_for(match: MatchDoc) -> str:
    if match.round is not None:
        return f"round-{match.round}"
    if match.stage:
        parts = ["stage", match.stage]
        if match.group:
            parts.extend(("group", match.group))
        if match.leg is not None:
            parts.extend(("leg", str(match.leg)))
        return "-".join(parts)
    raise NotFoundError("Match needs a league round or tournament stage before KPI calculation")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
