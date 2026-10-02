from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import argparse
import re
import time
from typing import Any, Callable

from google.api_core import retry as retries
from pydantic import ValidationError

from backend.system.system_firestore import BackendError, JpdDidData
from backend.system.system_schema import storage_contract_for_path, storage_field_expectation


SUPPORTED_TABLES = {
    "ff_team", "ff_player", "ff_league", "ff_season", "ff_stadium", "ff_player_team",
    "ff_game", "ff_game_info", "ff_game_record", "ff_game_player", "ff_game_player_log",
    "ff_game_card", "ff_game_path", "ff_game_bap",
}


@dataclass(slots=True)
class ParsedTable:
    columns: list[str] | None
    rows: list[list[Any]]


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    matches_replaced: int
    documents_written: int
    records_written: int


ImportProgress = Callable[[str, int, int, int, int], None]
CHECKPOINT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "legacy-import"
MAX_MATCH_ATTEMPTS = 3

# Competition documents remain in the existing ``leagues`` collection.  The
# importer only accepts this controlled registry; an arbitrary value in an old
# SQL dump must not recreate a retired competition document.
COMPETITION_TYPES: dict[str, str] = {
    "EPL": "league",
    "LALIGA": "league",
    "EREDIVISIE": "league",
    "K1": "league",
    "K2": "league",
    "BUNDESLIGA1": "league",
    "BUNDESLIGA2": "league",
    "LIGUE1PSG": "league",
    "UCL": "cup",
    "UEL": "cup",
    "AC2019": "national",
    "AC2023": "national",
    "COPA2019": "national",
    "EAFF": "national",
    "FF": "national",
    "OL2020": "national",
    "WC2018": "national",
    "WC2022": "national",
    "WQ": "national",
    "TEST": "test",
}


def normalize_legacy_date(value: Any) -> str:
    """Return one date representation for legacy identity fields.

    SQL history has both ``YYYY-MM-DD`` and ``YYYY.MM.DD`` values.  Contract
    document IDs are derived from this result, so cosmetic formatting cannot
    create a second contract any more.
    """
    raw = _string(value).strip()
    if not raw:
        return ""
    if match := re.fullmatch(r"(\d{4})[./-](\d{2})[./-](\d{2})", raw):
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if match := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", raw):
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return raw


def normalize_legacy_contract_end(value: Any) -> str | None:
    """Treat legacy open-ended sentinels as one nullable contract end."""
    normalized = normalize_legacy_date(value)
    return None if normalized in {"", "0000-00-00", "9999-99-99"} else normalized


def normalize_season_id(value: Any) -> str:
    """Normalize legacy season labels to the match ID's eight-digit contract."""
    raw = _string(value).strip().replace(" ", "")
    if re.fullmatch(r"\d{8}", raw):
        return raw
    if match := re.fullmatch(r"(\d{4})[./-](\d{2}|\d{4})", raw):
        start, end = match.groups()
        return f"{start}{end if len(end) == 4 else start[:2] + end}"
    return raw


# The installed Firestore client exposes the raw ``unary_stream`` callable for
# RunQuery. Passing the SDK default retry sentinel then attempts to read its
# private ``_retry`` member and crashes. The import preflight uses this explicit
# Retry object for its single read-only connection check.
NO_QUERY_RETRY = retries.Retry(predicate=lambda _exc: False)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _legacy_label_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _string(value).lower())




def _normalize_position(value: Any) -> str:
    raw = re.sub(r"[^A-Z]", "", _string(value).upper())
    if raw in {"GK", "G", "GOALKEEPER", "KEEPER"}:
        return "GK"
    if raw in {"FW", "F", "ST", "CF", "SS", "LW", "RW", "LF", "RF", "FORWARD", "STRIKER"}:
        return "FW"
    if raw in {"MF", "M", "DM", "CDM", "CM", "CAM", "AM", "LM", "RM", "MIDFIELDER"}:
        return "MF"
    if raw in {"DF", "D", "CB", "LB", "RB", "LWB", "RWB", "SW", "DEFENDER"}:
        return "DF"
    return _string(value).upper()

def _number(value: Any) -> int | float:
    try:
        return float(value) if "." in str(value) else int(value)
    except (TypeError, ValueError):
        return 0


def _integer(value: Any) -> int:
    return int(_number(value))


def _timestamp(value: Any) -> datetime | None:
    if not value or value in {"0000-00-00", "0000-00-00 00:00:00"}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _half(value: Any) -> str:
    return {"1": "H1", "H1": "H1", "2": "H2", "H2": "H2", "3": "H3", "H3": "H3"}.get(str(value), "H4")


def legacy_record_sort_key(row: dict[str, Any], recording: tuple[str, str] | None) -> tuple[str, str, int, int, str, str]:
    """Use source values, never SQL INSERT order, for repeatable record sequencing."""
    gm_id, side = recording or ("", "")
    half = _half(row.get("gr_half"))
    return (
        gm_id,
        side,
        {"H1": 1, "H2": 2, "H3": 3, "H4": 4}[half],
        _integer(row.get("gr_half_seconds")),
        _string(row.get("gr_regdt")),
        _string(row.get("gr_id")),
    )


def _literal(token: str, quoted: bool) -> Any:
    if quoted:
        return token.strip().replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\").replace("''", "'")
    token = token.strip()
    if not token or token.upper() == "NULL":
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return float(token) if "." in token else int(token)
    return token.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


def _tuple_values(source: str) -> list[Any]:
    values: list[Any] = []
    current = ""
    quoted = False
    quote = ""
    was_quoted = False
    index = 0
    while index < len(source):
        character = source[index]
        if quoted:
            if character == "\\":
                current += character
                index += 1
                if index < len(source):
                    current += source[index]
            elif character == quote:
                quoted = False
            else:
                current += character
        elif character in {"'", '"'}:
            quoted, quote, was_quoted = True, character, True
        elif character == ",":
            values.append(_literal(current, was_quoted))
            current, was_quoted = "", False
        else:
            current += character
        index += 1
    values.append(_literal(current, was_quoted))
    return values


def _split_tuples(source: str) -> list[list[Any]]:
    tuples: list[list[Any]] = []
    depth = 0
    current = ""
    quoted = False
    quote = ""
    index = 0
    while index < len(source):
        character = source[index]
        if quoted:
            current += character
            if character == "\\":
                index += 1
                if index < len(source):
                    current += source[index]
            elif character == quote:
                quoted = False
        elif character in {"'", '"'}:
            quoted, quote = True, character
            current += character
        elif character == "(":
            depth += 1
            if depth == 1:
                current = ""
            else:
                current += character
        elif character == ")":
            depth -= 1
            if depth == 0:
                tuples.append(_tuple_values(current))
            else:
                current += character
        elif depth:
            current += character
        index += 1
    return tuples


def parse_sql_dump(sql: str) -> dict[str, ParsedTable]:
    tables: dict[str, ParsedTable] = {}
    for match in re.finditer(r"CREATE TABLE\s+`?(\w+)`?\s*\(([\s\S]*?)\)\s*ENGINE", sql, re.IGNORECASE):
        columns = [line_match.group(1) for line in match.group(2).split(",\n") if (line_match := re.match(r"\s*`([^`]+)`", line))]
        tables[match.group(1)] = ParsedTable(columns, [])
    pattern = re.compile(r"INSERT INTO\s+`?(\w+)`?\s*(?:\(([^)]+)\))?\s*VALUES\s*([\s\S]*?);", re.IGNORECASE)
    for match in pattern.finditer(sql):
        name, explicit_columns, values = match.group(1), match.group(2), match.group(3)
        table = tables.setdefault(name, ParsedTable(None, []))
        if explicit_columns:
            table.columns = [column.strip().strip("`") for column in explicit_columns.split(",")]
        table.rows.extend(_split_tuples(values))
    return tables


def _rows(table: ParsedTable | None) -> list[dict[str, Any]]:
    if not table or not table.columns:
        return []
    return [dict(zip(table.columns, row)) for row in table.rows]


def _merge_document_values(target: dict[str, Any], update: dict[str, Any]) -> None:
    """Apply Firestore merge-set semantics for the nested maps emitted by the importer."""
    for key, value in update.items():
        existing = target.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_document_values(existing, value)
        else:
            target[key] = value


class BuildLegacyImport:
    """Safely converge legacy match trees to the output of one SQL dump."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()
        self.db = self.data.db

    def import_sql(self, sql: str, *, on_progress: ImportProgress | None = None) -> LegacyImportResult:
        tables = parse_sql_dump(sql)
        games = _rows(tables.get("ff_game"))
        if not games:
            raise BackendError("SQL dump must include ff_game rows", status_code=422, code="legacy_games_missing")
        unsupported = [name for name, table in tables.items() if name in SUPPORTED_TABLES and table.columns is None]
        if unsupported:
            raise BackendError(f"SQL dump has no column mapping: {', '.join(sorted(unsupported))}", status_code=422, code="legacy_columns_missing")
        if on_progress:
            on_progress("preparing", len(games), 0, 0, 0)
        items, gm_ids, record_count = self._build_items(tables, games)
        if not gm_ids:
            raise BackendError("SQL dump has no valid gm_id", status_code=422, code="legacy_games_missing")
        expected_documents = self._expected_match_documents(items)
        ordered_gm_ids = sorted(gm_ids)
        global_items = [item for item in items if not item[0].startswith("matches/")]
        documents_total = len(global_items) + len(expected_documents)
        expected_by_match: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for path, document in expected_documents.items():
            expected_by_match[path.split("/")[1]][path] = document
        dump_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        self._preflight_import(expected_documents)
        completed_gm_ids, global_complete = self._load_checkpoint(dump_hash, set(ordered_gm_ids))
        completed_document_count = sum(len(expected_by_match[gm_id]) for gm_id in completed_gm_ids)
        resumed_written = (len(global_items) if global_complete else 0) + completed_document_count
        if on_progress:
            on_progress("resuming" if (completed_gm_ids or global_complete) else "writing", len(ordered_gm_ids), len(completed_gm_ids), documents_total, resumed_written)

        # Shared reference data does not require match-tree replacement.
        def report_global(_state: str, _total: int, _processed: int, _documents: int, global_written: int) -> None:
            if on_progress:
                on_progress("writing", len(ordered_gm_ids), 0, documents_total, global_written)

        if global_complete:
            written = len(global_items) + completed_document_count
        else:
            written = self._run_with_retry(
                "shared reference write",
                None,
                lambda: self._write_items(global_items, on_progress=report_global),
            )
            self._save_checkpoint(dump_hash, completed_gm_ids, global_complete=True)
            written += completed_document_count
        for index, gm_id in enumerate(ordered_gm_ids, start=1):
            if gm_id in completed_gm_ids:
                continue
            match_documents = expected_by_match[gm_id]
            # SQL import owns only its known document paths. It overwrites those
            # paths directly and deliberately leaves UI snapshots and unknown
            # legacy documents alone. Unexpected RAW documents are handled by
            # the read-only verifier and a targeted repair, never by a costly
            # recursive scan during a bulk import.
            def report_match(match_written: int) -> None:
                if on_progress:
                    on_progress("writing", len(ordered_gm_ids), index - 1, documents_total, written + match_written)

            def replace_match() -> int:
                match_written = self._write_documents(match_documents, on_progress=report_match)
                self._verify_written_match(gm_id, match_documents)
                return match_written

            match_written = self._run_with_retry("match write and verification", gm_id, replace_match)
            written += match_written
            completed_gm_ids.add(gm_id)
            self._save_checkpoint(dump_hash, completed_gm_ids, global_complete=True)
            if on_progress:
                on_progress("writing", len(ordered_gm_ids), len(completed_gm_ids), documents_total, written)
        self._clear_checkpoint(dump_hash)
        return LegacyImportResult(len(gm_ids), written, record_count)

    def _preflight_import(self, expected_documents: dict[str, dict[str, Any]]) -> None:
        """Fail before writes for malformed output or an unavailable Firestore query path."""
        largest_path = ""
        largest_size = 0
        for path, document in expected_documents.items():
            if not path.startswith("matches/") or len(path.split("/")) % 2:
                raise BackendError(f"Invalid Firestore document path: {path}", status_code=422, code="legacy_import_invalid_path")
            self._validate_firestore_value(document, path)
            self._validate_storage_contract(path, document)
            estimated_size = len(json.dumps(document, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))
            if estimated_size > largest_size:
                largest_path, largest_size = path, estimated_size
            # Keep a margin below Firestore's 1 MiB document limit because JSON
            # is only an estimate of the encoded protobuf payload.
            if estimated_size >= 900_000:
                raise BackendError(
                    f"Firestore document is too large before import: {path} ({estimated_size} bytes estimated)",
                    status_code=422,
                    code="legacy_import_document_too_large",
                )
        try:
            # Use the exact retry mode used by stale cleanup, before any write.
            next(iter(self.db.collection("matches").limit(1).stream(retry=NO_QUERY_RETRY, timeout=10)), None)
        except Exception as exc:
            raise BackendError(
                f"Firestore read preflight failed before import: {type(exc).__name__}: {exc}",
                status_code=503,
                code="legacy_import_firestore_unavailable",
            ) from exc

    @staticmethod
    def _validate_storage_contract(path: str, document: dict[str, Any]) -> None:
        """Reject a malformed SQL match tree before its first Firestore write."""
        contract = storage_contract_for_path(path)
        if contract is None:
            return
        try:
            contract.model.model_validate(document)
        except ValidationError as exc:
            first = exc.errors(include_url=False)[0]
            location = tuple(str(item) for item in first["loc"])
            field = ".".join(location) or "document"
            raise BackendError(
                f"SQL importer DID contract violation at {path} field {field}: "
                f"expected {storage_field_expectation(path, location)}; "
                f"actual {first.get('input')!r} ({first['type']}: {first['msg']})",
                status_code=422,
                code="legacy_import_contract_violation",
            ) from exc

    @classmethod
    def _validate_firestore_value(cls, value: Any, path: str) -> None:
        if value is None or isinstance(value, (str, bool, int, float, datetime)):
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                cls._validate_firestore_value(item, f"{path}[{index}]")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise BackendError(f"Firestore map key must be a string: {path}", status_code=422, code="legacy_import_invalid_value")
                cls._validate_firestore_value(item, f"{path}.{key}")
            return
        raise BackendError(
            f"Unsupported Firestore value at {path}: {type(value).__name__}",
            status_code=422,
            code="legacy_import_invalid_value",
        )

    @staticmethod
    def _checkpoint_path(dump_hash: str) -> Path:
        return CHECKPOINT_DIRECTORY / f"{dump_hash}.json"

    def _load_checkpoint(self, dump_hash: str, allowed_gm_ids: set[str]) -> tuple[set[str], bool]:
        path = self._checkpoint_path(dump_hash)
        if not path.exists():
            return set(), False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            completed = payload.get("completedGmIds", [])
            return (
                {gm_id for gm_id in completed if isinstance(gm_id, str) and gm_id in allowed_gm_ids},
                payload.get("globalComplete") is True,
            )
        except (OSError, ValueError, TypeError):
            return set(), False

    def _save_checkpoint(self, dump_hash: str, completed_gm_ids: set[str], *, global_complete: bool) -> None:
        CHECKPOINT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = self._checkpoint_path(dump_hash)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"globalComplete": global_complete, "completedGmIds": sorted(completed_gm_ids)}, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _clear_checkpoint(self, dump_hash: str) -> None:
        self._checkpoint_path(dump_hash).unlink(missing_ok=True)

    @staticmethod
    def _run_with_retry(stage: str, gm_id: str | None, operation: Callable[[], Any]) -> Any:
        for attempt in range(1, MAX_MATCH_ATTEMPTS + 1):
            try:
                return operation()
            except BackendError:
                raise
            except Exception as exc:
                if attempt == MAX_MATCH_ATTEMPTS:
                    scope = gm_id or "shared reference data"
                    raise BackendError(
                        f"SQL import failed for {scope} during {stage} after {attempt} attempts: {type(exc).__name__}: {exc}",
                        status_code=500,
                        code="legacy_import_retry_exhausted",
                    ) from exc
                time.sleep(attempt)

    def build_expected_match_documents(self, sql: str) -> tuple[dict[str, dict[str, Any]], set[str], int]:
        """Build the exact match-tree documents an import would write, without Firestore I/O.

        The verifier uses this to compare an uploaded SQL dump against an
        already-imported match tree.  It must stay on the same conversion path
        as ``import_sql`` while never calling its delete or write phases.
        """
        tables = parse_sql_dump(sql)
        games = _rows(tables.get("ff_game"))
        if not games:
            raise BackendError("SQL dump must include ff_game rows", status_code=422, code="legacy_games_missing")
        unsupported = [name for name, table in tables.items() if name in SUPPORTED_TABLES and table.columns is None]
        if unsupported:
            raise BackendError(f"SQL dump has no column mapping: {', '.join(sorted(unsupported))}", status_code=422, code="legacy_columns_missing")
        items, gm_ids, record_count = self._build_items(tables, games)
        return self._expected_match_documents(items), gm_ids, record_count

    def backfill_legacy_kpi_evidence(self, sql: str, *, on_progress: ImportProgress | None = None) -> LegacyImportResult:
        """Add only SQL KPI evidence to existing RAW; never replace match trees."""
        tables = parse_sql_dump(sql)
        games = _rows(tables.get("ff_game"))
        items, gm_ids, record_count = self._build_items(tables, games)
        evidence_by_path: dict[str, dict[str, Any]] = {}
        for path, payload, _ in items:
            values = {key: value for key, value in payload.items() if key in {"legacyPathId", "legacyPathType", "legacyPathTtp", "legacyKpiFlags"}}
            if "/records/" in path and values:
                evidence_by_path.setdefault(path, {}).update(values)
        evidence = [(path, payload, True) for path, payload in evidence_by_path.items()]
        self._write_items(evidence, on_progress=on_progress, total_matches=len(gm_ids))
        return LegacyImportResult(len(gm_ids), len(evidence), record_count)

    def _write_items(
        self,
        items: list[tuple[str, dict[str, Any], bool]],
        *,
        on_progress: ImportProgress | None = None,
        total_matches: int = 0,
    ) -> int:
        for offset in range(0, len(items), 200):
            batch = self.db.batch()
            for path, payload, merge in items[offset:offset + 200]:
                batch.set(self.db.document(path), payload, merge=merge)
            batch.commit(timeout=60)
            if on_progress:
                on_progress("writing", total_matches, total_matches, len(items), min(offset + 200, len(items)))
        return len(items)

    def _write_documents(
        self,
        documents: dict[str, dict[str, Any]],
        *,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        """Write one complete payload per document, avoiding duplicate writes in a batch."""
        paths = sorted(documents)
        for offset in range(0, len(paths), 200):
            batch = self.db.batch()
            for path in paths[offset:offset + 200]:
                batch.set(self.db.document(path), documents[path], merge=False)
            batch.commit(timeout=60)
            if on_progress:
                on_progress(min(offset + 200, len(paths)))
        return len(paths)

    @staticmethod
    def _expected_match_documents(items: list[tuple[str, dict[str, Any], bool]]) -> dict[str, dict[str, Any]]:
        documents: dict[str, dict[str, Any]] = {}
        for path, payload, merge in items:
            if not path.startswith("matches/"):
                continue
            if merge and path in documents:
                _merge_document_values(documents[path], payload)
            else:
                documents[path] = dict(payload)
        return documents

    def _verify_written_match(self, gm_id: str, expected_documents: dict[str, dict[str, Any]]) -> None:
        missing: list[str] = []
        paths = sorted(expected_documents)
        for offset in range(0, len(paths), 300):
            references = [self.db.document(path) for path in paths[offset:offset + 300]]
            actual_paths = {
                snapshot.reference.path
                for snapshot in self.db.get_all(references, retry=None, timeout=30)
                if snapshot.exists
            }
            missing.extend(path for path in paths[offset:offset + 300] if path not in actual_paths)
        if missing:
            preview = ", ".join(missing[:3])
            raise BackendError(
                f"SQL import write verification failed for {gm_id}: {len(missing)} documents missing ({preview})",
                status_code=500,
                code="legacy_import_write_incomplete",
            )

    def _build_items(self, tables: dict[str, ParsedTable], games: list[dict[str, Any]]) -> tuple[list[tuple[str, dict[str, Any], bool]], set[str], int]:
        now = datetime.now(timezone.utc)
        items: list[tuple[str, dict[str, Any], bool]] = []
        append = items.append
        gm_ids = {_string(row.get("gm_id")) for row in games if _string(row.get("gm_id"))}
        team_rows = _rows(tables.get("ff_team"))
        player_names = {_string(row.get("p_id")): _string(row.get("p_name")) for row in _rows(tables.get("ff_player"))}
        contracts: dict[str, list[dict[str, str | None]]] = defaultdict(list)
        team_season_competitions: dict[tuple[str, str], set[str]] = defaultdict(set)
        team_ids_by_label: dict[str, str] = {}
        for row in team_rows:
            team_id = _string(row.get("t_code"))
            for label in (row.get("t_name"), row.get("t_name_full"), row.get("t_name_short")):
                if team_id and (key := _legacy_label_key(label)):
                    team_ids_by_label[key] = team_id
        home_teams_by_stadium: dict[str, Counter[str]] = defaultdict(Counter)
        for row in games:
            stadium_id = _string(row.get("gm_s_code"))
            home_team_id = _string(row.get("gm_h_t_code"))
            if stadium_id and home_team_id:
                home_teams_by_stadium[stadium_id][home_team_id] += 1

        for row in team_rows:
            team_id = _string(row.get("t_code"))
            if team_id:
                # A historical dump can enrich immutable team identity fields,
                # but must never overwrite the team's present-day league/cup
                # cache or operational audit fields.
                payload = {
                    "name": _string(row.get("t_name")),
                    "nameKr": _string(row.get("t_name_kr")) or None,
                    "nameFull": _string(row.get("t_name_full")) or None,
                    "nameShort": _string(row.get("t_name_short")) or None,
                }
                if stadium_id := _string(row.get("s_code")):
                    payload["stadiumId"] = stadium_id
                append((f"teams/{team_id}", payload, True))
        for row in _rows(tables.get("ff_player")):
            player_id = _string(row.get("p_id"))
            if player_id:
                append((f"players/{player_id}", {"name": _string(row.get("p_name")), "nameEn": _string(row.get("p_name_en")) or None, "nameFull": _string(row.get("p_name_full")) or None, "foot": _string(row.get("p_foot")) or None, "birth": _string(row.get("p_birth")) or None, "height": _number(row.get("p_height")) if row.get("p_height") is not None else None, "nation": _string(row.get("p_nation")) or None, "legacyPlayerId": _integer(row.get("p_id_old")) if row.get("p_id_old") is not None else None, "active": True, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_league")):
            league_id = _string(row.get("league_code"))
            if league_id in COMPETITION_TYPES:
                append((f"leagues/{league_id}", {"name": _string(row.get("league_name")), "nameEn": _string(row.get("league_name_en")) or None, "competitionType": COMPETITION_TYPES[league_id], "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_season")):
            league_id, name = _string(row.get("l_code")), _string(row.get("s_name")).replace(" ", "")
            if league_id and name:
                append((f"seasons/{league_id}_{name}", {"leagueId": league_id, "name": _string(row.get("s_name")), "from": _string(row.get("s_fr_date")), "to": _string(row.get("s_to_date")), "alias": _string(row.get("s_alias")) or None, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_stadium")):
            stadium_id = _string(row.get("s_code"))
            if stadium_id:
                home_team_id = team_ids_by_label.get(_legacy_label_key(row.get("s_hometeam")))
                if home_team_id is None and home_teams_by_stadium.get(stadium_id):
                    home_team_id = home_teams_by_stadium[stadium_id].most_common(1)[0][0]
                payload = {
                    "name": _string(row.get("s_name")),
                    "nameKr": _string(row.get("s_name_kr")) or None,
                    "seats": _integer(row.get("s_seats")) if row.get("s_seats") is not None else None,
                    "country": _string(row.get("s_country")) or None,
                    "city": _string(row.get("s_city")) or None,
                    "surface": _string(row.get("s_ground")) or None,
                    "createdAt": now,
                    "createdBy": "legacy-import",
                    "updatedAt": now,
                    "updatedBy": "legacy-import",
                }
                if home_team_id:
                    payload["homeTeamId"] = home_team_id
                append((f"stadiums/{stadium_id}", payload, True))
        for row in _rows(tables.get("ff_player_team")):
            player_id, team_id, begin = _string(row.get("p_id")), _string(row.get("t_code")), normalize_legacy_date(row.get("pt_begin"))
            if not player_id or not team_id or not begin:
                continue
            competition_id = _string(row.get("l_code")) or None
            contract = {"from": begin, "to": normalize_legacy_contract_end(row.get("pt_end")), "no": _string(row.get("pt_num")), "pos": _string(row.get("pt_pos"))}
            contracts[f"{player_id}_{team_id}"].append(contract)
            append((f"players/{player_id}/contracts/{team_id}_{begin}", {
                "teamId": team_id, "leagueId": competition_id,
                "competitionType": COMPETITION_TYPES.get(competition_id or ""),
                **contract, "createdAt": now, "createdBy": "legacy-import",
                "updatedAt": now, "updatedBy": "legacy-import",
            }, True))
            if not contract["to"]:
                append((f"teams/{team_id}/squad/{player_id}", {"name": player_names.get(player_id, ""), "no": contract["no"], "pos": contract["pos"], "contractId": f"{team_id}_{begin}", "since": begin}, True))

        matches: dict[str, dict[str, Any]] = {}
        for row in games:
            gm_id = _string(row.get("gm_id"))
            if not gm_id:
                continue
            match = {"home": _string(row.get("gm_h_t_code")), "away": _string(row.get("gm_a_t_code")), "date": _string(row.get("gm_date")), "realtime": _string(row.get("is_realtime")).upper() == "Y" or _integer(row.get("is_realtime")) == 1}
            matches[gm_id] = match
            append((f"matches/{gm_id}", {"date": match["date"], "kickoffTime": _string(row.get("gm_time")) or None, "leagueId": _string(row.get("gm_league")), "seasonId": gm_id[:8], "matchType": "league", "round": _integer(row.get("gm_round")) if row.get("gm_round") is not None else None, "group": _string(row.get("gm_sub_league")) or None, "stadiumId": _string(row.get("gm_s_code")), "homeTeamId": match["home"], "awayTeamId": match["away"], "score": {"home": _integer(row.get("gi_goal_home")), "away": _integer(row.get("gi_goal_away"))}, "createdAt": now, "updatedAt": now}, False))
            competition_id = _string(row.get("gm_league"))
            for team_id in (match["home"], match["away"]):
                if team_id and competition_id:
                    team_season_competitions[(team_id, gm_id[:8])].add(competition_id)

        # A team can play EPL and UCL in one season.  Store the source truth as
        # a set of competition IDs; presentation can classify it via
        # leagues/{competitionId}.competitionType without one match overwriting
        # another competition's membership.
        for (team_id, season_id), competition_ids in sorted(team_season_competitions.items()):
            append((f"teams/{team_id}/seasons/{season_id}", {"competitionIds": sorted(competition_ids)}, True))

        recordings: dict[str, tuple[str, str]] = {}
        for row in _rows(tables.get("ff_game_info")):
            gm_id = _string(row.get("gm_id"))
            if gm_id not in matches:
                continue
            side = _string(row.get("gi_write_code")).upper()
            if side not in {"H", "A"}:
                side = "H" if matches[gm_id]["home"] == _string(row.get("gi_h_t_code")) else "A"
            home, away = _string(row.get("gi_h_t_code")), _string(row.get("gi_a_t_code"))
            team_id, opponent_id = (home, away) if side == "H" else (away, home)
            recordings[_string(row.get("gi_id"))] = (gm_id, side)
            append((f"matches/{gm_id}/recordings/{side}", {"side": side, "teamId": team_id, "opponentTeamId": opponent_id, "recorders": {}, "recorderIds": [_string(row.get("gi_user_id"))] if _string(row.get("gi_user_id")) else [], "status": "final", "inputMode": "실시간" if matches[gm_id]["realtime"] else "분석", "fieldSide": "right" if _string(row.get("gi_part")) == "R" else "left", "fieldSideEx": "right" if _string(row.get("gi_part_ex")) == "R" else ("left" if _string(row.get("gi_part_ex")) else None), "formationKey": _string(row.get("gi_formation")), "lineup": {}, "halves": {"H1": {"startedAt": _timestamp(row.get("gi_h1_begin")), "seconds": _integer(row.get("gi_h1_seconds"))}, "H2": {"startedAt": _timestamp(row.get("gi_h2_begin")), "seconds": _integer(row.get("gi_h2_seconds"))}}, "h1Locked": True, "h2Locked": True, "maxSeq": 0, "kpi": {"TAP": _number(row.get("gi_tmp")), "DAP": _number(row.get("gi_tap")), "TTP": _number(row.get("gi_ttp")), "DTP": _number(row.get("gi_ctp")), "BAP": _number(row.get("gi_bap")), "DTB": _number(row.get("gi_ctb")), "DTM": _number(row.get("gi_ctm")), "DTA": _number(row.get("gi_cta")), "DTS": _number(row.get("gi_cts")), "SHOT": _number(row.get("gi_sht")), "ASR": _number(row.get("gi_asr")), "SSR": _number(row.get("gi_ssr")), "GOAL": _number(row.get("gi_gol")), "OG": 0}, "kpiComputedAt": now, "kpiVersion": 1, "teamRating": None, "ratingBasedOn": None, "legacyGiId": _string(row.get("gi_id")), "syncedAt": None, "createdAt": _timestamp(row.get("gi_regdt")) or now, "updatedAt": _timestamp(row.get("gi_moddt")) or now}, False))

        path_records: dict[str, list[str]] = defaultdict(list)
        counters: dict[str, int] = defaultdict(int)
        record_count = 0
        record_rows = [
            row for row in _rows(tables.get("ff_game_record"))
            if _string(row.get("gi_id")) in recordings
        ]
        for row in sorted(
            record_rows,
            key=lambda item: legacy_record_sort_key(item, recordings.get(_string(item.get("gi_id")))),
        ):
            recording = recordings.get(_string(row.get("gi_id")))
            if not recording:
                continue
            gm_id, side = recording
            half, seconds = _half(row.get("gr_half")), _integer(row.get("gr_half_seconds"))
            key = f"{gm_id}:{side}:{half}:{seconds}"
            seq, counters[key] = counters[key], counters[key] + 1
            record_id = _string(row.get("gr_id"))
            if row.get("gt_id") is not None:
                path_records[f"{gm_id}:{side}:{_string(row.get('gt_id'))}"].append(record_id)
            legacy_flags = {"tap": bool(_integer(row.get("gr_is_tmp"))), "tapSuccess": bool(_integer(row.get("gr_is_tmp_s"))), "dap": bool(_integer(row.get("gr_is_tap"))), "dapSuccess": bool(_integer(row.get("gr_is_tap_s"))), "ast": bool(_integer(row.get("gr_is_ast"))), "shot": bool(_integer(row.get("gr_is_sht"))), "shotSuccess": bool(_integer(row.get("gr_is_sht_s"))), "goal": bool(_integer(row.get("gr_is_gol"))), "dtb": bool(_integer(row.get("gr_is_ctb"))), "dtm": bool(_integer(row.get("gr_is_ctm"))), "dta": bool(_integer(row.get("gr_is_cta"))), "dts": bool(_integer(row.get("gr_is_cts"))), "gtb": bool(_integer(row.get("gr_is_gtb"))), "gtm": bool(_integer(row.get("gr_is_gtm")))}
            append((f"matches/{gm_id}/recordings/{side}/records/{record_id}", {"half": half, "halfSeconds": seconds, "seq": seq, "act": _string(row.get("gr_act_code")), "res": _string(row.get("gr_res_code")), "area": _integer(row.get("gr_area_code")), "posX": _number(row.get("gr_pos_x")) / 971 * 100 if row.get("gr_pos_x") is not None else None, "posY": _number(row.get("gr_pos_y")) / 634 * 100 if row.get("gr_pos_y") is not None else None, "shootPosX": _number(row.get("gr_shoot_pos_x")) if row.get("gr_shoot_pos_x") is not None else None, "shootPosY": _number(row.get("gr_shoot_pos_y")) if row.get("gr_shoot_pos_y") is not None else None, "legacyPathId": _string(row.get("gt_id")) or None, "legacyKpiFlags": legacy_flags, "playerId": _string(row.get("p_id")) or None, "createdBy": "legacy-import", "source": "did", "createdAt": _timestamp(row.get("gr_regdt")) or now}, False))
            record_count += 1

        lineups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        player_rows = _rows(tables.get("ff_game_player"))
        for row in player_rows:
            recording = recordings.get(_string(row.get("gi_id")))
            if not recording:
                continue
            gm_id, side, player_id, team_id = *recording, _string(row.get("p_id")), _string(row.get("t_code"))
            candidates = contracts.get(f"{player_id}_{team_id}", [])
            date = matches[gm_id]["date"]
            contract = next((item for item in candidates if item["from"] <= date and (not item["to"] or date <= item["to"])), candidates[0] if candidates else {"no": "", "pos": ""})
            player_type = "BENCH" if _string(row.get("gp_type")) == "ST" else "START"
            position = _normalize_position(contract["pos"])
            lineups[f"{gm_id}:{side}"][player_id] = {
                "slot": "gk" if player_type == "START" and position == "GK" else ("bench" if player_type == "BENCH" else "start"),
                "order": 1 if player_type == "START" and position == "GK" else _integer(row.get("gp_order")),
                "type": player_type,
                "no": contract["no"] or "", "name": player_names.get(player_id, ""), "pos": position,
                "inHalf": None if player_type == "BENCH" else "H1",
                "inSeconds": None if player_type == "BENCH" else 0,
                "outHalf": None, "outSeconds": None,
            }
            append((f"matches/{gm_id}/recordings/{side}/playerStats/{player_id}", {"playerId": player_id, "TAP": _number(row.get("gp_tmp")), "DAP": _number(row.get("gp_tap")), "UTP": _number(row.get("gp_utp")), "DTP": _number(row.get("gp_ctp")), "TTP": _number(row.get("gp_ttp")), "SHOT": _number(row.get("gp_sht")), "AST": _number(row.get("gp_ast")), "GOAL": _number(row.get("gp_gol")), "DTB": _number(row.get("gp_ctb")), "DTM": _number(row.get("gp_ctm")), "DTA": _number(row.get("gp_cta")), "DTS": _number(row.get("gp_cts")), "GTB": _number(row.get("gp_gtb")), "GTM": _number(row.get("gp_gtm")), "ASR": _number(row.get("gp_asr")), "SSR": _number(row.get("gp_ssr")), "scoreRel": None, "scoreAbs": None, "score": None, "jmx": None, "apx": None, "apxGrade": None, "tpx": None, "tpxGrade": None, "fpx": None, "fpxGrade": None, "ratingBasedOn": None}, False))
        for row in _rows(tables.get("ff_game_player_log")):
            recording = recordings.get(_string(row.get("gi_id")))
            if not recording:
                continue
            gm_id, side = recording
            lineup = lineups[f"{gm_id}:{side}"]
            half, seconds = _half(row.get("pl_in_half")), _integer(row.get("pl_in_seconds"))
            if (player_id := _string(row.get("pl_in_p_id"))) in lineup:
                lineup[player_id]["inHalf"], lineup[player_id]["inSeconds"] = half, seconds
                if _string(row.get("pl_in_position")):
                    lineup[player_id]["pos"] = _string(row.get("pl_in_position"))
            if (player_id := _string(row.get("pl_out_p_id"))) in lineup:
                lineup[player_id]["outHalf"], lineup[player_id]["outSeconds"] = half, seconds
        for key, lineup in lineups.items():
            gm_id, side = key.split(":")
            append((f"matches/{gm_id}/recordings/{side}", {"lineup": lineup}, True))

        for row in _rows(tables.get("ff_game_card")):
            if (recording := recordings.get(_string(row.get("gi_id")))):
                gm_id, side = recording
                append((f"matches/{gm_id}/recordings/{side}/cards/{_string(row.get('c_id'))}", {"playerId": _string(row.get("gp_id")), "half": _half(row.get("gp_card_half")), "halfSeconds": _integer(row.get("gp_card_time")), "card": "R" if _string(row.get("gp_card_card")) == "R" else "Y", "createdBy": "legacy-import", "createdAt": now}, False))
        for row in _rows(tables.get("ff_game_path")):
            if (recording := recordings.get(_string(row.get("gi_id")))):
                gm_id, side, path_id = *recording, _string(row.get("gt_id"))
                ptype = "DTP" if _integer(row.get("gt_ctp")) else ("UTP" if _integer(row.get("gt_utp")) else ("STP" if _integer(row.get("gt_stp")) else "UPP"))
                append((f"matches/{gm_id}/recordings/{side}/paths/{path_id}", {"gtId": path_id, "resCode": _string(row.get("gt_res_code")), "dsp": bool(_integer(row.get("gt_csp"))), "ttp": bool(_integer(row.get("gt_ttp"))), "ptype": ptype, "recordIds": path_records[f"{gm_id}:{side}:{path_id}"]}, False))
                for record_id in path_records[f"{gm_id}:{side}:{path_id}"]:
                    append((f"matches/{gm_id}/recordings/{side}/records/{record_id}", {"legacyPathType": ptype, "legacyPathTtp": bool(_integer(row.get("gt_ttp")))}, True))
        for row in _rows(tables.get("ff_game_bap")):
            if (recording := recordings.get(_string(row.get("gi_id")))):
                gm_id, side = recording
                append((f"matches/{gm_id}/recordings/{side}/records/{_string(row.get('gr_id'))}", {"bapReason": "legacy-import"}, True))
        return items, gm_ids, record_count

MetadataRepairProgress = Callable[[int, int, dict[str, int]], None]


class LegacyMetadataRepair:
    """Repair shared metadata only; match trees, KPI, rating, and snapshots stay untouched."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()
        self.db = self.data.db

    @staticmethod
    def _merge_prefer_existing(values: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for value in values:
            for key, candidate in value.items():
                if key not in result or result[key] is None or result[key] == "":
                    result[key] = candidate
        return result

    @staticmethod
    def _season_competitions(document: dict[str, Any]) -> set[str]:
        values: list[Any] = [document.get("leagueId")]
        for key in ("competitionIds", "leagueIds", "cupIds"):
            if isinstance(document.get(key), list):
                values.extend(document[key])
        return {str(value) for value in values if isinstance(value, str) and value}

    def _commit(self, operations: list[tuple[str, Any, dict[str, Any] | None]]) -> None:
        for offset in range(0, len(operations), 200):
            for attempt in range(1, MAX_MATCH_ATTEMPTS + 1):
                try:
                    batch = self.db.batch()
                    for operation, reference, payload in operations[offset:offset + 200]:
                        if operation == "delete":
                            batch.delete(reference)
                        else:
                            batch.set(reference, payload or {}, merge=True)
                    batch.commit(timeout=90)
                    break
                except Exception as exc:
                    if attempt == MAX_MATCH_ATTEMPTS:
                        raise BackendError(
                            f"Legacy metadata repair batch {offset // 200 + 1} failed after {attempt} attempts: {type(exc).__name__}: {exc}",
                            status_code=500,
                            code="legacy_metadata_repair_retry_exhausted",
                        ) from exc
                    time.sleep(attempt)

    def repair(self, *, on_progress: MetadataRepairProgress | None = None) -> dict[str, int]:
        league_snapshots = list(self.db.collection("leagues").stream(retry=None, timeout=30))
        team_snapshots = list(self.db.collection("teams").stream(retry=None, timeout=60))
        stadium_snapshots = list(self.db.collection("stadiums").stream(retry=None, timeout=60))
        contract_snapshots = list(self.db.collection_group("contracts").stream(retry=None, timeout=60))
        english_teams = [snapshot for snapshot in team_snapshots if snapshot.id.startswith("E-")]
        player_contracts = [
            snapshot for snapshot in contract_snapshots
            if snapshot.reference.path.split("/")[:3:2] == ["players", "contracts"]
        ]
        total = len(league_snapshots) + len(COMPETITION_TYPES) + len(team_snapshots) + len(stadium_snapshots) + len(english_teams) + len(player_contracts)
        processed = 0
        counts = {
            "leaguesConfigured": 0, "leaguesDeleted": 0,
            "teamSeasonsMerged": 0, "teamSeasonsDeleted": 0, "teamsRestored": 0,
            "stadiumHomeTeamsNormalized": 0, "teamStadiumsRestored": 0,
            "contractsNormalized": 0, "contractsDeleted": 0, "contractConflicts": 0,
            "contractCompetitionTypesAssigned": 0,
        }

        def report() -> None:
            if on_progress:
                on_progress(processed, total, dict(counts))

        report()
        league_operations: list[tuple[str, Any, dict[str, Any] | None]] = []
        existing_leagues = {snapshot.id: snapshot for snapshot in league_snapshots}
        now = datetime.now(timezone.utc)
        for league_id, competition_type in COMPETITION_TYPES.items():
            existing = existing_leagues.get(league_id)
            payload: dict[str, Any] = {
                "competitionType": competition_type, "active": True,
                "updatedAt": now, "updatedBy": "legacy-metadata-repair",
            }
            if existing is None:
                payload.update({"name": league_id, "createdAt": now, "createdBy": "legacy-metadata-repair"})
            league_operations.append(("set", self.db.document(f"leagues/{league_id}"), payload))
            counts["leaguesConfigured"] += 1
        for snapshot in league_snapshots:
            processed += 1
            if snapshot.id not in COMPETITION_TYPES:
                league_operations.append(("delete", snapshot.reference, None))
                counts["leaguesDeleted"] += 1
        self._commit(league_operations)
        processed += len(COMPETITION_TYPES)
        report()

        # Stadium documents used display names such as "Liverpool" or
        # "Spurs" in homeTeamId.  Normalize them to the actual teams/{id}
        # reference, then use that reference to fill a genuinely missing team
        # stadiumId.  The team document remains the management source of truth.
        team_ids_by_label: dict[str, str] = {}
        team_ids_by_stadium: dict[str, str] = {}
        for team_snapshot in team_snapshots:
            document = team_snapshot.to_dict() or {}
            for label in (document.get("name"), document.get("nameFull"), document.get("nameShort")):
                if key := _legacy_label_key(label):
                    team_ids_by_label[key] = team_snapshot.id
            if stadium_id := document.get("stadiumId"):
                team_ids_by_stadium[str(stadium_id)] = team_snapshot.id
        stadium_ids_by_team: dict[str, list[str]] = defaultdict(list)
        stadium_operations: list[tuple[str, Any, dict[str, Any] | None]] = []
        for stadium_snapshot in stadium_snapshots:
            document = stadium_snapshot.to_dict() or {}
            team_id = team_ids_by_stadium.get(stadium_snapshot.id) or team_ids_by_label.get(_legacy_label_key(document.get("homeTeamId")))
            if team_id:
                stadium_ids_by_team[team_id].append(stadium_snapshot.id)
                if document.get("homeTeamId") != team_id:
                    stadium_operations.append(("set", stadium_snapshot.reference, {
                        "homeTeamId": team_id, "updatedAt": now, "updatedBy": "legacy-metadata-repair",
                    }))
                    counts["stadiumHomeTeamsNormalized"] += 1
            processed += 1
        for team_snapshot in team_snapshots:
            document = team_snapshot.to_dict() or {}
            candidates = stadium_ids_by_team.get(team_snapshot.id, [])
            if not document.get("stadiumId") and len(candidates) == 1:
                stadium_operations.append(("set", team_snapshot.reference, {
                    "stadiumId": candidates[0], "updatedAt": now, "updatedBy": "legacy-metadata-repair",
                }))
                counts["teamStadiumsRestored"] += 1
            processed += 1
        self._commit(stadium_operations)
        report()

        for team_snapshot in english_teams:
            team_data = team_snapshot.to_dict() or {}
            season_snapshots = list(team_snapshot.reference.collection("seasons").stream(retry=None, timeout=30))
            groups: dict[str, list[Any]] = defaultdict(list)
            for season_snapshot in season_snapshots:
                groups[normalize_season_id(season_snapshot.id)].append(season_snapshot)
            operations: list[tuple[str, Any, dict[str, Any] | None]] = []
            for season_id, snapshots in groups.items():
                payloads = [snapshot.to_dict() or {} for snapshot in snapshots]
                canonical = next((snapshot for snapshot in snapshots if snapshot.id == season_id), None)
                ordered_payloads = ([canonical.to_dict() or {}] if canonical is not None else []) + [
                    payload for snapshot, payload in zip(snapshots, payloads)
                    if canonical is None or snapshot.id != canonical.id
                ]
                merged = self._merge_prefer_existing(ordered_payloads)
                competition_ids = sorted({competition for payload in payloads for competition in self._season_competitions(payload)})
                league_ids = [competition for competition in competition_ids if COMPETITION_TYPES.get(competition) == "league"]
                cup_ids = [competition for competition in competition_ids if COMPETITION_TYPES.get(competition) == "cup"]
                if competition_ids:
                    merged.update({
                        "competitionIds": competition_ids,
                        "leagueIds": league_ids,
                        "cupIds": cup_ids,
                        "leagueId": league_ids[0] if league_ids else competition_ids[0],
                    })
                merged.update({"updatedAt": now, "updatedBy": "legacy-metadata-repair"})
                target = team_snapshot.reference.collection("seasons").document(season_id)
                operations.append(("set", target, merged))
                if any(snapshot.id != season_id for snapshot in snapshots):
                    counts["teamSeasonsMerged"] += 1
                for snapshot in snapshots:
                    if snapshot.id != season_id:
                        operations.append(("delete", snapshot.reference, None))
                        counts["teamSeasonsDeleted"] += 1
            current_league = str(team_data.get("currentLeagueId") or "")
            if COMPETITION_TYPES.get(current_league) == "cup":
                operations.append(("set", team_snapshot.reference, {
                    "currentLeagueId": "EPL", "updatedAt": now, "updatedBy": "legacy-metadata-repair",
                }))
                counts["teamsRestored"] += 1
            self._commit(operations)
            processed += 1
            report()

        contract_groups: dict[str, list[Any]] = defaultdict(list)
        for snapshot in player_contracts:
            document = snapshot.to_dict() or {}
            path = snapshot.reference.path.split("/")
            team_id = str(document.get("teamId") or "")
            start = normalize_legacy_date(document.get("from"))
            if len(path) == 4 and team_id and start:
                contract_groups[f"players/{path[1]}/contracts/{team_id}_{start}"].append(snapshot)
            else:
                processed += 1
        contract_operations: list[tuple[str, Any, dict[str, Any] | None]] = []
        for target_path, snapshots in contract_groups.items():
            normalized_documents: list[dict[str, Any]] = []
            requires_write = False
            for snapshot in snapshots:
                document = dict(snapshot.to_dict() or {})
                normalized_from = normalize_legacy_date(document.get("from"))
                normalized_to = normalize_legacy_contract_end(document.get("to"))
                if snapshot.reference.path != target_path or document.get("from") != normalized_from or document.get("to") != normalized_to:
                    requires_write = True
                document["from"] = normalized_from
                document["to"] = normalized_to
                competition_type = COMPETITION_TYPES.get(str(document.get("leagueId") or ""))
                if competition_type and document.get("competitionType") != competition_type:
                    document["competitionType"] = competition_type
                    requires_write = True
                    counts["contractCompetitionTypesAssigned"] += 1
                normalized_documents.append(document)
            signatures = {
                # A contract belongs to a player and a team, not to one
                # competition.  EPL/UCL source rows therefore must not split
                # the same player contract into two documents.
                tuple(document.get(key) for key in ("teamId", "from", "to", "no", "pos"))
                for document in normalized_documents
            }
            if len(signatures) != 1:
                counts["contractConflicts"] += 1
                processed += len(snapshots)
                report()
                continue
            if not requires_write:
                processed += len(snapshots)
                report()
                continue
            target = self.db.document(target_path)
            canonical_index = next((index for index, snapshot in enumerate(snapshots) if snapshot.reference.path == target_path), 0)
            ordered_documents = [normalized_documents[canonical_index]] + [
                document for index, document in enumerate(normalized_documents) if index != canonical_index
            ]
            merged = self._merge_prefer_existing(ordered_documents)
            merged.update({"updatedAt": now, "updatedBy": "legacy-metadata-repair"})
            contract_operations.append(("set", target, merged))
            if any(snapshot.reference.path != target_path for snapshot in snapshots):
                counts["contractsNormalized"] += 1
            for snapshot in snapshots:
                if snapshot.reference.path != target_path:
                    contract_operations.append(("delete", snapshot.reference, None))
                    counts["contractsDeleted"] += 1
            processed += len(snapshots)
            report()
        self._commit(contract_operations)
        return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace legacy SQL dump matches in JPD-DID Firestore.")
    parser.add_argument("sql_file", type=Path, help="Path to a UTF-8 mysqldump file")
    args = parser.parse_args()
    try:
        sql = args.sql_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        parser.error(f"Cannot read SQL file: {exc}")
    result = BuildLegacyImport().import_sql(sql)
    print(
        f"Imported {result.matches_replaced} matches, "
        f"{result.documents_written} documents, {result.records_written} raw records."
    )


if __name__ == "__main__":
    main()
