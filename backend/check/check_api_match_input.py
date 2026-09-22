from backend.api.api_match_input import MatchInputCommit


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
