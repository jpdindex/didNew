from __future__ import annotations

from collections import defaultdict
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
from time import monotonic
from typing import Annotated, Any, Callable, Literal

import firebase_admin
from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from firebase_admin import auth, credentials, firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core.exceptions import NotFound as FirestoreDocumentNotFound
from google.api_core.exceptions import AlreadyExists
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from backend.system.system_kpi_rules import KpiRecord, calculate_kpis
from backend.system.system_schema import CardDoc, MatchDoc, PathSnapshotDoc, PlayerKpi, RecordDoc, RecordingDoc, RecordingKpi, Side, TeamRatingSnapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_FILE = PROJECT_ROOT / ".env"
PLAYER_RATING_FIELDS = {
    "scoreRel", "scoreAbs", "score", "jmx", "apx", "apxGrade",
    "tpx", "tpxGrade", "fpx", "fpxGrade", "ratingBasedOn",
}


def _summary_display_name(document: dict[str, Any], fallback: str) -> str:
    # The schedule has always presented Korean names when source metadata has
    # them. Keep that presentation contract in the materialized read model.
    return str(document.get("nameKr") or document.get("name") or document.get("nameFull") or document.get("nameEn") or fallback)


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
    paths: list[tuple[str, PathSnapshotDoc]]
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

    # Match identity changes far less often than an input command.  Keeping a
    # short process-local cache removes repeated match reads made by enter,
    # socket authentication, and draft saves without changing Firestore's role
    # as the durable source of truth.
    _match_cache: dict[str, tuple[float, MatchDoc]] = {}
    _match_cache_lock = Lock()
    _MATCH_CACHE_TTL_SECONDS = 30.0

    def __init__(self) -> None:
        self.db = get_firestore_client()

    def get_match(self, gm_id: str) -> MatchDoc:
        now = monotonic()
        with self._match_cache_lock:
            cached = self._match_cache.get(gm_id)
            if cached and cached[0] > now:
                return cached[1].model_copy(deep=True)
        reference = self.db.collection("matches").document(gm_id)
        snapshot = reference.get(retry=None, timeout=10)
        if not snapshot.exists:
            raise NotFoundError(f"Match not found: {gm_id}")
        match = _validate_document(MatchDoc, snapshot.to_dict() or {}, path=reference.path)
        with self._match_cache_lock:
            self._match_cache[gm_id] = (now + self._MATCH_CACHE_TTL_SECONDS, match)
        return match.model_copy(deep=True)

    def list_matches(
        self,
        *,
        league_id: str | None = None,
        season_id: str | None = None,
        round_from: int | None = None,
        round_to: int | None = None,
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
            and (round_from is None or item[1].round >= round_from)
            and (round_to is None or item[1].round <= round_to)
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

    @staticmethod
    def _normalise_match_date(value: Any) -> str:
        """Keep every fixture date in the single sortable YYYY.MM.DD form."""
        raw = str(value or "").strip()
        parts = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", raw)
        if parts is None:
            digits = re.sub(r"\D", "", raw)
            if len(digits) < 8:
                raise BackendError("Match date must contain a calendar day", status_code=422, code="match_date_invalid")
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        else:
            year, month, day = (int(item) for item in parts.groups())
        try:
            datetime(year, month, day)
        except ValueError as exc:
            raise BackendError("Match date must contain a valid calendar day", status_code=422, code="match_date_invalid") from exc
        return f"{year:04d}.{month:02d}.{day:02d}"

    @staticmethod
    def _normalise_season_id(value: Any) -> str:
        """Store a season consistently as YYYYYYYY (for example 20232024)."""
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) != 8:
            raise BackendError("Season must contain two four-digit years", status_code=422, code="season_id_invalid")
        return digits

    def _input_summary_reference(self, gm_id: str):
        return self.db.collection("matches").document(gm_id).collection("inputSummaries").document("current")

    def _input_summary_backfill_reference(self, season_id: str):
        return self.db.collection("inputSummaryBackfills").document(season_id)

    def _input_summary_month_reference(self, year: int, month: int):
        return self.db.collection("inputSummaryBackfillMonths").document(f"{year}.{month:02d}")

    def input_summary_backfill_complete_for_month(self, year: int, month: int) -> bool:
        snapshot = self._input_summary_month_reference(year, month).get(retry=None, timeout=10)
        return snapshot.exists and (snapshot.to_dict() or {}).get("complete") is True

    def invalidate_input_summary_month(self, match: MatchDoc) -> None:
        date = self._normalise_match_date(match.date)
        self._input_summary_month_reference(int(date[:4]), int(date[5:7])).delete(retry=None, timeout=20)

    @staticmethod
    def _summary_participants(values: dict[str, Any]) -> list[dict[str, Any]]:
        participants = values.get("participants")
        # Draft roots store a UID-keyed map. The schedule batch reader returns
        # the same information as an array. Accept both so backfill cannot
        # silently retain primaryUid while dropping the visible roster.
        if isinstance(participants, dict):
            return [
                {"uid": uid, "role": item.get("role"), "name": item.get("name")}
                for uid, item in participants.items() if isinstance(item, dict)
            ]
        if isinstance(participants, list):
            return [
                {"uid": str(item.get("uid")), "role": item.get("role"), "name": item.get("name")}
                for item in participants if isinstance(item, dict) and isinstance(item.get("uid"), str) and item.get("uid")
            ]
        return []

    def refresh_input_summary(self, gm_id: str, match: MatchDoc | None = None, *, write_batch: Any = None) -> dict[str, Any]:
        """Refresh the compact schedule/lobby read model for one fixture.

        It deliberately lives beside recordings as ``inputSummaries/current`` so
        a collection-group query can list a month without opening Drafts or RAW
        for every match.
        """
        match = match or self.get_match(gm_id)
        # Backfill/import is the one place where we resolve presentation data.
        # Never retain a stale English or blank value from an earlier summary.
        teams = self.get_documents_by_ids("teams", {match.homeTeamId, match.awayTeamId})
        stadiums = self.get_documents_by_ids("stadiums", {match.stadiumId} if match.stadiumId else set())
        home_name = _summary_display_name(teams.get(match.homeTeamId, {}), match.homeTeamId)
        away_name = _summary_display_name(teams.get(match.awayTeamId, {}), match.awayTeamId)
        stadium_name = _summary_display_name(stadiums.get(match.stadiumId, {}), match.stadiumId)
        heads = self.get_recording_heads_many([gm_id]).get(gm_id, {"H": {}, "A": {}})
        drafts = self.get_input_draft_participants_many([gm_id]).get(gm_id, {"H": {}, "A": {}})
        sides: dict[str, dict[str, Any]] = {}
        primary_uids: dict[str, str | None] = {}
        live_score = None
        for side in ("H", "A"):
            draft = drafts.get(side, {})
            raw = heads.get(side, {})
            raw_status = raw.get("status")
            recorders = raw.get("recorders") if isinstance(raw.get("recorders"), dict) else {}
            # Active Draft collaboration is authoritative. RAW personnel become
            # visible only after its Draft is gone at final promotion; merging
            # them creates a schedule primary that cannot actually enter.
            if draft:
                participants = self._summary_participants(draft)
                primary_uid = draft.get("primaryUid") if isinstance(draft.get("primaryUid"), str) else None
                status = draft.get("status") or ("final" if raw_status == "final" else "ready")
            else:
                participants = [
                    {"uid": uid, "role": {"main": "primary", "sub": "assistant", "manager": "manager"}.get(item.get("rank"), "assistant"), "name": item.get("name")}
                    for uid, item in recorders.items() if isinstance(item, dict)
                ]
                primary_uid = next((item["uid"] for item in participants if item.get("role") == "primary"), None)
                status = "final" if raw_status == "final" else "ready"
            primary_uids[side] = primary_uid
            sides[side] = {"rawStatus": raw_status, "completed": status == "final", "lifecycleStatus": status, "participants": participants}
            if live_score is None and isinstance(draft.get("score"), dict):
                live_score = draft["score"]
        values = {
            "gmId": gm_id,
            "date": self._normalise_match_date(match.date),
            "kickoffTime": match.kickoffTime,
            "leagueId": match.leagueId,
            "seasonId": self._normalise_season_id(match.seasonId),
            "round": match.round,
            "stadiumId": match.stadiumId,
            "stadiumName": stadium_name,
            "home": {"teamId": match.homeTeamId, "name": home_name},
            "away": {"teamId": match.awayTeamId, "name": away_name},
            "score": live_score or {"home": match.score.home, "away": match.score.away},
            "inputStatus": sides,
            "collaboration": {
                side: {
                    "status": sides[side]["lifecycleStatus"],
                    "participants": sides[side]["participants"],
                    "primaryUid": primary_uids[side],
                }
                for side in ("H", "A")
            },
            "updatedAt": utc_now(),
        }
        if write_batch is not None:
            write_batch.set(self._input_summary_reference(gm_id), values)
        else:
            self._input_summary_reference(gm_id).set(values, retry=None, timeout=20)
        return values

    def create_schedule_matches(self, matches: dict[str, MatchDoc]) -> None:
        """Publish only explicitly submitted fixtures and their read models atomically."""
        batch = self.db.batch()
        for gm_id, match in matches.items():
            reference = self.db.collection("matches").document(gm_id)
            if reference.get(retry=None, timeout=10).exists:
                raise BackendError(f"이미 등록된 gm_id입니다: {gm_id}", status_code=409, code="match_exists")
            batch.create(reference, match.model_dump(mode="python", exclude_none=True))
            # New fixtures have no imported RAW. Do not invoke season migration.
            self.refresh_input_squads(gm_id, match, legacy_recordings={}, write_batch=batch)
            self.refresh_input_summary(gm_id, match, write_batch=batch)
        try:
            batch.commit(retry=None, timeout=60)
        except AlreadyExists as exc:
            raise BackendError("이미 등록된 경기가 있습니다. 목록을 새로고침하세요.", status_code=409, code="match_exists") from exc
        with self._match_cache_lock:
            for gm_id in matches:
                self._match_cache.pop(gm_id, None)

    def _update_input_summary_side(self, gm_id: str, side: Side, *, input_state: dict[str, Any], collaboration: dict[str, Any], score: dict[str, int] | None = None) -> None:
        """Update only one team's schedule fields without rereading either Draft.

        H and A analysts can save concurrently. Field-path updates prevent one
        side from replacing the other's participant roster.
        """
        values: dict[str, Any] = {
            f"inputStatus.{side}": input_state,
            f"collaboration.{side}": collaboration,
            "updatedAt": utc_now(),
        }
        if score is not None:
            values["score"] = score
        reference = self._input_summary_reference(gm_id)
        try:
            reference.update(values, retry=None, timeout=20)
        except FirestoreDocumentNotFound:
            # Only un-migrated fixtures take this exceptional path. Build the
            # full static document once, then apply the exact accepted state.
            self.refresh_input_summary(gm_id)
            reference.update(values, retry=None, timeout=20)

    def sync_input_summary_from_draft(self, gm_id: str, side: Side, draft: dict[str, Any]) -> None:
        """Mirror the accepted Draft ownership/state directly into the schedule."""
        payload = draft.get("payload") if isinstance(draft.get("payload"), dict) else {}
        shared = draft.get("sharedState") if isinstance(draft.get("sharedState"), dict) else {}
        lifecycle = shared.get("halfStatus") or payload.get("status") or draft.get("status") or "ready"
        participants = self._summary_participants(draft)
        primary_uid = draft.get("primaryUid") if isinstance(draft.get("primaryUid"), str) else None
        score = None
        score_source = shared if "homeScore" in shared or "awayScore" in shared else payload
        if isinstance(score_source.get("homeScore"), int) and isinstance(score_source.get("awayScore"), int):
            score = {"home": score_source["homeScore"], "away": score_source["awayScore"]}
        self._update_input_summary_side(
            gm_id,
            side,
            input_state={"rawStatus": "final" if lifecycle == "final" else None, "completed": lifecycle == "final", "lifecycleStatus": lifecycle, "participants": participants},
            collaboration={"status": lifecycle, "primaryUid": primary_uid, "participants": participants},
            score=score,
        )

    def sync_input_summary_from_recording(self, gm_id: str, side: Side, *, recorders: dict[str, Any], score: dict[str, int]) -> None:
        """Replace a promoted Draft's collaborators with its final RAW roster."""
        participants = [
            {"uid": uid, "role": {"main": "primary", "sub": "assistant", "manager": "manager"}.get(values.get("rank"), "assistant"), "name": values.get("name")}
            for uid, values in recorders.items() if isinstance(values, dict)
        ]
        primary_uid = next((item["uid"] for item in participants if item.get("role") == "primary"), None)
        self._update_input_summary_side(
            gm_id,
            side,
            input_state={"rawStatus": "final", "completed": True, "lifecycleStatus": "final", "participants": participants},
            collaboration={"status": "final", "primaryUid": primary_uid, "participants": participants},
            score=score,
        )

    def list_input_summaries_for_month(self, year: int, month: int) -> list[dict[str, Any]]:
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        start, end = f"{year}.{month:02d}.01", f"{next_year}.{next_month:02d}.01"
        documents = self.db.collection_group("inputSummaries").where(
            filter=FieldFilter("date", ">=", start)
        ).where(filter=FieldFilter("date", "<", end)).stream(retry=None, timeout=30)
        return [snapshot.to_dict() or {} for snapshot in documents]

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
            # A side with neither RAW nor a live Draft is a fresh "ready" session;
            # the lobby resets its shared state only on an explicit ready status.
            "H": {"rawStatus": None, "completed": False, "fieldSide": None, "lifecycleStatus": "ready"},
            "A": {"rawStatus": None, "completed": False, "fieldSide": None, "lifecycleStatus": "ready"},
        }
        recordings = self.db.collection("matches").document(gm_id).collection("recordings")
        recording_refs = {side: recordings.document(side) for side in ("H", "A")}
        snapshots = {snapshot.reference.path: snapshot for snapshot in self.db.get_all(list(recording_refs.values()), retry=None, timeout=20)}

        for side in ("H", "A"):
            snapshot = snapshots.get(recording_refs[side].path)
            if snapshot is None or not snapshot.exists:
                continue
            document = snapshot.to_dict() or {}
            raw_status = document.get("status")
            field_side = document.get("fieldSide")
            states[side] = {
                "rawStatus": str(raw_status) if raw_status is not None else None,
                "completed": raw_status == "final",
                "fieldSide": field_side if field_side in {"left", "right"} else None,
                "lifecycleStatus": "final" if raw_status == "final" else "ready",
            }
        # A live Draft is the shared lifecycle source until its RAW is final.
        # Read Draft roots only for unfinished sides.  Final-match lobbies use
        # the immutable recording head and must not pay for missing Draft reads.
        active_sides = [side for side in ("H", "A") if not states[side]["completed"]]
        draft_refs = {side: self._input_setup_reference(gm_id, side) for side in active_sides}
        draft_snapshots = {
            snapshot.reference.path: snapshot
            for snapshot in self.db.get_all(list(draft_refs.values()), retry=None, timeout=20)
        } if draft_refs else {}
        # IndexedDB is intentionally excluded: it belongs to one device only.
        for side in active_sides:
            snapshot = draft_snapshots.get(draft_refs[side].path)
            if snapshot is None or not snapshot.exists:
                continue
            draft = snapshot.to_dict() or {}
            payload = draft.get("payload")
            if draft.get("gmId") not in {None, gm_id} or (
                isinstance(payload, dict) and (payload.get("gmId") != gm_id or payload.get("side") != side)
            ):
                raise BackendError("Input draft root identity does not match its path", status_code=409, code="draft_identity_mismatch")
            shared = draft.get("sharedState") if isinstance(draft.get("sharedState"), dict) else {}
            payload = payload if isinstance(payload, dict) else {}
            setup = self._normalise_input_setup(draft.get("setup"))
            if states[side]["fieldSide"] is None and setup and setup.get("fieldSide") in {"left", "right"}:
                states[side]["fieldSide"] = setup["fieldSide"]
            lifecycle = shared.get("halfStatus") or payload.get("status") or draft.get("status")
            if lifecycle in {"H1", "H1_done", "H2", "H2_done", "final"}:
                states[side]["lifecycleStatus"] = lifecycle
        return states

    def get_recording_statuses_many(self, gm_ids: list[str]) -> dict[str, dict[Side, str | None]]:
        """Batch-read H/A recording status for many fixtures."""
        return {
            gm_id: {side: heads[side].get("status") for side in ("H", "A")}
            for gm_id, heads in self.get_recording_heads_many(gm_ids).items()
        }

    def get_recording_heads_many(self, gm_ids: list[str]) -> dict[str, dict[Side, dict[str, Any]]]:
        """Batch-read H/A recording heads (status, recorders) for many fixtures.

        This avoids one recordings subcollection stream per schedule row. Only the
        two known recording documents (H and A) are requested for each fixture.
        """
        unique_ids = list(dict.fromkeys(gm_id for gm_id in gm_ids if gm_id))
        heads: dict[str, dict[Side, dict[str, Any]]] = {
            gm_id: {"H": {}, "A": {}} for gm_id in unique_ids
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
                if match_ref is None or match_ref.id not in heads:
                    continue
                document = snapshot.to_dict() or {}
                value = document.get("status")
                recorders = document.get("recorders")
                heads[match_ref.id][snapshot.id] = {
                    "status": str(value) if value is not None else None,
                    "recorders": recorders if isinstance(recorders, dict) else {},
                }
        return heads

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
        path_collection = self.db.collection("matches").document(gm_id).collection("recordings").document(side).collection("paths")
        paths = [
            (snapshot.id, _validate_document(PathSnapshotDoc, snapshot.to_dict() or {}, path=snapshot.reference.path))
            for snapshot in path_collection.stream(retry=None, timeout=15)
        ]
        serializable = [
            {"id": record_id, **record.model_dump(mode="json", exclude_none=False)}
            for record_id, record in records
        ]
        serializable.extend(
            {"legacyPath": path_id, **path.model_dump(mode="json")}
            for path_id, path in paths
        )
        return MatchKpiSource(
            match=self.get_match(gm_id),
            recording=self.get_recording(gm_id, side),
            records=records,
            paths=paths,
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

    def _input_setup_reference(self, gm_id: str, side: Side):
        """Return the temporary, team-specific Draft document holding setup.

        `matches` is reserved for final RAW. Setup must therefore live beside
        the collaboration Draft until promotion, rather than creating a
        partially populated match document when the first half begins.
        """
        return self.db.collection("inputDrafts").document(f"{gm_id}_{side}")

    @staticmethod
    def _input_sub_id(value: dict[str, Any], index: int) -> str:
        # A player cannot make the same OUT→IN move twice in one match, so a
        # deterministic legacy key is safer than an array index after sorting.
        return str(value.get("id") or f"legacy-sub:{value.get('half')}:{value.get('seconds')}:{value.get('outPlayer')}:{value.get('inPlayer')}")

    @staticmethod
    def _normalise_input_subs(values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []
        normalised: list[dict[str, Any]] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            half = value.get("half")
            out_player = value.get("outPlayer")
            in_player = value.get("inPlayer")
            seconds = value.get("seconds")
            if half not in {"H1", "H2"} or not isinstance(out_player, str) or not isinstance(in_player, str):
                continue
            if not isinstance(seconds, int) or seconds < 0:
                continue
            normalised.append({
                "id": JpdDidData._input_sub_id(value, index),
                "half": half,
                "seconds": seconds,
                "outPlayer": out_player,
                "inPlayer": in_player,
            })
        return sorted(normalised, key=lambda item: (item["half"], item["seconds"], item["outPlayer"], item["inPlayer"], item["id"]))

    @classmethod
    def _subs_from_input_lineup(cls, lineup: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recover substitutions from final RAW lineup metadata when needed."""
        outgoing: dict[tuple[str, int], list[str]] = {}
        incoming: dict[tuple[str, int], list[str]] = {}
        for item in lineup:
            player_id = item.get("playerId")
            if not isinstance(player_id, str):
                continue
            out_half, out_seconds = item.get("outHalf"), item.get("outSeconds")
            in_half, in_seconds = item.get("inHalf"), item.get("inSeconds")
            if out_half in {"H1", "H2"} and isinstance(out_seconds, int):
                outgoing.setdefault((out_half, out_seconds), []).append(player_id)
            if in_half in {"H1", "H2"} and isinstance(in_seconds, int) and in_seconds > 0:
                incoming.setdefault((in_half, in_seconds), []).append(player_id)
        result: list[dict[str, Any]] = []
        for key in sorted(set(outgoing) | set(incoming)):
            for out_player, in_player in zip(sorted(outgoing.get(key, [])), sorted(incoming.get(key, []))):
                result.append({
                    "id": f"legacy-sub:{key[0]}:{key[1]}:{out_player}:{in_player}",
                    "half": key[0],
                    "seconds": key[1],
                    "outPlayer": out_player,
                    "inPlayer": in_player,
                })
        return result

    def _with_input_setup_subs(self, reference, setup: dict[str, Any] | None) -> dict[str, Any] | None:
        if setup is None:
            return None
        documents = list(reference.collection("substitutions").stream(retry=None, timeout=20))
        if not documents:
            return setup
        subs = self._normalise_input_subs([
            {**(values := item.to_dict()), "id": values.get("id") or item.id}
            for item in documents
            if isinstance((values := item.to_dict()), dict) and not values.get("deleted")
        ])
        return {**setup, "subs": subs}

    @staticmethod
    def _lineup_before_substitutions(lineup: list[dict[str, Any]], subs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recover the starting slots once when an older Draft becomes event-based."""
        base = [dict(item) for item in lineup if isinstance(item, dict)]
        ordered = sorted(subs, key=lambda item: (str(item.get("half")), int(item.get("seconds", 0)), str(item.get("id"))))
        for sub in reversed(ordered):
            out_player, in_player = sub.get("outPlayer"), sub.get("inPlayer")
            out_index = next((index for index, item in enumerate(base) if item.get("playerId") == out_player), None)
            in_index = next((index for index, item in enumerate(base) if item.get("playerId") == in_player), None)
            if out_index is not None and in_index is not None:
                base[out_index]["playerId"], base[in_index]["playerId"] = in_player, out_player
        return base

    @staticmethod
    def _normalise_input_setup(values: Any) -> dict[str, Any] | None:
        if not isinstance(values, dict):
            return None
        formation_key = values.get("formationKey")
        lineup = values.get("lineup")
        base_lineup = values.get("baseLineup")
        field_side = values.get("fieldSide")
        if not isinstance(formation_key, str) or not formation_key or not isinstance(lineup, list):
            return None
        return {
            "formationKey": formation_key,
            "fieldSide": field_side if field_side in {"left", "right"} else None,
            "lineup": [item for item in (base_lineup if isinstance(base_lineup, list) else lineup) if isinstance(item, dict)],
            "lineupIsBaseline": isinstance(base_lineup, list),
            "subs": JpdDidData._normalise_input_subs(values.get("subs")),
            "inputMode": values.get("inputMode") if values.get("inputMode") in {"분석", "실시간"} else "분석",
            "revision": int(values.get("revision") or 0),
        }

    def get_input_setup(self, gm_id: str) -> dict[Side, dict[str, Any] | None]:
        references = {side: self._input_setup_reference(gm_id, side) for side in ("H", "A")}
        snapshots = {snapshot.reference.path: snapshot for snapshot in self.db.get_all(list(references.values()), retry=None, timeout=20)}
        base: dict[Side, tuple[Any, dict[str, Any]] | None] = {"H": None, "A": None}
        for side, reference in references.items():
            snapshot = snapshots.get(reference.path)
            if snapshot is not None and snapshot.exists:
                setup = self._normalise_input_setup((snapshot.to_dict() or {}).get("setup"))
                if setup is not None:
                    base[side] = (reference, setup)

        result: dict[Side, dict[str, Any] | None] = {"H": None, "A": None}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="input-setup") as executor:
            futures = {
                side: executor.submit(self._with_input_setup_subs, value[0], value[1])
                for side, value in base.items() if value is not None
            }
            for side, future in futures.items():
                result[side] = future.result()
        return result

    def save_input_setup(
        self,
        gm_id: str,
        side: Side,
        *,
        payload: dict[str, Any],
        client_state: dict[str, Any],
        deleted_sub_ids: list[str] | None = None,
        merge_substitutions: bool = False,
    ) -> dict[str, Any] | None:
        """Checkpoint Draft setup without allowing blank state writes to erase it."""
        reference = self._input_setup_reference(gm_id, side)
        current = reference.get(retry=None, timeout=10).to_dict() or {}
        previous = self._with_input_setup_subs(
            reference,
            self._normalise_input_setup(current.get("setup")),
        ) or {}
        formation_key = payload.get("formationKey")
        lineup = payload.get("lineup")
        selected_field_side = client_state.get("side")
        if selected_field_side not in {"left", "right"}:
            selected_field_side = payload.get("fieldSide")

        # A Draft may be created by a join/recovery before its UI has hydrated.
        # Such an empty payload must never destroy a completed lobby setup.
        if not isinstance(formation_key, str) or not formation_key or not isinstance(lineup, list):
            if previous:
                return previous
            return None

        next_subs = self._normalise_input_subs(client_state.get("subs")) \
            if isinstance(client_state.get("subs"), list) \
            else self._subs_from_input_lineup([item for item in lineup if isinstance(item, dict)])
        stored_setup = current.get("setup") if isinstance(current.get("setup"), dict) else {}
        stored_base = stored_setup.get("baseLineup") if isinstance(stored_setup.get("baseLineup"), list) else None
        base_lineup = [item for item in stored_base if isinstance(item, dict)] if merge_substitutions and stored_base is not None else None
        if merge_substitutions and base_lineup is None:
            base_lineup = self._lineup_before_substitutions(
                [item for item in stored_setup.get("lineup", []) if isinstance(item, dict)],
                self._normalise_input_subs(previous.get("subs")),
            )
        if merge_substitutions and not base_lineup:
            base_lineup = [item for item in lineup if isinstance(item, dict)]
        next_comparable = {
            "formationKey": previous.get("formationKey", formation_key) if merge_substitutions else formation_key,
            "fieldSide": previous.get("fieldSide") if merge_substitutions else (selected_field_side if selected_field_side in {"left", "right"} else previous.get("fieldSide")),
            "lineup": base_lineup if merge_substitutions else [item for item in lineup if isinstance(item, dict)],
            "inputMode": previous.get("inputMode", "분석") if merge_substitutions else (payload.get("inputMode") if payload.get("inputMode") in {"분석", "실시간"} else previous.get("inputMode", "분석")),
        }
        previous_comparable = {key: previous.get(key) for key in next_comparable}
        previous_subs = self._normalise_input_subs(previous.get("subs"))
        deleted_ids = {str(value) for value in deleted_sub_ids or [] if value}
        if previous and previous_comparable == next_comparable and previous_subs == next_subs and not deleted_ids:
            return previous

        next_setup = {
            **next_comparable,
            "baseLineup": next_comparable["lineup"],
            # Legacy fallback only. Once substitutions has documents, reads
            # always use that merge-safe event collection instead.
            "subs": previous.get("subs", next_subs),
            "revision": int(previous.get("revision") or 0) + 1,
            "updatedAt": utc_now(),
        }
        substitutions = reference.collection("substitutions")
        existing = list(substitutions.stream(retry=None, timeout=20))
        batch = self.db.batch()
        # Seed a pre-event-array Draft once. Subsequent saves upsert by event
        # ID, so an earlier browser snapshot cannot replace another event.
        if not existing:
            for index, legacy in enumerate(previous_subs):
                sub_id = self._input_sub_id(legacy, index)
                batch.set(substitutions.document(sub_id), {**legacy, "id": sub_id, "deleted": False}, merge=True)
        for index, sub in enumerate(next_subs):
            sub_id = self._input_sub_id(sub, index)
            batch.set(substitutions.document(sub_id), {**sub, "id": sub_id, "deleted": False}, merge=True)
        for sub_id in deleted_ids:
            batch.set(substitutions.document(sub_id), {"id": sub_id, "deleted": True}, merge=True)
        batch.set(reference, {"setup": next_setup}, merge=True)
        batch.commit(retry=None, timeout=20)
        return self._with_input_setup_subs(reference, self._normalise_input_setup(next_setup))

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
        squads, cached, _ = self._get_or_create_input_squads_snapshot(gm_id, match)
        return squads, cached

    def _get_or_create_input_squads_snapshot(
        self,
        gm_id: str,
        match: MatchDoc,
    ) -> tuple[dict[str, list[dict[str, str]]], bool, dict[str, Any]]:
        """Return squads and their backing snapshot for the lobby fast path."""
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
            return {"H": home, "A": away}, True, document
        seed_squads = {"H": home, "A": away} if (
            document.get("source") == source and isinstance(home, list) and isinstance(away, list)
        ) else None
        squads = self.refresh_input_squads(gm_id, match, seed_squads=seed_squads)
        # A cache miss is exceptional. Read back once so the caller receives
        # the same normalized legacy lineup that subsequent lobby opens reuse.
        refreshed = reference.get(retry=None, timeout=10).to_dict() or {}
        return squads, False, refreshed

    @staticmethod
    def _final_lobby_snapshot_values(
        match: MatchDoc,
        side: Side,
        *,
        input_mode: str,
        field_side: str | None,
        formation_key: str,
        lineup: list[dict[str, Any]],
        halves: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Small, display-only final state kept beside the squad snapshot."""
        if field_side not in {"left", "right"} or not formation_key or not isinstance(lineup, list):
            return None
        return {
            "status": "final",
            "inputMode": input_mode if input_mode in {"분석", "실시간"} else "분석",
            "fieldSide": field_side,
            "formationKey": formation_key,
            "lineup": lineup,
            "halves": halves,
            "homeScore": match.score.home,
            "awayScore": match.score.away,
            "updatedAt": utc_now(),
        }

    @classmethod
    def _final_lobby_session_from_snapshot(
        cls,
        gm_id: str,
        side: Side,
        match: MatchDoc,
        field_side: str | None,
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build a display-only final lobby shell from a cached snapshot."""
        lobby = snapshot.get("lobby") if isinstance(snapshot.get("lobby"), dict) else {}
        stored = lobby.get(side) if isinstance(lobby.get(side), dict) else None
        if stored is None:
            # Imported fixtures already carry this normalized lineup under the
            # old name. Convert it in memory so historic snapshots are fast
            # even before the Swagger backfill rewrites them.
            if field_side not in {"left", "right"}:
                return None
            legacy = snapshot.get("legacyLineup") if isinstance(snapshot.get("legacyLineup"), dict) else {}
            lineup_snapshot = legacy.get(side) if isinstance(legacy.get(side), dict) else {}
            stored = cls._final_lobby_snapshot_values(
                match,
                side,
                input_mode="분석",
                field_side=field_side,
                formation_key=str(lineup_snapshot.get("formationKey") or ""),
                lineup=lineup_snapshot.get("lineup") if isinstance(lineup_snapshot.get("lineup"), list) else [],
                halves={"H1": {"seconds": 0}, "H2": {"seconds": 0}},
            )
        if stored is None:
            return None
        formation_key = stored.get("formationKey")
        lineup = stored.get("lineup")
        stored_side = stored.get("fieldSide")
        if not isinstance(formation_key, str) or not formation_key or not isinstance(lineup, list) or stored_side not in {"left", "right"}:
            return None
        return {
            "status": "ok",
            "gmId": gm_id,
            "side": side,
            "payload": {
                "gmId": gm_id,
                "side": side,
                "inputMode": stored.get("inputMode") if stored.get("inputMode") in {"분석", "실시간"} else "분석",
                "fieldSide": stored_side,
                "formationKey": formation_key,
                "homeScore": int(stored.get("homeScore") or match.score.home),
                "awayScore": int(stored.get("awayScore") or match.score.away),
                "status": "final",
                "halves": stored.get("halves") if isinstance(stored.get("halves"), dict) else {"H1": {"seconds": 0}, "H2": {"seconds": 0}},
                "lineup": lineup,
                "records": [],
                "cards": [],
                "recorderLevel": "advanced",
                "matchSnapshot": {
                    "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
                    "date": match.date, "kickoffTime": match.kickoffTime, "stadiumId": match.stadiumId,
                    "matchType": match.matchType, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
                },
                "formationChanges": [],
            },
            "clientState": {"restoredFromRaw": True, "summaryOnly": True, "fromInputSnapshot": True},
            "updatedAt": stored.get("updatedAt") or snapshot.get("updatedAt"),
        }

    def save_final_input_lobby_snapshot(
        self,
        gm_id: str,
        side: Side,
        match: MatchDoc,
        payload: dict[str, Any],
    ) -> None:
        """Persist final lineup display data with the reusable squad snapshot."""
        lineup = [item for item in payload.get("lineup", []) if isinstance(item, dict)]
        summary = self._final_lobby_snapshot_values(
            match,
            side,
            input_mode=str(payload.get("inputMode") or "분석"),
            field_side=payload.get("fieldSide"),
            formation_key=str(payload.get("formationKey") or ""),
            lineup=lineup,
            halves=payload.get("halves") if isinstance(payload.get("halves"), dict) else {},
        )
        if summary is None:
            return
        self._input_squads_reference(gm_id).set({"lobby": {side: summary}}, merge=True, retry=None, timeout=20)

    def refresh_input_squads(
        self,
        gm_id: str,
        match: MatchDoc,
        *,
        legacy_recordings: dict[Side, RecordingDoc] | None = None,
        seed_squads: dict[str, list[dict[str, Any]]] | None = None,
        write_batch: Any = None,
    ) -> dict[str, list[dict[str, str]]]:
        """Rebuild squad + imported-lineup snapshot after source data is corrected."""
        reference = self._input_squads_reference(gm_id)
        existing = reference.get(retry=None, timeout=10)
        existing_document = existing.to_dict() or {}
        legacy_recordings = legacy_recordings if legacy_recordings is not None else self._legacy_recordings(gm_id)
        if legacy_recordings:
            squads, legacy_lineups = self._build_legacy_input_snapshot(match, legacy_recordings, seed_squads=seed_squads)
        else:
            squads, legacy_lineups = self._build_input_squads(match), {}
        final_lobby: dict[str, dict[str, Any]] = {}
        for side, recording in legacy_recordings.items():
            legacy = legacy_lineups.get(side, {})
            lineup = legacy.get("lineup") if isinstance(legacy.get("lineup"), list) else []
            summary = self._final_lobby_snapshot_values(
                match,
                side,
                input_mode=recording.inputMode,
                field_side=recording.fieldSide,
                formation_key=recording.formationKey,
                lineup=lineup,
                halves={half: {"seconds": values.seconds} for half, values in recording.halves.items() if half in {"H1", "H2"}},
            )
            if summary is not None:
                final_lobby[side] = summary
        now = utc_now()
        values = {
            "schemaVersion": 3,
            "source": self._input_squad_source(match),
            "H": squads["H"],
            "A": squads["A"],
            "legacyLineup": legacy_lineups,
            "createdAt": existing_document.get("createdAt", now) if existing.exists else now,
            "updatedAt": now,
        }
        if final_lobby:
            values["lobby"] = final_lobby
        elif isinstance(existing_document.get("lobby"), dict):
            values["lobby"] = existing_document["lobby"]
        if write_batch is not None:
            write_batch.set(reference, values)
        else:
            reference.set(values, retry=None, timeout=30)
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

    def _legacy_snapshot_matches(
        self,
        *,
        season_id: str,
        from_gm_id: str | None = None,
        to_gm_id: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, MatchDoc]]:
        if from_gm_id and to_gm_id and from_gm_id > to_gm_id:
            raise BackendError("from_gm_id must not be greater than to_gm_id", status_code=422, code="snapshot_range_invalid")
        # Snapshot backfill is always season-scoped.  Do not stream the entire
        # matches collection and filter it in Python as legacy data grows.
        matches = [
            (
                snapshot.id,
                _validate_document(MatchDoc, snapshot.to_dict() or {}, path=snapshot.reference.path),
            )
            for snapshot in self.db.collection("matches")
            .where(filter=FieldFilter("seasonId", "==", season_id))
            .stream(retry=None, timeout=30)
            if (from_gm_id is None or snapshot.id >= from_gm_id)
            and (to_gm_id is None or snapshot.id <= to_gm_id)
        ]
        matches.sort(key=lambda item: item[0])
        return matches[:limit] if limit is not None else matches

    def get_legacy_input_snapshot_status(
        self,
        *,
        season_id: str,
        from_gm_id: str | None = None,
        to_gm_id: str | None = None,
    ) -> dict[str, int]:
        """Count usable fast lobby snapshots for an explicit season scope."""
        counts = {"matched": 0, "legacy": 0, "ready": 0, "missing": 0}
        matches = self._legacy_snapshot_matches(
            season_id=season_id, from_gm_id=from_gm_id, to_gm_id=to_gm_id,
        )
        recordings_by_match, snapshots_by_match = self._legacy_snapshot_state(matches)
        for gm_id, match in matches:
            counts["matched"] += 1
            recordings = recordings_by_match.get(gm_id, {})
            snapshot = snapshots_by_match.get(gm_id, {})
            if recordings:
                counts["legacy"] += 1
                ready = self._legacy_input_snapshot_ready(match, recordings, snapshot)
            else:
                ready = (
                    snapshot.get("schemaVersion") == 3
                    and snapshot.get("source") == self._input_squad_source(match)
                    and isinstance(snapshot.get("H"), list)
                    and isinstance(snapshot.get("A"), list)
                )
            counts["ready" if ready else "missing"] += 1
        return counts

    def _legacy_input_snapshot_ready(
        self,
        match: MatchDoc,
        recordings: dict[Side, RecordingDoc],
        snapshot: dict[str, Any],
    ) -> bool:
        lineups = snapshot.get("legacyLineup") if isinstance(snapshot.get("legacyLineup"), dict) else {}
        lobby = snapshot.get("lobby") if isinstance(snapshot.get("lobby"), dict) else {}
        return (
            snapshot.get("schemaVersion") == 3
            and snapshot.get("source") == self._input_squad_source(match)
            and all(
                isinstance(lineups.get(side), dict)
                and lineups[side].get("fingerprint") == self._legacy_lineup_fingerprint(recording)
                and isinstance(lineups[side].get("lineup"), list)
                and isinstance(lobby.get(side), dict)
                and lobby[side].get("formationKey") == recording.formationKey
                and lobby[side].get("fieldSide") == recording.fieldSide
                and isinstance(lobby[side].get("lineup"), list)
                for side, recording in recordings.items()
            )
        )

    def _legacy_snapshot_state(
        self,
        matches: list[tuple[str, MatchDoc]],
    ) -> tuple[dict[str, dict[Side, RecordingDoc]], dict[str, dict[str, Any]]]:
        """Read one backfill range in batches instead of per-document RPCs."""
        recording_refs: dict[str, tuple[str, Side]] = {}
        snapshot_refs: dict[str, str] = {}
        references: list[Any] = []
        for gm_id, _ in matches:
            for side in ("H", "A"):
                reference = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
                recording_refs[reference.path] = (gm_id, side)
                references.append(reference)
            snapshot_reference = self._input_squads_reference(gm_id)
            snapshot_refs[snapshot_reference.path] = gm_id
            references.append(snapshot_reference)

        recordings_by_match: dict[str, dict[Side, RecordingDoc]] = defaultdict(dict)
        snapshots_by_match: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(references), 200):
            for snapshot in self.db.get_all(references[offset:offset + 200], retry=None, timeout=30):
                path = snapshot.reference.path
                if path in recording_refs:
                    if not snapshot.exists:
                        continue
                    gm_id, side = recording_refs[path]
                    recording = _validate_document(RecordingDoc, snapshot.to_dict() or {}, path=path)
                    if recording.status == "final" and recording.legacyGiId is not None and recording.lineup:
                        recordings_by_match[gm_id][side] = recording
                elif path in snapshot_refs and snapshot.exists:
                    snapshots_by_match[snapshot_refs[path]] = snapshot.to_dict() or {}
        return recordings_by_match, snapshots_by_match

    def backfill_legacy_input_squads(
        self,
        *,
        season_id: str,
        from_gm_id: str | None = None,
        to_gm_id: str | None = None,
        limit: int | None = None,
        on_progress: Callable[[dict[str, int]], None] | None = None,
    ) -> dict[str, int]:
        """Backfill one season's fast input read models without touching RAW.

        The Swagger job deliberately owns the one-time migration as well:
        canonical match date/season fields, reusable squad snapshots, and the
        schedule/lobby summary document are created together.
        """
        matches = self._legacy_snapshot_matches(
            season_id=season_id, from_gm_id=from_gm_id, to_gm_id=to_gm_id, limit=limit,
        )
        counts = {"matched": len(matches), "processed": 0, "scanned": 0, "created": 0, "unchanged": 0, "failed": 0}
        recordings_by_match, snapshots_by_match = self._legacy_snapshot_state(matches)
        for gm_id, match in matches:
            try:
                normalised_date = self._normalise_match_date(match.date)
                normalised_season = self._normalise_season_id(match.seasonId)
                if normalised_date != match.date or normalised_season != match.seasonId:
                    self.db.collection("matches").document(gm_id).update({
                        "date": normalised_date, "seasonId": normalised_season, "updatedAt": utc_now(),
                    }, retry=None, timeout=20)
                    with self._match_cache_lock:
                        self._match_cache.pop(gm_id, None)
                    match = match.model_copy(update={"date": normalised_date, "seasonId": normalised_season})
                recordings = recordings_by_match.get(gm_id, {})
                current = snapshots_by_match.get(gm_id, {})
                if recordings:
                    counts["scanned"] += 1
                regular_snapshot_ready = (
                    current.get("schemaVersion") == 3
                    and current.get("source") == self._input_squad_source(match)
                    and isinstance(current.get("H"), list)
                    and isinstance(current.get("A"), list)
                )
                if (recordings and self._legacy_input_snapshot_ready(match, recordings, current)) or (not recordings and regular_snapshot_ready):
                    counts["unchanged"] += 1
                else:
                    # This includes never-started fixtures. Their squad snapshot
                    # lets a fresh waiting room open without contract/roster I/O.
                    self.refresh_input_squads(gm_id, match, legacy_recordings=recordings)
                    counts["created"] += 1
                self.refresh_input_summary(gm_id, match)
            except Exception:
                logger.exception("Legacy input snapshot backfill failed for %s (date=%r)", gm_id, match.date)
                counts["failed"] += 1
            finally:
                counts["processed"] += 1
                if on_progress is not None:
                    on_progress(dict(counts))
        if not from_gm_id and not to_gm_id and limit is None and counts["failed"] == 0:
            self._input_summary_backfill_reference(season_id).set({
                "seasonId": season_id,
                "complete": True,
                "completedAt": utc_now(),
                "matched": counts["matched"],
            }, retry=None, timeout=20)
            months: dict[tuple[int, int], set[str]] = defaultdict(set)
            for gm_id, match in matches:
                date = self._normalise_match_date(match.date)
                months[(int(date[:4]), int(date[5:7]))].add(gm_id)
            for (year, month), migrated_ids in months.items():
                # Mark a calendar month complete only when this full-season job
                # covered every fixture in it. Season IDs in historical data do
                # not reliably correspond to calendar years.
                all_ids = {gm_id for gm_id, _ in self.list_matches_for_month(year, month)}
                if all_ids and all_ids <= migrated_ids:
                    self._input_summary_month_reference(year, month).set({
                        "complete": True,
                        "seasonId": season_id,
                        "completedAt": utc_now(),
                        "matchCount": len(all_ids),
                    }, retry=None, timeout=20)
        return counts

    @staticmethod
    def _input_draft_id(gm_id: str, side: Side) -> str:
        return f"{gm_id}_{side}"

    _LIFECYCLE_ORDER = ("ready", "H1", "H1_done", "H2", "H2_done", "H3", "H3_done", "H4", "H4_done", "final")

    @classmethod
    def _lifecycle_rank(cls, status: Any) -> int:
        return cls._LIFECYCLE_ORDER.index(status) if status in cls._LIFECYCLE_ORDER else -1

    def get_input_draft(self, gm_id: str, side: Side) -> dict[str, Any]:
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        snapshot = reference.get(retry=None, timeout=10)
        if not snapshot.exists:
            raise NotFoundError(f"Input draft not found: {gm_id}/{side}")
        document = snapshot.to_dict() or {}
        payload = document.get("payload")
        # A document path is not sufficient identity proof: old browser bugs
        # could leave a payload from another session under a reused Draft key.
        # Never let that data enter bootstrap, polling, or the live socket.
        if document.get("gmId") not in {None, gm_id} or document.get("side") not in {None, side}:
            raise BackendError("Input draft root identity does not match its path", status_code=409, code="draft_identity_mismatch")
        if isinstance(payload, dict) and (payload.get("gmId") != gm_id or payload.get("side") != side):
            raise BackendError("Input draft payload identity does not match its path", status_code=409, code="draft_identity_mismatch")
        return self._with_draft_records(reference, document)

    @staticmethod
    def _normalise_draft_record(record_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        """Accept both the old browser record shape and the API payload shape."""
        if values.get("deleted"):
            return None
        record = {key: value for key, value in values.items() if key not in {"deleted", "updatedAt", "updatedBy", "edited"}}
        record["id"] = str(record.get("id") or record_id)
        if "halfSeconds" not in record and "seconds" in record:
            record["halfSeconds"] = record.pop("seconds")
        record.setdefault("half", "H1")
        return record

    def _with_draft_records(self, reference: Any, document: dict[str, Any]) -> dict[str, Any]:
        """Hydrate a Draft payload from server-owned per-record documents.

        Root payloads remain useful for first open/offline recovery.  Once a
        records subcollection exists, it is the merge authority so independent
        analysts cannot overwrite each other's latest event edits.
        """
        payload = document.get("payload")
        if not isinstance(payload, dict):
            return document
        next_payload = dict(payload)
        record_documents = list(reference.collection("records").stream(retry=None, timeout=20))
        if record_documents:
            records = [
                normalised for item in record_documents
                if isinstance((values := item.to_dict()), dict)
                if (normalised := self._normalise_draft_record(item.id, values)) is not None
            ]
            records.sort(key=lambda item: (str(item.get("half", "H1")), int(item.get("halfSeconds", 0)), int(item.get("seq", 0))))
            next_payload["records"] = records
        card_documents = list(reference.collection("cards").stream(retry=None, timeout=20))
        if card_documents:
            cards = [
                normalised for item in card_documents
                if isinstance((values := item.to_dict()), dict)
                if not values.get("deleted")
                if (normalised := self._normalise_draft_card(item.id, values)) is not None
            ]
            cards.sort(key=lambda item: (str(item.get("half", "H1")), int(item.get("halfSeconds", 0)), str(item.get("id"))))
            next_payload["cards"] = cards
        return {**document, "payload": next_payload}

    @staticmethod
    def _normalise_draft_card(card_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        player_id = values.get("playerId")
        half = values.get("half")
        seconds = values.get("halfSeconds")
        card = values.get("card")
        if not isinstance(player_id, str) or half not in {"H1", "H2"} or not isinstance(seconds, int) or card not in {"Y", "R"}:
            return None
        return {"id": str(values.get("id") or card_id), "playerId": player_id, "half": half, "halfSeconds": seconds, "card": card}

    def _draft_cards(self, reference: Any) -> list[dict[str, Any]]:
        cards = [
            normalised for item in reference.collection("cards").stream(retry=None, timeout=20)
            if isinstance((values := item.to_dict()), dict)
            if not values.get("deleted")
            if (normalised := self._normalise_draft_card(item.id, values)) is not None
        ]
        cards.sort(key=lambda item: (str(item.get("half", "H1")), int(item.get("halfSeconds", 0)), str(item.get("id"))))
        return cards

    def save_input_draft(
        self,
        gm_id: str,
        side: Side,
        *,
        payload: dict[str, Any],
        client_state: dict[str, Any],
        user_id: str,
        sync_scope: Literal["checkpoint", "state", "records", "state_records", "cards"] = "checkpoint",
        deleted_record_ids: list[str] | None = None,
        deleted_card_ids: list[str] | None = None,
        cleared_record_player_ids: list[str] | None = None,
        include_events: bool = True,
    ) -> dict[str, Any]:
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        now = utc_now()
        current = reference.get(retry=None, timeout=10)
        previous = current.to_dict() or {}
        previous_shared = previous.get("sharedState") if isinstance(previous.get("sharedState"), dict) else {}
        previous_client = previous.get("clientState") if isinstance(previous.get("clientState"), dict) else {}
        previous_payload = previous.get("payload") if isinstance(previous.get("payload"), dict) else {}
        # Only the primary analyst can advance shared match lifecycle/clock.
        # An assistant's record sync must never pause, restart or reopen a half.
        primary_uid = str(previous.get("primaryUid") or "")
        participants = previous.get("participants") if isinstance(previous.get("participants"), dict) else {}
        participant = participants.get(user_id) if isinstance(participants.get(user_id), dict) else {}
        is_participant_primary = participant.get("role") == "primary"
        can_write_live_state = not primary_uid or primary_uid == user_id or is_participant_primary
        if is_participant_primary and primary_uid != user_id:
            primary_uid = user_id
        if sync_scope in {"state", "state_records"} and can_write_live_state:
            # A live state write queued before "half end" can reach the server after
            # the H1_done/H2_done checkpoint. It must never reopen a finished half.
            previous_status = previous_shared.get("halfStatus") or previous_payload.get("status")
            if self._lifecycle_rank(payload.get("status")) < self._lifecycle_rank(previous_status):
                payload = {**payload, "status": previous_status}
                client_state = {
                    **client_state, "halfStatus": previous_status,
                    "seconds": previous_shared.get("seconds", client_state.get("seconds", 0)),
                    "clockStartedAt": previous_shared.get("clockStartedAt"),
                }
        if sync_scope in {"records", "cards"} or not can_write_live_state:
            shared_state = previous_shared
        else:
            shared_state = {
                **previous_shared,
                "halfStatus": payload.get("status", "ready"),
                "seconds": client_state.get("seconds", previous_shared.get("seconds", 0)),
                "h1Seconds": client_state.get("h1Seconds", payload.get("halves", {}).get("H1", {}).get("seconds", 0)),
                "h2Seconds": client_state.get("h2Seconds", payload.get("halves", {}).get("H2", {}).get("seconds", 0)),
                "clockStartedAt": client_state.get("clockStartedAt"),
                "homeScore": payload.get("homeScore", previous_shared.get("homeScore", 0)),
                "awayScore": payload.get("awayScore", previous_shared.get("awayScore", 0)),
            }
        root_payload = payload if sync_scope == "checkpoint" or not previous_payload else previous_payload
        payload_update: dict[str, Any] | None = None
        if sync_scope == "checkpoint":
            root_payload = {**payload, "records": previous_payload.get("records", [])}
            payload_update = root_payload
        elif sync_scope in {"state", "state_records"} and can_write_live_state:
            root_payload = {
                **previous_payload,
                "status": payload.get("status", previous_payload.get("status", "ready")),
                "homeScore": payload.get("homeScore", previous_payload.get("homeScore", 0)),
                "awayScore": payload.get("awayScore", previous_payload.get("awayScore", 0)),
                "halves": payload.get("halves", previous_payload.get("halves", {})),
            }
            payload_update = {
                "status": root_payload["status"], "homeScore": root_payload["homeScore"],
                "awayScore": root_payload["awayScore"], "halves": root_payload["halves"],
            }
        elif sync_scope == "cards":
            # Card events live in their own merge authority. A stale browser
            # may add/update its own card but can never replace another card's
            # list by posting an older array.
            root_payload = previous_payload
        if sync_scope == "checkpoint" and not can_write_live_state and previous_payload:
            root_payload = previous_payload

        records_reference = reference.collection("records")
        records_batch = None
        record_mutation_count = 0
        cleared_player_ids = {str(value) for value in cleared_record_player_ids or [] if value}
        if sync_scope in {"checkpoint", "records", "state_records"}:
            records_batch = self.db.batch()
            for index, record in enumerate(payload.get("records", [])):
                if not isinstance(record, dict):
                    continue
                record_id = str(record.get("id") or uuid4())
                record_update = {
                    key: value for key, value in record.items()
                    if value is not None or (key == "playerId" and record_id in cleared_player_ids)
                }
                records_batch.set(records_reference.document(record_id), {
                    **record_update, "id": record_id, "half": record.get("half") or "H1",
                    "deleted": False, "updatedAt": now, "updatedBy": user_id,
                }, merge=True)
                record_mutation_count += 1
            for record_id in deleted_record_ids or []:
                if record_id:
                    records_batch.set(records_reference.document(str(record_id)), {
                        "id": str(record_id), "deleted": True, "updatedAt": now, "updatedBy": user_id,
                    }, merge=True)
                    record_mutation_count += 1

        cards_batch = None
        if sync_scope == "cards":
            cards_reference = reference.collection("cards")
            cards_batch = self.db.batch()
            existing_cards = list(cards_reference.stream(retry=None, timeout=20))
            # Migrate legacy root-array cards once before accepting per-event
            # writes. Tombstones are documents too, so an all-card deletion is
            # preserved rather than falling back to the old root array.
            if not existing_cards:
                for index, legacy in enumerate(previous_payload.get("cards", [])):
                    if not isinstance(legacy, dict):
                        continue
                    legacy_id = str(legacy.get("id") or f"legacy-card:{legacy.get('playerId')}:{legacy.get('half')}:{legacy.get('halfSeconds')}:{legacy.get('card')}:{index}")
                    cards_batch.set(cards_reference.document(legacy_id), {
                        **legacy, "id": legacy_id, "deleted": False, "updatedAt": now, "updatedBy": user_id,
                    }, merge=True)
            for index, card in enumerate(payload.get("cards", [])):
                if not isinstance(card, dict):
                    continue
                card_id = str(card.get("id") or f"legacy-card:{card.get('playerId')}:{card.get('half')}:{card.get('halfSeconds')}:{card.get('card')}:{index}")
                cards_batch.set(cards_reference.document(card_id), {
                    **card, "id": card_id, "deleted": False, "updatedAt": now, "updatedBy": user_id,
                }, merge=True)
            for card_id in deleted_card_ids or []:
                if card_id:
                    cards_batch.set(cards_reference.document(str(card_id)), {
                        "id": str(card_id), "deleted": True, "updatedAt": now, "updatedBy": user_id,
                    }, merge=True)

        # Persist recovery-safe clock data without allowing a record-only sync
        # to replace the rest of another analyst's client snapshot.
        if sync_scope in {"state", "state_records"} and can_write_live_state:
            payload_halves = payload.get("halves", {}) if isinstance(payload.get("halves"), dict) else {}
            h1_payload = payload_halves.get("H1", {}) if isinstance(payload_halves.get("H1"), dict) else {}
            h2_payload = payload_halves.get("H2", {}) if isinstance(payload_halves.get("H2"), dict) else {}
            next_client_state = {
                **previous_client,
                **{key: client_state[key] for key in ("seconds", "h1Seconds", "h2Seconds", "clockStartedAt", "halfStatus", "homeScore", "awayScore") if key in client_state},
                "halfStatus": client_state.get("halfStatus", payload.get("status", previous_client.get("halfStatus", "ready"))),
                "seconds": client_state.get("seconds", previous_client.get("seconds", 0)),
                "h1Seconds": client_state.get("h1Seconds", h1_payload.get("seconds", previous_client.get("h1Seconds", 0))),
                "h2Seconds": client_state.get("h2Seconds", h2_payload.get("seconds", previous_client.get("h2Seconds", 0))),
                "clockStartedAt": client_state.get("clockStartedAt"),
            }
        elif sync_scope in {"records", "cards"}:
            next_client_state = previous_client or client_state
        elif sync_scope == "checkpoint" and not can_write_live_state:
            next_client_state = previous_client
        else:
            next_client_state = client_state
        document: dict[str, Any] = {
            "gmId": gm_id,
            "side": side,
            "clientState": next_client_state,
            "recorderLevel": root_payload.get("recorderLevel", "advanced"),
            "status": root_payload.get("status", "ready"),
            "updatedBy": user_id,
            "updatedAt": now,
            "revision": int(previous.get("revision") or 0) + 1,
            "createdAt": previous.get("createdAt", now) if current.exists else now,
            # Collaboration metadata is deliberately retained when lifecycle saves
            # replace the validated payload snapshot.
            "participants": participants,
            "primaryUid": primary_uid or previous.get("primaryUid"),
            "collaboration": previous.get("collaboration", {}),
            "sharedState": shared_state,
        }
        if payload_update is not None:
            document["payload"] = payload_update
        # A normal live command touches one or a handful of record documents.
        # Commit its root state and record deltas together so subscribers never
        # receive a timer/lifecycle revision before its accompanying event.
        # Large restore checkpoints keep the existing split path to stay below
        # Firestore's 500-write batch limit.
        if sync_scope == "state_records" and records_batch is not None and record_mutation_count < 450:
            records_batch.set(reference, document, merge=True)
            records_batch.commit(retry=None, timeout=20)
        else:
            if records_batch is not None:
                records_batch.commit(retry=None, timeout=20)
            if cards_batch is not None:
                cards_batch.commit(retry=None, timeout=20)
            # Setup has its own event-driven write path. Never include it in a
            # record/clock checkpoint: this request may have read an older Draft
            # just before a substitution wrote a newer setup revision.
            reference.set(document, merge=True, retry=None, timeout=20)
        saved = reference.get(retry=None, timeout=10).to_dict() or {}
        # Clock ticks are frequent and never affect a schedule card. Refresh
        # only when its visible lifecycle/score changes (or a full checkpoint
        # is written), keeping the read model from becoming another live-load
        # source. It is always derived after the Draft write commits.
        visible_summary_changed = (
            sync_scope == "checkpoint"
            or (
                sync_scope in {"state", "state_records"}
                and can_write_live_state
                and any(
                    root_payload.get(key) != previous_payload.get(key)
                    for key in ("status", "homeScore", "awayScore")
                )
            )
        )
        if visible_summary_changed:
            self.sync_input_summary_from_draft(gm_id, side, saved)
        if include_events:
            return self._with_draft_records(reference, saved)
        if sync_scope == "cards" and isinstance(saved.get("payload"), dict):
            saved = {**saved, "payload": {**saved["payload"], "cards": self._draft_cards(reference)}}
        return saved

    def join_input_draft_participant(
        self,
        gm_id: str,
        side: Side,
        *,
        user_id: str,
        role: Literal["primary", "assistant", "manager"],
        display_name: str | None = None,
    ) -> dict[str, Any]:
        # Two analysts can press setup save together, including on different
        # backend workers. The Draft read and seat claim must commit together.
        @firestore.transactional
        def claim(transaction):
            return self._join_input_draft_participant(
                gm_id, side, user_id=user_id, role=role,
                display_name=display_name, transaction=transaction,
            )

        document = claim(self.db.transaction())
        self.sync_input_summary_from_draft(gm_id, side, document)
        return document

    def _join_input_draft_participant(
        self,
        gm_id: str,
        side: Side,
        *,
        user_id: str,
        role: Literal["primary", "assistant", "manager"],
        display_name: str | None = None,
        transaction: Any,
    ) -> dict[str, Any]:
        """Register a recorder on the existing Draft root without touching records.

        Seats: one primary, one assistant. Once both are taken, managers may
        join when allowed. A finished team still accepts its original primary or
        assistant under the same role, plus manager entry.

        The browser writes live state/records directly under this same Draft. This
        server endpoint only establishes the durable role assignment used by rules
        and by final RAW promotion.
        """
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        snapshot = reference.get(transaction=transaction, retry=None, timeout=10)
        now = utc_now()
        current = snapshot.to_dict() or {}
        profile_snapshot = self.db.collection("recorders").document(user_id).get(retry=None, timeout=10)
        profile = profile_snapshot.to_dict() or {}
        recorder_level = "basic" if profile.get("level") == "basic" else "advanced"
        primary_uid = str(current.get("primaryUid") or "")
        participants = current.get("participants") if isinstance(current.get("participants"), dict) else {}
        existing_participant = participants.get(user_id) if isinstance(participants.get(user_id), dict) else {}
        existing_role = existing_participant.get("role")

        def raw_roster_role_for_user() -> str | None:
            heads = self.get_recording_heads_many([gm_id]).get(gm_id, {}).get(side, {})
            recorders = heads.get("recorders") if isinstance(heads.get("recorders"), dict) else {}
            rank_roles = {"main": "primary", "sub": "assistant", "manager": "manager"}
            direct = recorders.get(user_id) if isinstance(recorders.get(user_id), dict) else {}
            direct_role = rank_roles.get(direct.get("rank"))
            if direct_role:
                return direct_role
            if not display_name:
                return None
            legacy = recorders.get("local-did-input") if isinstance(recorders.get("local-did-input"), dict) else {}
            return rank_roles.get(legacy.get("rank")) if legacy.get("name") == display_name else None

        finished_role_reentry = False
        # Re-entry never changes a role. The schedule can carry an old/default
        # role query after a refresh, so return the role already assigned to
        # this UID instead of treating its own re-entry as a conflict.
        if existing_role in {"primary", "assistant", "manager"}:
            role = existing_role
            # Older local sessions could retain a legacy primaryUid while the
            # participant record had already moved to the authenticated UID.
            # That made the UI correctly display "primary" but disabled every
            # lifecycle control because server ownership still pointed at the
            # orphaned legacy participant. Repair only that unambiguous legacy
            # case; a real different primary remains a conflict.
            if role == "primary" and primary_uid != user_id:
                legacy_primary = participants.get(primary_uid) if isinstance(participants.get(primary_uid), dict) else {}
                if (
                    primary_uid == "local-did-input"
                    and display_name
                    and legacy_primary.get("name") == display_name
                ):
                    participants.pop(primary_uid, None)
                    current["participants"] = participants
                    primary_uid = user_id
                elif legacy_primary.get("role") != "primary":
                    primary_uid = user_id
                else:
                    raise BackendError("Draft primary ownership is inconsistent", status_code=409, code="primary_owner_conflict")
        else:
            shared = current.get("sharedState") if isinstance(current.get("sharedState"), dict) else {}
            payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
            finished = (
                self.get_recording_statuses(gm_id)[side] == "final"
                or (shared.get("halfStatus") or payload.get("status") or current.get("status")) == "final"
            )
            raw_role = raw_roster_role_for_user() if finished else None
            if finished and role != "manager" and raw_role == role:
                finished_role_reentry = True
            elif role == "manager":
                if recorder_level != "advanced":
                    raise BackendError("Only an advanced analyst can join as a manager", status_code=403, code="manager_requires_advanced")
                if not finished and not primary_uid:
                    raise BackendError("The primary analyst must join before a manager", status_code=409, code="primary_required")
            elif finished:
                raise BackendError("A finished team input accepts managers only", status_code=409, code="manager_only")
            elif role == "assistant" and any(
                isinstance(item, dict) and item.get("role") == "assistant" for item in participants.values()
            ):
                raise BackendError("An assistant analyst is already assigned for this team", status_code=409, code="assistant_already_assigned")
        if role == "primary":
            if primary_uid and primary_uid != user_id:
                legacy_primary = participants.get(primary_uid) if isinstance(participants.get(primary_uid), dict) else {}
                # Drafts created before local Firebase UID forwarding used one
                # shared local UID. Let the same named analyst reclaim it once.
                if primary_uid == "local-did-input" and display_name and legacy_primary.get("name") == display_name:
                    participants.pop(primary_uid, None)
                    current["participants"] = participants
                elif finished_role_reentry:
                    pass
                else:
                    raise BackendError("A primary analyst is already assigned for this team", status_code=409, code="primary_already_assigned")
            primary_uid = user_id
        elif role == "assistant" and not primary_uid and not finished_role_reentry:
            raise BackendError("The primary analyst must join before an assistant", status_code=409, code="primary_required")

        participants[user_id] = {
            "role": role,
            "name": display_name or user_id,
            "level": recorder_level,
            "joinedAt": participants.get(user_id, {}).get("joinedAt", now),
            "lastSeenAt": now,
        }
        updates = {
            "gmId": gm_id,
            "side": side,
            "primaryUid": primary_uid,
            "participants": participants,
            "updatedAt": now,
            "updatedBy": user_id,
            "createdAt": current.get("createdAt", now),
        }
        # A participant refresh must not overwrite setup with a stale document
        # read. Merge only collaboration metadata into the Draft root.
        transaction.set(reference, updates, merge=True)
        document = {**current, **updates}
        return document

    def ensure_recorder_profile(self, user_id: str, *, name: str | None = None) -> dict[str, Any]:
        """Create/update the minimal operational profile after Firebase Auth login."""
        reference = self.db.collection("recorders").document(user_id)
        snapshot = reference.get(retry=None, timeout=10)
        current = snapshot.to_dict() or {}
        now = utc_now()
        document = {
            **current,
            "name": name or current.get("name") or user_id,
            "level": current.get("level") if current.get("level") in {"basic", "advanced"} else "advanced",
            "updatedAt": now,
            "updatedBy": "backend-login-bootstrap",
            "createdAt": current.get("createdAt", now),
        }
        reference.set(document, retry=None, timeout=20)
        return document

    def get_recorder_profile(self, user_id: str) -> dict[str, Any]:
        snapshot = self.db.collection("recorders").document(user_id).get(retry=None, timeout=10)
        if not snapshot.exists:
            return self.ensure_recorder_profile(user_id)
        return snapshot.to_dict() or {}

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
        reference = self.db.collection("inputDrafts").document(self._input_draft_id(gm_id, side))
        # A document delete does not delete subcollections in Firestore. Drafts
        # currently own record documents, so delete them first before removing
        # the root; this prevents an old collaboration record surviving a RAW
        # promotion and reappearing when a later edit creates a fresh Draft.
        records = list(reference.collection("records").stream(retry=None, timeout=20))
        for start in range(0, len(records), 400):
            batch = self.db.batch()
            for snapshot in records[start:start + 400]:
                batch.delete(snapshot.reference)
            batch.commit(retry=None, timeout=20)
        reference.delete(retry=None, timeout=20)

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

        def calculated_draft(side: Side) -> dict[str, int | float] | None:
            """Use the shared Draft for an in-progress side with no final RAW."""
            try:
                payload = self.get_input_draft(gm_id, side).get("payload")
            except NotFoundError:
                return None
            if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
                return None
            source = []
            for index, item in enumerate(payload["records"]):
                if not isinstance(item, dict):
                    continue
                record_half = str(item.get("half") or "H1")
                if half != "all" and record_half != half:
                    continue
                source.append(KpiRecord(
                    id=str(item.get("id") or f"draft-{index}"), half=record_half,
                    seconds=int(item.get("halfSeconds") or 0), seq=int(item.get("seq") or index),
                    act=str(item.get("act") or ""), res=str(item.get("res") or ""), area=int(item.get("area") or 1),
                    player_id=str(item["playerId"]) if item.get("playerId") else None,
                    shoot_pos_x=item.get("shootPosX"), shoot_pos_y=item.get("shootPosY"),
                    shoot_dsp_range=item.get("shootDspRange"), is_shot=item.get("isShot"),
                ))
            return display(calculate_kpis(source).team_kpi)

        result: dict[Side, dict[str, int | float]] = {"H": display({}), "A": display({})}
        for side in ("H", "A"):
            try:
                recording = self.get_recording(gm_id, side)
            except NotFoundError:
                draft_kpis = calculated_draft(side)
                if draft_kpis is not None:
                    result[side] = draft_kpis
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

    def read_match_dashboard_kpis_bootstrap(
        self,
        gm_id: str,
        halves: list[Literal["H1", "H2"]],
    ) -> dict[Side, dict[str, dict[str, int | float]]]:
        """Return only the half KPI views that are immediately useful in lobby.

        All-match KPI remains lazy-loaded. During H1/H1_done the lobby needs H1;
        during H2/H2_done it needs H1 and H2. Ready/final states do not preload
        KPI here, keeping the initial lobby payload light.
        """
        result = self._empty_input_dashboard_kpis()
        for half in halves:
            half_kpis = self.read_match_dashboard_kpis(gm_id, half=half)
            for side in ("H", "A"):
                result[side][half] = half_kpis[side]
        return result

    @staticmethod
    def _empty_input_dashboard_kpis() -> dict[Side, dict[str, dict[str, int | float]]]:
        fields = ("TAP", "DAP", "DTP", "Shoot", "Goal", "SSR", "BAP", "ASR")
        empty = {field: 0 for field in fields}
        return {
            side: {"all": dict(empty), "H1": dict(empty), "H2": dict(empty)}
            for side in ("H", "A")
        }

    def input_bootstrap_kpi_halves(self, lifecycle: str | None) -> list[Literal["H1", "H2"]]:
        if lifecycle in {"H1", "H1_done"}:
            return ["H1"]
        if lifecycle in {"H2", "H2_done"}:
            return ["H1", "H2"]
        return []

    def build_input_bootstrap(self, gm_id: str, side: Side) -> dict[str, Any]:
        """Return the lobby-critical data in one response with parallel independent reads."""
        match = self.get_match(gm_id)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="input-bootstrap") as executor:
            squads_future = executor.submit(self._get_or_create_input_squads_snapshot, gm_id, match)
            summary_future = executor.submit(lambda: self._input_summary_reference(gm_id).get(retry=None, timeout=10))
            summary_snapshot = summary_future.result()
            summary = summary_snapshot.to_dict() or {} if summary_snapshot.exists else {}
            summary_status = summary.get("inputStatus") if isinstance(summary.get("inputStatus"), dict) else {}
            input_status = summary_status if all(isinstance(summary_status.get(item), dict) for item in ("H", "A")) else self.get_recording_input_states(gm_id)
            selected_status = input_status[side]
            squads, cached, squad_snapshot = squads_future.result()

        # A completed recording has no live collaboration state.  The lobby
        # only needs the cached squads and the compact RAW lineup shell to
        # render; Draft, setup, KPI, and RAW event rows are deferred until the
        # user asks for an action that needs them.
        is_final_lobby = selected_status["rawStatus"] == "final"
        bootstrap_kpi_halves = [] if is_final_lobby else self.input_bootstrap_kpi_halves(selected_status.get("lifecycleStatus"))
        if is_final_lobby:
            input_setup: dict[Side, dict[str, Any] | None] = {"H": None, "A": None}
            dashboard_kpis = self._empty_input_dashboard_kpis()
            # The completed lobby still renders the final formation.  Read the
            # compact recording root and rebuild only its lineup/order, score,
            # clock, and field-side shell; records and cards remain deferred
            # to an explicit correction action.
            session = self._final_lobby_session_from_snapshot(
                gm_id, side, match, selected_status.get("fieldSide"), squad_snapshot,
            )
            if session is None:
                # Modern final recordings do not yet have a display snapshot.
                # Keep their existing compact RAW fallback; records/cards are
                # still excluded from this lobby read.
                recording = self.get_recording(gm_id, side)
                session = {
                    "status": "ok",
                    **self.read_input_state_from_raw(
                        gm_id,
                        side,
                        include_records=False,
                        match=match,
                        recording=recording,
                    ),
                }
        elif selected_status.get("lifecycleStatus") == "ready":
            # Fresh scheduled fixtures have no Draft, setup, or KPI data. The
            # prebuilt summary + roster snapshot are enough for the waiting
            # room, so do not spend reads proving that those documents miss.
            input_setup = {"H": None, "A": None}
            dashboard_kpis = self._empty_input_dashboard_kpis()
            session = {"status": "missing", "gmId": gm_id, "side": side}
        else:
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="input-bootstrap-live") as executor:
                setup_future = executor.submit(self.get_input_setup, gm_id)
                kpi_future = executor.submit(self.read_match_dashboard_kpis_bootstrap, gm_id, bootstrap_kpi_halves)
                draft_future = executor.submit(self.get_input_draft, gm_id, side)
                input_setup = setup_future.result()
                dashboard_kpis = kpi_future.result()
                try:
                    draft = draft_future.result()
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

        # Existing active Drafts predate the dedicated setup field. Copy their
        # completed configuration once; later record/timer writes never touch it.
        if input_setup[side] is None and isinstance(session.get("payload"), dict):
            self.save_input_setup(
                gm_id,
                side,
                payload=session["payload"],
                client_state=session.get("clientState") if isinstance(session.get("clientState"), dict) else {},
            )
            input_setup = self.get_input_setup(gm_id)
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
            "inputSetup": input_setup,
            "session": session,
            "dashboardKpiHalves": bootstrap_kpi_halves,
            "dashboardKpis": dashboard_kpis,
        }

    def restore_input_draft_from_raw(self, gm_id: str, side: Side, *, user_id: str) -> dict[str, Any]:
        """Make (or reuse) an editable Draft from an already-final RAW recording."""
        try:
            existing = self.get_input_draft(gm_id, side)
            if existing.get("payload"):
                return existing
        except NotFoundError:
            pass
        raw_state = self.read_input_state_from_raw(gm_id, side)
        draft = self.save_input_draft(
            gm_id,
            side,
            payload=raw_state["payload"],
            client_state=raw_state["clientState"],
            user_id=user_id,
        )
        # Final RAW already owns formation, field side and lineup. Seed the
        # temporary setup when the explicit edit action recreates its Draft.
        self.save_input_setup(
            gm_id, side,
            payload=raw_state["payload"],
            client_state=raw_state["clientState"],
        )
        return draft

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
        # Validate the complete shared RAW contract before deleting any prior
        # final tree. This is the new-input counterpart to importer preflight.
        RecordingDoc.model_validate(recording)
        for _, values in records:
            RecordDoc.model_validate(values)
        for _, values in cards:
            CardDoc.model_validate(values)
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
