from datetime import datetime, timezone

import pytest

from backend.system.system_firestore import BackendError
from backend.system.system_schema import (
    CardDoc,
    MatchDoc,
    PathSnapshotDoc,
    RecordDoc,
    RecordingDoc,
    storage_contract_for_path,
    storage_field_scope,
)


def test_record_area_contract_is_enforced() -> None:
    payload = {
        "half": "H1",
        "halfSeconds": 42,
        "seq": 1,
        "act": "P",
        "res": "O",
        "area": 5,
        "createdBy": "user-1",
        "source": "did",
        "createdAt": "2026-09-09T00:00:00Z",
    }
    record = RecordDoc.model_validate(payload)
    assert record.area == 5


def test_legacy_goal_result_is_normalized_for_kpi_calculation() -> None:
    payload = {
        "half": "H1",
        "halfSeconds": 42,
        "seq": 1,
        "act": "S",
        "res": "G",
        "area": 5,
        "createdBy": "user-1",
        "source": "did",
        "createdAt": "2026-09-09T00:00:00Z",
    }

    assert RecordDoc.model_validate(payload).res == "GOAL"


def test_legacy_path_goal_code_remains_readable_without_rewriting_firestore() -> None:
    path = PathSnapshotDoc.model_validate({
        "gtId": "legacy-path",
        "recordIds": ["record-1"],
        "ptype": "UPP",
        "dsp": False,
        "ttp": False,
        "resCode": "G",
    })

    assert path.resCode == "G"


def test_match_tree_contract_separates_common_raw_from_legacy_evidence() -> None:
    now = datetime.now(timezone.utc)
    match = MatchDoc.model_validate({
        "date": "2027-09-01", "leagueId": "EPL", "seasonId": "20272028", "matchType": "league",
        "stadiumId": "stadium-1", "homeTeamId": "home", "awayTeamId": "away", "score": {"home": 0, "away": 0},
        "createdAt": now, "updatedAt": now,
    })
    recording = RecordingDoc.model_validate({
        "side": "H", "teamId": "home", "opponentTeamId": "away", "status": "final", "inputMode": "분석",
        "fieldSide": "left", "formationKey": "4-3-3", "lineup": {},
        "halves": {"H1": {"startedAt": None, "seconds": 0}, "H2": {"startedAt": None, "seconds": 0}},
        "h1Locked": True, "h2Locked": True, "maxSeq": 0, "createdAt": now, "updatedAt": now,
    })
    record = RecordDoc.model_validate({
        "half": "H1", "halfSeconds": 1, "seq": 0, "act": "P", "res": "O", "area": 5,
        "source": "did", "createdAt": now,
    })
    card = CardDoc.model_validate({
        "playerId": "player-1", "half": "H1", "halfSeconds": 1, "card": "Y", "createdAt": now,
    })

    assert match.seasonId == "20272028"
    assert recording.legacyGiId is None
    assert record.legacyPathId is None
    assert card.card == "Y"
    assert storage_contract_for_path("matches/gm-1").model is MatchDoc
    assert storage_field_scope("matches/gm-1/recordings/H", ("legacyGiId",)) == "legacy-optional"
    assert storage_field_scope("matches/gm-1/recordings/H/records/r-1", ("legacyPathId",)) == "legacy-optional"
    assert storage_field_scope("matches/gm-1/recordings/H/paths/path-1", ("gtId",)) == "legacy-only"


def test_importer_contract_diagnostic_identifies_path_field_and_expected_type() -> None:
    from backend.temporary.build_legacy_import import BuildLegacyImport

    with pytest.raises(BackendError, match=r"matches/gm-1/recordings/H field formationKey: expected common-required"):
        BuildLegacyImport._validate_storage_contract("matches/gm-1/recordings/H", {
            "side": "H", "teamId": "home", "opponentTeamId": "away", "status": "final", "inputMode": "분석",
            "fieldSide": "left", "lineup": {},
        })


def test_audit_schema_diagnostic_includes_contract_scope_and_expectation() -> None:
    from backend.check.audit_legacy_firestore_import import AuditCollector, _validate_schema

    collector = AuditCollector()
    _validate_schema(
        RecordDoc,
        {"half": "H1", "halfSeconds": 1, "seq": 0, "act": "P", "res": "O", "source": "did"},
        document_path="matches/gm-1/recordings/H/records/r-1",
        source="firestore",
        collector=collector,
    )

    missing_area = next(item for item in collector.examples if item["fieldPath"] == "area")
    assert missing_area["contractScope"] == "common-required"
    assert str(missing_area["expected"]).startswith("common-required required field")
