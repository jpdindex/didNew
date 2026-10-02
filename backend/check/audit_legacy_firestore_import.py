from __future__ import annotations

"""Read-only parity audit between a legacy SQL dump and its Firestore import."""

from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from backend.system.system_firestore import BackendError, JpdDidData, PROJECT_ROOT
from backend.system.system_schema import (
    CardDoc,
    MatchDoc,
    PathSnapshotDoc,
    PlayerStatsDoc,
    RecordDoc,
    RecordingDoc,
    storage_contract_for_path,
    storage_field_expectation,
    storage_field_scope,
)
from backend.temporary.build_legacy_import import BuildLegacyImport


AuditProgress = Callable[[dict[str, int]], None]
AUDIT_DIRECTORY = PROJECT_ROOT / "backend" / "data" / "audit"
VOLATILE_FIELDS = {"createdAt", "updatedAt", "kpiComputedAt"}
DERIVED_RECORDING_FIELDS = {"teamRating", "ratingBasedOn", "kpiSourceFingerprint"}
DERIVED_PLAYER_STAT_FIELDS = {
    "scoreRel", "scoreAbs", "score", "jmx", "apx", "apxGrade",
    "tpx", "tpxGrade", "fpx", "fpxGrade", "ratingBasedOn",
}
MAX_AUDIT_EXAMPLES = 100
RECORDING_SUBCOLLECTIONS = ("records", "paths", "playerStats", "cards")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        items = list(value.items())
        result = {str(key): _json_value(item) for key, item in items[:10]}
        if len(items) > 10:
            result["__truncated__"] = f"{len(items) - 10} entries omitted"
        return result
    if isinstance(value, (list, tuple)):
        result = [_json_value(item) for item in value[:10]]
        if len(value) > 10:
            result.append(f"{len(value) - 10} entries omitted")
        return result
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, datetime):
        return "datetime"
    return type(value).__name__


class AuditCollector:
    """Keep complete aggregate coverage without writing one row per repeated field."""

    def __init__(self) -> None:
        self.category_counts: Counter[str] = Counter()
        self.examples: list[dict[str, Any]] = []
        self._unexpected_paths: set[str] = set()
        self._groups: dict[tuple[str, str, str, str | None, str | None], dict[str, Any]] = {}
        self.total = 0

    @staticmethod
    def _document_shape(document_path: str) -> str:
        parts = document_path.split("/")
        if len(parts) < 4:
            return document_path
        if len(parts) == 4:
            return "matches/{gmId}/recordings/{side}"
        return f"matches/{{gmId}}/recordings/{{side}}/{parts[4]}/{{documentId}}"

    @staticmethod
    def _field_shape(field_path: str | None) -> str:
        if not field_path:
            return ""
        parts = field_path.split(".")
        if parts[0] == "lineup" and len(parts) > 1:
            parts[1] = "{playerId}"
        return ".".join(parts)

    def add(self, item: dict[str, Any]) -> None:
        self.total += 1
        category = str(item["category"])
        self.category_counts[category] += 1
        if category == "document_unexpected":
            self._unexpected_paths.add(str(item["documentPath"]))
        field_path = item.get("fieldPath")
        source = item.get("source")
        message = item.get("message")
        key = (
            category,
            self._document_shape(str(item["documentPath"])),
            self._field_shape(str(field_path) if field_path else None),
            str(source) if source else None,
            str(message) if message else None,
        )
        group = self._groups.setdefault(key, {
            "category": category,
            "documentShape": key[1],
            "fieldPath": key[2] or None,
            "source": source,
            "message": message,
            "count": 0,
            "affectedTargets": set(),
            "sample": None,
        })
        group["count"] += 1
        if item.get("gmId"):
            group["affectedTargets"].add(f"{item['gmId']}/{item.get('side') or '-'}")
        if group["sample"] is None:
            group["sample"] = item
        if len(self.examples) < MAX_AUDIT_EXAMPLES:
            self.examples.append(item)

    def groups(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for group in self._groups.values():
            values.append({
                **{key: value for key, value in group.items() if key != "affectedTargets"},
                "affectedTargets": sorted(group["affectedTargets"]),
            })
        return sorted(values, key=lambda item: (-int(item["count"]), str(item["category"]), str(item["fieldPath"])))

    def unexpected_paths(self) -> list[str]:
        return sorted(self._unexpected_paths)


def _schema_for_path(path: str) -> type[BaseModel] | None:
    contract = storage_contract_for_path(path)
    return contract.model if contract else None


def _recording_schema_for_path(path: str) -> type[BaseModel] | None:
    parts = path.split("/")
    return RecordingDoc if len(parts) == 4 and parts[0] == "matches" and parts[2] == "recordings" else _schema_for_path(path)


def _is_derived_field(document_path: str, field_path: tuple[str, ...]) -> bool:
    if not field_path:
        return False
    if field_path[-1] in VOLATILE_FIELDS:
        return True
    if "/recordings/" in document_path and "/playerStats/" not in document_path:
        return field_path[0] in DERIVED_RECORDING_FIELDS
    if "/playerStats/" in document_path:
        return field_path[0] in DERIVED_PLAYER_STAT_FIELDS
    return False


def _diagnostic(
    collector: AuditCollector,
    *,
    category: str,
    document_path: str,
    field_path: tuple[str, ...] = (),
    expected: Any = None,
    actual: Any = None,
    message: str | None = None,
    source: str | None = None,
) -> None:
    match = document_path.split("/")[1] if document_path.startswith("matches/") else None
    side = document_path.split("/")[3] if document_path.count("/") >= 3 and "/recordings/" in document_path else None
    item: dict[str, Any] = {
        "category": category,
        "gmId": match,
        "side": side,
        "documentPath": document_path,
    }
    if field_path:
        item["fieldPath"] = ".".join(field_path)
    scope = storage_field_scope(document_path, field_path)
    if scope != "outside-contract":
        item["contractScope"] = scope
    if source:
        item["source"] = source
    if message:
        item["message"] = message
    if category in {
        "field_missing", "field_unexpected", "value_mismatch", "type_mismatch",
        "importer_schema_mismatch", "firestore_schema_mismatch",
    }:
        item["expected"] = _json_value(expected)
        item["expectedType"] = _type_name(expected)
        item["actual"] = _json_value(actual)
        item["actualType"] = _type_name(actual)
    collector.add(item)


def _recording_key(document_path: str) -> tuple[str, str] | None:
    parts = document_path.split("/")
    if len(parts) < 4 or parts[0] != "matches" or parts[2] != "recordings":
        return None
    return parts[1], parts[3]


def _compare_values(
    expected: Any,
    actual: Any,
    *,
    document_path: str,
    field_path: tuple[str, ...],
    collector: AuditCollector,
) -> None:
    if _is_derived_field(document_path, field_path):
        return
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            _diagnostic(collector, category="type_mismatch", document_path=document_path, field_path=field_path, expected=expected, actual=actual)
            return
        for key in expected:
            next_path = (*field_path, key)
            if key not in actual:
                _diagnostic(collector, category="field_missing", document_path=document_path, field_path=next_path, expected=expected[key], actual=None)
            else:
                _compare_values(expected[key], actual[key], document_path=document_path, field_path=next_path, collector=collector)
        for key in actual:
            next_path = (*field_path, key)
            if key not in expected and not _is_derived_field(document_path, next_path):
                _diagnostic(collector, category="field_unexpected", document_path=document_path, field_path=next_path, expected=None, actual=actual[key])
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            _diagnostic(collector, category="type_mismatch", document_path=document_path, field_path=field_path, expected=expected, actual=actual)
            return
        if len(expected) != len(actual):
            _diagnostic(collector, category="value_mismatch", document_path=document_path, field_path=field_path, expected=expected, actual=actual, message="array length differs")
            return
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            _compare_values(expected_item, actual_item, document_path=document_path, field_path=(*field_path, str(index)), collector=collector)
        return
    # Firestore returns DatetimeWithNanoseconds, a datetime subclass. It is
    # semantically the same value as the Python datetime emitted by the SQL
    # importer and must not be reported as a type mismatch.
    if isinstance(expected, datetime) and isinstance(actual, datetime):
        if expected != actual:
            _diagnostic(collector, category="value_mismatch", document_path=document_path, field_path=field_path, expected=expected, actual=actual)
    elif type(expected) is not type(actual):
        _diagnostic(collector, category="type_mismatch", document_path=document_path, field_path=field_path, expected=expected, actual=actual)
    elif expected != actual:
        _diagnostic(collector, category="value_mismatch", document_path=document_path, field_path=field_path, expected=expected, actual=actual)


def _validate_schema(
    model: type[BaseModel] | None,
    document: dict[str, Any],
    *,
    document_path: str,
    source: str,
    collector: AuditCollector,
) -> None:
    if model is None:
        return
    try:
        model.model_validate(document)
    except ValidationError as exc:
        category = "importer_schema_mismatch" if source == "importer" else "firestore_schema_mismatch"
        for error in exc.errors(include_url=False):
            _diagnostic(
                collector,
                category=category,
                document_path=document_path,
                field_path=tuple(str(item) for item in error["loc"]),
                expected=storage_field_expectation(document_path, tuple(str(item) for item in error["loc"])),
                actual=error.get("input"),
                message=f"{error['type']}: {error['msg']}",
                source=source,
            )


def _report_path(gm_ids: set[str]) -> Path:
    ordered = sorted(gm_ids)
    suffix = "empty" if not ordered else f"{ordered[0][-4:]}_{ordered[-1][-4:]}"
    return AUDIT_DIRECTORY / f"legacy_firestore_import_{suffix}.json"


class LegacyFirestoreImportAudit:
    """Compare importer output with Firestore without writing any document."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def verify(self, sql: str, *, on_progress: AuditProgress | None = None) -> dict[str, Any]:
        expected_documents, gm_ids, record_count = BuildLegacyImport(self.data).build_expected_match_documents(sql)
        if not gm_ids:
            raise BackendError("SQL dump has no valid gm_id", status_code=422, code="legacy_games_missing")
        expected_by_match: dict[str, list[str]] = defaultdict(list)
        expected_by_recording: dict[tuple[str, str], set[str]] = defaultdict(set)
        for path in expected_documents:
            expected_by_match[path.split("/")[1]].append(path)
            if (recording_key := _recording_key(path)) is not None:
                expected_by_recording[recording_key].add(path)
        collector = AuditCollector()
        documents_compared = 0
        documents_actual = 0
        matches_processed = 0
        if on_progress:
            on_progress({"matchesTotal": len(gm_ids), "matchesProcessed": 0, "documentsTotal": len(expected_documents), "documentsCompared": 0})

        for gm_id in sorted(gm_ids):
            paths = expected_by_match[gm_id]
            actual_documents: dict[str, dict[str, Any]] = {}
            for offset in range(0, len(paths), 300):
                references = [self.data.db.document(path) for path in paths[offset:offset + 300]]
                for snapshot in self.data.db.get_all(references, retry=None, timeout=30):
                    if snapshot.exists:
                        actual_documents[snapshot.reference.path] = snapshot.to_dict() or {}

            # The SQL importer owns these collections. Enumerating them also catches
            # records stored under an unexpected document ID, rather than treating
            # them only as a missing expected document.
            for side in ("H", "A"):
                expected_recording_paths = expected_by_recording.get((gm_id, side))
                if not expected_recording_paths:
                    continue
                recording_ref = self.data.db.collection("matches").document(gm_id).collection("recordings").document(side)
                for collection_name in RECORDING_SUBCOLLECTIONS:
                    for snapshot in recording_ref.collection(collection_name).stream(retry=None, timeout=30):
                        actual_documents.setdefault(snapshot.reference.path, snapshot.to_dict() or {})
            for path in paths:
                expected = expected_documents[path]
                model = _recording_schema_for_path(path)
                _validate_schema(model, expected, document_path=path, source="importer", collector=collector)
                actual = actual_documents.get(path)
                if actual is None:
                    _diagnostic(collector, category="document_missing", document_path=path, expected=expected, actual=None)
                else:
                    _validate_schema(model, actual, document_path=path, source="firestore", collector=collector)
                    _compare_values(expected, actual, document_path=path, field_path=(), collector=collector)
                documents_compared += 1
            expected_paths = set(paths)
            for path in sorted(set(actual_documents) - expected_paths):
                _diagnostic(
                    collector,
                    category="document_unexpected",
                    document_path=path,
                    expected=None,
                    actual=actual_documents[path],
                    message="Firestore document is outside the SQL importer output",
                )
            documents_actual += len(actual_documents)
            matches_processed += 1
            if on_progress:
                on_progress({"matchesTotal": len(gm_ids), "matchesProcessed": matches_processed, "documentsTotal": len(expected_documents), "documentsCompared": documents_compared})

        summary = {
            "matches": len(gm_ids),
            "records": record_count,
            "documentsExpected": len(expected_documents),
            "documentsCompared": documents_compared,
            "documentsActual": documents_actual,
            "passed": collector.total == 0,
            "diagnostics": collector.total,
            "diagnosticGroups": len(collector.groups()),
            **dict(sorted(collector.category_counts.items())),
        }
        report = {
            "summary": summary,
            "examples": collector.examples,
            "groups": collector.groups(),
            # A repair only ever deletes these exact verifier-discovered RAW
            # document paths; it never recursively scans or touches snapshots.
            "repairPaths": collector.unexpected_paths(),
        }
        report_path = _report_path(gm_ids)
        AUDIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "summary": summary,
            "reportPath": str(report_path),
            "examples": collector.examples,
        }

    def repair_report(self, report_path: Path) -> dict[str, int]:
        """Delete only the unexpected RAW documents from one completed audit report."""
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BackendError(f"Cannot read legacy audit report: {report_path}", status_code=422, code="legacy_repair_report_invalid") from exc
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        paths = report.get("repairPaths")
        if not isinstance(paths, list):
            # Reports created before repairPaths existed retained up to 100
            # examples. Accept them only when that list is provably complete.
            paths = [
                item.get("documentPath")
                for item in report.get("examples", [])
                if isinstance(item, dict) and item.get("category") == "document_unexpected"
            ]
        target_paths = sorted({path for path in paths if isinstance(path, str)})
        expected_count = int(summary.get("document_unexpected", 0))
        if len(target_paths) != expected_count:
            raise BackendError(
                f"Legacy repair report is incomplete: expected {expected_count} paths, found {len(target_paths)}",
                status_code=422,
                code="legacy_repair_report_incomplete",
            )
        for path in target_paths:
            parts = path.split("/")
            if (
                len(parts) != 6
                or parts[0] != "matches"
                or parts[2] != "recordings"
                or parts[4] not in RECORDING_SUBCOLLECTIONS
                or not all(parts)
            ):
                raise BackendError(f"Refusing non-RAW repair path: {path}", status_code=422, code="legacy_repair_path_invalid")
        deleted = 0
        for offset in range(0, len(target_paths), 200):
            batch = self.data.db.batch()
            for path in target_paths[offset:offset + 200]:
                batch.delete(self.data.db.document(path))
            batch.commit(timeout=60)
            deleted += len(target_paths[offset:offset + 200])
        return {"targets": len(target_paths), "deleted": deleted}
