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
router = APIRouter(prefix='/aifootballx')
# Keep legacy hybrid clients valid while exposing only the two distinct choices.
SourceMode = Annotated[Literal['auto', 'did_only', 'hybrid'], Form(
    json_schema_extra={'enum': ['auto', 'did_only']},
    description='원천 선택: auto(기본)는 DID 확정 기록을 우선하고 기존 SQL 실데이터가 있는 경기는 보충합니다. did_only는 DID 확정 기록만 사용하며 필요한 기존 SQL 실데이터를 DID로 충족하지 못하면 차단합니다. 등록된 전체 일정에서 미입력 경기는 기본값으로 유지하고, 추가 입력 후 재전송하면 해당 경기와 관련 집계를 갱신합니다. 전송 전에 필수 값 누락·번호 충돌을 검사하며 결과는 GET 상태 조회에서 확인합니다.')]


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


def goalx_scope(season, league_id, round_from, round_to, gm_id):
    if league_id == 'TESTGOAL':
        if season != 'test' or round_from is not None or round_to is not None:
            raise BackendError('TESTGOAL은 season=test, 라운드 범위 없음으로 입력하세요.', 422, 'goalx_scope_invalid')
        if gm_id and ('/' in gm_id or not gm_id.startswith('testgoal-')):
            raise BackendError('올바른 TESTGOAL ID를 입력하세요.', 422, 'goalx_scope_invalid')
        return {'seasonId': 'test', 'roundFrom': None, 'roundTo': None, 'matchId': gm_id or None, 'leagueId': league_id}
    return {**input_scope(season, round_from, round_to, gm_id), 'leagueId': league_id}


@router.get('/send/status', tags=['aifootballx-delivery'], summary='AIfootballX 전송·GoalX 리빌드 상태',
            description='최근 전송 또는 GOALX 평가 리빌드 요청의 진행률·처리 건수·검증 결과·완료·실패 사유를 조회합니다.')
def send_status(user: Operator):
    return delivery_service().public_status(user.uid)


@router.post('/send/cancel', tags=['aifootballx-delivery'], summary='대기 작업 취소',
             description='상태 조회의 jobId를 job_id에 입력합니다. 현재 요청자 소유의 queued 작업만 취소합니다. '
                         '이미 실행을 시작했거나 종료된 작업은 409로 거절하며, 이미 취소한 작업은 성공으로 응답합니다. '
                         'GOALX 리빌드도 같은 API로 취소할 수 있습니다.')
def cancel_send(user: Operator, job_id: str = Form(..., min_length=1, max_length=100)):
    service = delivery_service()
    service.store.cancel_queued(job_id, user.uid)
    return {'jobId': job_id, 'status': 'cancelled'}


@router.post('/send/default', tags=['aifootballx-delivery'], status_code=202, summary='라운드 미경기 기본값 비동기 생성')
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


@router.post('/send/matches', tags=['aifootballx-delivery'], status_code=202, summary='경기 상세 비동기 전송',
             description='dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL')
def send(user: Operator, season: str = Form(..., description='대상 시즌. 예: 2026-2027 또는 20262027'),
         round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
         gm_id: str | None = Form(None, max_length=100, description='한 경기만 전송할 때 GM_ID 입력. 비우면 지정 시즌·라운드 범위의 확정 경기를 처리합니다. 기존 업로드와 경기 번호를 사전 대조합니다.'),
         latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id), 'mode':'matches', 'latest':latest,
                        'sourceMode': 'auto'},user.uid)
    return service.public_status(user.uid)


@router.get('/goalx/status', tags=['Goalx'], summary='GOALX 리빌드·전송 상태',
            description='현재 요청자가 가장 최근에 시작한 GOALX 평가 리빌드 또는 승인 골 전송 한 건만 반환합니다. '
                        '다음 GOALX 작업을 시작하면 이전 작업 대신 새 작업의 상태를 표시합니다. '
                        '응답 맨 위의 status와 reason에서 완료·실패 상태와 실패 사유를 먼저 확인하고, '
                        'operation에서 rebuild/send를 구분합니다. 실행 이력이 없으면 idle입니다. '
                        '다른 경기·마스터·집계 전송은 GOALX 상태를 바꾸지 않습니다. '
                        '입력값은 필요 없으며 관리자 또는 Swagger API 키로 인증합니다.')
def goalx_status(user: Operator):
    service = delivery_service()
    return service.public_status(user.uid, modes=('goalx_rebuild', 'goalx'))


@router.post('/goalx/rebuild', tags=['Goalx'], status_code=202, summary='GOALX 평가 리빌드',
             description='DID에 저장된 분석관별 평가와 좌표·골라인 통과 소요시간으로 분석관 평가 점수와 백분율(100점 총점)을 '
                         '현재 서버 코드 기준으로 다시 계산해 rating 필드에 저장합니다. 대표 승인·원본 평가·좌표·시간은 수정하지 않습니다. '
                         'ShotX 측정값과 대표 승인 결과는 평점 계산에 사용하지 않습니다. SETTINGS는 코드 기준의 관리용 사본으로만 갱신합니다. '
                         '시즌·대회는 필수이며 라운드 범위나 gm_id로 대상을 좁힐 수 있습니다. TESTGOAL은 season=test로 입력합니다. '
                         '202 응답은 비동기 접수이며 GET /goalx/status에서 최근 작업의 진행률·리빌드 건수·데이터 누락·실패 사유를 확인합니다. '
                         '데이터가 부족한 골은 백분율을 미계산 상태로 저장합니다. 완료 후 /send/goalx에서 force=true로 전송하면 '
                         'aifootballx에 결과를 덮어씁니다. 이 리빌드 자체는 aifootballx에 접속하거나 전송하지 않습니다.')
def rebuild_goalx(user: Operator,
                  season: str = Form(..., description='예: 2026-2027 / 20262027. TESTGOAL은 test'),
                  league_id: str = Form('EPL', pattern=r'^[A-Za-z0-9_-]{1,50}$', description='DID 대회 ID'),
                  round_from: int | None = Form(None, ge=1, description='시작 라운드, 포함'),
                  round_to: int | None = Form(None, ge=1, description='끝 라운드, 포함'),
                  gm_id: str | None = Form(None, max_length=100, description='특정 경기 ID. 생략 시 지정 범위 전체')):
    scope = goalx_scope(season, league_id, round_from, round_to, gm_id)
    service = delivery_service()
    service.send_scope({**scope, 'mode': 'goalx_rebuild'}, user.uid)
    return service.public_status(user.uid)


@router.post('/send/goalx', tags=['Goalx'], status_code=202, summary='GoalX 승인 골 비동기 전송',
             description='DID 대표 승인이 유효한 골의 3D 추정 궤적·100점 평점·경기/팀/선수 식별자를 aifootballx의 goalx 컬렉션에 전송합니다. '
                         '기존 ShotX 결과는 이동하지 않습니다. 시즌은 2026-2027 또는 20262027, 대회는 EPL 등을 입력합니다. '
                         'TESTGOAL은 league_id=TESTGOAL, season=test로 입력하며 경기 범위는 비웁니다. '
                         '라운드 범위 또는 gm_id를 지정할 수 있고, 비우면 해당 대회·시즌 전체를 처리합니다. '
                         '동일 골은 갱신하며, 선택 범위의 삭제되거나 승인이 무효화된 골은 공개 해제합니다. '
                         '평점용 거리·시간이나 승인된 3D 좌표가 누락되면 검증 단계에서 전송을 차단합니다. '
                         '승인 좌표에서 골라인 통과 높이와 골대 중앙 방향 대비 평면 발사각을 계산해 metrics에 함께 전송합니다. '
                         '현재 코드 기준으로 저장된 rating이 없거나 오래되었으면 GOALX 평가 리빌드를 먼저 실행해야 합니다. '
                         'latest는 신규·변경분만 전송하며 force=true는 latest보다 우선해 동일 값도 다시 씁니다. '
                         'force도 대표 승인·원본 버전·목적지 동시 변경 검증을 유지하고 DID 관리 필드만 덮어씁니다. '
                         '202 응답은 접수 결과이며 완료·실패는 GET /goalx/status에서 최근 작업의 status와 reason으로 확인합니다. '
                         '쓰기 활성화 설정과 관리자 또는 Swagger API 키가 필요합니다.')
def send_goalx(user: Operator,
               season: str = Form(..., description='예: 2026-2027 / 20262027. TESTGOAL은 test'),
               league_id: str = Form('EPL', pattern=r'^[A-Za-z0-9_-]{1,50}$', description='DID 대회 ID. 예: EPL, TESTGOAL'),
               round_from: int | None = Form(None, ge=1, description='시작 라운드, 포함'),
               round_to: int | None = Form(None, ge=1, description='끝 라운드, 포함'),
               gm_id: str | None = Form(None, max_length=100, description='특정 경기의 DID gm_id. 생략 시 범위 전체'),
               latest: bool = Form(True, description='신규·변경된 골만 전송'),
               force: bool = Form(False, description='FORCE: 같은 결과도 다시 덮어쓰기. latest보다 우선하며 승인 검증은 유지')):
    scope = goalx_scope(season, league_id, round_from, round_to, gm_id)
    service = delivery_service()
    service.send_scope({**scope, 'mode': 'goalx', 'latest': latest, 'force': force}, user.uid)
    return service.public_status(user.uid)


@router.post('/send/masters', tags=['aifootballx-delivery'], status_code=202, summary='선수·감독 마스터 비동기 전송',
             description='Player, playerDB, Manager, code')
def send_masters(user: Operator, season: str = Form(..., description='2024-2025 또는 20242025'),
                 latest: bool = Form(False, description='신규·변경 문서만 전송')):
    service = delivery_service()
    service.send_scope({**input_scope(season,None,None,None), 'mode':'masters', 'latest':latest},user.uid)
    return service.public_status(user.uid)


@router.post('/send/aggregates', tags=['aifootballx-delivery'], status_code=202, summary='경기·라운드·시즌 집계 비동기 전송',
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


@router.post('/send/all', tags=['aifootballx-delivery'], status_code=202, summary='경기·집계 ALL 비동기 전송',
             description='선택한 경기 상세(matches)와 관련 집계(aggregates)만 하나의 비동기 작업으로 검증·전송합니다. '
                         '경기 상세: dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, '
                         'multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL. '
                         '집계: pMST, mMST, cAVG, rAVG, rank, sAVG 및 집계에 필요한 누락 시즌 코드. '
                         '선수·감독 마스터(masters), GoalX, 다음 라운드 기본값(default)은 포함하지 않으며 각각 별도 전송합니다. '
                         '시즌은 필수이고 라운드 범위 또는 gm_id로 대상을 좁힐 수 있습니다. '
                         'latest=true는 신규·변경 문서만 전송합니다. 두 그룹의 검증을 마친 뒤 전송을 시작하며, '
                         '202 응답은 접수 결과입니다. 진행률·검증 결과·완료·실패 사유는 GET /send/status에서 확인합니다.')
def send_all(user: Operator, season: str = Form(..., description='대상 시즌. 예: 2026-2027 또는 20262027'),
             round_from: int | None = Form(None, ge=1, le=38), round_to: int | None = Form(None, ge=1, le=38),
             gm_id: str | None = Form(None, max_length=100, description='상세 전송할 한 경기의 GM_ID. 집계는 해당 경기의 영향을 계산하는 데 필요한 경기 이력을 포함합니다. 마스터와 default는 실행하지 않습니다.'),
             latest: bool = Form(False, description='신규·변경 문서만 전송'),
             source_mode: SourceMode = 'auto'):
    service = delivery_service()
    service.send_scope({**input_scope(season,round_from,round_to,gm_id),
                        'mode':'all', 'unified':True, 'latest':latest, 'sourceMode': source_mode},user.uid)
    return service.public_status(user.uid)
