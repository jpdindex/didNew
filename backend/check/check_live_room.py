from copy import deepcopy

import pytest

from backend.api.api_match_input import DraftWriteRequest, InputSetupWriteRequest, FinalizeCommandRequest
from backend.system import system_live_room as live
from backend.system.system_firestore import BackendError, JpdDidData


class MemoryDB:
    def __init__(self, documents):
        self.documents = deepcopy(documents)
        self.fail_commit = False

    def collection(self, path):
        return MemoryCollection(self, path)

    def transaction(self, **kwargs):
        return MemoryTransaction(self)


class MemoryTransaction:
    def __init__(self, db):
        self.db, self.writes = db, {}

    def set(self, reference, values, merge=False):
        self.writes[reference.path] = live._merge(self.db.documents.get(reference.path, {}), values) if merge else deepcopy(values)

    def commit(self):
        if self.db.fail_commit:
            raise RuntimeError('commit lost')
        self.db.documents.update(self.writes)


class MemoryReference:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.rsplit('/', 1)[-1]

    def get(self, **kwargs):
        return live.Snapshot(self, self.db.documents.get(self.path))

    def collection(self, name):
        return MemoryCollection(self.db, self.path + '/' + name)

    def set(self, values, merge=False, **kwargs):
        self.db.documents[self.path] = live._merge(self.db.documents.get(self.path, {}), values) if merge else deepcopy(values)


class MemoryCollection:
    def __init__(self, db, path):
        self.db, self._collection_path = db, path
        self.id = path.rsplit('/', 1)[-1]
        self.parent = MemoryReference(db, path.rsplit('/', 1)[0]) if '/' in path else None

    def document(self, key):
        return MemoryReference(self.db, self._collection_path + '/' + key)

    def stream(self, **kwargs):
        return iter(live.Snapshot(MemoryReference(self.db, path), values) for path, values in list(self.db.documents.items()) if path.rsplit('/', 1)[0] == self._collection_path)


@pytest.fixture
def data(monkeypatch):
    def transactional(function):
        def run(transaction):
            result = function(transaction)
            transaction.commit()
            return result
        return run
    monkeypatch.setattr(live.firestore, 'transactional', transactional)
    result = object.__new__(JpdDidData)
    result.db = MemoryDB({'inputDrafts/m_H': {'gmId': 'm', 'side': 'H', 'revision': 0, 'primaryUid': 'p',
        'participants': {'p': {'role': 'primary'}, 'a': {'role': 'assistant'}}, 'payload': payload()}})
    result.sync_input_summary_from_draft = lambda *args: None
    return result


def payload(records=None, cards=None):
    return {'gmId': 'm', 'side': 'H', 'inputMode': '분석', 'fieldSide': 'left', 'formationKey': '4-3-3',
        'homeScore': 0, 'awayScore': 0, 'status': 'H1', 'halves': {'H1': {'seconds': 1}, 'H2': {'seconds': 0}},
        'lineup': [], 'records': records or [], 'cards': cards or [], 'recorderLevel': 'advanced'}


def record(key='r', player='one'):
    return {'id': key, 'half': 'H1', 'halfSeconds': 1, 'seq': 0, 'act': 'P', 'res': 'O', 'area': 1, 'playerId': player}


def command(key='c', records=None, scope='state_records', base=0, cards=None, **fields):
    return DraftWriteRequest(commandId=key, baseRevision=base, roomEpoch=0, payload=payload(records, cards), clientState={'seconds': 1}, syncScope=scope, **fields)


def test_live_ack_does_not_wait_for_derived_summary(data, monkeypatch):
    scheduled = []
    monkeypatch.setattr(live, '_queue_summary', lambda *args: scheduled.append(args))
    data.sync_input_summary_from_draft = lambda *args: pytest.fail('summary must not run before ACK')
    receipt = live.execute_command(data, 'm', 'H', 'draft', command('start', scope='state'), 'p', defer_summary=True)
    assert receipt['savedRevision'] == 1
    assert scheduled == [(data, 'm', 'H')]


def test_primary_and_assistant_interleaved_acts_merge_with_stale_independent_bases(data):
    for index in range(20):
        uid = 'p' if index % 2 == 0 else 'a'
        live.execute_command(data, 'm', 'H', 'draft', command(f'c{index}', [record(f'r{index}')], scope='records', base=max(0, index - 1)), uid)
    draft = data.get_input_draft('m', 'H')
    assert len(draft['payload']['records']) == 20
    assert draft['revision'] == 20


def test_lost_ack_retry_returns_same_revision_without_reapplying(data):
    request = command(records=[record()])
    first = live.execute_command(data, 'm', 'H', 'draft', request, 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('later', [record(player='two')], base=1), 'p')
    retried = live.execute_command(data, 'm', 'H', 'draft', request, 'p')
    assert first == retried
    assert retried['savedRevision'] == 1
    assert data.get_input_draft('m', 'H')['revision'] == 2
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'two'


def test_atomic_failure_does_not_leave_data_or_command_receipt(data):
    before = deepcopy(data.db.documents)
    data.db.fail_commit = True
    with pytest.raises(RuntimeError):
        live.execute_command(data, 'm', 'H', 'draft', command(records=[record()]), 'p')
    assert data.db.documents == before


def test_duplicate_id_with_different_content_is_rejected(data):
    live.execute_command(data, 'm', 'H', 'draft', command(records=[record()]), 'p')
    with pytest.raises(BackendError) as caught:
        live.execute_command(data, 'm', 'H', 'draft', command(records=[record(player='other')]), 'p')
    assert caught.value.code == 'command_id_reused'


def test_independent_and_same_record_peer_edits_continue_without_conflict(data):
    live.execute_command(data, 'm', 'H', 'draft', command('primary', [record('p')]), 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('assistant', [record('a')], scope='records'), 'a')
    assert {item['id'] for item in data.get_input_draft('m', 'H')['payload']['records']} == {'p', 'a'}
    live.execute_command(data, 'm', 'H', 'draft', command('next', [record('p', 'other')], scope='records'), 'a')
    assert data.get_input_draft('m', 'H')['revision'] == 3
    assert next(row for row in data.get_input_draft('m', 'H')['payload']['records'] if row['id'] == 'p')['playerId'] == 'other'


def test_same_uid_in_another_tab_saves_in_order(data):
    live.execute_command(data, 'm', 'H', 'draft', command('first', [record()]), 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('second', [record(player='new')]), 'p')
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'new'


def test_queued_edit_follows_exact_predecessor_across_transactions(data):
    first = command('first', [record()], scope='records')
    following = command('following', [record(player='two')], scope='records',
                        predecessorCommands={'record:r': 'first'})
    live.execute_command(data, 'm', 'H', 'draft', first, 'p')
    receipt = live.execute_command(data, 'm', 'H', 'draft', following, 'p')
    assert receipt['savedRevision'] == 2
    assert receipt['event']['records'][0]['playerId'] == 'two'
    assert 'lastCommandId' not in receipt['event']['records'][0]
    assert live.execute_command(data, 'm', 'H', 'draft', following, 'p') == receipt


@pytest.mark.parametrize('peer', ['a', 'p'])
@pytest.mark.parametrize('base', [0, 2])
def test_legacy_predecessor_does_not_block_ordered_collaboration(data, peer, base):
    live.execute_command(data, 'm', 'H', 'draft', command('first', [record()], scope='records'), 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('peer', [record(player='peer')], scope='records', base=1), peer)
    live.execute_command(data, 'm', 'H', 'draft', command('following', [record(player='mine')], scope='records',
        base=base, predecessorCommands={'record:r': 'first'}), 'p')
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'mine'


def test_another_users_legacy_predecessor_does_not_change_authority(data):
    live.execute_command(data, 'm', 'H', 'draft', command('peer', [record()], scope='records'), 'a')
    live.execute_command(data, 'm', 'H', 'draft', command('mine', [record(player='mine')], scope='records',
        predecessorCommands={'record:r': 'peer'}), 'p')
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'mine'


def test_state_and_card_can_follow_own_pending_command(data):
    for scope, target, fields in [('state', 'state', {}), ('cards', 'card:c', {
        'cards': [{'id': 'c', 'half': 'H1', 'halfSeconds': 1, 'playerId': 'one', 'card': 'Y'}]})]:
        live.execute_command(data, 'm', 'H', 'draft', command(scope, scope=scope, **fields), 'p')
        request = command(scope + '-next', scope=scope, predecessorCommands={target: scope}, **fields)
        live.execute_command(data, 'm', 'H', 'draft', request, 'p')


def test_substitution_does_not_advance_lineup_edit_conflict_version(data):
    setup = InputSetupWriteRequest(commandId='setup', baseRevision=0, roomEpoch=0, payload=payload(), clientState={}, syncScope='setup')
    live.execute_command(data, 'm', 'H', 'setup', setup, 'p')
    sub = {'id': 's', 'half': 'H1', 'seconds': 1, 'outPlayer': 'one', 'inPlayer': 'two'}
    request = InputSetupWriteRequest(commandId='sub', baseRevision=1, roomEpoch=0, payload=payload(), clientState={'subs': [sub]}, syncScope='substitutions')
    live.execute_command(data, 'm', 'H', 'setup', request, 'a')
    root = data.db.documents['inputDrafts/m_H']
    assert root['setupCommandRevision'] == 1
    assert root['setupCommandId'] == 'setup'


def test_fifty_chained_same_record_edits_cross_batch_boundaries(data):
    for offset in range(0, 50, 20):
        commands = []
        for index in range(offset, min(50, offset + 20)):
            commands.append(('draft', command(f'c{index}', [record(player=str(index))], scope='records',
                predecessorCommands={'record:r': f'c{index - 1}'} if index else {})))
        result = live.execute_batch(data, 'm', 'H', commands, 'p')
        assert all(item['status'] == 'ack' for item in result['results'])
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == '49'


def test_cards_do_not_erase_act_and_tombstone_survives_retry(data):
    live.execute_command(data, 'm', 'H', 'draft', command('act', [record()]), 'p')
    card = {'id': 'card', 'half': 'H1', 'halfSeconds': 1, 'playerId': 'one', 'card': 'Y'}
    live.execute_command(data, 'm', 'H', 'draft', command('card', scope='cards', cards=[card], base=1), 'a')
    assert len(data.get_input_draft('m', 'H')['payload']['records']) == 1
    deletion = command('delete', scope='cards', base=2, deletedCardIds=['card'])
    live.execute_command(data, 'm', 'H', 'draft', deletion, 'a')
    live.execute_command(data, 'm', 'H', 'draft', deletion, 'a')
    assert data.get_input_draft('m', 'H')['payload']['cards'] == []
    assert data.get_input_draft('m', 'H')['revision'] == 3


def test_substitutions_share_revision_and_do_not_erase_other_events(data):
    def setup(key, sub):
        return InputSetupWriteRequest(commandId=key, baseRevision=0, roomEpoch=0, payload=payload(),
            clientState={'subs': [sub]}, syncScope='substitutions')
    first = {'id': 's1', 'half': 'H1', 'seconds': 1, 'outPlayer': 'one', 'inPlayer': 'two'}
    second = {**first, 'id': 's2', 'seconds': 2, 'outPlayer': 'three', 'inPlayer': 'four'}
    live.execute_command(data, 'm', 'H', 'setup', setup('s1', first), 'p')
    live.execute_command(data, 'm', 'H', 'setup', setup('s2', second), 'a')
    reference = data._input_setup_reference('m', 'H')
    assert len(data._with_input_setup_subs(reference, data._normalise_input_setup(reference.get().to_dict()['setup']))['subs']) == 2
    assert data.get_input_draft('m', 'H')['revision'] == 2


@pytest.mark.parametrize('uid,scope', [('outsider', 'records'), ('a', 'state'), ('a', 'checkpoint')])
def test_authority_enforced_for_each_command(data, uid, scope):
    with pytest.raises(BackendError) as caught:
        live.execute_command(data, 'm', 'H', 'draft', command(scope=scope), uid)
    assert caught.value.status_code == 403


def test_old_session_cannot_reopen_a_restored_draft(data):
    data.db.documents['inputDrafts/m_H']['roomEpoch'] = 1
    with pytest.raises(BackendError) as caught:
        live.execute_command(data, 'm', 'H', 'draft', command(), 'p')
    assert caught.value.code == 'room_changed'


def test_presence_leases_are_per_connection_and_expire(data):
    live.update_presence(data, 'm', 'H', 'p', 'tab1', True)
    live.update_presence(data, 'm', 'H', 'p', 'tab2', True)
    live.update_presence(data, 'm', 'H', 'p', 'tab1', False)
    assert live.room_state(data, 'm', 'H')['participants']['p']['online']
    data.db.documents['inputDrafts/m_H/presence/tab2']['expiresAt'] = live.utc_now()
    assert not live.room_state(data, 'm', 'H')['participants']['p']['online']


@pytest.mark.parametrize('missing', [False, True])
def test_rejected_presence_logs_actual_draft_snapshot_without_writing(data, caplog, missing):
    import json
    import logging
    caplog.set_level(logging.INFO, logger='backend.system.system_live_room')
    if missing:
        del data.db.documents['inputDrafts/m_H']
    with pytest.raises(BackendError) as caught:
        live.update_presence(data, 'm', 'H', 'unjoined', 'socket-1', True)
    assert caught.value.code == 'participant_required'
    event = json.loads(caplog.records[-1].message.split('Draft admission ', 1)[1])
    assert event['event'] == 'presence_rejected'
    assert event['uid'] == 'unjoined'
    assert event['sessionId'] == 'socket-1'
    assert event['draftExists'] is not missing
    assert event['participantUids'] == ([] if missing else ['a', 'p'])
    assert event['draftPath'] == 'inputDrafts/m_H'
    assert 'inputDrafts/m_H/presence/socket-1' not in data.db.documents


def test_final_update_freezes_edits_closes_room_and_deduplicates(data):
    request = FinalizeCommandRequest(commandId='final', baseRevision=0, roomEpoch=0)
    calls = []
    def promote(draft):
        calls.append(draft)
        with pytest.raises(BackendError) as caught:
            live.execute_command(data, 'm', 'H', 'draft', command(), 'p')
        assert caught.value.code == 'room_closing'
        return {'status': 'ok', 'recordsSaved': 0}
    first = live.finalize_command(data, 'm', 'H', request, 'p', promote)
    assert live.finalize_command(data, 'm', 'H', request, 'p', promote) == first
    assert len(calls) == 1
    assert data.db.documents['inputDrafts/m_H']['liveClosed']
    assert data.db.documents['inputDrafts/m_H']['roomEpoch'] == 1


def test_interrupted_final_update_resumes_same_command(data):
    request = FinalizeCommandRequest(commandId='final', baseRevision=0, roomEpoch=0)
    with pytest.raises(RuntimeError):
        live.finalize_command(data, 'm', 'H', request, 'p', lambda _: (_ for _ in ()).throw(RuntimeError('RAW failed')))
    result = live.finalize_command(data, 'm', 'H', request, 'p', lambda _: {'status': 'ok'})
    assert result['commandId'] == 'final'


def test_raw_restore_is_frozen_and_deduplicated(data):
    root = data.db.documents['inputDrafts/m_H']
    root.update(liveClosed=True, payload=None)
    calls = []
    def restore(*args, **kwargs):
        calls.append(args)
        with pytest.raises(BackendError) as caught:
            live.execute_command(data, 'm', 'H', 'draft', command(), 'p')
        assert caught.value.code in {'room_closed', 'room_closing'}
        data.db.documents['inputDrafts/m_H'].update(liveClosed=False, payload=payload(), revision=1)
    data.restore_input_draft_from_raw = restore
    request = FinalizeCommandRequest(commandId='restore', baseRevision=0, roomEpoch=0)
    first = live.restore_command(data, 'm', 'H', request, 'p')
    assert live.restore_command(data, 'm', 'H', request, 'p') == first
    assert len(calls) == 1
    assert first['savedRevision'] == 2
    assert data.db.documents['inputDrafts/m_H']['liveRestoring'] is None


def test_basic_participant_cannot_claim_advanced_final_authority(data):
    data.db.documents['inputDrafts/m_H']['participants']['p']['level'] = 'basic'
    with pytest.raises(BackendError) as caught:
        live.execute_command(data, 'm', 'H', 'draft', command(), 'p')
    assert caught.value.code == 'recorder_level_mismatch'


class SDKTransport:
    """Only the RPC boundary is replaced; references/queries/transactions are real SDK objects."""
    def __init__(self, client, documents):
        self.client = client
        self.prefix = f'projects/{client.project}/databases/(default)/documents/'
        self.documents = deepcopy(documents)
        self.reads = []
        self.queries = []
        self.read_calls = 0
        self.commits = 0
        self.transaction_modes = []

    def begin_transaction(self, **kwargs):
        from google.cloud.firestore_v1.types import BeginTransactionResponse
        options = kwargs['request']['options']
        self.transaction_modes.append(options._pb.WhichOneof('mode') if options is not None else 'read_write')
        return BeginTransactionResponse(transaction=b'sdk-test')

    def rollback(self, **kwargs):
        pass

    def _document(self, path, values):
        from google.cloud.firestore_v1 import _helpers
        from google.cloud.firestore_v1.types import Document
        return Document(name=self.prefix + path, fields=_helpers.encode_dict(values))

    def batch_get_documents(self, request, **kwargs):
        from google.cloud.firestore_v1.types import BatchGetDocumentsResponse
        self.read_calls += 1
        self.reads.extend(request['documents'])
        for name in request['documents']:
            path = name.removeprefix(self.prefix)
            if path in self.documents:
                yield BatchGetDocumentsResponse(found=self._document(path, self.documents[path]))
            else:
                yield BatchGetDocumentsResponse(missing=name)

    def run_query(self, request, **kwargs):
        from google.cloud.firestore_v1.types import RunQueryResponse
        parent = request['parent'].removeprefix(self.prefix.rstrip('/')).strip('/')
        collection_id = request['structured_query'].from_[0].collection_id
        collection_path = f'{parent}/{collection_id}' if parent else collection_id
        self.queries.append(collection_path)
        for path, values in self.documents.items():
            if path.rsplit('/', 1)[0] == collection_path:
                yield RunQueryResponse(document=self._document(path, values))

    def commit(self, request, **kwargs):
        from google.cloud.firestore_v1 import _helpers
        from google.cloud.firestore_v1.types import CommitResponse
        self.commits += 1
        for write in request['writes']:
            path = write.update.name.removeprefix(self.prefix)
            self.documents[path] = _helpers.decode_dict(write.update.fields, self.client)
        return CommitResponse()


@pytest.fixture
def sdk_data():
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore_v1 import Client
    client = Client(project='live-room-sdk-tests', credentials=AnonymousCredentials())
    transport = SDKTransport(client, {'inputDrafts/m_H': {'gmId': 'm', 'side': 'H', 'revision': 0,
        'primaryUid': 'p', 'participants': {'p': {'role': 'primary'}}, 'payload': payload()},
        'inputDrafts/m_H/records/r': record()})
    client._firestore_api_internal = transport
    result = object.__new__(JpdDidData)
    result.db = client
    result.sync_input_summary_from_draft = lambda *args: None
    yield result
    client.close()


def test_real_sdk_snapshot_reads_nested_collections_without_collection_path(sdk_data):
    assert not hasattr(sdk_data.db.collection('inputDrafts'), 'path')
    assert not hasattr(sdk_data.db.collection('inputDrafts').document('m_H').collection('records'), 'path')
    draft = sdk_data.get_input_draft('m', 'H')
    assert draft['payload']['records'][0]['id'] == 'r'
    assert draft['payload']['cards'] == []
    assert sdk_data.db._firestore_api_internal.transaction_modes == ['read_only']


def test_snapshot_read_abort_restarts_the_entire_read_only_snapshot(sdk_data, monkeypatch):
    from google.api_core.exceptions import Aborted
    rpc = sdk_data.db._firestore_api_internal
    run_query = rpc.run_query
    aborted = False
    def contended(request, **kwargs):
        nonlocal aborted
        if request['structured_query'].from_[0].collection_id == 'cards' and not aborted:
            aborted = True
            rpc.documents['inputDrafts/m_H']['revision'] = 7
            rpc.documents['inputDrafts/m_H/records/r']['playerId'] = 'new'
            raise Aborted('contention while streaming cards')
        return run_query(request, **kwargs)
    monkeypatch.setattr(rpc, 'run_query', contended)
    monkeypatch.setattr(live.time, 'sleep', lambda _: None)
    draft = sdk_data.get_input_draft('m', 'H')
    assert draft['revision'] == 7
    assert draft['payload']['records'][0]['playerId'] == 'new'
    assert rpc.transaction_modes == ['read_only', 'read_only']
    assert rpc.queries.count('inputDrafts/m_H/records') == 2


def test_repeated_read_abort_returns_retryable_503_without_writes(sdk_data, monkeypatch):
    from google.api_core.exceptions import Aborted
    rpc = sdk_data.db._firestore_api_internal
    original = deepcopy(rpc.documents)
    def contended(*args, **kwargs):
        raise Aborted('contention')
    delays = []
    monkeypatch.setattr(rpc, 'run_query', contended)
    monkeypatch.setattr(live.time, 'sleep', delays.append)
    with pytest.raises(BackendError) as error:
        sdk_data.get_input_draft('m', 'H')
    assert error.value.code == 'draft_snapshot_busy'
    assert error.value.status_code == 503
    assert rpc.transaction_modes == ['read_only'] * 3
    assert delays == [0.05, 0.1]
    assert rpc.documents == original


def test_snapshot_permission_failure_is_not_retried(sdk_data, monkeypatch):
    from google.api_core.exceptions import PermissionDenied
    rpc = sdk_data.db._firestore_api_internal
    def forbidden(*args, **kwargs):
        raise PermissionDenied('permission')
    monkeypatch.setattr(rpc, 'run_query', forbidden)
    with pytest.raises(PermissionDenied):
        sdk_data.get_input_draft('m', 'H')
    assert rpc.transaction_modes == ['read_only']


def test_real_sdk_command_transactions_merge_events_and_deduplicate(sdk_data):
    first = command('sdk-act', [record(player='edited')])
    receipt = live.execute_command(sdk_data, 'm', 'H', 'draft', first, 'p')
    assert live.execute_command(sdk_data, 'm', 'H', 'draft', first, 'p') == receipt
    card = {'id': 'card', 'half': 'H1', 'halfSeconds': 1, 'playerId': 'one', 'card': 'Y'}
    live.execute_command(sdk_data, 'm', 'H', 'draft', command('sdk-card', scope='cards', cards=[card], base=1), 'p')
    setup = InputSetupWriteRequest(commandId='sdk-sub', baseRevision=2, roomEpoch=0, payload=payload(),
        clientState={'subs': [{'id': 's', 'half': 'H1', 'seconds': 2, 'outPlayer': 'one', 'inPlayer': 'two'}]}, syncScope='substitutions')
    live.execute_command(sdk_data, 'm', 'H', 'setup', setup, 'p')
    draft = sdk_data.get_input_draft('m', 'H')
    assert draft['revision'] == 3
    assert draft['payload']['records'][0]['playerId'] == 'edited'
    assert draft['payload']['cards'][0]['id'] == 'card'
    assert draft['_liveSetup']['subs'][0]['id'] == 's'


def test_empty_legacy_base_does_not_hide_saved_players():
    lineup = [{'playerId': 'starter', 'slot': 'gk', 'type': 'START', 'order': 1},
              {'playerId': 'bench', 'slot': 'b0', 'type': 'BENCH', 'order': 1}]
    setup = JpdDidData._normalise_input_setup({'formationKey': '4-3-3', 'lineup': lineup, 'baseLineup': []})
    assert setup['lineup'] == lineup
    assert setup['lineupIsBaseline'] is False


def test_populated_base_remains_authoritative_for_substitution_replay():
    base = [{'playerId': 'starter', 'type': 'START', 'slot': 'gk'}]
    setup = JpdDidData._normalise_input_setup({'formationKey': '4-3-3', 'lineup': [], 'baseLineup': base})
    assert setup['lineup'] == base
    assert setup['lineupIsBaseline'] is True


@pytest.mark.parametrize('history_size', [1, 1000])
def test_modern_ack_reads_only_changed_event_and_never_scans_history(sdk_data, history_size):
    from backend.api.api_match_input import _command_ack
    rpc = sdk_data.db._firestore_api_internal
    for index in range(history_size):
        rpc.documents[f'inputDrafts/m_H/records/history-{index}'] = record(f'history-{index}')
    receipt = live.execute_command(sdk_data, 'm', 'H', 'draft', command('small-ack', [record(player='edited')]), 'p')
    ack = _command_ack('m', 'H', receipt).model_dump(mode='json')
    assert rpc.queries == []
    assert len(rpc.reads) == 3  # receipt, root, affected record; independent of history size
    assert len(ack['event']['records']) == 1
    assert 'payload' not in ack and 'clientState' not in ack
    assert rpc.documents['inputDrafts/m_H']['lastEvent'] == receipt['event']
    assert live.execute_command(sdk_data, 'm', 'H', 'draft', command('small-ack', [record(player='edited')]), 'p') == receipt


def test_card_and_substitution_ack_do_not_query_existing_event_tables(sdk_data):
    rpc = sdk_data.db._firestore_api_internal
    card = {'id': 'card', 'half': 'H1', 'halfSeconds': 1, 'playerId': 'one', 'card': 'Y'}
    ack = live.execute_command(sdk_data, 'm', 'H', 'draft', command('card', scope='cards', cards=[card]), 'p')
    assert [item['id'] for item in ack['event']['cards']] == ['card']
    request = InputSetupWriteRequest(commandId='sub', baseRevision=1, roomEpoch=0, payload=payload(),
        clientState={'subs': [{'id': 's', 'half': 'H1', 'seconds': 2, 'outPlayer': 'one', 'inPlayer': 'two'}]}, syncScope='substitutions')
    ack = live.execute_command(sdk_data, 'm', 'H', 'setup', request, 'p')
    assert [item['id'] for item in ack['event']['subs']] == ['s']
    assert rpc.queries == []


def test_entry_draft_skips_setup_already_fetched_by_parallel_entry(sdk_data):
    rpc = sdk_data.db._firestore_api_internal
    draft = sdk_data.get_input_draft('m', 'H', include_setup=False)
    assert '_liveSetup' not in draft
    assert set(rpc.queries) == {'inputDrafts/m_H/records', 'inputDrafts/m_H/cards'}


def test_fifty_acts_use_three_bulk_reads_and_commits_with_original_receipts(sdk_data):
    rpc = sdk_data.db._firestore_api_internal
    requests = [('draft', command(f'c{i}', [record(f'r{i}')], scope='records')) for i in range(50)]
    for start in range(0, 50, 20):
        response = live.execute_batch(sdk_data, 'm', 'H', requests[start:start + 20], 'p')
        assert all(item['status'] == 'ack' for item in response['results'])
    assert rpc.read_calls == 3
    assert rpc.commits == 3
    assert rpc.queries == []
    assert rpc.documents['inputDrafts/m_H']['revision'] == 50
    assert all(f'inputDrafts/m_H/records/r{i}' in rpc.documents for i in range(50))
    retry = live.execute_batch(sdk_data, 'm', 'H', requests[:20], 'p')
    assert [item['savedRevision'] for item in retry['results']] == list(range(1, 21))
    assert retry['event'] is None
    assert rpc.documents['inputDrafts/m_H']['revision'] == 50


def test_batch_preserves_create_player_edit_delete_order_and_event_revisions(data, monkeypatch):
    monkeypatch.setattr(live, '_queue_summary', lambda *args: None)
    requests = [('draft', command('create', [record('same')], scope='records')),
                ('draft', command('player', [record('same', player='two')], scope='records')),
                ('draft', command('delete', scope='records', deletedRecordIds=['same']))]
    response = live.execute_batch(data, 'm', 'H', requests, 'p')
    assert [item['savedRevision'] for item in response['results']] == [1, 2, 3]
    assert response['event']['events'][0]['records'][0]['playerId'] == 'one'
    assert response['event']['events'][1]['records'][0]['playerId'] == 'two'
    assert response['event']['events'][2]['deletedRecordIds'] == ['same']
    assert data.get_input_draft('m', 'H')['payload']['records'] == []
    assert data.db.documents['inputDrafts/m_H/records/same']['commandRevision'] == 3


def test_batch_concurrent_edits_and_unrelated_act_and_card_all_continue(data, monkeypatch):
    monkeypatch.setattr(live, '_queue_summary', lambda *args: None)
    live.execute_command(data, 'm', 'H', 'draft', command('peer', [record('same')], scope='records'), 'a')
    card = {'id': 'card', 'half': 'H1', 'halfSeconds': 1, 'playerId': 'one', 'card': 'Y'}
    response = live.execute_batch(data, 'm', 'H', [
        ('draft', command('conflict', [record('same', player='two')], scope='records')),
        ('draft', command('dependent', [record('same', player='three')], scope='records')),
        ('draft', command('independent', [record('new')], scope='records')),
        ('draft', command('card', scope='cards', cards=[card])),
    ], 'p')
    assert [item.get('code', item['status']) for item in response['results']] == ['ack', 'ack', 'ack', 'ack']
    assert set(item['id'] for item in data.get_input_draft('m', 'H')['payload']['records']) == {'same', 'new'}
    assert len(response['event']['events']) == 4
    assert response['event']['events'][2]['records'][0]['id'] == 'new'
    assert 'records' not in response['event']['events'][3]


def edit(at, device='device-one', sequence=1, before=None):
    return {'changedAtMs': at, 'deviceId': device, 'sequence': sequence, 'before': before or {}}


def test_late_offline_edit_is_acknowledged_with_newer_server_record(data):
    live.execute_command(data, 'm', 'H', 'draft', command('new', [record(player='new')], scope='records', edit=edit(300)), 'p')
    older = command('old', [record(player='old')], scope='records', edit=edit(100, 'offline'))
    receipt = live.execute_command(data, 'm', 'H', 'draft', older, 'a')
    assert receipt['event']['records'][0]['playerId'] == 'new'
    assert receipt['savedRevision'] == 2
    assert live.execute_command(data, 'm', 'H', 'draft', older, 'a') == receipt


@pytest.mark.parametrize('reverse', [False, True])
def test_independent_fields_of_same_record_merge_in_any_delivery_order(data, reverse):
    original = record()
    live.execute_command(data, 'm', 'H', 'draft', command('initial', [original], scope='records', edit=edit(100)), 'p')
    edits = [('player', {**original, 'playerId': 'two'}, 200, 'p'), ('area', {**original, 'area': 8}, 300, 'a')]
    for key, row, at, uid in reversed(edits) if reverse else edits:
        live.execute_command(data, 'm', 'H', 'draft', command(key, [row], scope='records',
            edit=edit(at, key, before={'record:r': original})), uid)
    stored = data.get_input_draft('m', 'H')['payload']['records'][0]
    assert (stored['playerId'], stored['area']) == ('two', 8)


def test_equal_times_use_device_sequence_then_first_server_admission(data):
    live.execute_command(data, 'm', 'H', 'draft', command('second', [record(player='two')], scope='records', edit=edit(100, sequence=2)), 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('first', [record(player='one')], scope='records', edit=edit(100, sequence=1)), 'p')
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'two'
    peer = command('peer', [record(player='peer')], scope='records', edit=edit(100, 'other-device'))
    receipt = live.execute_command(data, 'm', 'H', 'draft', peer, 'a')
    assert receipt['event']['records'][0]['playerId'] == 'peer'
    live.execute_command(data, 'm', 'H', 'draft', command('later', [record(player='later')], scope='records', edit=edit(100, 'third-device')), 'p')
    assert live.execute_command(data, 'm', 'H', 'draft', peer, 'a') == receipt
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'later'


def test_equal_time_older_device_sequence_stays_old_across_peer_write(data):
    for key, player, device, sequence, uid in [('a2', 'a2', 'a', 2, 'p'), ('b', 'b', 'b', 1, 'a'), ('a1', 'a1', 'a', 1, 'p')]:
        live.execute_command(data, 'm', 'H', 'draft', command(key, [record(player=player)], scope='records', edit=edit(100, device, sequence)), uid)
    assert data.get_input_draft('m', 'H')['payload']['records'][0]['playerId'] == 'b'


def test_ordered_sdk_edits_keep_point_reads_and_hide_merge_metadata(sdk_data):
    rpc = sdk_data.db._firestore_api_internal
    for key, at in [('new', 300), ('late', 100)]:
        receipt = live.execute_command(sdk_data, 'm', 'H', 'draft', command(key, [record(player=key)], scope='records', edit=edit(at)), 'p')
    assert receipt['event']['records'][0]['playerId'] == 'new'
    assert '_editVersions' not in receipt['event']['records'][0]
    assert rpc.queries == []
    assert rpc.read_calls == 6


def test_late_state_cannot_rewind_clock_or_reopen_finished_half(data):
    newer = command('new-state', scope='state', edit=edit(300))
    newer.payload.status = 'H1_done'
    newer.clientState = {'seconds': 2700, 'clockStartedAt': None, 'h1Seconds': 2700}
    live.execute_command(data, 'm', 'H', 'draft', newer, 'p')
    for key, at in [('old-state', 100), ('wrong-reopen', 400)]:
        live.execute_command(data, 'm', 'H', 'draft', command(key, scope='state', edit=edit(at)), 'p')
    shared = data.db.documents['inputDrafts/m_H']['sharedState']
    assert shared['halfStatus'] == 'H1_done'
    assert shared['seconds'] == 2700


def test_newer_time_does_not_grant_assistant_setup_or_state_authority(data):
    with pytest.raises(BackendError) as caught:
        live.execute_command(data, 'm', 'H', 'draft', command('state', scope='state', edit=edit(999999)), 'a')
    assert caught.value.code == 'primary_required'
    setup = InputSetupWriteRequest(commandId='setup', baseRevision=0, roomEpoch=0, payload=payload(), clientState={}, edit=edit(999999))
    with pytest.raises(BackendError) as caught:
        live.execute_command(data, 'm', 'H', 'setup', setup, 'a')
    assert caught.value.code == 'primary_required'


@pytest.mark.parametrize('kind,scope,uid,code', [
    ('draft', 'state', 'a', 'primary_required'),
    ('draft', 'checkpoint', 'a', 'primary_required'),
    ('draft', 'records', 'unjoined', 'participant_required'),
    ('setup', 'setup', 'a', 'primary_required'),
    ('setup', 'substitutions', 'unjoined', 'participant_required'),
])
def test_legacy_api_cannot_bypass_participant_or_primary_checks(data, monkeypatch, kind, scope, uid, code):
    from backend.api import api_match_input as api
    from backend.system.system_firestore import CurrentUser
    before = deepcopy(data.db.documents)
    monkeypatch.setattr(api, 'JpdDidData', lambda: data)
    data.get_match = lambda *_: None
    data.get_input_draft = lambda *_: deepcopy(data.db.documents['inputDrafts/m_H'])
    data.save_input_draft = lambda *args, **kwargs: pytest.fail('rejected mutation must never write')
    data.save_input_setup = lambda *args, **kwargs: pytest.fail('rejected mutation must never write')
    user = CurrentUser(uid=uid, claims={})
    request = (command(scope=scope) if kind == 'draft' else InputSetupWriteRequest(payload=payload(), clientState={}, syncScope=scope))
    request = request.model_copy(update={'commandId': None})
    with pytest.raises(BackendError) as caught:
        (api._save_draft_mutation if kind == 'draft' else api._save_setup_mutation)('m', 'H', request, user)
    assert caught.value.code == code
    assert data.db.documents == before


def test_participant_role_alone_never_grants_lifecycle_control(data):
    current = data.db.documents['inputDrafts/m_H']
    current['participants']['a']['role'] = 'primary'
    with pytest.raises(BackendError) as caught:
        live.require_draft_writer(current, 'a', control=True)
    assert caught.value.code == 'primary_required'


def test_late_delete_or_edit_does_not_remove_or_resurrect_newer_record(data):
    live.execute_command(data, 'm', 'H', 'draft', command('new', [record()], scope='records', edit=edit(300)), 'p')
    receipt = live.execute_command(data, 'm', 'H', 'draft', command('old-delete', scope='records', deletedRecordIds=['r'], edit=edit(200)), 'a')
    assert receipt['event']['records'][0]['id'] == 'r'
    live.execute_command(data, 'm', 'H', 'draft', command('new-delete', scope='records', deletedRecordIds=['r'], edit=edit(400)), 'p')
    receipt = live.execute_command(data, 'm', 'H', 'draft', command('old-edit', [record()], scope='records', edit=edit(350)), 'a')
    assert receipt['event']['deletedRecordIds'] == ['r']
    assert data.get_input_draft('m', 'H')['payload']['records'] == []


def test_late_edit_does_not_overwrite_a_newer_explicit_recreation(data):
    original = record()
    live.execute_command(data, 'm', 'H', 'draft', command('create', [original], scope='records', edit=edit(100)), 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('delete', scope='records', deletedRecordIds=['r'], edit=edit(300)), 'p')
    live.execute_command(data, 'm', 'H', 'draft', command('recreate', [record(player='new')], scope='records',
        edit=edit(500, before={'record:r': original})), 'p')
    receipt = live.execute_command(data, 'm', 'H', 'draft', command('late', [record(player='late')], scope='records', edit=edit(400)), 'a')
    assert receipt['event']['records'][0]['playerId'] == 'new'


def test_late_setup_preserves_newer_formation_and_saved_baseline(data):
    for key, at, formation in [('new', 300, '4-3-3'), ('old', 100, '4-4-2')]:
        values = payload()
        values['formationKey'] = formation
        request = InputSetupWriteRequest(commandId=key, baseRevision=0, roomEpoch=0, payload=values, clientState={}, edit=edit(at))
        live.execute_command(data, 'm', 'H', 'setup', request, 'p')
    setup = data.db.documents['inputDrafts/m_H']['setup']
    assert setup['formationKey'] == '4-3-3'
    assert setup['baseLineup'] == []


def test_failed_batch_commit_persists_no_partial_events_or_receipts(data):
    data.db.fail_commit = True
    with pytest.raises(RuntimeError):
        live.execute_batch(data, 'm', 'H', [('draft', command('one', [record('one')], scope='records')),
                                         ('draft', command('two', [record('two')], scope='records'))], 'p')
    assert data.db.documents['inputDrafts/m_H']['revision'] == 0
    assert len(data.db.documents) == 1


def test_batch_mixed_card_and_substitution_preserves_each_receipt(sdk_data):
    card = {'id': 'card', 'half': 'H1', 'halfSeconds': 1, 'playerId': 'one', 'card': 'Y'}
    sub = InputSetupWriteRequest(commandId='sub', baseRevision=0, roomEpoch=0, payload=payload(),
        clientState={'subs': [{'id': 's', 'half': 'H1', 'seconds': 2, 'outPlayer': 'one', 'inPlayer': 'two'}]}, syncScope='substitutions')
    response = live.execute_batch(sdk_data, 'm', 'H', [('draft', command('act', [record('new')], scope='records')),
        ('draft', command('card', scope='cards', cards=[card])), ('setup', sub)], 'p')
    assert [item['savedRevision'] for item in response['results']] == [1, 2, 3]
    assert sdk_data.db._firestore_api_internal.commits == 1
    draft = sdk_data.get_input_draft('m', 'H')
    assert len(draft['payload']['cards']) == 1
    assert len(draft['_liveSetup']['subs']) == 1


def test_batch_api_uses_one_match_check_and_returns_individual_durable_acks(sdk_data, monkeypatch):
    from backend.api import api_match_input as api
    from backend.system.system_firestore import CurrentUser
    checked = []
    sdk_data.get_match = lambda gm_id: checked.append(gm_id) or object()
    monkeypatch.setattr(api, 'JpdDidData', lambda: sdk_data)
    request = api.BatchWriteRequest(commands=[{'kind': 'draft', 'request': command(f'c{i}', [record(f'r{i}')], scope='records').model_dump(mode='json')} for i in range(20)])
    result = api._save_batch_mutation('m', 'H', request, CurrentUser(uid='p', claims={}))
    assert checked == ['m']
    assert [item['savedRevision'] for item in result['results']] == list(range(1, 21))
    assert result['event']['revision'] == 20
    assert sdk_data.db._firestore_api_internal.commits == 1



def test_kpi_halves_share_one_records_only_snapshot_per_team(sdk_data):
    from backend.system.system_firestore import NotFoundError
    rpc = sdk_data.db._firestore_api_internal
    rpc.documents['inputDrafts/m_A'] = {'gmId': 'm', 'side': 'A', 'payload': {**payload(), 'side': 'A'}}
    rpc.documents['inputDrafts/m_H/records/h2'] = {**record('h2'), 'half': 'H2', 'act': 'SH', 'res': 'GOAL', 'isShot': True}
    def no_raw(*args):
        raise NotFoundError('no RAW')
    sdk_data.get_recording = no_raw
    views = sdk_data.read_match_dashboard_kpis_bootstrap('m', ['H1', 'H2'])
    assert views['H']['H1']['Goal'] == 0
    assert views['H']['H2']['Goal'] == 1
    assert rpc.queries.count('inputDrafts/m_H/records') == 1
    assert rpc.queries.count('inputDrafts/m_A/records') == 1
    assert all(not path.endswith(('/cards', '/substitutions')) for path in rpc.queries)
    assert rpc.transaction_modes == ['read_only', 'read_only']


def test_setup_bootstrap_returns_saved_setup_without_reading_records_or_kpis(sdk_data):
    from types import SimpleNamespace
    from backend.system.system_firestore import NotFoundError
    rpc = sdk_data.db._firestore_api_internal
    root = rpc.documents['inputDrafts/m_H']
    root['setup'] = {'formationKey': '4-3-3', 'fieldSide': 'left', 'lineup': []}
    root['payload']['status'] = 'H2_done'
    rpc.documents['inputDrafts/m_A'] = {'gmId': 'm', 'side': 'A', 'payload': {**payload(), 'side': 'A'}}
    status = {'rawStatus': None, 'lifecycleStatus': 'H2_done', 'completed': False}
    rpc.documents['matches/m/inputSummaries/current'] = {'inputStatus': {'H': status, 'A': status}}
    sdk_data.get_match = lambda gm_id: SimpleNamespace(homeTeamId='h', awayTeamId='a', leagueId='l', seasonId='s',
        round=1, date='2026.10.15', kickoffTime=None, stadiumId='stadium', matchType='league', score=SimpleNamespace(home=0, away=0))
    sdk_data._get_or_create_input_squads_snapshot = lambda *args: ({'H': [], 'A': []}, True, {})
    def no_raw(*args):
        raise NotFoundError('no RAW')
    sdk_data.get_recording = no_raw
    before = deepcopy(rpc.documents)
    result = sdk_data.build_input_bootstrap('m', 'H')
    assert result['session']['summaryOnly'] is True
    assert result['session']['sharedState']['halfStatus'] == 'H2_done'
    assert result['inputSetup']['H']['formationKey'] == '4-3-3'
    assert result['dashboardKpiHalves'] == []
    assert not any(path.endswith(('/records', '/cards')) for path in rpc.queries)
    assert rpc.queries.count('inputDrafts/m_H/substitutions') == 1
    assert rpc.transaction_modes == ['read_only']
    assert rpc.documents == before


def test_kpi_raw_halves_share_one_read_and_final_total_stays_cached(sdk_data):
    from types import SimpleNamespace
    calls = []
    sdk_data.get_recording = lambda gm_id, side: SimpleNamespace(kpi=SimpleNamespace(model_dump=lambda **kwargs: {'GOAL': 5}))
    def records(gm_id, side):
        calls.append(side)
        return [('one', SimpleNamespace(half='H1', halfSeconds=1, seq=1, act='SH', res='GOAL', area=1,
            playerId=None, shootPosX=None, shootPosY=None, shootDspRange=None, isShot=True))]
    sdk_data.list_records = records
    views = sdk_data.read_match_dashboard_kpis_bootstrap('m', ['H1', 'H2'])
    assert sorted(calls) == ['A', 'H']
    assert views['H']['H1']['Goal'] == 1
    assert views['H']['H2']['Goal'] == 0
    calls.clear()
    assert sdk_data.read_match_dashboard_kpis('m')['H']['Goal'] == 5
    assert calls == []



def test_missing_selected_snapshot_is_not_read_again_for_kpis(sdk_data):
    from backend.system.system_firestore import NotFoundError
    rpc = sdk_data.db._firestore_api_internal
    del rpc.documents['inputDrafts/m_H']
    def no_raw(*args):
        raise NotFoundError('no RAW')
    sdk_data.get_recording = no_raw
    views = sdk_data.read_match_dashboard_kpis_bootstrap('m', ['H1', 'H2'], drafts={'H': None})
    assert views['H']['H1']['Goal'] == views['H']['H2']['Goal'] == 0
    assert all(not name.endswith('inputDrafts/m_H') for name in rpc.reads)
    assert rpc.transaction_modes == ['read_only']


@pytest.mark.parametrize('lifecycle', ['ready', 'final'])
def test_ready_and_final_bootstrap_still_skip_event_reads(sdk_data, lifecycle):
    from types import SimpleNamespace
    rpc = sdk_data.db._firestore_api_internal
    status = {'rawStatus': 'final' if lifecycle == 'final' else None, 'lifecycleStatus': lifecycle, 'completed': lifecycle == 'final'}
    rpc.documents['matches/m/inputSummaries/current'] = {'inputStatus': {'H': status, 'A': status}}
    rpc.documents['inputDrafts/m_H']['setup'] = {'formationKey': '4-3-3', 'fieldSide': 'left', 'lineup': []}
    rpc.documents['inputDrafts/m_H']['payload']['status'] = lifecycle
    sdk_data.get_match = lambda gm_id: SimpleNamespace(homeTeamId='h', awayTeamId='a', leagueId='l', seasonId='s',
        round=1, date='2026.10.15', kickoffTime=None, stadiumId='stadium', matchType='league', score=SimpleNamespace(home=0, away=0))
    sdk_data._get_or_create_input_squads_snapshot = lambda *args: ({'H': [], 'A': []}, True, {})
    sdk_data._final_lobby_session_from_snapshot = lambda *args: {'status': 'ok', 'gmId': 'm', 'side': 'H'}
    result = sdk_data.build_input_bootstrap('m', 'H')
    assert result['dashboardKpiHalves'] == []
    assert not any(path.endswith(('/records', '/cards')) for path in rpc.queries)
    assert rpc.transaction_modes == (['read_only'] if lifecycle == 'ready' else [])
    if lifecycle == 'ready':
        assert result['inputSetup']['H']['formationKey'] == '4-3-3'
        assert result['session']['status'] == 'missing'
    else:
        assert result['inputSetup'] == {'H': None, 'A': None}
        assert rpc.queries == []



def test_lobby_setup_snapshot_retries_with_a_fresh_consistent_revision(sdk_data, monkeypatch):
    from google.api_core.exceptions import Aborted
    rpc = sdk_data.db._firestore_api_internal
    rpc.documents['inputDrafts/m_H']['setup'] = {'formationKey': '4-3-3', 'fieldSide': 'left', 'lineup': []}
    original = rpc.run_query
    def once(request, **kwargs):
        if rpc.documents['inputDrafts/m_H']['revision'] == 0:
            rpc.documents['inputDrafts/m_H'].update(revision=3, setup={'formationKey': '4-4-2', 'fieldSide': 'right', 'lineup': []})
            raise Aborted('setup changed')
        return original(request, **kwargs)
    monkeypatch.setattr(rpc, 'run_query', once)
    monkeypatch.setattr(live.time, 'sleep', lambda _: None)
    heads, setups = live.read_lobby_snapshot(sdk_data, 'm')
    assert heads['H']['revision'] == 3
    assert setups['H']['formationKey'] == '4-4-2'
    assert setups['H']['fieldSide'] == 'right'
    assert rpc.transaction_modes == ['read_only', 'read_only']
    assert not any(path.endswith(('/records', '/cards')) for path in rpc.queries)
