from backend.api import api_match_input
from backend.api.api_match_input import DraftResponse, DraftSocketHub, DraftWriteRequest, InputPreviewRequest, MatchInputCommit, ParticipantJoinRequest, calculate_input_preview, enter_draft, router
from backend.system.system_firestore import CurrentUser, NotFoundError
from pydantic_core import PydanticUndefined
import pytest


def test_draft_snapshot_busy_is_503_and_never_a_missing_draft(monkeypatch):
    from backend.system.system_firestore import BackendError
    class Data:
        def get_input_draft(self, *args):
            raise BackendError('snapshot busy', status_code=503, code='draft_snapshot_busy')
    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    with pytest.raises(BackendError) as error:
        api_match_input.get_draft('m', 'H')
    assert error.value.status_code == 503
    assert error.value.code == 'draft_snapshot_busy'


@pytest.mark.parametrize('code,status,close', [('participant_required', 403, 1008), ('presence_busy', 503, 1013)])
def test_live_socket_rejects_failed_admission_without_crash_or_presence_cleanup(monkeypatch, caplog, code, status, close):
    import asyncio
    import json
    import logging
    from backend.system.system_firestore import BackendError
    caplog.set_level(logging.INFO, logger='backend.system.system_live_room')

    class Data:
        def get_match(self, gm_id):
            return object()

    async def user(socket):
        return CurrentUser(uid='p', claims={})

    presence_calls = []
    def presence(*args):
        presence_calls.append(args[-1])
        raise BackendError('admission failed', status_code=status, code=code)

    class Socket:
        def __init__(self):
            self.messages = []
            self.close_code = None
        async def accept(self):
            pass
        async def send_json(self, message):
            self.messages.append(message)
        async def close(self, code):
            self.close_code = code

    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    monkeypatch.setattr(api_match_input, '_socket_user', user)
    monkeypatch.setattr(api_match_input, 'update_presence', presence)
    monkeypatch.setattr(api_match_input, 'draft_sockets', DraftSocketHub())
    socket = Socket()
    asyncio.run(api_match_input.live_draft_socket(socket, 'm', 'A'))
    assert socket.messages[0]['code'] == code
    assert socket.messages[0]['status'] == status
    assert socket.close_code == close
    assert presence_calls == [True]
    assert 'crashed' not in caplog.text
    assert 'Presence expires automatically' not in caplog.text
    events = [json.loads(record.message.split('Draft admission ', 1)[1])
              for record in caplog.records if record.message.startswith('Draft admission ')]
    rejection = next(event for event in events if event['event'] == 'socket_rejected')
    authenticated = next(event for event in events if event['event'] == 'socket_authenticated')
    assert rejection['uid'] == 'p'
    assert rejection['stage'] == 'presence_register'
    assert rejection['sessionId'] == authenticated['sessionId']


@pytest.mark.parametrize('code', ['presence_busy', 'participant_required', 'session_owner_required'])
def test_live_socket_heartbeat_busy_keeps_transport_but_authority_errors_close(monkeypatch, code):
    import asyncio
    import json
    from starlette.websockets import WebSocketDisconnect
    from backend.system.system_firestore import BackendError
    class Root:
        def on_snapshot(self, callback):
            return self
        def unsubscribe(self):
            pass
    class Data:
        def get_match(self, gm_id):
            return object()
        def _input_setup_reference(self, *args):
            return Root()
    async def user(socket):
        return CurrentUser(uid='p', claims={})
    calls = []
    def presence(*args):
        calls.append(args[-1])
        if len(calls) == 2:
            raise BackendError('heartbeat failed', status_code=503 if code == 'presence_busy' else 403, code=code)
    class Socket:
        def __init__(self):
            self.messages, self.receives, self.close_code = [], 0, None
        async def accept(self):
            pass
        async def send_json(self, message):
            self.messages.append(message)
        async def receive_text(self):
            self.receives += 1
            if self.receives == 1:
                return json.dumps({'type': 'heartbeat'})
            if self.receives == 2:
                return json.dumps({'type': 'heartbeat'})
            raise WebSocketDisconnect()
        async def close(self, code):
            self.close_code = code
    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    monkeypatch.setattr(api_match_input, '_socket_user', user)
    monkeypatch.setattr(api_match_input, 'update_presence', presence)
    monkeypatch.setattr(api_match_input, 'draft_sockets', DraftSocketHub())
    monkeypatch.setattr(api_match_input, 'room_state', lambda *args: {})
    socket = Socket()
    asyncio.run(api_match_input.live_draft_socket(socket, 'm', 'H'))
    assert socket.messages[0]['type'] == 'ready'
    if code == 'presence_busy':
        assert [message['type'] for message in socket.messages] == ['ready', 'pong', 'pong']
        assert socket.close_code is None
        assert calls == [True, True, True, False]
    else:
        assert socket.messages[-1]['code'] == code
        assert socket.close_code == 1008
        assert calls == [True, True, False]


@pytest.mark.parametrize('fails', [False, True])
def test_join_diagnostics_correlate_authenticated_user_and_result(caplog, monkeypatch, fails):
    import json
    import logging
    from backend.system.system_firestore import BackendError
    caplog.set_level(logging.INFO, logger='backend.system.system_live_room')
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'test-build')
    class Data:
        def join_input_draft_participant(self, gm_id, side, **kwargs):
            assert kwargs['user_id'] == 'verified-uid'
            if fails:
                raise BackendError('seat taken', status_code=409, code='primary_already_assigned')
            return {'participants': {'verified-uid': {'role': 'primary', 'name': 'private name'}}, 'revision': 3}
    request = ParticipantJoinRequest(role='primary', displayName='private name')
    user = CurrentUser(uid='verified-uid', claims={'token': 'never-log-this'})
    if fails:
        with pytest.raises(BackendError):
            api_match_input._join_draft_with_diagnostics(Data(), 'm', 'A', request, user, source='enter')
    else:
        api_match_input._join_draft_with_diagnostics(Data(), 'm', 'A', request, user, source='enter')
    events = [json.loads(record.message.split('Draft admission ', 1)[1]) for record in caplog.records]
    assert [event['event'] for event in events] == ['join_started', 'join_failed' if fails else 'join_succeeded']
    assert events[0]['attemptId'] == events[1]['attemptId']
    assert events[1]['uid'] == 'verified-uid'
    assert events[1]['build'] == 'test-build'
    if fails:
        assert events[1]['code'] == 'primary_already_assigned'
    else:
        assert events[1]['participantPresent'] is True
    assert 'never-log-this' not in caplog.text
    assert 'private name' not in caplog.text


@pytest.mark.parametrize('role,primary_uid,expected', [('assistant', None, False), ('manager', None, True), ('primary', 'u', True), ('manager', 'p', False)])
def test_join_control_matches_server_ownership_rule(monkeypatch, role, primary_uid, expected):
    class Data:
        def get_match(self, *_):
            return object()
        def join_input_draft_participant(self, *args, **kwargs):
            return {'primaryUid': primary_uid, 'participants': {'u': {'role': role}}}
    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    result = api_match_input.join_draft_participant('m', 'H', ParticipantJoinRequest(role=role), CurrentUser(uid='u', claims={}))
    assert result['control'] is expected


def test_live_socket_relays_root_delta_and_requests_resync_for_a_gap(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from starlette.websockets import WebSocketDisconnect

    class RootWatch:
        def on_snapshot(self, callback):
            self.callback = callback
            self.emit({'revision': 5, 'roomEpoch': 0, 'participants': {}})
            return self

        def emit(self, document):
            self.callback([SimpleNamespace(to_dict=lambda: document)])

        def unsubscribe(self):
            self.unsubscribed = True

    root = RootWatch()
    class Data:
        def get_match(self, gm_id):
            return object()

        def _input_setup_reference(self, gm_id, side):
            return root

        def get_input_draft(self, *args, **kwargs):
            raise AssertionError('socket subscriptions must not scan full Drafts')

    async def user(socket):
        return CurrentUser(uid='p', claims={})

    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    monkeypatch.setattr(api_match_input, '_socket_user', user)
    monkeypatch.setattr(api_match_input, 'draft_sockets', DraftSocketHub())
    monkeypatch.setattr(api_match_input, 'update_presence', lambda *args: None)
    monkeypatch.setattr(api_match_input, 'room_state', lambda *args: {'participants': {}, 'revision': 5})

    async def run():
        class Socket:
            def __init__(self):
                self.incoming = asyncio.Queue()
                self.outgoing = asyncio.Queue()
            async def accept(self):
                pass
            async def receive_text(self):
                message = await self.incoming.get()
                if message is None:
                    raise WebSocketDisconnect()
                return json.dumps(message)
            async def send_json(self, message):
                await self.outgoing.put(message)
        socket = Socket()
        task = asyncio.create_task(api_match_input.live_draft_socket(socket, 'm', 'H'))
        async def receive(kind):
            while True:
                message = await asyncio.wait_for(socket.outgoing.get(), timeout=2)
                if message['type'] == kind:
                    return message
        try:
            await receive('ready')
            await socket.incoming.put({'type': 'subscribe', 'revision': 5, 'roomEpoch': 0})
            await receive('room')
            event = {'type': 'delta', 'gmId': 'm', 'side': 'H', 'revision': 6, 'roomEpoch': 0, 'records': [{'id': 'one'}]}
            root.emit({'revision': 6, 'roomEpoch': 0, 'participants': {}, 'lastEvent': event})
            assert (await receive('mutation'))['event'] == event
            root.emit({'revision': 8, 'roomEpoch': 0, 'participants': {}, 'lastEvent': {**event, 'revision': 8}})
            assert (await receive('resync'))['revision'] == 8
            batch = {'type': 'batch', 'gmId': 'm', 'side': 'H', 'fromRevision': 8, 'revision': 10, 'roomEpoch': 0,
                     'events': [{**event, 'revision': 9}, {**event, 'revision': 10}]}
            root.emit({'revision': 10, 'roomEpoch': 0, 'participants': {}, 'lastEvent': batch})
            assert (await receive('mutation'))['event'] == batch
        finally:
            await socket.incoming.put(None)
            await asyncio.wait_for(task, timeout=2)
        assert root.unsubscribed
    asyncio.run(run())


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

        def get_input_draft(self, gm_id, side, *, include_setup=True):
            assert include_setup is False
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


@pytest.mark.parametrize("missing_summary", [False, True])
def test_unmarked_month_reads_details_only_for_missing_summaries(monkeypatch, missing_summary):
    from backend.system.system_schema import MatchDoc
    store = _ScheduleStore()
    data = _schedule_data(monkeypatch, store)
    request = api_match_input.ScheduleMatchesCreateRequest(matches=[_new_schedule_fixture(1), _new_schedule_fixture(2)])
    ids = api_match_input.create_schedule_matches(request)["gmIds"]
    summaries = [store.documents[f"matches/{gm_id}/inputSummaries/current"] for gm_id in ids]
    data.input_summary_backfill_complete_for_month = lambda *args: False
    data.list_input_summaries_for_month = lambda *args: summaries[:1] if missing_summary else summaries
    data.list_matches_for_month = lambda *args: [(gm_id, MatchDoc.model_validate(store.documents[f"matches/{gm_id}"])) for gm_id in ids]
    heads, drafts, metadata = [], [], []
    data.get_recording_heads_many = lambda keys: heads.append(keys) or {}
    data.get_input_draft_participants_many = lambda keys: drafts.append(keys) or {}
    data.get_documents_by_ids = lambda collection, keys: metadata.append((collection, keys)) or {}
    result = api_match_input.list_input_matches(2026, 10)["matches"]
    assert {row["gmId"] for row in result} == set(ids)
    assert next(row for row in result if row["gmId"] == ids[0]) == summaries[0]
    assert heads == ([[ids[1]]] if missing_summary else [])
    assert drafts == heads
    assert len(metadata) == (2 if missing_summary else 0)


def test_summary_index_unavailable_uses_legacy_even_for_completed_month(monkeypatch):
    from google.api_core.exceptions import FailedPrecondition
    from backend.system.system_schema import MatchDoc
    store = _ScheduleStore()
    data = _schedule_data(monkeypatch, store)
    gm_id = api_match_input.create_schedule_matches(api_match_input.ScheduleMatchesCreateRequest(matches=[_new_schedule_fixture()]))["gmIds"][0]
    def unavailable(*args):
        raise FailedPrecondition("index building")
    data.list_input_summaries_for_month = unavailable
    data.input_summary_backfill_complete_for_month = lambda *args: True
    data.list_matches_for_month = lambda *args: [(gm_id, MatchDoc.model_validate(store.documents[f"matches/{gm_id}"]))]
    assert [row["gmId"] for row in api_match_input.list_input_matches(2026, 10)["matches"]] == [gm_id]


def test_unmarked_month_ignores_orphaned_summaries(monkeypatch):
    store = _ScheduleStore()
    data = _schedule_data(monkeypatch, store)
    data.input_summary_backfill_complete_for_month = lambda *args: False
    data.list_input_summaries_for_month = lambda *args: [{"gmId": "deleted"}]
    data.list_matches_for_month = lambda *args: []
    assert api_match_input.list_input_matches(2026, 10)["matches"] == []



def test_daily_match_list_never_reads_the_whole_month(monkeypatch):
    from backend.system.system_schema import MatchDoc
    store = _ScheduleStore()
    data = _schedule_data(monkeypatch, store)
    gm_id = api_match_input.create_schedule_matches(api_match_input.ScheduleMatchesCreateRequest(matches=[_new_schedule_fixture()]))['gmIds'][0]
    summary = store.documents[f'matches/{gm_id}/inputSummaries/current']
    data.list_input_summaries_for_day = lambda day: [summary]
    data.list_matches_for_day = lambda day: [(gm_id, MatchDoc.model_validate(store.documents[f'matches/{gm_id}']))]
    data.list_matches_for_month = lambda *args: pytest.fail('day request must not scan the month')
    data.list_input_summaries_for_month = lambda *args: pytest.fail('day request must not scan monthly summaries')
    data.input_summary_backfill_complete_for_month = lambda *args: pytest.fail('day request does not need migration markers')
    data.get_recording_heads_many = lambda *args: pytest.fail('covered fixture must not open RAW')
    assert api_match_input.list_input_matches(2026, 10, day='2026-10-08')['matches'] == [summary]


@pytest.mark.parametrize('day', ['2026-11-01', '2026-10-99'])
def test_daily_match_list_rejects_invalid_or_out_of_month_dates(monkeypatch, day):
    from backend.system.system_firestore import BackendError
    monkeypatch.setattr(api_match_input, 'JpdDidData', lambda: object())
    with pytest.raises(BackendError) as error:
        api_match_input.list_input_matches(2026, 10, day=day)
    assert error.value.status_code == 422


def test_calendar_endpoint_returns_dates_without_any_match_details(monkeypatch):
    class Data:
        def list_calendar_fixtures(self, year, month):
            assert (year, month) == (2026, 10)
            return [{'date': '2026-10-08', 'leagueId': 'EPL'}]
    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    assert api_match_input.list_input_calendar(2026, 10)['fixtures'] == [{'date': '2026-10-08', 'leagueId': 'EPL'}]


@pytest.mark.parametrize('half', ['H1', 'H2'])
def test_preview_bootstrap_reads_only_the_requested_half_without_setup(monkeypatch, half):
    class Data:
        def get_match(self, gm_id):
            assert gm_id == 'm'
        def read_match_dashboard_kpis_bootstrap(self, gm_id, halves):
            assert halves == [half]
            return {'H': {half: {'Goal': 1}}, 'A': {half: {'Goal': 0}}}
    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    result = api_match_input.read_match_input_bootstrap('m', side='H', phase='preview', half=half)
    assert result['dashboardKpiHalves'] == [half]
    assert 'inputSetup' not in result and 'session' not in result


def test_dashboard_kpi_refresh_never_reads_or_returns_setup(monkeypatch):
    class Data:
        def get_match(self, gm_id):
            pass
        def read_match_dashboard_kpis(self, gm_id, half):
            return {'H': {'Goal': 1}, 'A': {'Goal': 0}}
    monkeypatch.setattr(api_match_input, 'JpdDidData', Data)
    result = api_match_input.read_match_dashboard_kpis('m', half='all')
    assert result['kpis']['H']['Goal'] == 1
    assert 'inputSetup' not in result
