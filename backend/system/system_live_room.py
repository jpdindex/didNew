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
import logging
import os
import time

from firebase_admin import firestore
from google.api_core.exceptions import Aborted, DeadlineExceeded, ServiceUnavailable

from backend.system.system_firestore import BackendError, utc_now


logger = logging.getLogger(__name__)


def draft_context(document, uid):
    """Capture admission state without tokens, names or input payloads."""
    participants = document.get('participants') or {}
    return {
        'participantPresent': uid in participants,
        'participantUids': sorted(participants),
        'primaryUid': document.get('primaryUid'),
        'revision': document.get('revision', 0),
        'roomEpoch': document.get('roomEpoch', 0),
        'liveClosed': bool(document.get('liveClosed')),
    }


def log_draft_event(event, gm_id, side, *, level=logging.INFO, **fields):
    logger.log(level, 'Draft admission %s', json.dumps({
        'event': event, 'gmId': gm_id, 'side': side,
        'diagnosticsVersion': 1,
        'draftPath': f'inputDrafts/{gm_id}_{side}',
        'build': os.environ.get('RENDER_GIT_COMMIT') or os.environ.get('GIT_COMMIT') or 'local',
        'instance': os.environ.get('RENDER_INSTANCE_ID') or str(os.getpid()),
        **fields,
    }, ensure_ascii=False, default=str, sort_keys=True))


def require_draft_writer(current, user_id, *, control=False):
    participant = (current.get('participants') or {}).get(user_id) or {}
    role = participant.get('role')
    if role not in {'primary', 'assistant', 'manager'}:
        raise BackendError('Join the Draft before editing', status_code=403, code='participant_required')
    owns_lifecycle = current.get('primaryUid') == user_id or (role == 'manager' and not current.get('primaryUid'))
    if control and not owns_lifecycle:
        raise BackendError('Only the primary analyst can change setup or lifecycle', status_code=403, code='primary_required')
    return owns_lifecycle


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


def read_snapshot(data, gm_id, side, *, include_setup=True, include_cards=True):
    @firestore.transactional
    def read(transaction):
        working = copy(data)
        working.db = TransactionStore(data.db, transaction)
        document = working.get_input_draft(gm_id, side, include_cards=include_cards)
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


def read_lobby_snapshot(data, gm_id):
    """Read saved setup and clock consistently, excluding ACT/card history."""
    @firestore.transactional
    def read(transaction):
        working = copy(data)
        working.db = TransactionStore(data.db, transaction)
        references = {side: working._input_setup_reference(gm_id, side) for side in ('H', 'A')}
        # Both roots belong to the same read-only transaction and share one
        # RPC. Seed its cache before resolving setup/substitution documents.
        for snapshot in data.db.get_all([reference.raw for reference in references.values()], transaction=transaction, retry=None, timeout=10):
            working.db.cache[snapshot.reference.path] = snapshot.to_dict()
        heads, setups = {}, {}
        for side in ('H', 'A'):
            reference = references[side]
            head = reference.get().to_dict() or {}
            heads[side] = head
            setups[side] = working._with_input_setup_subs(reference, working._normalise_input_setup(head.get('setup')))
        return heads, setups
    for attempt in range(3):
        try:
            return read(data.db.transaction(read_only=True, max_attempts=1))
        except (Aborted, DeadlineExceeded, ServiceUnavailable) as error:
            if attempt == 2:
                raise BackendError('세팅 조회가 일시적으로 지연되었습니다.', status_code=503, code='draft_snapshot_busy') from error
            time.sleep(0.05 * (attempt + 1))


def _newer_edit(incoming, previous):
    if not previous:
        return True
    if incoming['at'] != previous['at']:
        return incoming['at'] > previous['at']
    # A lower same-device sequence stays old even if another device became
    # the winner in between. Retain sequence heads for this exact time tie.
    if incoming['sequence'] < (previous.get('seen') or {}).get(incoming['device'], -1):
        return False
    if incoming['device'] == previous['device'] and incoming['sequence'] != previous['sequence']:
        return incoming['sequence'] > previous['sequence']
    return incoming['received'] >= previous['received']


def _edit_order(stamp, previous=None):
    seen = {}
    if previous and previous['at'] == stamp['at']:
        seen.update(previous.get('seen') or {previous['device']: previous['sequence']})
    seen[stamp['device']] = max(seen.get(stamp['device'], -1), stamp['sequence'])
    return {**stamp, 'seen': seen}


def _ordered_fields(previous, incoming, before, versions, stamp):
    """Merge actual edits only; stale full-row echoes never undo peer fields."""
    result, orders = deepcopy(previous), deepcopy(versions)
    for field, value in incoming.items():
        if before is not None and before.get(field) == value:
            continue
        if _newer_edit(stamp, orders.get(field)):
            result[field] = deepcopy(value)
            orders[field] = _edit_order(stamp, orders.get(field))
    return result, orders


def _merge_command_order(data, store, root, current, body, kind, scope, control, revision, previous_writes, score_only=False):
    """Keep the existing write pipeline; replace only its conflict decision.

    All comparisons use documents already read by the command transaction.
    A fresh revision is also the durable first-admission tie breaker, including
    ignored edits. A repeated command uses its original receipt before here.
    """
    edit = body.get('edit') or {}
    stamp = {'at': int(edit.get('changedAtMs', int(utc_now().timestamp() * 1000))),
             'device': edit.get('deviceId', 'legacy'), 'sequence': int(edit.get('sequence', 0)),
             'received': revision + 1}
    before = edit.get('before') or {}
    acknowledged_paths = set()
    for collection, field, deleted, prefix in [('records', 'records', 'deletedRecordIds', 'record'),
                                             ('cards', 'cards', 'deletedCardIds', 'card'),
                                             ('substitutions', 'subs', 'deletedSubIds', 'sub')]:
        if collection == 'records' and not (kind == 'draft' and scope in {'records', 'state_records', 'checkpoint'}):
            continue
        if collection == 'cards' and not (kind == 'draft' and scope == 'cards'):
            continue
        if collection == 'substitutions' and kind != 'setup':
            continue
        source = body['clientState'] if field == 'subs' else body['payload']
        rows = {str(row['id']): row for row in source.get(field, []) if row.get('id')}
        deletions = set(body.get(deleted, []))
        for key in rows.keys() | deletions:
            reference = root.collection(collection).document(key)
            old = deepcopy(previous_writes[reference.path][1] if reference.path in previous_writes else store.cache.get(reference.path) or {})
            versions = old.get('_editVersions') or {}
            if key in deletions:
                # A late deletion cannot remove a more recently edited record.
                merged, orders = deepcopy(old), deepcopy(versions)
                if all(_newer_edit(stamp, version) for version in versions.values()):
                    merged['deleted'] = True
                    orders['deleted'] = _edit_order(stamp, orders.get('deleted'))
            elif versions.get('deleted') and not _newer_edit(stamp, versions.get('deleted')):
                merged, orders = old, versions
            else:
                wanted = {name: value for name, value in rows[key].items() if name != 'id' and
                          (value is not None or (name == 'playerId' and key in body.get('clearedRecordPlayerIds', [])))}
                baseline = None if old.get('deleted') else before.get(f'{prefix}:{key}')
                merged, orders = _ordered_fields(old, wanted, baseline, versions, stamp)
                if not old or old.get('deleted'):
                    merged['deleted'] = False
                    orders['deleted'] = _edit_order(stamp, orders.get('deleted'))
            merged.update(id=key, _editVersions=orders)
            store.writes[reference.path] = (reference.raw, merged)
            acknowledged_paths.add(reference.path)

    saved = deepcopy(store.writes[root.path][1])
    root_orders = deepcopy(current.get('_editVersions') or {})
    if kind == 'draft' and scope in {'state', 'state_records', 'checkpoint'} and (control or score_only):
        old = current.get('sharedState') or {}
        wanted = saved.get('sharedState') or {}
        # These clock/lifecycle values form one coherent state, not separate
        # independently mixable timestamps and elapsed-second values.
        clock_keys = ('halfStatus', 'seconds', 'h1Seconds', 'h2Seconds', 'clockStartedAt')
        # A score-only command never proposes the clock: the stored clock stays as is.
        score_source = body['payload'] if score_only else wanted
        grouped = {**({} if score_only else {'clock': {key: wanted.get(key) for key in clock_keys}}),
                   **{key: score_source.get(key) for key in ('homeScore', 'awayScore')}}
        prior = {'clock': {key: old.get(key) for key in clock_keys},
                 **{key: old.get(key) for key in ('homeScore', 'awayScore')}}
        baseline = before.get('state')
        if baseline is not None:
            baseline = {'clock': {key: baseline.get(key) for key in clock_keys},
                        **{key: baseline.get(key) for key in ('homeScore', 'awayScore')}}
        combined, root_orders['state'] = _ordered_fields(prior, grouped, baseline, root_orders.get('state') or {}, stamp)
        shared = {**old, **combined['clock'], **{key: combined[key] for key in ('homeScore', 'awayScore')}}
        # Preserve the existing lifecycle rule: a delayed write cannot reopen
        # a finished half, even if its device reports a later edit time.
        if data._lifecycle_rank(shared.get('halfStatus')) < data._lifecycle_rank(old.get('halfStatus')):
            shared.update({key: old.get(key) for key in clock_keys})
        saved['sharedState'] = shared
        saved['clientState'] = {**saved.get('clientState', {}), **shared}
        saved['payload'] = {**saved.get('payload', {}), 'status': shared.get('halfStatus'),
                            **{key: shared[key] for key in ('homeScore', 'awayScore')}}
        halves = deepcopy(saved['payload'].get('halves') or {})
        for half, key in [('H1', 'h1Seconds'), ('H2', 'h2Seconds')]:
            if shared.get(key) is not None:
                halves[half] = {**halves.get(half, {}), 'seconds': shared[key]}
        saved['payload']['halves'] = halves
        if score_only:
            # The pre-merge Draft write used the sender's status; keep the stored lifecycle.
            saved['status'] = shared.get('halfStatus') or current.get('status') or saved.get('status')
    if kind == 'setup' and scope == 'setup' and saved.get('setup'):
        names = ('formationKey', 'fieldSide', 'lineup', 'inputMode')
        old = current.get('setup') or {}
        wanted = saved.get('setup') or {}
        # Keep the stabilized lineup/formation setup as one coherent unit.
        baseline = before.get('setup')
        baseline = None if baseline is None else {'configuration': {key: baseline.get(key) for key in names}}
        combined, root_orders['setup'] = _ordered_fields(
            {'configuration': {key: old.get(key) for key in names}},
            {'configuration': {key: wanted.get(key) for key in names}}, baseline, root_orders.get('setup') or {}, stamp)
        saved['setup'] = {**wanted, **combined['configuration']}
        if combined['configuration'] == {key: old.get(key) for key in names}:
            saved['setup']['baseLineup'] = old.get('baseLineup', old.get('lineup', []))
        else:
            saved['setup']['baseLineup'] = saved['setup'].get('lineup') or []
    saved['_editVersions'] = root_orders
    store.writes[root.path] = (root.raw, saved)
    return acknowledged_paths


def _execute_in_store(data, store, gm_id, side, kind, request, user_id, batch_versions=()):
    body = request.model_dump(mode='json')
    command_id = body.pop('commandId')
    base_revision = body.pop('baseRevision')
    # Keep digests compatible with receipts created before dependency metadata.
    if not body.get('predecessorCommands'):
        body.pop('predecessorCommands', None)
    if not body.get('edit'):
        body.pop('edit', None)
    if not body.get('scoreOnly'):
        body.pop('scoreOnly', None)
    digest = sha256(json.dumps({'kind': kind, 'body': body}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    root = store.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    receipt_ref = root.collection('commands').document(command_id)
    receipt = receipt_ref.get().to_dict()
    current = root.get().to_dict() or {}
    if receipt:
        if receipt['uid'] != user_id or receipt['digest'] != digest:
            raise BackendError('Command ID was reused with different input', status_code=409, code='command_id_reused')
        return receipt, None
    control = require_draft_writer(current, user_id)
    participant = current['participants'][user_id]
    scope = body.get('syncScope', 'checkpoint' if kind == 'draft' else 'setup')
    score_only = kind == 'draft' and bool(body.get('scoreOnly')) and scope in {'state', 'state_records'}
    if kind == 'setup' and scope == 'setup' and not control:
        raise BackendError('Only the primary analyst can change setup', status_code=403, code='primary_required')
    if kind == 'draft' and scope in {'state', 'checkpoint'} and not control and not score_only:
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
        raise BackendError('요청의 기준 버전이 서버보다 앞섭니다.', status_code=409, code='draft_version_ahead')
    targets = []
    if kind == 'draft':
        if scope in {'state', 'state_records', 'checkpoint'} and (control or score_only):
            targets.append(('state', int(current.get('stateRevision') or 0), current.get('stateUpdatedBy'), current.get('stateCommandId')))
        if scope in {'records', 'state_records', 'checkpoint'}:
            ids = {str(item['id']) for item in payload['records'] if item.get('id')} | set(body.get('deletedRecordIds', []))
            for key in ids:
                values = root.collection('records').document(key).get().to_dict() or {}
                targets.append((f'record:{key}', int(values.get('commandRevision') or 0), values.get('updatedBy'), values.get('lastCommandId')))
        if scope == 'cards':
            ids = {str(item['id']) for item in payload['cards'] if item.get('id')} | set(body.get('deletedCardIds', []))
            for key in ids:
                values = root.collection('cards').document(key).get().to_dict() or {}
                targets.append((f'card:{key}', int(values.get('commandRevision') or 0), values.get('updatedBy'), values.get('lastCommandId')))
    else:
        if scope == 'setup':
            targets.append(('setup', int(current.get('setupCommandRevision') or 0), current.get('setupUpdatedBy'), current.get('setupCommandId')))
        for item in body['clientState'].get('subs', []):
            values = root.collection('substitutions').document(item['id']).get().to_dict() or {}
            targets.append((f"sub:{item['id']}", int(values.get('commandRevision') or 0), values.get('updatedBy'), values.get('lastCommandId')))
        for key in body.get('deletedSubIds', []):
            values = root.collection('substitutions').document(key).get().to_dict() or {}
            targets.append((f'sub:{key}', int(values.get('commandRevision') or 0), values.get('updatedBy'), values.get('lastCommandId')))
    # Older base revisions are normal collaboration, not a reason to stop.
    # The affected documents above stay point reads for ordered field merging.
    previous_writes = {path: (ref, deepcopy(values)) for path, (ref, values) in store.writes.items()}
    working = copy(data)
    working.db = store
    working.sync_input_summary_from_draft = lambda *args: None
    if kind == 'draft':
        document = working.save_input_draft(gm_id, side, payload=payload, client_state=body['clientState'], user_id=user_id,
            sync_scope=scope, deleted_record_ids=body.get('deletedRecordIds'), deleted_card_ids=body.get('deletedCardIds'),
            cleared_record_player_ids=body.get('clearedRecordPlayerIds'), include_events=False, delta_only=True)
        if scope in {'state', 'state_records', 'checkpoint'} and (control or score_only):
            root.set({'stateRevision': revision + 1, 'stateUpdatedBy': user_id, 'stateCommandId': command_id}, merge=True)
    else:
        working.save_input_setup(gm_id, side, payload=payload, client_state=body['clientState'],
            deleted_sub_ids=body.get('deletedSubIds'), merge_substitutions=scope == 'substitutions', delta_only=True)
        root.set({'revision': revision + 1, 'updatedAt': utc_now()}, merge=True)
        if scope == 'setup':
            root.set({'setupCommandRevision': revision + 1, 'setupUpdatedBy': user_id, 'setupCommandId': command_id}, merge=True)
        document = root.get().to_dict()
    acknowledged_paths = _merge_command_order(data, store, root, current, body, kind, scope, control, revision, previous_writes, score_only)
    changed_writes = {path: item for path, item in store.writes.items() if path in acknowledged_paths or path not in previous_writes or item[1] != previous_writes[path][1]}
    for path, (reference, values) in list(changed_writes.items()):
        if path.rsplit('/', 2)[-2] in {'records', 'cards', 'substitutions'}:
            values.update(commandRevision=revision + 1, updatedBy=user_id, lastCommandId=command_id)
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
    if kind == 'draft' and scope in {'state', 'state_records', 'checkpoint'} and (control or score_only):
        event['sharedState'] = saved_root.get('sharedState') or {}
    receipt = {'commandId': command_id, 'savedRevision': revision + 1, 'uid': user_id, 'digest': digest,
               'savedAt': utc_now(), 'event': event, 'roomEpoch': event['roomEpoch']}
    receipt_ref.set(receipt)
    root.set({'lastCommandId': command_id, 'lastEvent': event}, merge=True)
    before = current.get('sharedState') or current.get('payload') or {}
    after = saved_root.get('sharedState') or saved_root.get('payload') or {}
    summary_changed = any(before.get(key) != after.get(key) for key in ('halfStatus', 'homeScore', 'awayScore'))
    return receipt, saved_root if summary_changed else None



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
    return {'results': [{key: value for key, value in item.items() if key not in {'uid', 'digest', 'savedAt'}} for item in results], 'event': event,
            'serverTimeMs': int(utc_now().timestamp() * 1000)}


def update_presence(data, gm_id, side, uid, session_id, connected):
    root = data.db.collection('inputDrafts').document(data._input_draft_id(gm_id, side))
    reference = root.collection('presence').document(session_id)
    context = {}

    @firestore.transactional
    def update(transaction):
        snapshot = root.get(transaction=transaction)
        draft = snapshot.to_dict() or {}
        if uid not in (draft.get('participants') or {}):
            context.update(draftExists=snapshot.exists, **draft_context(draft, uid))
            raise BackendError('Join the Draft first', status_code=403, code='participant_required')
        previous = reference.get(transaction=transaction).to_dict() or {}
        if previous and previous.get('uid') != uid:
            raise BackendError('Presence session belongs to another user', status_code=403, code='session_owner_required')
        now = utc_now()
        transaction.set(reference, {'uid': uid, 'connected': connected, 'lastSeenAt': now, 'expiresAt': now + timedelta(seconds=30)})
    try:
        update(data.db.transaction())
    except BackendError as exc:
        log_draft_event('presence_rejected', gm_id, side, uid=uid, sessionId=session_id,
                        connected=connected, code=exc.code, **context)
        raise


def lobby_head_state(document):
    keys = {'halfStatus', 'seconds', 'h1Seconds', 'h2Seconds', 'clockStartedAt', 'homeScore', 'awayScore'}
    return {
        'revision': int(document.get('revision') or 0),
        'roomEpoch': int(document.get('roomEpoch') or 0),
        'setupRevision': int((document.get('setup') or {}).get('revision') or 0),
        'closed': bool(document.get('liveClosed')),
        'sharedState': {key: value for key, value in (document.get('sharedState') or {}).items() if key in keys},
    }


def lobby_state(data, gm_id):
    """Two projected head reads, independent of ACT/card/substitution counts."""
    references = {side: data._input_setup_reference(gm_id, side) for side in ('H', 'A')}
    fields = ['revision', 'roomEpoch', 'setup.revision', 'liveClosed', 'sharedState']
    snapshots = {snapshot.reference.path: snapshot for snapshot in data.db.get_all(
        list(references.values()), field_paths=fields, retry=None, timeout=10,
    )}
    result = {}
    for side, reference in references.items():
        snapshot = snapshots.get(reference.path)
        document = snapshot.to_dict() or {} if snapshot else {}
        result[side] = lobby_head_state(document)
    return result


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
