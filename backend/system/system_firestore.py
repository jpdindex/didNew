from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

from backend.system.system_kpi_rules import KpiRecord, calculate_kpis
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
        local_uid = request.headers.get("X-Did-Local-User", "").strip()
        if local_uid:
            return CurrentUser(uid=local_uid, claims={"auth": "local_frontend", "uidSource": "firebase_client"})
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
                "lifecycleStatus": "final" if raw_status == "final" else "ready",
            }
        # A live Draft is the shared lifecycle source until its RAW is final.
        # IndexedDB is intentionally excluded: it belongs to one device only.
        for side in ("H", "A"):
            if states[side]["completed"]:
                continue
            try:
                draft = self.get_input_draft(gm_id, side)
            except NotFoundError:
                continue
            shared = draft.get("sharedState") if isinstance(draft.get("sharedState"), dict) else {}
            payload = draft.get("payload") if isinstance(draft.get("payload"), dict) else {}
            lifecycle = shared.get("halfStatus") or payload.get("status") or draft.get("status")
            if lifecycle in {"H1", "H1_done", "H2", "H2_done", "final"}:
                states[side]["lifecycleStatus"] = lifecycle
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
    def _normalize_input_position(value: Any) -> str:
        """Collapse historic position labels to the four input-screen groups."""
        raw = re.sub(r"[^A-Z]", "", str(value or "").upper())
        if raw in {"GK", "G", "GOALKEEPER", "KEEPER"}:
            return "GK"
        if raw in {"FW", "F", "ST", "CF", "SS", "LW", "RW", "LF", "RF", "FORWARD", "STRIKER"}:
            return "FW"
        if raw in {"MF", "M", "DM", "CDM", "CM", "CAM", "AM", "LM", "RM", "MIDFIELDER"}:
            return "MF"
        if raw in {"DF", "D", "CB", "LB", "RB", "LWB", "RWB", "SW", "DEFENDER"}:
            return "DF"
        return str(value or "").upper()

    @classmethod
    def _input_squad_players(cls, players: list[tuple[str, dict[str, Any]]]) -> list[dict[str, str]]:
        return sorted(
            [
                {
                    "playerId": player_id,
                    "no": str(player.get("no") or ""),
                    "name": str(player.get("name") or player_id),
                    "pos": cls._normalize_input_position(player.get("pos")),
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

    @staticmethod
    def _legacy_lineup_fingerprint(recording: RecordingDoc) -> str:
        """Fingerprint only the durable fields that define an imported lineup."""
        body = {
            "formationKey": recording.formationKey,
            "lineup": {
                player_id: entry.model_dump(mode="json")
                for player_id, entry in sorted(recording.lineup.items())
            },
        }
        return sha256(json.dumps(body, ensure_ascii=True, sort_keys=True).encode()).hexdigest()

    def _legacy_recordings(self, gm_id: str) -> dict[Side, RecordingDoc]:
        """Return only SQL-imported final recordings that need lineup snapshots."""
        result: dict[Side, RecordingDoc] = {}
        for side in ("H", "A"):
            try:
                recording = self.get_recording(gm_id, side)
            except NotFoundError:
                continue
            if recording.status == "final" and recording.legacyGiId is not None and recording.lineup:
                result[side] = recording
        return result

    def _merge_legacy_lineup_squad(
        self,
        recording: RecordingDoc,
        roster: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Merge imported lineup players into an already-resolved roster."""
        by_id = {
            str(player.get("playerId")): {
                "playerId": str(player.get("playerId")),
                "no": str(player.get("no") or ""),
                "name": str(player.get("name") or player.get("playerId") or ""),
                "pos": self._normalize_input_position(player.get("pos")),
            }
            for player in roster if player.get("playerId")
        }
        for player_id, entry in recording.lineup.items():
            existing = by_id.get(player_id, {})
            by_id[player_id] = {
                "playerId": player_id,
                "no": str(existing.get("no") or entry.no or ""),
                "name": str(existing.get("name") or entry.name or player_id),
                "pos": self._normalize_input_position(existing.get("pos") or entry.pos),
            }
        return sorted(by_id.values(), key=lambda player: (player["pos"], player["no"], player["name"]))

    def _legacy_lineup_squad(
        self,
        match: MatchDoc,
        side: Side,
        recording: RecordingDoc,
    ) -> list[dict[str, str]]:
        """Resolve a historic roster from date-valid contracts, then merge lineup players."""
        team_id = match.homeTeamId if side == "H" else match.awayTeamId
        roster = self._input_squad_players(self.list_players_for_team(
            team_id, match_date=match.date, league_id=match.leagueId,
        ))
        return self._merge_legacy_lineup_squad(recording, roster)

    def _legacy_lineup_snapshot(
        self,
        recording: RecordingDoc,
        squad: list[dict[str, str]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Resolve one imported lineup into explicit GK / START / BENCH lanes.

        Historic SQL numbers GK separately from outfield START players, so both the
        goalkeeper and the first outfield player can have order=1. Position is used
        only to identify the GK; outfield and bench order remain the original SQL
        order and are never inferred from formation coordinates.
        """
        squad_by_id = {player["playerId"]: player for player in squad}
        rows: list[dict[str, Any]] = []
        for player_id, entry in recording.lineup.items():
            squad_player = squad_by_id.get(player_id, {})
            pos = self._normalize_input_position(squad_player.get("pos") or entry.pos)
            rows.append({
                "playerId": player_id,
                "slot": str(entry.slot or ""),
                "order": int(entry.order),
                "type": entry.type,
                "no": str(squad_player.get("no") or entry.no or ""),
                "name": str(squad_player.get("name") or entry.name or player_id),
                "pos": pos,
                "inHalf": entry.inHalf,
                "inSeconds": entry.inSeconds,
                "outHalf": entry.outHalf,
                "outSeconds": entry.outSeconds,
            })

        starters = [row for row in rows if row["type"] == "START"]
        goalkeeper_candidates = [
            row for row in starters
            if row["slot"] == "gk" or row["pos"] == "GK"
        ]
        issues: list[str] = []
        goalkeeper: dict[str, Any] | None = None
        if len(goalkeeper_candidates) == 1:
            goalkeeper = goalkeeper_candidates[0]
        elif len(goalkeeper_candidates) > 1:
            # A historic substitution can leave more than one GK-classified player
            # in START. The actual initial GK is the earliest SQL lane/order entry.
            goalkeeper = min(goalkeeper_candidates, key=lambda row: (row["order"], row["playerId"]))
            issues.append("multiple_goalkeeper_candidates")
        else:
            issues.append("goalkeeper_unresolved")

        if goalkeeper is not None:
            goalkeeper["slot"] = "gk"
            # SQL's GK lane has its own order sequence starting at 1.
            goalkeeper["order"] = 1
            goalkeeper["pos"] = "GK"

        outfield = sorted(
            [row for row in starters if row is not goalkeeper],
            key=lambda row: (row["order"], row["playerId"]),
        )
        bench = sorted(
            [row for row in rows if row["type"] == "BENCH"],
            key=lambda row: (row["order"], row["playerId"]),
        )

        if len(outfield) != 10:
            issues.append(f"outfield_start_count:{len(outfield)}")
        if len({row["order"] for row in outfield}) != len(outfield):
            issues.append("duplicate_outfield_order")

        # The UI assigns these rows to formation coordinates. Keep the lane label
        # semantic rather than leaking old player-id slots into the new snapshot.
        for row in outfield:
            row["slot"] = "start"
        for row in bench:
            row["slot"] = "bench"

        normalized = ([goalkeeper] if goalkeeper is not None else []) + outfield + bench
        return normalized, issues

    def _build_legacy_input_snapshot(
        self,
        match: MatchDoc,
        recordings: dict[Side, RecordingDoc],
        *,
        seed_squads: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, Any]]]:
        squads: dict[str, list[dict[str, str]]] = {}
        legacy_lineups: dict[str, dict[str, Any]] = {}
        team_ids = {"H": match.homeTeamId, "A": match.awayTeamId}
        seed_squads = seed_squads or {}
        for side in ("H", "A"):
            recording = recordings.get(side)  # type: ignore[arg-type]
            seed = seed_squads.get(side)
            if recording is None:
                if seed is not None:
                    squads[side] = [
                        {
                            "playerId": str(player.get("playerId")),
                            "no": str(player.get("no") or ""),
                            "name": str(player.get("name") or player.get("playerId") or ""),
                            "pos": self._normalize_input_position(player.get("pos")),
                        }
                        for player in seed if player.get("playerId")
                    ]
                else:
                    squads[side] = self._input_squad_players(self.list_players_for_team(
                        team_ids[side], match_date=match.date, league_id=match.leagueId,
                    ))
                continue
            squad = (
                self._merge_legacy_lineup_squad(recording, seed)
                if seed is not None
                else self._legacy_lineup_squad(match, side, recording)  # type: ignore[arg-type]
            )
            lineup, issues = self._legacy_lineup_snapshot(recording, squad)
            squads[side] = squad
            legacy_lineups[side] = {
                "formationKey": recording.formationKey,
                "fingerprint": self._legacy_lineup_fingerprint(recording),
                "lineup": lineup,
                "issues": issues,
            }
        return squads, legacy_lineups

    def get_or_create_input_squads(self, gm_id: str, match: MatchDoc) -> tuple[dict[str, list[dict[str, str]]], bool]:
        """Return a fixture roster snapshot without re-querying contracts once cached."""
        reference = self._input_squads_reference(gm_id)
        existing = reference.get(retry=None, timeout=10)
        document = existing.to_dict() or {}
        source = self._input_squad_source(match)
        home, away = document.get("H"), document.get("A")
        if (
            document.get("schemaVersion") == 3
            and document.get("source") == source
            and isinstance(home, list)
            and isinstance(away, list)
        ):
            return {"H": home, "A": away}, True
        seed_squads = {"H": home, "A": away} if (
            document.get("source") == source and isinstance(home, list) and isinstance(away, list)
        ) else None
        return self.refresh_input_squads(gm_id, match, seed_squads=seed_squads), False

    def refresh_input_squads(
        self,
        gm_id: str,
        match: MatchDoc,
        *,
        legacy_recordings: dict[Side, RecordingDoc] | None = None,
        seed_squads: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        """Rebuild squad + imported-lineup snapshot after source data is corrected."""
        reference = self._input_squads_reference(gm_id)
        existing = reference.get(retry=None, timeout=10)
        legacy_recordings = legacy_recordings if legacy_recordings is not None else self._legacy_recordings(gm_id)
        if legacy_recordings:
            squads, legacy_lineups = self._build_legacy_input_snapshot(match, legacy_recordings, seed_squads=seed_squads)
        else:
            squads, legacy_lineups = self._build_input_squads(match), {}
        now = utc_now()
        reference.set({
            "schemaVersion": 3,
            "source": self._input_squad_source(match),
            "H": squads["H"],
            "A": squads["A"],
            "legacyLineup": legacy_lineups,
            "createdAt": (existing.to_dict() or {}).get("createdAt", now) if existing.exists else now,
            "updatedAt": now,
        }, retry=None, timeout=30)
        return squads

    def _read_legacy_snapshot_lineup(
        self,
        gm_id: str,
        side: Side,
        recording: RecordingDoc,
        match: MatchDoc,
    ) -> list[dict[str, Any]] | None:
        """Read normalized imported lineup; create the v3 snapshot once if needed."""
        reference = self._input_squads_reference(gm_id)
        snapshot = reference.get(retry=None, timeout=10)
        document = snapshot.to_dict() or {}
        source = self._input_squad_source(match)
        if document.get("schemaVersion") != 3 or document.get("source") != source:
            self.refresh_input_squads(gm_id, match)
            snapshot = reference.get(retry=None, timeout=10)
            document = snapshot.to_dict() or {}
        legacy = document.get("legacyLineup") if isinstance(document.get("legacyLineup"), dict) else {}
        side_snapshot = legacy.get(side) if isinstance(legacy.get(side), dict) else {}
        lineup = side_snapshot.get("lineup")
        if (
            side_snapshot.get("fingerprint") == self._legacy_lineup_fingerprint(recording)
            and isinstance(lineup, list)
        ):
            return lineup
        # Explicit RAW/squad refreshes can change the imported lineup. Rebuild only
        # in that exceptional path; normal lobby reads stay cache-only.
        self.refresh_input_squads(gm_id, match)
        refreshed = reference.get(retry=None, timeout=10).to_dict() or {}
        legacy = refreshed.get("legacyLineup") if isinstance(refreshed.get("legacyLineup"), dict) else {}
        side_snapshot = legacy.get(side) if isinstance(legacy.get(side), dict) else {}
        return side_snapshot.get("lineup") if isinstance(side_snapshot.get("lineup"), list) else None

    def backfill_legacy_input_squads(self, *, limit: int = 100) -> dict[str, int]:
        """Create v3 imported-match snapshots without mutating RAW recordings."""
        counts = {"scanned": 0, "created": 0, "unchanged": 0, "failed": 0}
        for gm_id, match in self.list_matches(limit=limit):
            recordings = self._legacy_recordings(gm_id)
            if not recordings:
                continue
            counts["scanned"] += 1
            try:
                reference = self._input_squads_reference(gm_id)
                current = reference.get(retry=None, timeout=10).to_dict() or {}
                if current.get("schemaVersion") == 3 and current.get("source") == self._input_squad_source(match):
                    counts["unchanged"] += 1
                    continue
                self.refresh_input_squads(gm_id, match, legacy_recordings=recordings)
                counts["created"] += 1
            except Exception:
                logger.exception("Legacy input snapshot backfill failed for %s", gm_id)
                counts["failed"] += 1
        return counts

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
        previous = current.to_dict() or {}
        previous_shared = previous.get("sharedState") if isinstance(previous.get("sharedState"), dict) else {}
        # Direct Firestore sync owns normal live edits. A REST lifecycle save is
        # an explicit checkpoint, so it must advance the same shared status too;
        # otherwise schedule reads can keep showing the old H1/H2 value.
        shared_state = {
            **previous_shared,
            "halfStatus": payload.get("status", "ready"),
            "seconds": client_state.get("seconds", previous_shared.get("seconds", 0)),
            "h1Seconds": client_state.get("h1Seconds", payload.get("halves", {}).get("H1", {}).get("seconds", 0)),
            "h2Seconds": client_state.get("h2Seconds", payload.get("halves", {}).get("H2", {}).get("seconds", 0)),
            "clockStartedAt": client_state.get("clockStartedAt"),
        }
        document = {
            "gmId": gm_id,
            "side": side,
            "payload": payload,
            "clientState": client_state,
            "recorderLevel": payload.get("recorderLevel", "advanced"),
            "status": payload.get("status", "ready"),
            "updatedBy": user_id,
            "updatedAt": now,
            "createdAt": previous.get("createdAt", now) if current.exists else now,
            # Collaboration metadata is deliberately retained when lifecycle saves
            # replace the validated payload snapshot.
            "participants": previous.get("participants", {}),
            "primaryUid": previous.get("primaryUid"),
            "collaboration": previous.get("collaboration", {}),
            "sharedState": shared_state,
        }
        reference.set(document, retry=None, timeout=20)
        return document

    def join_input_draft_participant(
        self,
        gm_id: str,
        side: Side,
        *,
        user_id: str,
        role: Literal["primary", "assistant"],
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Register a recorder on the existing Draft root without touching records.

        The browser writes live state/records directly under this same Draft. This
        server endpoint only establishes the durable role assignment used by rules
        and by final RAW promotion.
        """
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        snapshot = reference.get(retry=None, timeout=10)
        now = utc_now()
        current = snapshot.to_dict() or {}
        profile_snapshot = self.db.collection("recorders").document(user_id).get(retry=None, timeout=10)
        profile = profile_snapshot.to_dict() or {}
        recorder_level = "basic" if profile.get("level") == "basic" else "advanced"
        primary_uid = str(current.get("primaryUid") or "")
        participants = current.get("participants") if isinstance(current.get("participants"), dict) else {}
        existing_participant = participants.get(user_id) if isinstance(participants.get(user_id), dict) else {}
        existing_role = existing_participant.get("role")
        if existing_role in {"primary", "assistant"} and existing_role != role:
            raise BackendError("An analyst cannot hold both primary and assistant roles for one team session", status_code=409, code="participant_role_conflict")
        if role == "primary":
            if primary_uid and primary_uid != user_id:
                legacy_primary = participants.get(primary_uid) if isinstance(participants.get(primary_uid), dict) else {}
                # Drafts created before local Firebase UID forwarding used one
                # shared local UID. Let the same named analyst reclaim it once.
                if primary_uid == "local-did-input" and display_name and legacy_primary.get("name") == display_name:
                    participants.pop(primary_uid, None)
                    current["participants"] = participants
                else:
                    raise BackendError("A primary analyst is already assigned for this team", status_code=409, code="primary_already_assigned")
            primary_uid = user_id
        elif not primary_uid:
            raise BackendError("The primary analyst must join before an assistant", status_code=409, code="primary_required")

        participants[user_id] = {
            "role": role,
            "name": display_name or user_id,
            "level": recorder_level,
            "joinedAt": participants.get(user_id, {}).get("joinedAt", now),
            "lastSeenAt": now,
        }
        document = {
            **current,
            "gmId": gm_id,
            "side": side,
            "primaryUid": primary_uid,
            "participants": participants,
            "updatedAt": now,
            "updatedBy": user_id,
            "createdAt": current.get("createdAt", now),
        }
        reference.set(document, retry=None, timeout=20)
        return document

    def get_input_draft_participants_many(self, gm_ids: list[str]) -> dict[str, dict[Side, dict[str, Any]]]:
        """Schedule-friendly collaboration summary without streaming all Drafts."""
        result: dict[str, dict[Side, dict[str, Any]]] = {gm_id: {"H": {}, "A": {}} for gm_id in gm_ids}
        refs = [
            self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
            for gm_id in result for side in ("H", "A")
        ]
        for start in range(0, len(refs), 300):
            for snapshot in self.db.get_all(refs[start:start + 300], retry=None, timeout=20):
                if not snapshot.exists:
                    continue
                document = snapshot.to_dict() or {}
                gm_id, side = document.get("gmId"), document.get("side")
                if gm_id not in result or side not in {"H", "A"}:
                    continue
                participants = document.get("participants") if isinstance(document.get("participants"), dict) else {}
                shared = document.get("sharedState") if isinstance(document.get("sharedState"), dict) else {}
                payload = document.get("payload") if isinstance(document.get("payload"), dict) else {}
                lifecycle = shared.get("halfStatus") or payload.get("status") or document.get("status")
                # Live input writes scores to sharedState. A lifecycle checkpoint
                # keeps the same values in payload, which also covers a Draft
                # opened before direct Firestore sync has started.
                score_source = shared if "homeScore" in shared or "awayScore" in shared else payload
                score = None
                if isinstance(score_source.get("homeScore"), int) and isinstance(score_source.get("awayScore"), int):
                    score = {"home": score_source["homeScore"], "away": score_source["awayScore"]}
                result[gm_id][side] = {
                    "status": lifecycle,
                    "primaryUid": document.get("primaryUid"),
                    "score": score,
                    "participants": [
                        {"uid": uid, "role": values.get("role"), "name": values.get("name")}
                        for uid, values in participants.items() if isinstance(values, dict)
                    ],
                }
        return result

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

    def build_analyst_dashboard(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        level: Literal["basic", "advanced"] | None = None,
        lifecycle: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate operational analyst work from collaborative Drafts.

        Analyst identities intentionally never enter finalized RAW. A Draft is
        retained after promotion, so it is the durable operational audit source
        for role, team assignment, and the KPI volume of a worked fixture.
        """
        def normalized_date(value: Any) -> str:
            digits = re.sub(r"\D", "", str(value or ""))
            return digits[:8] if len(digits) >= 8 else ""

        def as_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def timestamp_value(value: Any) -> str | None:
            return value.isoformat() if hasattr(value, "isoformat") else None

        from_key = normalized_date(date_from)
        to_key = normalized_date(date_to)
        drafts: list[tuple[Any, dict[str, Any], str, Side]] = []
        for snapshot in self.db.collection("inputDrafts").stream(retry=None, timeout=45):
            document = snapshot.to_dict() or {}
            gm_id = str(document.get("gmId") or "")
            side = document.get("side")
            participants = document.get("participants")
            if not gm_id or side not in {"H", "A"} or not isinstance(participants, dict) or not participants:
                continue
            drafts.append((snapshot, document, gm_id, side))

        match_refs = [self.db.collection("matches").document(gm_id) for _, _, gm_id, _ in drafts]
        matches: dict[str, dict[str, Any]] = {}
        for start in range(0, len(match_refs), 300):
            for snapshot in self.db.get_all(match_refs[start:start + 300], retry=None, timeout=30):
                if snapshot.exists:
                    matches[snapshot.id] = snapshot.to_dict() or {}

        team_ids = {
            str(match.get(team_key) or "")
            for match in matches.values()
            for team_key in ("homeTeamId", "awayTeamId")
            if match.get(team_key)
        }
        teams = self.get_documents_by_ids("teams", team_ids)

        sessions: list[dict[str, Any]] = []
        participant_ids: set[str] = set()
        for snapshot, document, gm_id, side in drafts:
            match = matches.get(gm_id, {})
            payload = document.get("payload") if isinstance(document.get("payload"), dict) else {}
            shared = document.get("sharedState") if isinstance(document.get("sharedState"), dict) else {}
            match_snapshot = payload.get("matchSnapshot") if isinstance(payload.get("matchSnapshot"), dict) else {}
            match_date = normalized_date(match.get("date") or match_snapshot.get("date"))
            if (from_key and (not match_date or match_date < from_key)) or (to_key and (not match_date or match_date > to_key)):
                continue
            status = str(shared.get("halfStatus") or payload.get("status") or document.get("status") or "ready")
            if lifecycle and status != lifecycle:
                continue

            team_id = str((match.get("awayTeamId") if side == "A" else match.get("homeTeamId")) or "")
            opponent_id = str((match.get("homeTeamId") if side == "A" else match.get("awayTeamId")) or "")
            team = teams.get(team_id, {})
            opponent = teams.get(opponent_id, {})
            team_name = str(team.get("nameKr") or team.get("name") or team.get("nameShort") or team_id)
            opponent_name = str(opponent.get("nameKr") or opponent.get("name") or opponent.get("nameShort") or opponent_id)

            # Prefer each live record document when present. The checkpoint
            # payload supplies the completed-half fallback and old Draft data.
            record_map = {
                str(record.get("id")): record
                for record in payload.get("records", [])
                if isinstance(record, dict) and record.get("id")
            }
            for record_snapshot in snapshot.reference.collection("records").stream(retry=None, timeout=30):
                record = record_snapshot.to_dict() or {}
                if record.get("deleted"):
                    record_map.pop(record_snapshot.id, None)
                else:
                    record_map[record_snapshot.id] = {"id": record_snapshot.id, **record}
            kpi_records = [
                KpiRecord(
                    id=str(record.get("id") or record_id),
                    half=str(record.get("half") or "H1"),
                    seconds=as_int(record.get("halfSeconds", record.get("seconds", 0))),
                    seq=as_int(record.get("seq")),
                    act=str(record.get("act") or ""),
                    res=str(record.get("res") or ""),
                    area=as_int(record.get("area"), 18),
                    player_id=str(record["playerId"]) if record.get("playerId") else None,
                    shoot_pos_x=record.get("shootPosX"),
                    shoot_pos_y=record.get("shootPosY"),
                    shoot_dsp_range=record.get("shootDspRange"),
                    is_shot=record.get("isShot"),
                    created_by=str(record.get("updatedBy") or ""),
                )
                for record_id, record in record_map.items()
            ]
            dap = calculate_kpis(kpi_records).team_kpi["DAP"] if kpi_records else 0
            score_source = shared if "homeScore" in shared else payload
            match_score = match.get("score") if isinstance(match.get("score"), dict) else {}
            participants = document.get("participants") if isinstance(document.get("participants"), dict) else {}
            participant_rows = []
            for uid, participant in participants.items():
                if not isinstance(participant, dict) or participant.get("role") not in {"primary", "assistant"}:
                    continue
                participant_ids.add(uid)
                participant_rows.append({
                    "uid": uid,
                    "name": str(participant.get("name") or uid),
                    "role": participant["role"],
                    "level": "basic" if participant.get("level") == "basic" else "advanced",
                })
            if not participant_rows:
                continue
            sessions.append({
                "gmId": gm_id,
                "side": side,
                "date": match_date,
                "status": status,
                "teamId": team_id,
                "teamName": team_name,
                "opponentName": opponent_name,
                "leagueId": str(match.get("leagueId") or match_snapshot.get("leagueId") or "-"),
                "round": match.get("round") if match.get("round") is not None else match_snapshot.get("round"),
                "score": {"home": as_int(score_source.get("homeScore", match_score.get("home", 0))), "away": as_int(score_source.get("awayScore", match_score.get("away", 0)))},
                "dap": dap,
                "recordCount": len(kpi_records),
                "updatedAt": timestamp_value(document.get("updatedAt")),
                "participants": participant_rows,
            })

        profiles = self.get_documents_by_ids("recorders", participant_ids)
        analysts: dict[str, dict[str, Any]] = {}
        for session in sessions:
            for participant in session["participants"]:
                uid = participant["uid"]
                profile = profiles.get(uid, {})
                analyst = analysts.setdefault(uid, {
                    "uid": uid,
                    "name": str(profile.get("name") or profile.get("displayName") or participant["name"]),
                    "level": "basic" if profile.get("level") == "basic" else participant["level"],
                    "matchIds": set(), "teamInputs": 0, "primaryCount": 0, "assistantCount": 0,
                    "dapTotal": 0, "teams": {}, "recentInputs": [], "latestAt": None,
                })
                analyst["matchIds"].add(session["gmId"])
                analyst["teamInputs"] += 1
                analyst["primaryCount"] += int(participant["role"] == "primary")
                analyst["assistantCount"] += int(participant["role"] == "assistant")
                analyst["dapTotal"] += session["dap"]
                analyst["teams"][session["teamId"]] = {
                    "teamId": session["teamId"], "teamName": session["teamName"],
                    "count": analyst["teams"].get(session["teamId"], {}).get("count", 0) + 1,
                }
                analyst["recentInputs"].append({
                    **{key: session[key] for key in ("gmId", "side", "date", "status", "teamName", "opponentName", "leagueId", "round", "score", "dap", "recordCount", "updatedAt")},
                    "role": participant["role"],
                })
                if session["updatedAt"] and (not analyst["latestAt"] or session["updatedAt"] > analyst["latestAt"]):
                    analyst["latestAt"] = session["updatedAt"]

        rows = []
        for analyst in analysts.values():
            if level and analyst["level"] != level:
                continue
            recent = sorted(analyst["recentInputs"], key=lambda item: (item["date"], item["updatedAt"] or ""), reverse=True)
            top_dap_input = max(
                analyst["recentInputs"],
                key=lambda item: (item["dap"], item["date"], item["updatedAt"] or ""),
                default=None,
            )
            rows.append({
                "uid": analyst["uid"], "name": analyst["name"], "level": analyst["level"],
                "matches": len(analyst["matchIds"]), "teamInputs": analyst["teamInputs"],
                "primaryCount": analyst["primaryCount"], "assistantCount": analyst["assistantCount"],
                "dapTotal": analyst["dapTotal"], "latestAt": analyst["latestAt"],
                "teams": sorted(analyst["teams"].values(), key=lambda item: (-item["count"], item["teamName"])),
                "topDapInput": top_dap_input,
                "recentInputs": recent[:8],
            })
        rows.sort(key=lambda item: (-item["dapTotal"], -item["matches"], item["name"]))
        return {
            "summary": {
                "analystCount": len(rows),
                "matchCount": len({session["gmId"] for session in sessions}),
                "teamInputCount": len(sessions),
                "dapTotal": sum(session["dap"] for session in sessions),
            },
            "analysts": rows,
            "generatedAt": utc_now().isoformat(),
        }

    def read_input_state_from_raw(
        self,
        gm_id: str,
        side: Side,
        *,
        include_records: bool = True,
        match: MatchDoc | None = None,
        recording: RecordingDoc | None = None,
    ) -> dict[str, Any]:
        """Convert final RAW to an input-screen snapshot without writing a Draft.

        Lobby bootstrap uses ``include_records=False`` so lineup + KPI can paint
        without streaming every event. The edit/restore endpoint keeps the full
        path and loads records/cards only when the user actually edits RAW.
        """
        recording = recording or self.get_recording(gm_id, side)
        if recording.status != "final":
            raise BackendError("Only final RAW can be opened as a completed input", status_code=409, code="recording_not_final")
        match = match or self.get_match(gm_id)

        raw_lineup = [{"playerId": player_id, **entry.model_dump(mode="python")} for player_id, entry in recording.lineup.items()]
        if recording.legacyGiId is not None:
            normalized = self._read_legacy_snapshot_lineup(gm_id, side, recording, match)
            if normalized:
                raw_lineup = normalized

        records: list[tuple[str, RecordDoc]] = []
        cards: list[dict[str, Any]] = []
        if include_records:
            records = self.list_records(gm_id, side)
            recording_ref = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
            cards = [snapshot.to_dict() or {} for snapshot in recording_ref.collection("cards").stream(retry=None, timeout=15)]

        record_fields = {
            "half", "halfSeconds", "seq", "act", "res", "area", "posX", "posY",
            "shootPosX", "shootPosY", "shootDspRange", "isShot", "playerId",
        }
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
            "lineup": raw_lineup,
            "records": [{"id": record_id, **{key: value for key, value in record.model_dump(mode="python").items() if key in record_fields}} for record_id, record in records],
            "cards": [{key: value for key, value in card.items() if key in {"playerId", "half", "halfSeconds", "card"}} for card in cards],
            "matchSnapshot": {
                "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
                "date": match.date, "kickoffTime": match.kickoffTime, "stadiumId": match.stadiumId,
                "matchType": match.matchType, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
            },
            "formationChanges": [],
        }
        return {
            "gmId": gm_id,
            "side": side,
            "payload": payload,
            "clientState": {"restoredFromRaw": True, "summaryOnly": not include_records},
            "updatedAt": recording.updatedAt,
        }

    def read_match_dashboard_kpis(
        self,
        gm_id: str,
        *,
        half: Literal["all", "H1", "H2"] = "all",
    ) -> dict[Side, dict[str, int | float]]:
        """Return both teams' display KPI without reading unused historic records."""
        fields = ("TAP", "DAP", "DTP", "Shoot", "Goal", "SSR", "BAP", "ASR")

        def display(values: dict[str, Any]) -> dict[str, int | float]:
            result: dict[str, int | float] = {}
            for field in fields:
                source_field = {"Shoot": "SHOT", "Goal": "GOAL"}.get(field, field)
                value = values.get(source_field, 0)
                # KPI engine ratios are 0..1; legacy final RAW stores 0..100.
                if field in {"ASR", "SSR"} and isinstance(value, (int, float)) and 0 <= value <= 1:
                    value = round(value * 100)
                result[field] = value if isinstance(value, (int, float)) else 0
            return result

        def calculated(records: list[tuple[str, RecordDoc]]) -> dict[str, int | float]:
            source = [
                KpiRecord(
                    id=record_id, half=record.half, seconds=record.halfSeconds, seq=record.seq,
                    act=record.act, res=record.res, area=record.area, player_id=record.playerId,
                    shoot_pos_x=record.shootPosX, shoot_pos_y=record.shootPosY,
                    shoot_dsp_range=record.shootDspRange, is_shot=record.isShot,
                )
                for record_id, record in records
            ]
            return display(calculate_kpis(source).team_kpi)

        result: dict[Side, dict[str, int | float]] = {"H": display({}), "A": display({})}
        for side in ("H", "A"):
            try:
                recording = self.get_recording(gm_id, side)
            except NotFoundError:
                continue
            # Imported/finalized all-match KPI is already stored on the small
            # recording document. Avoid streaming thousands of raw rows until a
            # user explicitly opens an individual half.
            if half == "all" and recording.kpi is not None:
                result[side] = display(recording.kpi.model_dump(mode="python"))
                continue
            records = self.list_records(gm_id, side)
            scoped = records if half == "all" else [item for item in records if item[1].half == half]
            result[side] = calculated(scoped)
        return result

    def read_match_dashboard_kpis_bootstrap(self, gm_id: str) -> dict[Side, dict[str, dict[str, int | float]]]:
        """Return only the initially visible all-match KPI view.

        H1/H2 are lazy-loaded when the user selects those tabs. This avoids two
        full RAW scans per team before the lobby can render.
        """
        fields = ("TAP", "DAP", "DTP", "Shoot", "Goal", "SSR", "BAP", "ASR")
        empty = {field: 0 for field in fields}
        all_match = self.read_match_dashboard_kpis(gm_id, half="all")
        return {
            side: {
                "all": all_match[side],
                "H1": dict(empty),
                "H2": dict(empty),
            }
            for side in ("H", "A")
        }

    def build_input_bootstrap(self, gm_id: str, side: Side) -> dict[str, Any]:
        """Return the lobby-critical data in one response with parallel independent reads."""
        match = self.get_match(gm_id)
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="input-bootstrap") as executor:
            squads_future = executor.submit(self.get_or_create_input_squads, gm_id, match)
            status_future = executor.submit(self.get_recording_input_states, gm_id)
            kpi_future = executor.submit(self.read_match_dashboard_kpis_bootstrap, gm_id)
            squads, cached = squads_future.result()
            input_status = status_future.result()
            dashboard_kpis = kpi_future.result()

        selected_status = input_status[side]
        if selected_status["rawStatus"] == "final":
            # Reuse the already-read final recording state where possible, but do
            # not stream event rows just to paint the lobby.
            recording = self.get_recording(gm_id, side)
            session: dict[str, Any] = {
                "status": "ok",
                **self.read_input_state_from_raw(
                    gm_id,
                    side,
                    include_records=False,
                    match=match,
                    recording=recording,
                ),
            }
        else:
            try:
                draft = self.get_input_draft(gm_id, side)
            except NotFoundError:
                session = {"status": "missing", "gmId": gm_id, "side": side}
            else:
                session = {
                    "status": "ok",
                    "gmId": gm_id,
                    "side": side,
                    "payload": draft.get("payload"),
                    "clientState": draft.get("clientState") or {},
                    "updatedAt": draft.get("updatedAt"),
                } if draft.get("payload") else {"status": "missing", "gmId": gm_id, "side": side}
        return {
            "gmId": gm_id,
            "homeTeamId": match.homeTeamId,
            "awayTeamId": match.awayTeamId,
            "H": squads["H"],
            "A": squads["A"],
            "cached": cached,
            "inputStatus": input_status,
            "matchSnapshot": {
                "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
                "date": match.date, "kickoffTime": match.kickoffTime, "stadiumId": match.stadiumId,
                "matchType": match.matchType, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
            },
            "session": session,
            "dashboardKpis": dashboard_kpis,
        }

    def restore_input_draft_from_raw(self, gm_id: str, side: Side, *, user_id: str) -> dict[str, Any]:
        """Make a new editable Draft from an already-final RAW recording."""
        raw_state = self.read_input_state_from_raw(gm_id, side)
        return self.save_input_draft(
            gm_id,
            side,
            payload=raw_state["payload"],
            client_state=raw_state["clientState"],
            user_id=user_id,
        )

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
