import httpx
from fastapi import APIRouter, Response
from firebase_admin import auth, exceptions
from pydantic import BaseModel, Field, SecretStr
from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.system.system_firestore import BackendError, ensure_firebase_app, get_firestore_client


router = APIRouter(prefix='/auth', include_in_schema=False)


class ShortLoginRequest(BaseModel):
    loginId: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=4096)
    apiKey: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


def _find_account(login_id: str, firebase_app) -> tuple[str, str] | None:
    # Only explicitly registered aliases are accepted. Email prefixes must not
    # silently become a second login identity or collide with somebody's name.
    if not login_id or '/' in login_id or '@' in login_id or login_id in {'.', '..'}:
        return None
    snapshot = get_firestore_client().collection('loginIds').document(login_id).get(retry=None, timeout=10)
    uid = (snapshot.to_dict() or {}).get('uid')
    if not isinstance(uid, str) or not uid:
        return None
    user = auth.get_user(uid, app=firebase_app)
    return (user.uid, user.email) if user.email and not user.disabled else None


def registered_login_name(uid: str) -> str | None:
    aliases = list(get_firestore_client().collection('loginIds').where(
        filter=FieldFilter('uid', '==', uid),
    ).limit(2).stream(retry=None, timeout=10))
    # Enforce one canonical display name per account; ambiguous configuration
    # must not randomly rename a recorder on each login.
    return aliases[0].id if len(aliases) == 1 else None


@router.post('/short-login')
def short_login(request: ShortLoginRequest, response: Response) -> dict[str, str]:
    firebase_app = ensure_firebase_app()
    invalid_credentials = BackendError('아이디 또는 비밀번호를 확인해 주세요.', 401, 'invalid_login')
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
    except (httpx.HTTPError, exceptions.FirebaseError, GoogleAPICallError, ValueError) as error:
        raise BackendError('로그인 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.', 503, 'login_unavailable') from error

    response.headers['Cache-Control'] = 'no-store'
    return {'token': token}
