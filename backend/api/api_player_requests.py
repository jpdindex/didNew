"""Player requests deliberately require Firebase bearer auth, including locally."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from firebase_admin import auth

from backend.system.system_firestore import AuthenticationError, CurrentUser, ensure_firebase_app, get_settings
from backend.system.system_player_requests import PlayerRegistration, PlayerRequestService, ReviewDecision, duplicate_players, require_manager

router = APIRouter(prefix='/player-requests', tags=['선수 등록 요청'])


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


@router.get('')
def list_requests(user: User):
    return {'requests': PlayerRequestService().list(user)}


@router.post('')
def submit(payload: PlayerRegistration, user: User):
    return PlayerRequestService().submit(payload, user)


@router.post('/duplicates')
def duplicates(payload: PlayerRegistration, user: User):
    require_manager(user)
    service = PlayerRequestService()
    return {'players': duplicate_players(service.db, payload)}


@router.post('/{request_id}/review')
def review(request_id: str, decision: ReviewDecision, user: User):
    return PlayerRequestService().review(request_id, decision, user)
