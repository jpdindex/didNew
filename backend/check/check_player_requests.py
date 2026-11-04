"""Approval and request regressions. No Firebase or operational DB writes."""
from copy import deepcopy
from functools import wraps
from types import SimpleNamespace
import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.api import api_player_requests as api
from backend.system import system_player_requests as registration
from backend.system.system_firestore import BackendError, CurrentUser, JpdDidData
from backend.system.system_player_requests import PlayerRegistration, PlayerNumberChange, DirectPlayerRegistration, PlayerRequestService, ReviewDecision


class Snapshot:
    def __init__(self, ref, value):
        self.reference, self.id, self.value = ref, ref.id, deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self.value)


class Reference:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.split('/')[-1]

    def get(self, transaction=None, **kwargs):
        if transaction:
            assert not transaction.writes, 'Firestore transactions must finish reads before writing'
        return Snapshot(self, self.db.documents.get(self.path))

    def collection(self, name):
        return Collection(self.db, f'{self.path}/{name}')

    def set(self, values, **kwargs):
        self.db.documents[self.path] = deepcopy(values)


class Collection:
    def __init__(self, db, path, predicate=None):
        self.db, self.path, self.predicate = db, path, predicate

    def document(self, key=None):
        if key is None:
            self.db.counter += 1
            key = f'new-{self.db.counter}'
        return Reference(self.db, f'{self.path}/{key}')

    def where(self, filter):
        if filter.field_path == '__name__':
            predicate = lambda d: d['__id__'] <= filter.value.id
        elif filter.op_string == 'array_contains':
            predicate = lambda d: filter.value in (d.get(filter.field_path) or [])
        else:
            predicate = lambda d: (d.get(filter.field_path) in filter.value) if filter.op_string == 'in' else d.get(filter.field_path) == filter.value
        return Collection(self.db, self.path, predicate)

    def order_by(self, field, direction):
        assert field == '__name__' and direction == 'DESCENDING'
        self.descending = True
        return self

    def select(self, fields):
        assert fields == []
        return self

    def stream(self, transaction=None):
        if transaction:
            assert not transaction.writes
        entries = list(self.db.documents.items())
        if getattr(self, 'descending', False):
            entries.sort(reverse=True)
        for path, value in entries:
            if path.rsplit('/', 1)[0] == self.path and (not self.predicate or self.predicate({**value, '__id__': path.rsplit('/', 1)[-1]})):
                yield Snapshot(Reference(self.db, path), value)


class Transaction:
    def __init__(self, db):
        self.db, self.writes = db, []

    def set(self, ref, values, merge=False):
        self.writes.append(('set', ref.path, values, merge))

    def create(self, ref, values):
        self.writes.append(('create', ref.path, values, False))

    def update(self, ref, values):
        self.writes.append(('update', ref.path, values, True))

    def commit(self):
        if self.db.fail_commit:
            raise RuntimeError('commit failed')
        changes = deepcopy(self.db.documents)
        for operation, path, values, merge in self.writes:
            assert operation != 'create' or path not in changes
            changes[path] = {**(changes.get(path, {}) if merge else {}), **deepcopy(values)}
        self.db.documents = changes


class MemoryDB:
    def __init__(self):
        self.counter = 0
        self.fail_commit = False
        self.documents = {
            'teams/T': {'name': '세비야', 'currentLeagueId': 'LALIGA'},
            'teams/T/seasons/20262027': {'leagueId': 'LALIGA', 'division': 'D1'},
            'leagues/LALIGA': {'name': '라리가', 'competitionType': 'league'},
            'seasons/20262027': {'name': '2026/27'},
            'matches/M': {'leagueId': 'LALIGA', 'seasonId': '20262027', 'homeTeamId': 'T', 'awayTeamId': 'A', 'date': '2026-10-17'},
        }

    def collection(self, name):
        return Collection(self, name)

    def transaction(self):
        return Transaction(self)


@pytest.fixture
def db(monkeypatch):
    def transactional(fn):
        @wraps(fn)
        def run(tx):
            result = fn(tx)
            tx.commit()
            return result
        return run
    monkeypatch.setattr(registration.firestore, 'transactional', transactional)
    return MemoryDB()


@pytest.fixture
def payload():
    return PlayerRegistration(leagueId='LALIGA', seasonId='20262027', teamId='T', matchId='M', kind='YOUTH',
                              name='헤수스 크루스', nameFull='Jesus Cruz', birth='2007-06-24', nation='ESP',
                              no='29', pos='MF', fromDate='2026-10-17', sourceUrl='https://example.com/player')


analyst = CurrentUser(uid='analyst', claims={'email': 'analyst@example.com', 'name': '분석관'})
manager = CurrentUser(uid='manager', claims={'email': 'jpdindex@gmail.com'})


def submitted(db, payload):
    service = PlayerRequestService(db)
    request_id = service.submit(payload, analyst)['id']
    return service, request_id


def test_submission_only_stages_request_and_owner_reads_only_own(db, payload):
    service, request_id = submitted(db, payload)
    assert not any(key.startswith('players/') for key in db.documents)
    assert [r['id'] for r in service.list(analyst)] == [request_id]
    assert service.list(CurrentUser(uid='other', claims={})) == []
    assert len(service.list(manager)) == 1


def test_approval_atomically_creates_profile_contract_squad_and_cache_revision(db, payload):
    service, request_id = submitted(db, payload)
    result = service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    player_id = result['playerId']
    player = db.documents[f'players/{player_id}']
    contract = db.documents[f'players/{player_id}/contracts/request-{request_id}']
    assert player['birth'] == '2007.06.24' and player['nation'] == 'ESP'
    assert player['currentTeamId'] == 'T' and 'jesus' in player['searchTerms']
    assert contract['from'] == '2026.10.17' and contract['to'] is None
    assert contract['transferType'] == 'YOUTH'
    assert contract['createdBy'] == 'manager'
    assert db.documents[f'teams/T/squad/{player_id}']['no'] == '29'
    assert db.documents['playerRegistrationRosterVersions/T']['version'] == 1
    assert db.documents[f'playerRegistrationRequests/{request_id}']['status'] == 'approved'
    before = deepcopy(db.documents)
    assert service.review(request_id, ReviewDecision(action='approve', version=1), manager) == result
    assert db.documents == before


def test_failed_commit_does_not_partially_register_or_approve(db, payload):
    service, request_id = submitted(db, payload)
    before = deepcopy(db.documents)
    db.fail_commit = True
    with pytest.raises(RuntimeError):
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert db.documents == before


@pytest.mark.parametrize('kind', ['YOUTH', 'TRANSFER', 'LOAN'])
def test_request_kind_is_preserved_as_contract_transfer_type(db, payload, kind):
    changed = PlayerRegistration.model_validate({**payload.model_dump(), 'kind': kind})
    service, request_id = submitted(db, changed)
    result = service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert db.documents[f"players/{result['playerId']}/contracts/request-{request_id}"]['transferType'] == kind


@pytest.mark.parametrize('omit_date', [False, True])
def test_blank_start_date_can_be_submitted_and_completed_during_review(db, payload, omit_date):
    values = {**payload.model_dump(), 'fromDate': ''}
    if omit_date:
        values.pop('fromDate')
    service, request_id = submitted(db, PlayerRegistration.model_validate(values))
    request_path = f'playerRegistrationRequests/{request_id}'
    assert db.documents[request_path]['payload']['fromDate'] == ''
    assert not any(path.startswith('players/') for path in db.documents)
    with pytest.raises(BackendError) as rejected:
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert rejected.value.code == 'start_date_required'
    assert db.documents[request_path]['status'] == 'pending'
    service.review(request_id, ReviewDecision(action='edit', version=1, payload=payload), manager)
    result = service.review(request_id, ReviewDecision(action='approve', version=2), manager)
    contract = db.documents[f"players/{result['playerId']}/contracts/request-{request_id}"]
    assert contract['from'] == payload.fromDate.replace('-', '.')
    assert db.documents[request_path]['submittedPayload']['fromDate'] == ''


def test_league_visibility_does_not_restrict_backend_references(db, payload):
    db.documents['leagues/OTHER'] = {'name': 'Other league', 'competitionType': 'league'}
    db.documents['teams/T']['currentLeagueId'] = 'OTHER'
    db.documents['teams/T/seasons/20262027'] = {'leagueId': 'OTHER'}
    changed = PlayerRegistration.model_validate({**payload.model_dump(), 'leagueId': 'OTHER', 'matchId': '', 'fromDate': ''})
    _, request_id = submitted(db, changed)
    assert db.documents[f'playerRegistrationRequests/{request_id}']['payload']['leagueId'] == 'OTHER'


def test_analyst_cannot_approve_even_with_admin_claim(db, payload):
    service, request_id = submitted(db, payload)
    with pytest.raises(BackendError) as error:
        service.review(request_id, ReviewDecision(action='approve', version=1), CurrentUser(uid='other', claims={'admin': True}))
    assert error.value.status_code == 403


@pytest.mark.parametrize('entry', [None, {'leagueId': 'LALIGA', 'division': 'D2'},
    {'leagueId': 'LALIGA'}, {'division': 'D1', 'leagueId': 'SEGUNDA', 'competitionIds': ['LALIGA'], 'cupIds': ['LALIGA']},
    {'division': 'D1', 'leagueId': 'LALIGA', 'leagueIds': ['SEGUNDA']}])
def test_request_submit_and_approval_require_season_first_division(db, payload, entry):
    service, request_id = submitted(db, payload)
    path = 'teams/T/seasons/20262027'
    if entry is None:
        db.documents.pop(path)
    else:
        db.documents[path] = entry
    before = deepcopy(db.documents)
    with pytest.raises(BackendError) as rejected:
        service.submit(payload, analyst)
    assert rejected.value.code == 'team_season_mismatch'
    with pytest.raises(BackendError) as rejected:
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert rejected.value.code == 'team_season_mismatch'
    assert db.documents == before


def test_duplicate_identity_requires_link_and_keeps_existing_profile(db, payload):
    service, request_id = submitted(db, payload)
    db.documents['players/P'] = {'name': '기존 표기', 'nameFull': 'Jesús Cruz', 'nameFullKey': 'jesuscruz', 'birth': '2007.06.24', 'nation': 'ESP'}
    with pytest.raises(BackendError) as error:
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert error.value.code == 'duplicate_player'
    result = service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='P'), manager)
    assert result['playerId'] == 'P'
    assert db.documents['players/P']['name'] == '기존 표기'
    assert db.documents['teams/T/squad/P']['name'] == '기존 표기'


def test_same_team_affiliation_is_not_created_twice(db, payload):
    service, request_id = submitted(db, payload)
    db.documents['players/P'] = {'name': 'Jesus Cruz', 'nameFull': payload.nameFull, 'birth': '2007.06.24'}
    db.documents['players/P/contracts/C'] = {'teamId': 'T', 'from': '2026.08.01', 'to': None}
    service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='P'), manager)
    assert 'players/P/contracts/request-' + request_id not in db.documents


def test_other_club_contract_is_not_silently_closed_or_overwritten(db, payload):
    service, request_id = submitted(db, payload)
    db.documents['players/P'] = {'name': 'Jesus Cruz', 'nameFull': payload.nameFull, 'birth': '2007.06.24', 'currentTeamId': 'OTHER'}
    db.documents['players/P/contracts/C'] = {'teamId': 'OTHER', 'from': '2026.08.01', 'to': None}
    with pytest.raises(BackendError) as error:
        service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='P'), manager)
    assert error.value.code == 'affiliation_conflict'
    assert db.documents['players/P']['currentTeamId'] == 'OTHER'
    assert db.documents['players/P/contracts/C']['to'] is None


def test_manager_edit_keeps_pending_until_separate_approval(db, payload):
    service, request_id = submitted(db, payload)
    with pytest.raises(BackendError):
        service.review(request_id, ReviewDecision(action='approve', version=2), manager)
    corrected = PlayerRegistration.model_validate({**payload.model_dump(), 'no': '31'})
    with pytest.raises(BackendError):
        service.review(request_id, ReviewDecision(action='edit', version=1, payload=corrected), analyst)
    service.review(request_id, ReviewDecision(action='edit', version=1, payload=corrected), manager)
    saved = db.documents[f'playerRegistrationRequests/{request_id}']
    assert saved['version'] == 2 and saved['status'] == 'pending'
    assert saved['payload']['no'] == '31' and saved['submittedPayload']['no'] == '29'
    assert not any(key.startswith('players/') for key in db.documents)
    with pytest.raises(BackendError):
        service.review(request_id, ReviewDecision(action='edit', version=1, payload=payload), manager)
    result = service.review(request_id, ReviewDecision(action='approve', version=2), manager)
    assert db.documents[f"teams/T/squad/{result['playerId']}"]['no'] == '31'


@pytest.mark.parametrize('action', ['reject', 'needs_info'])
def test_review_only_accepts_edit_and_approval(action):
    with pytest.raises(ValidationError):
        ReviewDecision(action=action, version=1)


@pytest.mark.parametrize('patch', [{'teamId': 'missing'}, {'leagueId': 'missing'}, {'seasonId': '20252026', 'matchId': ''}, {'fromDate': '2026-10-18'}])
def test_invalid_db_references_and_dates_cannot_be_submitted(db, payload, patch):
    with pytest.raises(BackendError):
        PlayerRequestService(db).submit(PlayerRegistration.model_validate({**payload.model_dump(), **patch}), analyst)


@pytest.mark.parametrize('patch', [{'sourceUrl': 'javascript:alert(1)'}, {'pos': 'ST'}, {'birth': '2007-02-30'}, {'seasonId': '20262028'}, {'teamId': 'a/b'}, {'fromDate': '2099-01-01'}])
def test_invalid_form_values_rejected(payload, patch):
    with pytest.raises(ValidationError):
        PlayerRegistration.model_validate({**payload.model_dump(), **patch})


def test_registration_api_does_not_trust_local_uid_header_or_swagger():
    app = FastAPI()
    app.include_router(api.router)

    @app.exception_handler(BackendError)
    async def error_handler(request, error):
        return JSONResponse({'code': error.code}, status_code=error.status_code)

    with TestClient(app) as client:
        assert client.get('/player-requests', headers={'X-Did-Local-User': 'manager', 'X-Swagger-Key': 'local'}).status_code == 401
        assert client.post('/player-requests/create-player', json={}, headers={'X-Did-Local-User': 'manager'}).status_code == 401


def test_registration_verifies_bearer_claims_instead_of_uid_header(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setattr(api, 'ensure_firebase_app', lambda _: 'test-app')
    monkeypatch.setattr(api, 'get_settings', lambda: None)
    calls = []

    def verify(token, app):
        calls.append((token, app))
        return {'uid': 'real-user', 'email': 'analyst@example.com'}

    monkeypatch.setattr(api.auth, 'verify_id_token', verify)
    request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer real-token'), (b'x-did-local-user', b'manager')]})
    user = api.verified_user(request)
    assert user.uid == 'real-user' and not registration.is_manager(user)
    assert calls == [('real-token', 'test-app')]


def test_invalid_registration_date_returns_validation_details_not_500(payload):
    from fastapi.exceptions import RequestValidationError
    from backend.app import request_validation_error_handler
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.verified_user] = lambda: analyst
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    with TestClient(app) as client:
        response = client.post('/player-requests', json={**payload.model_dump(), 'birth': '2007-02-30'})
    assert response.status_code == 422
    assert response.json()['error']['details'][0]['loc'][-1] == 'birth'


def test_reentry_refreshes_approved_roster_without_changing_saved_lineups(db):
    data = object.__new__(JpdDidData)
    data.db = db
    match = SimpleNamespace(date='2026-10-17', leagueId='LALIGA', homeTeamId='T', awayTeamId='A')
    data._legacy_recordings = lambda _: {}
    data._build_input_squads = lambda _: {'H': [{'playerId': 'old'}, {'playerId': 'approved'}], 'A': []}
    path = 'matches/M/inputSnapshots/squads'
    db.documents[path] = {'schemaVersion': 3, 'source': data._input_squad_source(match), 'H': [{'playerId': 'old'}], 'A': [],
                          'lobby': {'H': {'lineup': [{'playerId': 'old', 'slot': 'gk'}]}}}
    assert data._get_or_create_input_squads_snapshot('M', match)[1] is True
    db.documents['playerRegistrationRosterVersions/T'] = {'version': 1}
    squads, cached, snapshot = data._get_or_create_input_squads_snapshot('M', match)
    assert not cached and squads['H'][-1]['playerId'] == 'approved'
    assert snapshot['lobby']['H']['lineup'] == [{'playerId': 'old', 'slot': 'gk'}]
    assert data._get_or_create_input_squads_snapshot('M', match)[1] is True


def direct_payload(payload, request_id='manual-request', **changes):
    return DirectPlayerRegistration.model_validate({**payload.model_dump(), 'kind': 'TRANSFER',
        'matchId': '', 'requestId': request_id, **changes})


def test_approval_and_direct_registration_share_latest_numeric_id_and_atomic_links(db, payload):
    db.documents['players/0000000099'] = {'name': 'Old'}
    db.documents['players/PR-legacy'] = {'name': 'Legacy request'}
    db.documents['players/9random'] = {'name': 'Old random ID'}
    db.documents['playerRegistrationState/numbering'] = {'lastAssigned': 8}
    service, request_id = submitted(db, payload)
    result = service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert result['playerId'] == '0000000100'
    assert db.documents[f'players/0000000100/contracts/request-{request_id}']['teamId'] == 'T'
    assert db.documents['teams/T/squad/0000000100']['contractId'] == f'request-{request_id}'
    assert service.review(request_id, ReviewDecision(action='approve', version=1), manager) == result
    direct = direct_payload(payload, name='Other player', nameFull='Other player')
    assert service.create_player(direct, manager) == {'playerId': '0000000101'}
    before = deepcopy(db.documents)
    assert service.create_player(direct, manager) == {'playerId': '0000000101'}
    assert db.documents == before
    # A later SQL import can advance the numeric ceiling beyond our counter.
    db.documents['players/0000000500'] = {'name': 'New legacy import'}
    next_player = direct_payload(payload, 'next-manual', name='Third', nameFull='Third')
    assert service.create_player(next_player, manager)['playerId'] == '0000000501'
    assert db.documents['playerRegistrationState/numbering']['lastAssigned'] == 501


def test_existing_player_link_does_not_allocate_or_rename_id(db, payload):
    db.documents['players/PR-existing'] = {'name': payload.name, 'nameFull': payload.nameFull, 'birth': payload.birth}
    service, request_id = submitted(db, payload)
    result = service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='PR-existing'), manager)
    assert result['playerId'] == 'PR-existing'
    assert 'playerRegistrationState/numbering' not in db.documents


def test_direct_registration_preserves_optional_profile_fields_and_rolls_back_failure(db, payload):
    service = PlayerRequestService(db)
    direct = direct_payload(payload, nameFull='', birth='', nation='', no='', pos='')
    before = deepcopy(db.documents)
    db.fail_commit = True
    with pytest.raises(RuntimeError): service.create_player(direct, manager)
    assert db.documents == before
    db.fail_commit = False
    assert service.create_player(direct, manager)['playerId'] == '0000000001'
    assert db.documents['players/0000000001']['birth'] is None
    with pytest.raises(BackendError) as denied: service.create_player(direct, analyst)
    assert denied.value.code == 'manager_required'
    changed = direct_payload(payload, name='Changed')
    with pytest.raises(BackendError) as conflict: service.create_player(changed, manager)
    assert conflict.value.code == 'request_conflict'


@pytest.mark.parametrize('same_identity', [False, True])
@pytest.mark.parametrize('other_flow', ['approval', 'direct'])
def test_concurrent_numeric_allocations_retry_without_duplicates(db, payload, monkeypatch, same_identity, other_flow):
    class Conflict(Exception): pass
    original_get, original_commit = Reference.get, Transaction.commit
    def tracked_get(ref, transaction=None, **kwargs):
        if transaction:
            if not hasattr(transaction, 'reads'): transaction.reads = {}
            transaction.reads[ref.path] = deepcopy(db.documents.get(ref.path))
        return original_get(ref, transaction=transaction, **kwargs)
    pending = []
    def commit(tx):
        if pending: pending.pop()()
        if any(db.documents.get(path) != value for path, value in getattr(tx, 'reads', {}).items()):
            raise Conflict()
        original_commit(tx)
    def transactional(fn):
        def run(_):
            for _attempt in range(5):
                tx = db.transaction()
                try:
                    result = fn(tx)
                    tx.commit()
                    return result
                except Conflict: pass
            raise AssertionError('Transaction did not converge')
        return run
    monkeypatch.setattr(Reference, 'get', tracked_get)
    monkeypatch.setattr(Transaction, 'commit', commit)
    monkeypatch.setattr(registration.firestore, 'transactional', transactional)
    service, first = submitted(db, payload)
    other = payload if same_identity else PlayerRegistration.model_validate({**payload.model_dump(), 'name': 'Other', 'nameFull': 'Other'})
    if other_flow == 'approval':
        second = service.submit(other, analyst)['id']
        pending.append(lambda: service.review(second, ReviewDecision(action='approve', version=1), manager))
    else:
        pending.append(lambda: service.create_player(direct_payload(other), manager))
    if same_identity:
        with pytest.raises(BackendError) as duplicate:
            service.review(first, ReviewDecision(action='approve', version=1), manager)
        assert duplicate.value.code == 'duplicate_player'
        assert db.documents[f'playerRegistrationRequests/{first}']['status'] == 'pending'
    else:
        assert service.review(first, ReviewDecision(action='approve', version=1), manager)['playerId'] == '0000000002'
    ids = sorted(path for path in db.documents if re.fullmatch(r'players/[0-9]{10}', path))
    assert len(ids) == (1 if same_identity else 2)


@pytest.fixture
def number_change(db):
    db.documents['players/P'] = {'name': 'Existing player', 'birth': '2000.01.01', 'currentTeamId': 'T', 'currentNo': '10'}
    db.documents['players/P/contracts/C'] = {'teamId': 'T', 'leagueId': 'LALIGA', 'competitionType': 'league',
        'from': '2026.08.01', 'to': None, 'no': '10', 'pos': 'FW', 'transferType': 'TRANSFER'}
    db.documents['teams/T/squad/P'] = {'name': 'Existing player', 'no': '10', 'pos': 'FW', 'contractId': 'C', 'since': '2026.08.01'}
    return PlayerNumberChange(leagueId='LALIGA', seasonId='20262027', teamId='T', playerId='P',
        contractId='C', currentNo='10', newNo='20', changeDate='2026-10-01')


def test_number_request_only_stages_and_approval_splits_number_periods_atomically(db, number_change):
    service = PlayerRequestService(db)
    before_player = deepcopy(db.documents['players/P'])
    result = service.submit(number_change, analyst)
    request_id = result['id']
    assert db.documents['players/P'] == before_player
    assert db.documents['players/P/contracts/C']['no'] == '10'
    assert db.documents[f'playerRegistrationRequests/{request_id}']['requestType'] == 'NUMBER_CHANGE'
    # No previously published RAW or input setup may be modified by approval.
    db.documents['matches/M/recordings/H'] = {'lineup': {'P': {'no': '10'}}}
    db.documents['inputDrafts/M_H'] = {'setup': {'lineup': [{'playerId': 'P', 'no': '10'}]}}
    before_input = {path: deepcopy(value) for path, value in db.documents.items() if path.startswith(('matches/', 'inputDrafts/'))}
    decision = ReviewDecision(action='approve', version=1)
    approved = service.review(request_id, decision, manager)
    assert approved['playerId'] == 'P'
    assert db.documents['players/P/contracts/C']['to'] == '2026.09.30'
    next_id = 'number-request-' + request_id
    assert db.documents[f'players/P/contracts/{next_id}']['from'] == '2026.10.01'
    assert db.documents[f'players/P/contracts/{next_id}']['no'] == '20'
    assert db.documents['players/P']['currentNo'] == '20'
    assert db.documents['teams/T/squad/P']['contractId'] == next_id
    assert db.documents['teams/T/squad/P']['no'] == '20'
    assert db.documents['playerRegistrationRosterVersions/T']['version'] == 1
    assert 'playerRegistrationState/numbering' not in db.documents
    for path, value in before_input.items(): assert db.documents[path] == value
    after = deepcopy(db.documents)
    assert service.review(request_id, decision, manager) == approved
    assert db.documents == after


def test_duplicate_lookup_uses_fullname_only_and_returns_player_ids(db, payload):
    db.documents['players/0000000010'] = {'name': 'Different display', 'nameFull': payload.nameFull, 'birth': '1999.01.01'}
    db.documents['players/0000000011'] = {'name': payload.name, 'nameEn': payload.nameEn, 'nameFull': 'Different Full Name', 'birth': payload.birth}
    db.documents['players/0000000012'] = {'name': payload.name, 'birth': payload.birth}
    db.documents['players/0000000013'] = {'nameFull': 'JESÚS  CRUZ', 'nameFullKey': 'jesuscruz', 'birth': '1998.01.01'}
    db.documents['players/0000000014'] = {'nameFull': 'Jesús-Cruz', 'searchTerms': ['jesuscruz']}
    # An abbreviation indexed as a search term is not a full-name match.
    db.documents['players/0000000015'] = {'nameFull': 'Jesus Cruz Junior', 'searchTerms': ['jesuscruz']}
    matches = registration.duplicate_players(db, payload)
    assert [p['id'] for p in matches] == ['0000000010', '0000000013', '0000000014']
    assert all('sameBirth' not in match for match in matches)
    service, request_id = submitted(db, payload)
    before = deepcopy(db.documents)
    with pytest.raises(BackendError) as rejected:
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert rejected.value.code == 'duplicate_player'
    assert db.documents == before


def test_direct_pid_lookup_validates_legacy_unindexed_fullname_and_rejects_wrong_identity(db, payload):
    db.documents['players/0000000042'] = {'name': 'Legacy', 'nameFull': 'JESÚS CRUZ'}
    # Old documents without normalized indexes can still be confirmed by p_id.
    assert registration.duplicate_players(db, payload) == []
    matches = registration.duplicate_players(db, payload, '42')
    assert matches[0]['id'] == '0000000042'
    db.documents['players/0000000043'] = {'name': payload.name, 'nameFull': 'Someone Else', 'birth': payload.birth}
    with pytest.raises(BackendError) as rejected: registration.duplicate_players(db, payload, '43')
    assert rejected.value.code == 'player_fullname_mismatch'
    with pytest.raises(BackendError) as rejected: registration.duplicate_players(db, payload, 'missing')
    assert rejected.value.code == 'invalid_player'
    service, request_id = submitted(db, payload)
    before = deepcopy(db.documents)
    with pytest.raises(BackendError) as rejected:
        service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='0000000043'), manager)
    assert rejected.value.code == 'player_fullname_mismatch'
    assert db.documents == before
    assert service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='0000000042'), manager)['playerId'] == '0000000042'


def test_fullname_identity_guard_is_independent_of_birth(db, payload):
    service, first = submitted(db, payload)
    service.review(first, ReviewDecision(action='approve', version=1), manager)
    different_birth = PlayerRegistration.model_validate({**payload.model_dump(), 'birth': '2000-01-01'})
    assert registration.registration_identity(payload) == registration.registration_identity(different_birth)
    other = service.submit(different_birth, analyst)['id']
    with pytest.raises(BackendError) as rejected:
        service.review(other, ReviewDecision(action='approve', version=1), manager)
    assert rejected.value.code == 'duplicate_player'


def test_duplicate_lookup_api_returns_fullname_and_pid(db, payload, monkeypatch):
    monkeypatch.setattr(api, 'PlayerRequestService', lambda: PlayerRequestService(db))
    db.documents['players/0000000042'] = {'nameFull': 'JESÚS CRUZ', 'birth': '1999.01.01'}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.verified_user] = lambda: manager
    with TestClient(app) as client:
        response = client.post('/player-requests/duplicates', json={**payload.model_dump(), 'lookupPlayerId': '42'})
    assert response.status_code == 200
    assert response.json()['players'][0]['id'] == '0000000042'


def test_number_approval_failure_is_atomic_and_analyst_cannot_approve(db, number_change):
    service = PlayerRequestService(db)
    request_id = service.submit(number_change, analyst)['id']
    with pytest.raises(BackendError) as denied:
        service.review(request_id, ReviewDecision(action='approve', version=1), analyst)
    assert denied.value.code == 'manager_required'
    before = deepcopy(db.documents)
    db.fail_commit = True
    with pytest.raises(RuntimeError): service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert db.documents == before


@pytest.mark.parametrize('patch,code', [({'playerId': 'missing'}, 'number_contract_mismatch'),
    ({'contractId': 'wrong'}, 'number_contract_mismatch'), ({'currentNo': '9'}, 'number_changed'),
    ({'newNo': '010'}, 'same_number'), ({'changeDate': '2026-07-31'}, 'outside_season')])
def test_invalid_number_request_rejected_without_writes(db, number_change, patch, code):
    payload = PlayerNumberChange.model_validate({**number_change.model_dump(), **patch})
    before = deepcopy(db.documents)
    with pytest.raises(BackendError) as rejected: PlayerRequestService(db).submit(payload, analyst)
    assert rejected.value.code == code
    assert db.documents == before


def test_stale_number_request_and_wrong_payload_type_cannot_publish(db, number_change, payload):
    service = PlayerRequestService(db)
    request_id = service.submit(number_change, analyst)['id']
    with pytest.raises(BackendError) as rejected:
        service.review(request_id, ReviewDecision(action='approve', version=1, payload=payload), manager)
    assert rejected.value.code == 'request_type_mismatch'
    db.documents['players/P/contracts/C']['no'] = '11'
    before = deepcopy(db.documents)
    with pytest.raises(BackendError) as rejected:
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert rejected.value.code == 'number_changed'
    assert db.documents == before


def test_number_request_edit_stays_pending_and_start_date_change_preserves_contract(db, number_change):
    service = PlayerRequestService(db)
    request_id = service.submit(number_change, analyst)['id']
    changed = PlayerNumberChange.model_validate({**number_change.model_dump(), 'newNo': '30', 'changeDate': '2026-08-01'})
    service.review(request_id, ReviewDecision(action='edit', version=1, payload=changed), manager)
    assert db.documents['players/P/contracts/C']['no'] == '10'
    assert db.documents[f'playerRegistrationRequests/{request_id}']['status'] == 'pending'
    service.review(request_id, ReviewDecision(action='approve', version=2), manager)
    assert db.documents['players/P/contracts/C']['no'] == '30'
    assert db.documents['players/P/contracts/C']['to'] is None
    assert len([p for p in db.documents if p.startswith('players/P/contracts/')]) == 1


def test_historical_number_change_does_not_replace_current_team_cache(db, number_change):
    service = PlayerRequestService(db)
    db.documents['players/P/contracts/C']['to'] = '2026.10.15'
    db.documents['players/P']['currentTeamId'] = 'OTHER'
    db.documents['players/P']['currentNo'] = '99'
    db.documents['teams/T/squad/P']['contractId'] = 'later-contract'
    request_id = service.submit(number_change, analyst)['id']
    service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert db.documents['players/P']['currentNo'] == '99'
    assert db.documents['teams/T/squad/P']['contractId'] == 'later-contract'
    assert db.documents[f'players/P/contracts/number-request-{request_id}']['to'] == '2026.10.15'


def test_number_change_api_shares_authenticated_request_list_and_legacy_requests(db, number_change, payload, monkeypatch):
    # Emulate Firestore resolving server timestamps before the list is read.
    monkeypatch.setattr(registration.firestore, 'SERVER_TIMESTAMP', '2026-11-03T00:00:00Z')
    monkeypatch.setattr(api, 'PlayerRequestService', lambda: PlayerRequestService(db))
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.verified_user] = lambda: analyst
    with TestClient(app) as client:
        number_id = client.post('/player-requests', json=number_change.model_dump()).json()['id']
        registration_id = client.post('/player-requests', json=payload.model_dump()).json()['id']
        db.documents[f'playerRegistrationRequests/{registration_id}'].pop('requestType')
        rows = client.get('/player-requests').json()['requests']
        assert {row['id'] for row in rows} == {number_id, registration_id}
        app.dependency_overrides[api.verified_user] = lambda: manager
        result = client.post(f'/player-requests/{number_id}/review', json={'action': 'approve', 'version': 1})
        assert result.status_code == 200 and result.json()['playerId'] == 'P'
