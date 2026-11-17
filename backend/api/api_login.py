from threading import Lock
from time import monotonic

import httpx
from fastapi import APIRouter, Response
from firebase_admin import auth, exceptions
from pydantic import BaseModel, Field, SecretStr

from backend.system.system_firestore import BackendError, ensure_firebase_app


router = APIRouter(prefix='/auth', include_in_schema=False)
_directory_lock = Lock()
_directory_expires = 0.0
_accounts_by_id: dict[str, tuple[str, str] | None] = {}


class ShortLoginRequest(BaseModel):
    loginId: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=4096)
    apiKey: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


def _find_account(login_id: str, firebase_app) -> tuple[str, str] | None:
    global _directory_expires, _accounts_by_id
    with _directory_lock:
        if monotonic() >= _directory_expires:
            accounts: dict[str, tuple[str, str] | None] = {}
            for user in auth.list_users(app=firebase_app).iterate_all():
                if user.email:
                    alias = user.email.split('@', 1)[0].casefold()
                    accounts[alias] = None if alias in accounts else (user.uid, user.email)
            _accounts_by_id = accounts
            _directory_expires = monotonic() + 15
        return _accounts_by_id.get(login_id.casefold())


@router.post('/short-login')
def short_login(request: ShortLoginRequest, response: Response) -> dict[str, str]:
    firebase_app = ensure_firebase_app()
    invalid_credentials = BackendError('아이디 또는 비밀번호를 확인해 주세요. 같은 아이디가 있으면 이메일 전체를 입력해 주세요.', 401, 'invalid_login')
    try:
        account = _find_account(request.loginId.strip(), firebase_app)
        if account is None:
            raise invalid_credentials
        uid, email = account
        verification = httpx.post(
            'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword',
            params={'key': request.apiKey},
            json={'email': email, 'password': request.password.get_secret_value(), 'returnSecureToken': True},
            timeout=8,
        )
        if verification.status_code >= 500:
            raise BackendError('로그인 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.', 503, 'login_unavailable')
        result = verification.json()
        if verification.status_code == 429 or result.get('error', {}).get('message') == 'TOO_MANY_ATTEMPTS_TRY_LATER':
            raise BackendError('로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.', 429, 'login_throttled')
        if verification.is_success and result.get('mfaPendingCredential'):
            raise BackendError('추가 인증이 필요한 계정은 이메일 전체를 입력해 주세요.', 401, 'login_requires_email')
        if not verification.is_success or not result.get('idToken'):
            raise invalid_credentials

        # A browser API key can select any project. Only a token verified by
        # this project's Admin SDK may be exchanged for a custom token.
        claims = auth.verify_id_token(result['idToken'], app=firebase_app, check_revoked=True)
        if claims.get('uid') != uid or str(claims.get('email', '')).casefold() != email.casefold():
            raise invalid_credentials
        token = auth.create_custom_token(uid, app=firebase_app).decode('ascii')
    except (auth.InvalidIdTokenError, auth.UserDisabledError, auth.UserNotFoundError) as error:
        raise invalid_credentials from error
    except (httpx.HTTPError, exceptions.FirebaseError, ValueError) as error:
        raise BackendError('로그인 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.', 503, 'login_unavailable') from error

    response.headers['Cache-Control'] = 'no-store'
    return {'token': token}
