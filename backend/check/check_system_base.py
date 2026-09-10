from backend.system.system_base import RecordDoc


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
