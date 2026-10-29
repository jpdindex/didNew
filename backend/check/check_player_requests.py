"""Approval and request regressions. No Firebase or operational DB writes."""
from copy import deepcopy
from functools import wraps
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.api import api_player_requests as api
from backend.system import system_player_requests as registration
from backend.system.system_firestore import BackendError, CurrentUser, JpdDidData
from backend.system.system_player_requests import PlayerRegistration, PlayerRequestService, ReviewDecision


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
        return Collection(self.db, self.path, lambda d: (d.get(filter.field_path) in filter.value)
                          if filter.op_string == 'in' else d.get(filter.field_path) == filter.value)

    def stream(self, transaction=None):
        if transaction:
            assert not transaction.writes
        for path, value in list(self.db.documents.items()):
            if path.rsplit('/', 1)[0] == self.path and (not self.predicate or self.predicate(value)):
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


def test_analyst_cannot_approve_even_with_admin_claim(db, payload):
    service, request_id = submitted(db, payload)
    with pytest.raises(BackendError) as error:
        service.review(request_id, ReviewDecision(action='approve', version=1), CurrentUser(uid='other', claims={'admin': True}))
    assert error.value.status_code == 403


def test_duplicate_identity_requires_link_and_keeps_existing_profile(db, payload):
    service, request_id = submitted(db, payload)
    db.documents['players/P'] = {'name': '기존 표기', 'nameFull': 'Jesús Cruz', 'birth': '2007.06.24', 'nation': 'ESP'}
    with pytest.raises(BackendError) as error:
        service.review(request_id, ReviewDecision(action='approve', version=1), manager)
    assert error.value.code == 'duplicate_player'
    result = service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='P'), manager)
    assert result['playerId'] == 'P'
    assert db.documents['players/P']['name'] == '기존 표기'
    assert db.documents['teams/T/squad/P']['name'] == '기존 표기'


def test_same_team_affiliation_is_not_created_twice(db, payload):
    service, request_id = submitted(db, payload)
    db.documents['players/P'] = {'name': 'Jesus Cruz', 'birth': '2007.06.24'}
    db.documents['players/P/contracts/C'] = {'teamId': 'T', 'from': '2026.08.01', 'to': None}
    service.review(request_id, ReviewDecision(action='approve', version=1, existingPlayerId='P'), manager)
    assert 'players/P/contracts/request-' + request_id not in db.documents


def test_other_club_contract_is_not_silently_closed_or_overwritten(db, payload):
    service, request_id = submitted(db, payload)
    db.documents['players/P'] = {'name': 'Jesus Cruz', 'birth': '2007.06.24', 'currentTeamId': 'OTHER'}
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
