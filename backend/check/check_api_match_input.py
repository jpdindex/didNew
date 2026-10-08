from backend.api import api_match_input
from backend.api.api_match_input import DraftResponse, DraftSocketHub, DraftWriteRequest, InputPreviewRequest, MatchInputCommit, ParticipantJoinRequest, calculate_input_preview, enter_draft, router
from backend.system.system_firestore import CurrentUser, NotFoundError
from pydantic_core import PydanticUndefined
import pytest


def _new_schedule_fixture(serial=1):
    return {
        "gmId": f"20262027epltest{serial:04d}", "date": "2026-10-08", "kickoffTime": "20:00",
        "leagueId": "EPL", "seasonId": "20262027", "round": 1,
        "stadiumId": "stadium", "homeTeamId": "home", "awayTeamId": "away",
    }


class _ScheduleStore:
    def __init__(self, documents=None):
        self.documents = documents or {}
        self.fail_commit = False

    def collection(self, path):
        store = self

        class Collection:
            def document(self, key):
                return _ScheduleReference(store, f"{path}/{key}")

        return Collection()

    def batch(self):
        store = self

        class Batch:
            def __init__(self):
                self.writes = []

            def create(self, reference, values):
                self.writes.append(("create", reference.path, values))

            def set(self, reference, values):
                self.writes.append(("set", reference.path, values))

            def commit(self, **kwargs):
                from google.api_core.exceptions import AlreadyExists
                if store.fail_commit:
                    raise RuntimeError("commit failed")
                for operation, path, _ in self.writes:
                    if operation == "create" and path in store.documents:
                        raise AlreadyExists(path)
                store.documents.update({path: values for _, path, values in self.writes})

        return Batch()


class _ScheduleReference:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def collection(self, name):
        return self.store.collection(f"{self.path}/{name}")

    def get(self, **kwargs):
        values = self.store.documents.get(self.path)

        class Snapshot:
            exists = values is not None

            def to_dict(self):
                return values

        return Snapshot()


def _schedule_data(monkeypatch, store):
    from backend.system.system_firestore import JpdDidData
    data = object.__new__(JpdDidData)
    data.db = store
    data.get_documents_by_ids = lambda collection, ids: {key: {"nameKr": key} for key in ids}
    data.get_recording_heads_many = lambda ids: {}
    data.get_input_draft_participants_many = lambda ids: {}
    data._build_input_squads = lambda match: {"H": [{"playerId": "h1"}], "A": [{"playerId": "a1"}]}
    data._legacy_recordings = lambda gm_id: pytest.fail("New schedules must not scan imported RAW")
    data.backfill_legacy_input_squads = lambda **kwargs: pytest.fail("Must not run a season backfill")
    monkeypatch.setattr(api_match_input, "JpdDidData", lambda: data)
    return data


def test_schedule_create_publishes_only_new_matches_in_backfilled_month(monkeypatch):
    import copy
    store = _ScheduleStore({"matches/imported-final": {"status": "final"},
                            "matches/imported-final/recordings/H": {"raw": "keep"}})
    before = copy.deepcopy(store.documents)
    data = _schedule_data(monkeypatch, store)
    request = api_match_input.ScheduleMatchesCreateRequest(matches=[_new_schedule_fixture(1), _new_schedule_fixture(2)])
    result = api_match_input.create_schedule_matches(request)
    assert result["gmIds"] == [item.gmId for item in request.matches]
    for path, values in before.items():
        assert store.documents[path] == values
    assert len(store.documents) == len(before) + 6
    for gm_id in result["gmIds"]:
        assert store.documents[f"matches/{gm_id}/inputSnapshots/squads"]["schemaVersion"] == 3
        assert store.documents[f"matches/{gm_id}/inputSummaries/current"]["inputStatus"]["H"]["lifecycleStatus"] == "ready"
    data.input_summary_backfill_complete_for_month = lambda year, month: True
    data.list_input_summaries_for_month = lambda year, month: [values for path, values in store.documents.items() if path.endswith("/inputSummaries/current")]
    data.list_matches_for_month = lambda *args: pytest.fail("Completed month should use summaries")
    assert {row["gmId"] for row in api_match_input.list_input_matches(2026, 10)["matches"]} == set(result["gmIds"])


def test_schedule_create_failure_leaves_no_partial_documents(monkeypatch):
    store = _ScheduleStore()
    _schedule_data(monkeypatch, store)
    store.fail_commit = True
    with pytest.raises(RuntimeError, match="commit failed"):
        api_match_input.create_schedule_matches(api_match_input.ScheduleMatchesCreateRequest(matches=[_new_schedule_fixture()]))
    assert store.documents == {}


def test_schedule_create_snapshot_failure_does_not_save_matches(monkeypatch):
    store = _ScheduleStore()
    data = _schedule_data(monkeypatch, store)

    def fail_snapshot(match):
        raise RuntimeError("roster unavailable")

    data._build_input_squads = fail_snapshot
    with pytest.raises(RuntimeError, match="roster unavailable"):
        api_match_input.create_schedule_matches(api_match_input.ScheduleMatchesCreateRequest(matches=[_new_schedule_fixture()]))
    assert store.documents == {}


@pytest.mark.parametrize("fixtures", [
    [_new_schedule_fixture(), _new_schedule_fixture()],
    [{**_new_schedule_fixture(), "seasonId": "20272028"}],
    [{**_new_schedule_fixture(), "awayTeamId": "home"}],
])
def test_schedule_create_rejects_invalid_batch_before_storage(monkeypatch, fixtures):
    from backend.system.system_firestore import BackendError
    monkeypatch.setattr(api_match_input, "JpdDidData", lambda: pytest.fail("Invalid batch must not access storage"))
    with pytest.raises(BackendError) as error:
        api_match_input.create_schedule_matches(api_match_input.ScheduleMatchesCreateRequest(matches=fixtures))
    assert error.value.status_code == 422


def test_setup_save_claims_one_primary_and_shows_ready_roster(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from backend.system import system_firestore
    from backend.system.system_firestore import BackendError, JpdDidData

    store = _ScheduleStore()
    data = object.__new__(JpdDidData)
    data.db = store
    data.get_recording_statuses = lambda gm_id: {"H": None, "A": None}
    summaries = []
    data._update_input_summary_side = lambda gm_id, side, **values: summaries.append(values)
    commit_lock = Lock()
    transactions = []

    class Transaction:
        def __init__(self):
            self.writes = []

        def set(self, reference, values, *, merge):
            assert merge is True
            self.writes.append((reference.path, values))

    def new_transaction():
        transaction = Transaction()
        transactions.append(transaction)
        return transaction

    def transactional(function):
        def run(transaction):
            with commit_lock:
                document = function(transaction)
                for path, values in transaction.writes:
                    store.documents[path] = {**store.documents.get(path, {}), **values}
                return document
        return run

    original_get = _ScheduleReference.get
    draft_reads = []

    def get(reference, **kwargs):
        if reference.path.startswith("inputDrafts/"):
            assert kwargs.get("transaction") in transactions
            draft_reads.append(reference.path)
        return original_get(reference, **kwargs)

    store.transaction = new_transaction
    monkeypatch.setattr(system_firestore.firestore, "transactional", transactional)
    monkeypatch.setattr(_ScheduleReference, "get", get)

    def claim(uid):
        try:
            return data.join_input_draft_participant("sample-match", "H", user_id=uid, role="primary", display_name=uid)
        except BackendError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["primary-a", "primary-b"]))
    accepted = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, BackendError)]
    assert len(accepted) == len(rejected) == 1
    assert rejected[0].code == "primary_already_assigned"
    assert len(draft_reads) == 2
    winner = accepted[0]["primaryUid"]
    assert list(store.documents["inputDrafts/sample-match_H"]["participants"]) == [winner]
    assert summaries[0]["collaboration"]["status"] == "ready"
    assert summaries[0]["collaboration"]["participants"][0]["uid"] == winner
    # Re-saving one's setup keeps the same seat, rather than creating a conflict.
    assert claim(winner)["primaryUid"] == winner


def test_schedule_create_rejects_existing_id_without_changing_it(monkeypatch):
    from backend.system.system_firestore import BackendError
    fixture = _new_schedule_fixture()
    path = f"matches/{fixture['gmId']}"
    store = _ScheduleStore({path: {"status": "final"}})
    _schedule_data(monkeypatch, store)
    with pytest.raises(BackendError) as error:
        api_match_input.create_schedule_matches(api_match_input.ScheduleMatchesCreateRequest(matches=[fixture]))
    assert error.value.status_code == 409
    assert store.documents == {path: {"status": "final"}}


@pytest.mark.parametrize("overrides", [{"date": "2026-02-30"}, {"date": "2026.10.08"}, {"kickoffTime": "24:00"}])
def test_schedule_create_validates_dates_and_times(overrides):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        api_match_input.ScheduleMatchesCreateRequest(matches=[{**_new_schedule_fixture(), **overrides}])


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


def test_draft_write_contract_supports_backend_record_merge() -> None:
    payload = {
        "gmId": "sample-match", "side": "H", "inputMode": "분석", "fieldSide": "left",
        "formationKey": "4-3-3", "homeScore": 0, "awayScore": 0, "status": "H1",
        "halves": {"H1": {"seconds": 12}, "H2": {"seconds": 0}}, "lineup": [], "records": [], "cards": [],
    }
    request = DraftWriteRequest.model_validate({
        "payload": payload, "clientState": {"seconds": 12}, "syncScope": "state_records",
        "deletedRecordIds": ["old-record"], "clearedRecordPlayerIds": ["r1"],
    })

    assert request.syncScope == "state_records"
    assert request.deletedRecordIds == ["old-record"]
    assert request.clearedRecordPlayerIds == ["r1"]


def test_card_mutation_broadcasts_only_cards_not_an_incomplete_draft(monkeypatch) -> None:
    payload = {
        "gmId": "sample-match", "side": "H", "inputMode": "분석", "fieldSide": "left",
        "formationKey": "4-3-3", "homeScore": 0, "awayScore": 0, "status": "H1",
        "halves": {"H1": {"seconds": 12}, "H2": {"seconds": 0}}, "lineup": [], "records": [],
        "cards": [{"id": "card-1", "playerId": "player-1", "half": "H1", "halfSeconds": 12, "card": "Y"}],
    }
    request = DraftWriteRequest.model_validate({"payload": payload, "clientState": {}, "syncScope": "cards"})
    response = DraftResponse.model_validate({"status": "ok", "gmId": "sample-match", "side": "H", "payload": payload, "clientState": {}})
    published: list[str] = []

    monkeypatch.setattr(api_match_input, "_save_draft_mutation", lambda *_: response)
    monkeypatch.setattr(api_match_input, "_publish_cards_response", lambda _: published.append("cards"))
    monkeypatch.setattr(api_match_input, "_publish_draft_response", lambda _: published.append("draft"))

    assert api_match_input.save_draft("sample-match", "H", request) is response
    assert published == ["cards"]


def test_state_mutation_broadcasts_only_state_not_an_incomplete_draft(monkeypatch) -> None:
    payload = {
        "gmId": "sample-match", "side": "H", "inputMode": "분석", "fieldSide": "left",
        "formationKey": "4-3-3", "homeScore": 0, "awayScore": 0, "status": "H1",
        "halves": {"H1": {"seconds": 12}, "H2": {"seconds": 0}}, "lineup": [], "records": [], "cards": [],
    }
    request = DraftWriteRequest.model_validate({"payload": payload, "clientState": {"seconds": 12}, "syncScope": "state"})
    response = DraftResponse.model_validate({
        "status": "ok", "gmId": "sample-match", "side": "H", "payload": payload,
        "clientState": {"seconds": 12}, "sharedState": {"seconds": 12},
    })
    published: list[str] = []

    monkeypatch.setattr(api_match_input, "_save_draft_mutation", lambda *_: response)
    monkeypatch.setattr(api_match_input, "_publish_state_response", lambda _: published.append("state"))
    monkeypatch.setattr(api_match_input, "_publish_draft_response", lambda _: published.append("draft"))

    assert api_match_input.save_draft("sample-match", "H", request) is response
    assert published == ["state"]


def test_input_preview_returns_kpi_and_per_record_flags() -> None:
    request = InputPreviewRequest.model_validate({
        "half": "H1",
        "records": [{"id": "r1", "half": "H1", "halfSeconds": 5, "seq": 0, "act": "P", "res": "O", "area": 5}],
    })
    result = calculate_input_preview(request)

    assert result["status"] == "ok"
    assert "TAP" in result["kpis"]
    assert "r1" in result["flags"]


def test_internal_bootstrap_route_requires_selected_side_and_is_hidden_from_openapi() -> None:
    route = next(route for route in router.routes if route.path == "/match-input/matches/{gm_id}/bootstrap")

    assert route.include_in_schema is False
    parameters = {item.name: item for item in route.dependant.query_params}
    assert parameters["side"].field_info.default is PydanticUndefined


def test_live_draft_socket_route_is_not_exposed_in_openapi() -> None:
    route = next(route for route in router.routes if route.path == "/match-input/drafts/{gm_id}/{side}/live")
    assert route in router.routes
    assert isinstance(DraftSocketHub(), DraftSocketHub)


def test_enter_route_returns_join_and_parallel_entry_data(monkeypatch) -> None:
    class FakeData:
        def get_match(self, gm_id):
            assert gm_id == "sample-match"
            return object()

        def join_input_draft_participant(self, *args, **kwargs):
            assert kwargs["user_id"] == "analyst-1"
            return {
                "primaryUid": "analyst-1",
                "participants": {"analyst-1": {"role": "primary", "name": "Primary"}},
            }

        def get_or_create_input_squads(self, gm_id, match):
            return {"H": [{"playerId": "h-1"}], "A": [{"playerId": "a-1"}]}, True

        def get_input_setup(self, gm_id):
            return {"H": None, "A": None}

        def get_input_draft(self, gm_id, side):
            raise NotFoundError("missing")

    monkeypatch.setattr(api_match_input, "JpdDidData", FakeData)
    response = enter_draft(
        "sample-match", "H", ParticipantJoinRequest(role="primary", displayName="Primary"),
        CurrentUser(uid="analyst-1", claims={}),
    )

    assert response.participant["role"] == "primary"
    assert response.draft.status == "missing"
    assert response.squads["H"][0]["playerId"] == "h-1"


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
