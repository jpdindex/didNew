"""Status first and form inputs, matching the existing DID pipelines."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form

from backend.system.system_firestore import RequiredUser, BackendError, utc_now
from backend.system.system_aifootballx import delivery_service
from backend.system.system_aifootballx import season_label
from backend.system.system_player_requests import is_manager
from backend.build.build_legacy_import import normalize_season_id


def delivery_operator(user: RequiredUser):
    if user.claims.get('auth') == 'swagger_api_key' or is_manager(user):
        return user
    raise BackendError('Swagger key or administrator account required', status_code=403, code='aifootballx_operator_required')


Operator = Annotated[object, Depends(delivery_operator)]
router = APIRouter(prefix='/aifootballx', tags=['aifootballx-delivery'])
SourceMode = Annotated[Literal['auto', 'did_only', 'hybrid'], Form(
    description='원천 선택: auto(기본)는 DID 확정 기록을 우선하고 부족한 경기는 기존 SQL 업로드에서 확인합니다. did_only는 DID 기록만 허용합니다. hybrid는 두 원천을 병합하되 같은 경기는 DID로 대체합니다. 전송 전에 필수 값 누락·번호 충돌을 검사해 쓰기 전에 차단하며 결과는 GET 상태 조회에서 확인합니다.')]


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


@router.post('/send/default', status_code=202, summary='라운드 미경기 기본값 비동기 생성')
def send_default(user: Operator,
                 round_no: int = Form(..., alias='round', ge=1, le=38),
                 season: str | None = Form(None, description='생략 시 현재 EPL 시즌'),
                 force: bool = Form(False, description='이미 생성된 미경기 기본값도 다시 계산')):
    if not season:
        now = utc_now()
        start = now.year if now.month >= 7 else now.year - 1
        season = f'{start}{start + 1}'
    service = delivery_service()
    service.send_scope({**input_scope(season, round_no, round_no, None),
                        'mode': 'default', 'force': force}, user.uid)
    return service.public_status(user.uid)


@router.post('/send/matches', status_code=202, summary='경기 상세 비동기 전송',
             description='dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL')
def send(user: Operator, season: str = Form(..., description='대상 시즌. 예: 2026-2027 또는 20262027'),
         round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
         gm_id: str | None = Form(None, max_length=100, description='한 경기만 전송할 때 GM_ID 입력. 비우면 지정 시즌·라운드 범위의 확정 경기를 처리합니다. 기존 업로드와 경기 번호를 사전 대조합니다.'),
         latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id), 'mode':'matches', 'latest':latest,
                        'sourceMode': 'auto'},user.uid)
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
def send_aggregates(user: Operator, season: str = Form(..., description='대상 시즌. 예: 2026-2027 또는 20262027'),
                    round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
                    gm_id: str | None = Form(None, max_length=100, description='수정·추가한 경기의 GM_ID. 집계 계산에는 필요한 이전 경기 이력도 포함하고 영향받는 라운드·시즌 집계를 갱신합니다.'),
                    latest: bool = Form(False, description='신규·변경 문서만 전송'),
                    source_mode: SourceMode = 'auto'):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id), 'mode':'aggregates', 'latest':latest,
                        'sourceMode': source_mode},user.uid)
    return service.public_status(user.uid)


@router.post('/send/all', status_code=202, summary='경기·마스터·집계 ALL 비동기 전송',
             description='dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL, Player, playerDB, Manager, code, pMST, mMST, cAVG, rAVG, rank, sAVG')
def send_all(user: Operator, season: str = Form(..., description='대상 시즌. 예: 2026-2027 또는 20262027'),
             round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
             gm_id: str | None = Form(None, max_length=100, description='상세 전송할 한 경기의 GM_ID. 마스터는 시즌 전체, 집계는 필요한 경기 이력을 포함해 전송 전에 함께 검증합니다.'),
             latest: bool = Form(False, description='신규·변경 문서만 전송'),
             source_mode: SourceMode = 'auto'):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id),
                        'mode':'all', 'unified':True, 'latest':latest, 'sourceMode': source_mode},user.uid)
    return service.public_status(user.uid)
