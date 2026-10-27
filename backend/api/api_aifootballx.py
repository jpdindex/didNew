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


@router.get('/send/status', summary='AIfootballX 생성·검증·전송 상태',
            description='최근 입력의 진행 상태·생성값·검증 결과·전송 성공/실패와 집계 보류 사유를 함께 조회합니다. blockedGroups가 aggregates이면 선수·감독 전송 결과는 counts에서, 집계 보류 사유는 warnings에서 확인합니다.')
def send_status(user: Operator):
    return delivery_service().public_status(user.uid)


@router.post('/preview', status_code=202, summary='AIfootballX 생성·검증 미리보기',
             description='DID 원천을 읽는 비동기 미리보기입니다. Firestore에 쓰지 않습니다. 결과는 위 send/status에서 확인합니다.')
def preview(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
            round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
            gm_id: str | None = Form(None, max_length=100)):
    service = delivery_service()
    service.preview({**input_scope(season,round_from,round_to,gm_id), 'mode':'matches'},user.uid)
    return service.public_status(user.uid)


@router.post('/send/matches', status_code=202, summary='경기 데이터 비동기 전송 / 실패 건 재시도',
             description='선택한 경기 상세만 생성·검증 후 전송합니다. 선수 식별 마스터·감독 이력·시즌 전체 집계는 포함하지 않습니다. 같은 입력으로 실패·미완료 건을 재시도합니다.')
def send(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
         round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
         gm_id: str | None = Form(None, max_length=100)):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id), 'mode':'matches'},user.uid)
    return service.public_status(user.uid)


@router.post('/send/all', status_code=202, summary='경기 외 데이터 ALL 비동기 전송',
             description='시즌 전체 선수 식별·소속·계약 정보, 감독 이력, 시즌 코드와 6개 집계를 생성·검증합니다. 집계 검증이 실패해도 유효한 선수·감독 데이터는 전송하고 집계만 보류합니다. 경기 상세는 전송하지 않으며 이미 동일한 값은 쓰기를 생략합니다.')
def send_all(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025')):
    service = delivery_service()
    service.send_scope({**input_scope(season,None,None,None), 'mode':'all'},user.uid)
    return service.public_status(user.uid)


@router.post('/send/latest', status_code=202, summary='경기 외 데이터 LATEST 변경분 비동기 전송',
             description='해당 시즌 참가 팀·관련 선수·계약과 해당 시즌 대상 문서를 비교하여 신규·변경분만 전송합니다. 이적·감독 교체와 계약 종료를 반영합니다. 집계 검증 실패는 별도 보류하며 경기 상세와 수동 관리 필드는 변경하지 않습니다.')
def send_latest(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025')):
    service = delivery_service()
    service.send_scope({**input_scope(season,None,None,None), 'mode':'latest'},user.uid)
    return service.public_status(user.uid)
