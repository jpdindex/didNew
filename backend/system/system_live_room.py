"""Transactional commands and presence on the existing inputDrafts aggregate.

The buffered store lets the existing Draft merge routines read their own writes,
while issuing all Firestore reads before the transaction's first write. No live
state is held exclusively in a worker process.
"""
from copy import copy, deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from datetime import timedelta
from hashlib import sha256
import json
import time

from firebase_admin import firestore
from google.api_core.exceptions import Aborted, DeadlineExceeded, ServiceUnavailable

from backend.system.system_firestore import BackendError, utc_now


_summary_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='draft-summary')
_summary_lock = Lock()
_summary_pending = {}
_summary_running = False


def _queue_summary(data, gm_id, side):
    """Coalesce derived updates; never hold a command's ACK for schedule reads."""
    global _summary_running
    def drain():
        global _summary_running
        while True:
            with _summary_lock:
                if not _summary_pending:
                    _summary_running = False
                    return
                (match_id, match_side), source = _summary_pending.popitem()
            try:
                # Read the latest head, rather than publishing a captured old
                # lifecycle after a newer start/pause/finalization command.
                document = source._input_setup_reference(match_id, match_side).get().to_dict() or {}
                source.sync_input_summary_from_draft(match_id, match_side, document)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Draft committed; schedule summary refresh failed')
    with _summary_lock:
        _summary_pending[(gm_id, side)] = data
        if not _summary_running:
            _summary_running = True
            _summary_pool.submit(drain)


def _merge(left, right):
    result = deepcopy(left) if isinstance(left, dict) else {}
    for key, value in right.items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return result


class Snapshot:
    def __init__(self, reference, values):
        self.reference, self.id, self.exists = reference, reference.id, values is not None
        self._values = values

    def to_dict(self):
        return deepcopy(self._values)


class Reference:
    def __init__(self, store, reference):
        self.store, self.raw = store, reference
        self.path, self.id = reference.path, reference.id

    def collection(self, name):
        return Collection(self.store, self.raw.collection(name))

    def get(self, **kwargs):
        return Snapshot(self, self.store.read(self))

    def set(self, values, merge=False, **kwargs):
        self.store.write(self, values, merge)


class Collection:
    def __init__(self, store, reference):
        self.store, self.raw = store, reference
        # CollectionReference exposes id/parent, unlike DocumentReference.path.
        parent = reference.parent
        self.path = f'{parent.path}/{reference.id}' if parent is not None else reference.id

    def document(self, key):
        return Reference(self.store, self.raw.document(key))

    def stream(self, **kwargs):
        path = self.path
        if path not in self.store.streams:
            documents = list(self.raw.stream(transaction=self.store.transaction, **kwargs))
            self.store.streams[path] = [item.reference.path for item in documents]
            for item in documents:
                self.store.cache.setdefault(item.reference.path, item.to_dict())
        paths = set(self.store.streams[path])
        paths.update(key for key in self.store.writes if key.rsplit('/', 1)[0] == path)
        return iter(Snapshot(self.document(key.rsplit('/', 1)[1]), self.store.read(self.document(key.rsplit('/', 1)[1]))) for key in sorted(paths))


class TransactionStore:
    live_transaction = True
    def __init__(self, db, transaction):
        self.raw, self.transaction = db, transaction
        self.cache, self.writes, self.streams = {}, {}, {}

    def collection(self, path):
        return Collection(self, self.raw.collection(path))

    def get_all(self, references, **kwargs):
        return iter(reference.get() for reference in references)

    def read(self, reference):
        if reference.path in self.writes:
            return deepcopy(self.writes[reference.path][1])
        if reference.path not in self.cache:
            self.cache[reference.path] = reference.raw.get(transaction=self.transaction).to_dict()
        return deepcopy(self.cache[reference.path])

    def write(self, reference, values, merge=False):
        previous = (self.read(reference) or {}) if merge else {}
        self.writes[reference.path] = (reference.raw, _merge(previous, values))

    def batch(self):
        return self

    def set(self, reference, values, merge=False):
        self.write(reference, values, merge)

    def commit(self, **kwargs):
        # Existing merge routines may commit more than one batch. Flush once,
        # with the command receipt, only after their complete read/merge phase.
        return None

    def flush(self):
        if len(self.writes) > 490:
            raise BackendError('Command exceeds the atomic write limit; split the input into smaller commands', status_code=422, code='command_too_large')
        for reference, values in self.writes.values():
            self.transaction.set(reference, values)


def read_snapshot(data, gm_id, side, *, include_setup=True):
    @firestore.transactional
    def read(transaction):
        working = copy(data)
        working.db = TransactionStore(data.db, transaction)
        document = working.get_input_draft(gm_id, side)
        if not include_setup:
            return document
        reference = working._input_setup_reference(gm_id, side)
        setup = working._with_input_setup_subs(reference, working._normalise_input_setup((reference.get().to_dict() or {}).get('setup')))
        return {**document, '_liveSetup': setup}
    # A snapshot needs consistency, not write locks on every event table.
    # The SDK retries commit aborts, but aborts raised while streaming reads
    # escape its retry loop. Restart the complete read with a fresh cache/tx.
    for attempt in range(3):
        try:
            return read(data.db.transaction(read_only=True, max_attempts=1))
        except (Aborted, DeadlineExceeded, ServiceUnavailable) as error:
            if attempt == 2:
                raise BackendError('Draft 조회가 일시적으로 지연되었습니다. 잠시 후 다시 시도하세요.',
                                   status_code=503, code='draft_snapshot_busy') from error
            time.sleep(0.05 * (attempt + 1))


def _execute_in_store(data, store, gm_id, side, kind, request, user_id, batch_versions=()):
    body = request.model_dump(mode='json')
    command_id = body.pop('commandId')
    base_revision = body.pop('baseRevision')
    digest = sha256(json.dumps({'kind': kind, 'body': body}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    root = store.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    receipt_ref = root.collection('commands').document(command_id)
    receipt = receipt_ref.get().to_dict()
    current = root.get().to_dict() or {}
    if receipt:
        if receipt['uid'] != user_id or receipt['digest'] != digest:
            raise BackendError('Command ID was reused with different input', status_code=409, code='command_id_reused')
        return receipt, None
    participant = (current.get('participants') or {}).get(user_id) or {}
    role = participant.get('role')
    if role not in {'primary', 'assistant', 'manager'}:
        raise BackendError('Join the Draft before editing', status_code=403, code='participant_required')
    control = current.get('primaryUid') == user_id or (role == 'manager' and not current.get('primaryUid'))
    scope = body.get('syncScope', 'checkpoint' if kind == 'draft' else 'setup')
    if kind == 'setup' and scope == 'setup' and not control:
        raise BackendError('Only the primary analyst can change setup', status_code=403, code='primary_required')
    if kind == 'draft' and scope in {'state', 'checkpoint'} and not control:
        raise BackendError('Only the primary analyst can change lifecycle', status_code=403, code='primary_required')
    payload = body['payload']
    if payload.get('recorderLevel') != participant.get('level', 'advanced'):
        raise BackendError('Recorder level does not match the participant', status_code=403, code='recorder_level_mismatch')
    if payload['gmId'] != gm_id or payload['side'] != side:
        raise BackendError('Draft target mismatch', status_code=422, code='draft_target_mismatch')
    # Tombstoned final Drafts cannot be resurrected by an offline command.
    if current.get('liveClosed'):
        raise BackendError('This input session has been finalized', status_code=409, code='room_closed')
    if current.get('liveClosing') or current.get('liveRestoring'):
        raise BackendError('Final update is in progress', status_code=409, code='room_closing')
    if body.get('roomEpoch') != int(current.get('roomEpoch') or 0):
        raise BackendError('This command belongs to an earlier input session', status_code=409, code='room_changed')
    if kind == 'draft' and scope != 'checkpoint' and not current.get('payload'):
        raise BackendError('The primary analyst has not prepared the Draft yet', status_code=503, code='draft_not_ready')
    revision = int(current.get('revision') or 0)
    if base_revision is None:
        raise BackendError('A server revision is required', status_code=422, code='base_revision_required')
    if base_revision > revision:
        raise BackendError('Draft revision changed', status_code=409, code='draft_conflict')
    targets = []
    if kind == 'draft':
        if scope in {'state', 'state_records', 'checkpoint'} and control:
            targets.append((int(current.get('stateRevision') or 0), current.get('stateUpdatedBy')))
        if scope in {'records', 'state_records', 'checkpoint'}:
            ids = {str(item['id']) for item in payload['records'] if item.get('id')} | set(body.get('deletedRecordIds', []))
            for key in ids:
                values = root.collection('records').document(key).get().to_dict() or {}
                targets.append((int(values.get('commandRevision') or 0), values.get('updatedBy')))
        if scope == 'cards':
            ids = {str(item['id']) for item in payload['cards'] if item.get('id')} | set(body.get('deletedCardIds', []))
            for key in ids:
                values = root.collection('cards').document(key).get().to_dict() or {}
                targets.append((int(values.get('commandRevision') or 0), values.get('updatedBy')))
    else:
        if scope == 'setup':
            targets.append((int(current.get('setupCommandRevision') or 0), current.get('setupUpdatedBy')))
        for item in body['clientState'].get('subs', []):
            values = root.collection('substitutions').document(item['id']).get().to_dict() or {}
            targets.append((int(values.get('commandRevision') or 0), values.get('updatedBy')))
        for key in body.get('deletedSubIds', []):
            values = root.collection('substitutions').document(key).get().to_dict() or {}
            targets.append((int(values.get('commandRevision') or 0), values.get('updatedBy')))
    if any(version > base_revision and version not in batch_versions for version, owner in targets):
        raise BackendError('The same input was edited by another analyst; reload the server Draft', status_code=409, code='draft_conflict')
    previous_writes = {path: (ref, deepcopy(values)) for path, (ref, values) in store.writes.items()}
    working = copy(data)
    working.db = store
    working.sync_input_summary_from_draft = lambda *args: None
    if kind == 'draft':
        document = working.save_input_draft(gm_id, side, payload=payload, client_state=body['clientState'], user_id=user_id,
            sync_scope=scope, deleted_record_ids=body.get('deletedRecordIds'), deleted_card_ids=body.get('deletedCardIds'),
            cleared_record_player_ids=body.get('clearedRecordPlayerIds'), include_events=False, delta_only=True)
        if scope in {'state', 'state_records', 'checkpoint'} and control:
            root.set({'stateRevision': revision + 1, 'stateUpdatedBy': user_id}, merge=True)
    else:
        working.save_input_setup(gm_id, side, payload=payload, client_state=body['clientState'],
            deleted_sub_ids=body.get('deletedSubIds'), merge_substitutions=scope == 'substitutions', delta_only=True)
        root.set({'revision': revision + 1, 'updatedAt': utc_now(), 'setupCommandRevision': revision + 1, 'setupUpdatedBy': user_id}, merge=True)
        document = root.get().to_dict()
    changed_writes = {path: item for path, item in store.writes.items() if path not in previous_writes or item[1] != previous_writes[path][1]}
    for path, (reference, values) in list(changed_writes.items()):
        if path.rsplit('/', 2)[-2] in {'records', 'cards', 'substitutions'}:
            values.update(commandRevision=revision + 1, updatedBy=user_id)
    event = {'type': 'delta', 'gmId': gm_id, 'side': side, 'revision': revision + 1,
             'roomEpoch': int(current.get('roomEpoch') or 0)}
    for collection, field, deleted_field in [('records', 'records', 'deletedRecordIds'), ('cards', 'cards', 'deletedCardIds'), ('substitutions', 'subs', 'deletedSubIds')]:
        for path, (_, values) in changed_writes.items():
            if path.rsplit('/', 2)[-2] != collection:
                continue
            if values.get('deleted'):
                event.setdefault(deleted_field, []).append(values['id'])
            else:
                item = (working._normalise_draft_record(values['id'], values) if collection == 'records'
                        else working._normalise_draft_card(values['id'], values) if collection == 'cards'
                        else working._normalise_input_subs([values])[0])
                event.setdefault(field, []).append(item)
    saved_root = root.get().to_dict() or {}
    if kind == 'setup' or scope == 'checkpoint':
        setup = working._normalise_input_setup(saved_root.get('setup'))
        if setup:
            event['setup'] = {key: value for key, value in setup.items() if key != 'subs'}
    if kind == 'draft' and scope in {'state', 'state_records', 'checkpoint'} and control:
        event['sharedState'] = saved_root.get('sharedState') or {}
    receipt = {'commandId': command_id, 'savedRevision': revision + 1, 'uid': user_id, 'digest': digest,
               'savedAt': utc_now(), 'event': event, 'roomEpoch': event['roomEpoch']}
    receipt_ref.set(receipt)
    root.set({'lastCommandId': command_id, 'lastEvent': event}, merge=True)
    before = current.get('sharedState') or current.get('payload') or {}
    after = saved_root.get('sharedState') or saved_root.get('payload') or {}
    summary_changed = any(before.get(key) != after.get(key) for key in ('halfStatus', 'homeScore', 'awayScore'))
    return receipt, document if summary_changed else None



def execute_command(data, gm_id, side, kind, request, user_id, *, defer_summary=False):
    @firestore.transactional
    def execute(transaction):
        store = TransactionStore(data.db, transaction)
        result = _execute_in_store(data, store, gm_id, side, kind, request, user_id)
        store.flush()
        return result

    receipt, document = execute(data.db.transaction())
    if document and kind == 'draft':
        if defer_summary:
            _queue_summary(data, gm_id, side)
            return receipt
        # The read model is derived, never part of the command's commit/ACK.
        try:
            data.sync_input_summary_from_draft(gm_id, side, document)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Draft committed; schedule summary refresh failed')
    return receipt


def command_targets(kind, request):
    body = request.model_dump(mode='json')
    scope = body.get('syncScope')
    targets = set()
    if kind == 'draft':
        if scope in {'state', 'state_records', 'checkpoint'}:
            targets.add('state')
        for field, deleted, prefix in [('records', 'deletedRecordIds', 'record'), ('cards', 'deletedCardIds', 'card')]:
            if (field == 'records' and scope in {'records', 'state_records', 'checkpoint'}) or (field == 'cards' and scope in {'cards', 'checkpoint'}):
                targets.update(f'{prefix}:{item["id"]}' for item in body['payload'].get(field, []))
                targets.update(f'{prefix}:{key}' for key in body.get(deleted, []))
    else:
        if scope == 'setup':
            targets.add('setup')
        targets.update(f'sub:{item["id"]}' for item in body.get('clientState', {}).get('subs', []))
        targets.update(f'sub:{key}' for key in body.get('deletedSubIds', []))
    return targets


def execute_batch(data, gm_id, side, commands, user_id):
    if not 1 <= len(commands) <= 20:
        raise BackendError('Batch must contain 1–20 commands', status_code=422, code='batch_size')

    @firestore.transactional
    def execute(transaction):
        store = TransactionStore(data.db, transaction)
        root = store.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
        if hasattr(data.db, 'get_all'):
            references = {root.path: root.raw}
            for kind, request in commands:
                receipt = root.collection('commands').document(request.commandId)
                references[receipt.path] = receipt.raw
                for target in command_targets(kind, request):
                    if ':' not in target:
                        continue
                    prefix, key = target.split(':', 1)
                    ref = root.collection({'record': 'records', 'card': 'cards', 'sub': 'substitutions'}[prefix]).document(key)
                    references[ref.path] = ref.raw
            for snapshot in data.db.get_all(list(references.values()), transaction=transaction):
                store.cache[snapshot.reference.path] = snapshot.to_dict()
        baseline = root.get().to_dict() or {}
        from_revision = int(baseline.get('revision') or 0)
        versions, blocked, results, events = set(), set(), [], []
        summary = False
        for kind, request in commands:
            targets = command_targets(kind, request)
            if '*' in blocked or targets & blocked:
                results.append({'commandId': request.commandId, 'status': 'error', 'code': 'dependency_blocked', 'httpStatus': 409, 'message': 'An earlier dependent command needs resolution'})
                blocked.update(targets)
                continue
            checkpoint = {path: (ref, deepcopy(values)) for path, (ref, values) in store.writes.items()}
            before = int((root.get().to_dict() or {}).get('revision') or 0)
            try:
                receipt, changed = _execute_in_store(data, store, gm_id, side, kind, request, user_id, versions)
                if len(store.writes) > 490:
                    raise BackendError('Batch write limit exceeded', status_code=422, code='command_too_large')
                results.append({'status': 'ack', **receipt})
                versions.add(receipt['savedRevision'])
                if receipt['savedRevision'] > before:
                    events.append(receipt['event'])
                summary = summary or bool(changed)
            except BackendError as error:
                store.writes = checkpoint
                results.append({'commandId': request.commandId, 'status': 'error', 'code': error.code, 'httpStatus': error.status_code, 'message': str(error)})
                blocked.update(targets if error.code in {'draft_conflict', 'primary_required', 'command_too_large'} else {'*'})
        event = None
        if events:
            event = {'type': 'batch', 'gmId': gm_id, 'side': side, 'fromRevision': from_revision,
                     'revision': events[-1]['revision'], 'roomEpoch': events[-1]['roomEpoch'], 'events': events}
            root.set({'lastEvent': event}, merge=True)
        store.flush()
        return results, event, summary

    results, event, summary = execute(data.db.transaction())
    if summary:
        _queue_summary(data, gm_id, side)
    return {'results': [{key: value for key, value in item.items() if key not in {'uid', 'digest', 'savedAt'}} for item in results], 'event': event}


def update_presence(data, gm_id, side, uid, session_id, connected):
    root = data.db.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    reference = root.collection('presence').document(session_id)

    @firestore.transactional
    def update(transaction):
        draft = root.get(transaction=transaction).to_dict() or {}
        if uid not in (draft.get('participants') or {}):
            raise BackendError('Join the Draft first', status_code=403, code='participant_required')
        previous = reference.get(transaction=transaction).to_dict() or {}
        if previous and previous.get('uid') != uid:
            raise BackendError('Presence session belongs to another user', status_code=403, code='session_owner_required')
        now = utc_now()
        transaction.set(reference, {'uid': uid, 'connected': connected, 'lastSeenAt': now, 'expiresAt': now + timedelta(seconds=30)})
    update(data.db.transaction())


def room_state(data, gm_id, side):
    root = data.db.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    draft = root.get().to_dict() or {}
    now = utc_now()
    online = {values['uid'] for snapshot in root.collection('presence').stream()
              if (values := snapshot.to_dict() or {}).get('connected') and values.get('expiresAt', now) > now}
    return {'revision': int(draft.get('revision') or 0), 'roomEpoch': int(draft.get('roomEpoch') or 0), 'closed': bool(draft.get('liveClosed')), 'primaryUid': draft.get('primaryUid'),
            'participants': {uid: {**values, 'online': uid in online} for uid, values in (draft.get('participants') or {}).items()}}


def finalize_command(data, gm_id, side, request, uid, promote):
    """Freeze the Draft before the existing chunked RAW promotion.

    A durable lease prevents two workers promoting at once. A failed/crashed
    promotion resumes under the same ID; RAW replacement is deterministic.
    The Draft is closed only after RAW and the final lobby snapshot succeed.
    """
    root = data.db.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    receipt_ref = root.collection('commands').document(request.commandId)

    @firestore.transactional
    def claim(transaction):
        receipt = receipt_ref.get(transaction=transaction).to_dict() or {}
        draft = root.get(transaction=transaction).to_dict() or {}
        if receipt:
            if receipt.get('uid') != uid or receipt.get('kind') != 'finalize':
                raise BackendError('Command ID already used', status_code=409, code='command_id_reused')
            if receipt.get('result'):
                return receipt['result']
        participant = (draft.get('participants') or {}).get(uid) or {}
        role = participant.get('role')
        if draft.get('primaryUid') != uid and not (role == 'manager' and not draft.get('primaryUid')):
            raise BackendError('Only the primary analyst can finalize', status_code=403, code='primary_required')
        if not receipt and (request.roomEpoch != int(draft.get('roomEpoch') or 0) or request.baseRevision != int(draft.get('revision') or 0)):
            raise BackendError('Reload the server Draft before finalizing', status_code=409, code='draft_conflict')
        if draft.get('liveClosed') or draft.get('liveRestoring') or (draft.get('liveClosing') and draft['liveClosing'] != request.commandId):
            raise BackendError('Room is closed or another final update is running', status_code=409, code='room_closed')
        now = utc_now()
        if receipt.get('leaseUntil') and receipt['leaseUntil'] > now:
            raise BackendError('This final update is still running', status_code=503, code='command_in_progress')
        if participant.get('level', 'advanced') != 'advanced' or (draft.get('payload') or {}).get('recorderLevel') != 'advanced':
            raise BackendError('Basic input requires administrator approval', status_code=403, code='basic_approval_required')
        transaction.set(root, {'liveClosing': request.commandId}, merge=True)
        transaction.set(receipt_ref, {'uid': uid, 'kind': 'finalize', 'commandId': request.commandId, 'leaseUntil': now + timedelta(minutes=10)}, merge=True)
        return None

    result = claim(data.db.transaction())
    if result:
        return result
    try:
        draft = data.get_input_draft(gm_id, side)
        result = promote(draft)
        result.update(commandId=request.commandId, savedRevision=int(draft.get('revision') or 0) + 1)

        @firestore.transactional
        def finish(transaction):
            current = root.get(transaction=transaction).to_dict() or {}
            if current.get('liveClosing') != request.commandId:
                raise BackendError('Final update ownership changed', status_code=409, code='room_changed')
            transaction.set(root, {'liveClosed': True, 'liveClosing': None, 'payload': None, 'clientState': {},
                'revision': result['savedRevision'], 'roomEpoch': int(current.get('roomEpoch') or 0) + 1,
                'updatedAt': utc_now()}, merge=True)
            transaction.set(receipt_ref, {'result': result, 'leaseUntil': utc_now()}, merge=True)
        finish(data.db.transaction())
        return result
    except Exception:
        receipt_ref.set({'leaseUntil': utc_now()}, merge=True)
        raise


def restore_command(data, gm_id, side, request, uid):
    root = data.db.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    receipt_ref = root.collection('commands').document(request.commandId)

    @firestore.transactional
    def claim(transaction):
        receipt = receipt_ref.get(transaction=transaction).to_dict() or {}
        draft = root.get(transaction=transaction).to_dict() or {}
        if receipt:
            if receipt.get('uid') != uid or receipt.get('kind') != 'restore':
                raise BackendError('Command ID already used', status_code=409, code='command_id_reused')
            if receipt.get('savedRevision') is not None:
                return receipt
        participant = (draft.get('participants') or {}).get(uid) or {}
        if participant.get('role') not in {'primary', 'manager'} or participant.get('level', 'advanced') != 'advanced':
            raise BackendError('Only an authorized advanced analyst can restore RAW', status_code=403, code='primary_required')
        if not receipt and (request.roomEpoch != int(draft.get('roomEpoch') or 0) or request.baseRevision != int(draft.get('revision') or 0)):
            raise BackendError('Reload the server state before restoring RAW', status_code=409, code='draft_conflict')
        if draft.get('liveClosing'):
            raise BackendError('Final update is running', status_code=409, code='room_closing')
        now = utc_now()
        if draft.get('liveRestoring') and draft['liveRestoring'] != request.commandId:
            raise BackendError('Another RAW restoration is running', status_code=503, code='command_in_progress')
        if receipt.get('leaseUntil') and receipt['leaseUntil'] > now:
            raise BackendError('RAW restoration is still running', status_code=503, code='command_in_progress')
        if draft.get('payload') and not draft.get('liveClosed') and not draft.get('liveRestoring'):
            receipt = {'uid': uid, 'kind': 'restore', 'commandId': request.commandId, 'savedRevision': int(draft.get('revision') or 0)}
            transaction.set(receipt_ref, receipt)
            return receipt
        transaction.set(root, {'liveRestoring': request.commandId}, merge=True)
        transaction.set(receipt_ref, {'uid': uid, 'kind': 'restore', 'commandId': request.commandId, 'leaseUntil': now + timedelta(minutes=10)}, merge=True)
        return None

    receipt = claim(data.db.transaction())
    if receipt:
        return receipt
    try:
        data.restore_input_draft_from_raw(gm_id, side, user_id=uid)

        @firestore.transactional
        def finish(transaction):
            draft = root.get(transaction=transaction).to_dict() or {}
            if draft.get('liveRestoring') != request.commandId:
                raise BackendError('RAW restoration ownership changed', status_code=409, code='room_changed')
            revision = int(draft.get('revision') or 0) + 1
            receipt = {'uid': uid, 'kind': 'restore', 'commandId': request.commandId, 'savedRevision': revision, 'leaseUntil': utc_now()}
            transaction.set(root, {'liveRestoring': None, 'revision': revision}, merge=True)
            transaction.set(receipt_ref, receipt, merge=True)
            return receipt
        return finish(data.db.transaction())
    except Exception:
        receipt_ref.set({'leaseUntil': utc_now()}, merge=True)
        raise
