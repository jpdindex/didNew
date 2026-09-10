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
from threading import Event, Lock, Thread
from typing import Annotated, Any, Literal

import firebase_admin
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import Client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from backend.system.system_base import MatchDoc, MatchKpiDoc, RecordDoc, RecordingDoc, Side

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_FILE = PROJECT_ROOT / ".env"


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
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
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
    swagger_api_key: Annotated[str | None, Security(swagger_key_auth)] = None,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if settings.app_env != "local":
        raise AuthenticationError("Swagger key authentication is available only in local environment")
    if not settings.swagger_api_key:
        raise AuthenticationError("SWAGGER_API_KEY is not configured")
    if not swagger_api_key or not compare_digest(swagger_api_key, settings.swagger_api_key):
        raise AuthenticationError("Invalid Swagger key")
    return CurrentUser(uid="local-swagger", claims={"auth": "swagger_api_key"})


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
    """JPD-DID source reads and derived matchKpis writes."""

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
        gm_id: str | None = None,
        limit: int = 2000,
    ) -> list[tuple[str, MatchDoc]]:
        if gm_id:
            return [(gm_id, self.get_match(gm_id))]
        matches = [
            (snapshot.id, _validate_document(MatchDoc, snapshot.to_dict() or {}, path=snapshot.reference.path))
            for snapshot in self.db.collection("matches").limit(limit).stream(retry=None, timeout=30)
        ]
        return [
            item for item in matches
            if (league_id is None or item[1].leagueId == league_id)
            and (season_id is None or item[1].seasonId == season_id)
            and (round_number is None or item[1].round == round_number)
        ]

    def get_recording(self, gm_id: str, side: Side) -> RecordingDoc:
        reference = self.db.collection("matches").document(gm_id).collection("recordings").document(side)
        snapshot = reference.get(retry=None, timeout=10)
        if not snapshot.exists:
            raise NotFoundError(f"Recording not found: {gm_id}/{side}")
        return _validate_document(RecordingDoc, snapshot.to_dict() or {}, path=reference.path)

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

    def get_match_kpi(
        self, *, league_id: str, season_id: str, round_key: str, team_id: str, gm_id: str
    ) -> MatchKpiDoc | None:
        snapshot = self._match_kpi_ref(league_id, season_id, round_key, team_id, gm_id).get(retry=None, timeout=10)
        if not snapshot.exists:
            return None
        return _validate_document(MatchKpiDoc, snapshot.to_dict() or {}, path=snapshot.reference.path)

    def save_match_kpi(self, document: MatchKpiDoc, *, round_key: str) -> None:
        self._match_kpi_ref(document.leagueId, document.seasonId, round_key, document.teamId, document.gmId).set(
            document.model_dump(mode="python"), retry=None, timeout=15
        )

    def _match_kpi_ref(self, league_id: str, season_id: str, round_key: str, team_id: str, gm_id: str):
        return (
            self.db.collection("matchKpis").document(league_id)
            .collection("seasons").document(season_id)
            .collection("rounds").document(round_key)
            .collection("teams").document(team_id)
            .collection("matches").document(gm_id)
        )


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
