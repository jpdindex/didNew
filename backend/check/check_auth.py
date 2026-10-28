"""Authentication regressions without Firebase or delivery writes."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.api_aifootballx import Operator
from backend.system import system_firestore as firestore


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
