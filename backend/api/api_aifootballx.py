"""Status first and form inputs, matching the existing DID pipelines."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form

from backend.system.system_firestore import RequiredUser, BackendError
from backend.system.system_aifootballx import delivery_service
from backend.system.system_aifootballx import season_label
from backend.build.build_legacy_import import normalize_season_id


def delivery_operator(user: RequiredUser):
    if user.claims.get('auth') == 'swagger_api_key' or user.claims.get('admin') is True or user.claims.get('role') == 'admin':
        return user
    raise BackendError('Swagger key or administrator account required', status_code=403, code='aifootballx_operator_required')


Operator = Annotated[object, Depends(delivery_operator)]
router = APIRouter(prefix='/aifootballx', tags=['aifootballx-delivery'])


def input_scope(season, round_from, round_to, gm_id):
    season_id = normalize_season_id(season)
    try:
        season_label(season_id)
    except ValueError as exc:
        raise BackendError('season must be 2024-2025 or 20242025 format',422,'aifootballx_scope_invalid') from exc
    if round_from is not None and round_to is not None and round_from>round_to:
        raise BackendError('round_from must not exceed round_to',422,'aifootballx_scope_invalid')
    gm_id = (gm_id or '').strip() or None
    if gm_id and (not gm_id.startswith(season_id) or not all(c.isalnum() or c in '_-' for c in gm_id)):
        raise BackendError('gm_id must belong to season and contain no path separator',422,'aifootballx_scope_invalid')
    return {'seasonId':season_id,'roundFrom':round_from,'roundTo':round_to,'matchId':gm_id}


@router.get('/send/status', summary='AIfootballX 전송 상태',
            description='최근 전송 요청의 진행률·검증 결과·컬렉션별 전송 결과를 조회합니다.')
def send_status(user: Operator):
    return delivery_service().public_status(user.uid)


@router.post('/send/matches', status_code=202, summary='경기 상세 비동기 전송',
             description='dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL')
def send(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
         round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
         gm_id: str | None = Form(None, max_length=100),
         latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id), 'mode':'matches', 'latest':latest},user.uid)
    return service.public_status(user.uid)


@router.post('/send/masters', status_code=202, summary='선수·감독 마스터 비동기 전송',
             description='Player, playerDB, Manager, code')
def send_masters(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
                 latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,None,None,None), 'mode':'masters', 'latest':latest},user.uid)
    return service.public_status(user.uid)


@router.post('/send/aggregates', status_code=202, summary='경기·라운드·시즌 집계 비동기 전송',
             description='pMST, mMST, cAVG, rAVG, rank, sAVG')
def send_aggregates(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
                    round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
                    gm_id: str | None = Form(None, max_length=100),
                    latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id), 'mode':'aggregates', 'latest':latest},user.uid)
    return service.public_status(user.uid)


@router.post('/send/all', status_code=202, summary='경기·마스터·집계 ALL 비동기 전송',
             description='dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL, Player, playerDB, Manager, code, pMST, mMST, cAVG, rAVG, rank, sAVG')
def send_all(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
             round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
             gm_id: str | None = Form(None, max_length=100),
             latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id),
                        'mode':'all', 'unified':True, 'latest':latest},user.uid)
    return service.public_status(user.uid)
