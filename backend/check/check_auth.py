"""Authentication regressions without Firebase or delivery writes."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from backend.api.api_aifootballx import Operator
from backend.system import system_firestore as firestore


def test_short_login_uses_explicit_uid_mapping_and_current_email(monkeypatch):
    from backend.api import api_login
    from unittest.mock import Mock

    db = Mock()
    reference = db.collection.return_value.document.return_value
    reference.get.return_value.to_dict.return_value = {'uid': 'real-account'}
    monkeypatch.setattr(api_login, 'get_firestore_client', lambda: db)
    account = SimpleNamespace(uid='real-account', email='actual@example.com', disabled=False)
    lookup = Mock(return_value=account)
    monkeypatch.setattr(api_login.auth, 'get_user', lookup)
    app = object()
    assert api_login._find_account('김태호', app) == ('real-account', 'actual@example.com')
    db.collection.assert_called_with('loginIds')
    db.collection.return_value.document.assert_called_with('김태호')
    lookup.assert_called_with('real-account', app=app)
    account.email = 'changed@example.com'
    assert api_login._find_account('김태호', app)[1] == 'changed@example.com'
    account.disabled = True
    assert api_login._find_account('김태호', app) is None
    reference.get.return_value.to_dict.return_value = {}
    assert api_login._find_account('unregistered-email-prefix', app) is None
    lookup.reset_mock()
    for invalid in ('', '../other', '.', '..', 'actual@example.com'):
        assert api_login._find_account(invalid, app) is None
    lookup.assert_not_called()


def test_recorder_profile_uses_registered_name_for_email_login(monkeypatch):
    from backend.api import api_match_input
    from unittest.mock import Mock

    data = Mock()
    data.ensure_recorder_profile.return_value = {'name': '김태호', 'level': 'advanced'}
    monkeypatch.setattr(api_match_input, 'JpdDidData', lambda: data)
    monkeypatch.setattr(api_match_input, 'registered_login_name', lambda uid: '김태호')
    user = firestore.CurrentUser(uid='real-account', claims={'email': 'actual@example.com'})
    result = api_match_input.ensure_recorder_profile(api_match_input.RecorderProfileRequest(name='actual@example.com'), user)
    assert result['name'] == '김태호'
    data.ensure_recorder_profile.assert_called_once_with('real-account', name='김태호')


def test_recorder_profile_without_alias_keeps_email_login(monkeypatch):
    from backend.api import api_match_input
    from unittest.mock import Mock

    data = Mock()
    data.ensure_recorder_profile.return_value = {'name': 'actual@example.com', 'level': 'basic'}
    monkeypatch.setattr(api_match_input, 'JpdDidData', lambda: data)
    monkeypatch.setattr(api_match_input, 'registered_login_name', lambda uid: None)
    user = firestore.CurrentUser(uid='real-account', claims={'email': 'actual@example.com'})
    api_match_input.ensure_recorder_profile(api_match_input.RecorderProfileRequest(name='untrusted-name'), user)
    data.ensure_recorder_profile.assert_called_once_with('real-account', name='actual@example.com')


def test_invalid_login_does_not_echo_or_log_password(caplog):
    from backend.app import app

    password = 'DO-NOT-LOG-THIS-' * 300

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            result = await client.post('/api/v1/auth/short-login', json={
                'loginId': 'Analyst', 'password': password, 'apiKey': 'test-key',
            })
            assert result.status_code == 422
            assert password not in result.text
            assert 'input' not in str(result.json())

    asyncio.run(check())
    assert password not in caplog.text


@pytest.mark.parametrize('result,claims,accepted', [
    ({'idToken': 'firebase-id-token'}, {'uid': 'account', 'email': 'account@company.example'}, True),
    ({'error': {'message': 'INVALID_PASSWORD'}}, {}, False),
    ({'mfaPendingCredential': 'challenge'}, {}, False),
    ({'idToken': 'firebase-id-token'}, {'uid': 'other', 'email': 'account@company.example'}, False),
])
def test_short_login_issues_token_only_for_verified_account(monkeypatch, result, claims, accepted):
    from backend.api import api_login

    monkeypatch.setattr(api_login, 'ensure_firebase_app', lambda: 'firebase-app')
    monkeypatch.setattr(api_login, '_find_account', lambda *_: ('account', 'account@company.example'))

    def verify_password(url, *, params, json, timeout):
        assert json == {'email': 'account@company.example', 'password': 'test-password', 'returnSecureToken': True}
        return httpx.Response(400 if 'error' in result else 200, json=result)

    def verify_token(token, *, app, check_revoked):
        assert (token, app, check_revoked) == ('firebase-id-token', 'firebase-app', True)
        return claims

    def create_token(uid, *, app):
        assert accepted and uid == 'account' and app == 'firebase-app'
        return b'custom-token'

    monkeypatch.setattr(api_login.httpx, 'post', verify_password)
    monkeypatch.setattr(api_login.auth, 'verify_id_token', verify_token)
    monkeypatch.setattr(api_login.auth, 'create_custom_token', create_token)
    request = api_login.ShortLoginRequest(loginId='account', password='test-password', apiKey='test-key')
    response = Response()
    if accepted:
        assert api_login.short_login(request, response) == {'token': 'custom-token'}
        assert response.headers['cache-control'] == 'no-store'
    else:
        with pytest.raises(firestore.BackendError) as failure:
            api_login.short_login(request, response)
        assert failure.value.status_code == 401


@pytest.mark.parametrize('environment', ['local', 'production'])
@pytest.mark.parametrize('key,expected', [('test-key', 200), ('wrong-key', 401), (None, 401)])
def test_swagger_key_authentication_and_operator_access(environment, key, expected):
    app = FastAPI()
    settings = SimpleNamespace(app_env=environment, swagger_api_key='test-key', cors_origins=[])
    app.dependency_overrides[firestore.get_settings] = lambda: settings

    @app.exception_handler(firestore.BackendError)
    async def error_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={'code': exc.code})

    @app.get('/operator')
    def operator(user: Operator):
        return {'auth': user.claims['auth']}

    with TestClient(app) as client:
        response = client.get('/operator', headers={'X-Swagger-Key': key} if key else {})
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {'auth': 'swagger_api_key'}


def test_production_bearer_auth_still_works_without_swagger_key(monkeypatch):
    settings = SimpleNamespace(app_env='production', swagger_api_key=None, cors_origins=['http://localhost:3000'])
    from starlette.requests import Request
    monkeypatch.setattr(firestore, 'ensure_firebase_app', lambda settings: 'firebase-app')
    calls = []
    def verify(token, app):
        calls.append((token, app))
        return {'uid': 'admin-user', 'admin': True}
    monkeypatch.setattr(firestore.auth, 'verify_id_token', verify)
    request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer test-token')]})
    user = firestore.get_current_user(request, None, settings)
    assert user.uid == 'admin-user' and user.claims['admin'] is True
    assert calls == [('test-token', 'firebase-app')]
    # A local frontend header must not bypass production authentication.
    request = Request({'type': 'http', 'headers': [(b'origin', b'http://localhost:3000'),
                                                   (b'x-did-local-user', b'admin-user')]})
    with pytest.raises(firestore.AuthenticationError):
        firestore.get_current_user(request, 'any-key', settings)


def test_management_api_enforces_account_access_without_blocking_analyst_tools(monkeypatch):
    from backend.api import api_match_input, api_player_requests
    from backend.app import app

    settings = SimpleNamespace(
        app_env='local', swagger_api_key='test-key', cors_origins=['http://localhost:3000'],
    )
    monkeypatch.setitem(app.dependency_overrides, firestore.get_settings, lambda: settings)
    monkeypatch.setattr(firestore, 'ensure_firebase_app', lambda settings: None)
    monkeypatch.setattr(api_player_requests, 'ensure_firebase_app', lambda settings: None)
    claims = {
        'analyst': {'uid': 'analyst', 'email': 'analyst@example.test', 'admin': True},
        'manager': {'uid': 'manager', 'email': 'jpdindex@gmail.com'},
    }
    monkeypatch.setattr(firestore.auth, 'verify_id_token', lambda token, app: claims[token])
    monkeypatch.setattr(api_match_input, 'JpdDidData', lambda: SimpleNamespace(
        list_input_assignments=lambda: [],
        release_input_assignment=lambda *args, **kwargs: {},
        build_analyst_dashboard=lambda **kwargs: {},
    ))
    monkeypatch.setattr(api_player_requests, 'PlayerRequestService', lambda: SimpleNamespace(
        list=lambda user: [],
    ))

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test',
        ) as client:
            base = '/api/v1'
            analyst = {'Authorization': 'Bearer analyst'}
            manager = {'Authorization': 'Bearer manager', 'Origin': 'http://localhost:3000'}
            restricted = (
                ('GET', '/match-input/approvals'),
                ('POST', '/match-input/approvals/test-match/H/promote'),
                ('GET', '/match-input/assignments'),
                ('DELETE', '/match-input/drafts/test-match/H/participants/analyst'),
                ('POST', '/match-input/schedules'),
                ('GET', '/player-drafts'),
                ('GET', '/legacy-import/status'),
                ('GET', '/match-kpis/status'),
                ('GET', '/match-ratings/status'),
                ('GET', '/aifootballx/send/status'),
            )
            for method, path in restricted:
                response = await client.request(method, base + path, headers=analyst)
                assert response.status_code == 403, path

            for path in ('/player-requests', '/match-input/analyst-dashboard'):
                assert (await client.get(base + path, headers=analyst)).status_code == 200
            assert (await client.get(base + '/match-input/assignments', headers=manager)).status_code == 200
            assert (await client.delete(
                base + '/match-input/drafts/test-match/H/participants/analyst', headers=manager,
            )).status_code == 200
            local_headers = {'Origin': 'http://localhost:3000', 'X-Did-Local-User': 'manager'}
            assert (await client.get(base + '/match-input/assignments', headers=local_headers)).status_code == 401
            assert (await client.get(
                base + '/legacy-import/status', headers={'X-Swagger-Key': 'test-key'},
            )).status_code == 200

    asyncio.run(check())
