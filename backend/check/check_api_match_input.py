from backend.api.api_match_input import MatchInputCommit
from backend.app import app


def test_input_commit_contract_accepts_raw_only_payload() -> None:
    payload = MatchInputCommit.model_validate({
        "gmId": "sample-match",
        "side": "H",
        "inputMode": "분석",
        "fieldSide": "left",
        "formationKey": "4-3-3",
        "homeScore": 1,
        "awayScore": 0,
        "status": "final",
        "halves": {"H1": {"seconds": 2700}, "H2": {"seconds": 2800}},
        "lineup": [{
            "playerId": "player-1", "slot": "gk", "order": 0, "type": "START",
            "no": "1", "name": "Goalkeeper", "pos": "GK",
            "inHalf": "H1", "inSeconds": 0, "outHalf": None, "outSeconds": None,
        }],
        "records": [{
            "id": "record-1", "half": "H1", "halfSeconds": 1, "seq": 0,
            "act": "P", "res": "O", "area": 10,
        }],
        "cards": [],
    })

    assert payload.gmId == "sample-match"
    assert payload.records[0].area == 10


def test_bootstrap_contract_requires_selected_side() -> None:
    operation = app.openapi()["paths"]["/api/v1/match-input/matches/{gm_id}/bootstrap"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert parameters["side"]["in"] == "query"
    assert parameters["side"]["required"] is True


def test_legacy_lineup_snapshot_separates_gk_from_start_order_one() -> None:
    from datetime import datetime, timezone

    from backend.system.system_firestore import JpdDidData
    from backend.system.system_schema import RecordingDoc

    now = datetime.now(timezone.utc)
    lineup = {
        "gk-player": {
            "slot": "gk-player", "order": 1, "type": "START", "no": "1", "name": "Keeper", "pos": "G",
            "inHalf": "H1", "inSeconds": 0, "outHalf": None, "outSeconds": None,
        },
        **{
            f"field-{order}": {
                "slot": f"field-{order}", "order": order, "type": "START", "no": str(order + 1),
                "name": f"Field {order}", "pos": "MF", "inHalf": "H1", "inSeconds": 0,
                "outHalf": None, "outSeconds": None,
            }
            for order in range(1, 11)
        },
        "bench-1": {
            "slot": "bench-1", "order": 1, "type": "BENCH", "no": "20", "name": "Bench", "pos": "FW",
            "inHalf": None, "inSeconds": None, "outHalf": None, "outSeconds": None,
        },
    }
    recording = RecordingDoc.model_validate({
        "side": "H", "teamId": "AVLX", "opponentTeamId": "CRYX", "status": "final", "inputMode": "분석",
        "fieldSide": "left", "fieldSideEx": None, "formationKey": "4-2-3-1", "lineup": lineup,
        "halves": {"H1": {"startedAt": None, "seconds": 2700}, "H2": {"startedAt": None, "seconds": 2700}},
        "h1Locked": True, "h2Locked": True, "maxSeq": 0, "legacyGiId": "legacy-1",
        "createdAt": now, "updatedAt": now,
    })
    squad = [
        {"playerId": "gk-player", "no": "1", "name": "Keeper", "pos": "GK"},
        *[
            {"playerId": f"field-{order}", "no": str(order + 1), "name": f"Field {order}", "pos": "MF"}
            for order in range(1, 11)
        ],
        {"playerId": "bench-1", "no": "20", "name": "Bench", "pos": "FW"},
    ]

    data = object.__new__(JpdDidData)
    normalized, issues = data._legacy_lineup_snapshot(recording, squad)

    assert normalized[0]["playerId"] == "gk-player"
    assert normalized[0]["slot"] == "gk"
    assert normalized[0]["order"] == 1
    assert [row["order"] for row in normalized if row["type"] == "START" and row["slot"] == "start"] == list(range(1, 11))
    assert [row["order"] for row in normalized if row["type"] == "BENCH"] == [1]
    assert issues == []
