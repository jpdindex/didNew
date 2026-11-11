"""Player requests deliberately require Firebase bearer auth, including locally."""
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Security
from firebase_admin import auth

from backend.system.system_firestore import (
    AuthenticationError, CurrentUser, Settings, ensure_firebase_app,
    get_settings, swagger_key_auth,
)
from backend.system.system_player_requests import PlayerRegistration, PlayerDuplicateLookup, PlayerNumberChange, DirectPlayerRegistration, PlayerRequestService, ReviewDecision, duplicate_players, require_manager

router = APIRouter(prefix='/player-requests', tags=['선수 등록 요청'], include_in_schema=False)


def verified_user(request: Request) -> CurrentUser:
    authorization = request.headers.get('Authorization', '')
    if not authorization.startswith('Bearer '):
        raise AuthenticationError('로그인이 필요합니다.')
    try:
        claims = auth.verify_id_token(authorization[7:].strip(), app=ensure_firebase_app(get_settings()))
        return CurrentUser(uid=claims['uid'], claims=claims)
    except Exception as exc:
        raise AuthenticationError('로그인 정보를 확인할 수 없습니다. 다시 로그인하세요.') from exc


User = Annotated[CurrentUser, Depends(verified_user)]


def manager_user(user: User) -> CurrentUser:
    require_manager(user)
    return user


ManagerUser = Annotated[CurrentUser, Depends(manager_user)]


def management_operator(
    request: Request,
    swagger_key: Annotated[str | None, Security(swagger_key_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    """Authenticate existing maintenance clients or a signed-in manager."""
    if swagger_key and settings.swagger_api_key and compare_digest(
        swagger_key.encode(), settings.swagger_api_key.encode()
    ):
        return CurrentUser(uid='local-swagger', claims={'auth': 'swagger_api_key'})
    return manager_user(verified_user(request))


ManagementOperator = Annotated[CurrentUser, Depends(management_operator)]


@router.get('')
def list_requests(user: User):
    return {'requests': PlayerRequestService().list(user)}


@router.post('')
def submit(payload: PlayerRegistration | PlayerNumberChange, user: User):
    return PlayerRequestService().submit(payload, user)


@router.post('/duplicates')
def duplicates(payload: PlayerDuplicateLookup, _: ManagerUser):
    service = PlayerRequestService()
    return {'players': duplicate_players(service.db, payload, payload.lookupPlayerId)}


@router.post('/create-player')
def create_player(payload: DirectPlayerRegistration, user: ManagerUser):
    return PlayerRequestService().create_player(payload, user)


@router.post('/{request_id}/review')
def review(request_id: str, decision: ReviewDecision, user: ManagerUser):
    return PlayerRequestService().review(request_id, decision, user)
