
"""Manager-only comparison and approval for Transfer Ops deliveries."""
from hmac import compare_digest
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse
from google.api_core.exceptions import GoogleAPICallError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from backend.api.api_player_requests import verified_user
from backend.system.system_firestore import BackendError, CurrentUser, get_settings
from backend.system.system_player_drafts import DraftEdit, PlayerDraftService, MAX_REQUEST_SIZE, MAX_FILES
from backend.system.system_player_requests import require_manager

router = APIRouter(prefix='/player-drafts', tags=['이적 현황 업데이트'])
bearer = HTTPBearer(scheme_name='FirebaseManagerToken', auto_error=False,
                    description='JPD-DID 관리자 계정의 Firebase ID 토큰입니다.')


def manager(request: Request, _: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]) -> CurrentUser:
    user = verified_user(request)
    require_manager(user)
    return user


Manager = Annotated[CurrentUser, Depends(manager)]
JobId = Annotated[str, Path(pattern=r'^transfer-ops-[0-9]+-[0-9a-f]{20}$')]
ItemId = Annotated[str, Path(pattern=r'^(?:transfer|roster)-[0-9]+$')]


@router.post('/receive', status_code=201, include_in_schema=False)
async def receive(request: Request):
    """Accept a Transfer Ops upload using a separate server-to-server credential."""
    expected = get_settings().jpd_did_transfer_token.get_secret_value().strip()
    if not expected:
        raise BackendError('didNew Render에 JPD_DID_TRANSFER_TOKEN을 설정하세요.', 503, 'transfer_not_configured')
    authorization = request.headers.get('Authorization', '')
    if not compare_digest(authorization.encode(), f'Bearer {expected}'.encode()):
        raise BackendError('전송 키가 올바르지 않습니다.', 401, 'invalid_transfer_token')
    length = request.headers.get('Content-Length', '')
    if not length.isascii() or not length.isdigit() or len(length) > 10:
        raise BackendError('전송 요청의 크기를 확인할 수 없습니다.', 411, 'upload_length_required')
    if int(length) > MAX_REQUEST_SIZE:
        raise BackendError('한 번에 전송할 수 있는 엑셀은 합계 50MB까지입니다.', 413, 'upload_too_large')
    async with request.form(max_files=MAX_FILES, max_fields=1, max_part_size=750_000) as form:
        manifest, files = form.get('manifest'), form.getlist('files')
        if not isinstance(manifest, str) or not files or not all(isinstance(file, UploadFile) for file in files):
            raise BackendError('전송 메타데이터와 엑셀 파일이 필요합니다.', 422, 'invalid_delivery')
        return await run_in_threadpool(lambda: PlayerDraftService().receive(manifest, files))


@router.get('', summary='수신한 Transfer Ops 작업 목록')
def list_drafts(_: Manager):
    """전송 건 ID, 대조 상태와 변경 대상 수를 반환합니다."""
    return {'jobs': PlayerDraftService().list()}


@router.get('/{job_id}/items', summary='저장된 선수 대조 결과 조회')
def list_items(job_id: JobId, _: Manager):
    """엑셀 원본값, 현재 DB 값, 관리자 수정값과 반영 상태를 조회합니다."""
    return PlayerDraftService().details(job_id)


@router.post('/{job_id}/compare', summary='확정 엑셀과 선수 DB 대조')
def compare(job_id: JobId, _: Manager):
    """선택한 전송 건의 변경 대상만 읽어 신규·변경·동일·확인 필요를 분류합니다. 재호출하면 미대조 항목과 재대조 필요 항목을 확인합니다."""
    return {'items': PlayerDraftService().compare(job_id)}


@router.post('/{job_id}/compare-progress', summary='선수 대조 진행률 스트림')
def compare_progress(job_id: JobId, _: Manager):
    def events():
        try:
            for progress in PlayerDraftService().compare_steps(job_id):
                yield json.dumps(progress) + '\n'
        except BackendError as error:
            yield json.dumps({'error': error.message}, ensure_ascii=False) + '\n'
        except GoogleAPICallError:
            yield json.dumps({'error': 'Firestore 대조 중 오류가 발생했습니다. 다시 시도하세요.'}, ensure_ascii=False) + '\n'
        else:
            yield '{"complete":true}\n'

    return StreamingResponse(events(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store, no-transform', 'X-Accel-Buffering': 'no'})


@router.delete('/{job_id}/items/{item_id}', summary='수신함의 선수 항목 삭제')
def delete_item(job_id: JobId, item_id: ItemId, _: Manager):
    PlayerDraftService().delete_item(job_id, item_id)
    return {'deleted': True}


@router.delete('/{job_id}', summary='전송 건과 대조 항목 삭제')
def delete_job(job_id: JobId, _: Manager):
    PlayerDraftService().delete_job(job_id)
    return {'deleted': True}


@router.patch('/{job_id}/items/{item_id}', summary='반영할 값과 선수 매칭을 직접 수정')
def edit(job_id: JobId, item_id: ItemId, payload: DraftEdit, user: Manager):
    """대조 결과의 version과 입력한 값을 저장합니다. 기존 선수를 지정하거나 신규 등록을 선택할 수 있습니다."""
    return PlayerDraftService().edit(job_id, item_id, payload, user)


@router.post('/{job_id}/items/{item_id}/apply', summary='선택한 선수 한 건을 선수 관리에 반영')
def apply_item(job_id: JobId, item_id: ItemId, user: Manager, version: Annotated[int, Query(ge=1)]):
    """신규·변경 항목을 반영하고 항목 상태를 반환합니다. 대조 후 DB가 바뀐 경우 재대조 대상으로 분류합니다."""
    return PlayerDraftService().apply(job_id, item_id, version, user)


class BulkApplyItem(BaseModel):
    id: str = Field(pattern=r'^(transfer|roster)-[0-9]+$')
    version: int = Field(ge=1)


@router.post('/{job_id}/apply', summary='선택한 전송 건의 신규·변경 전체 반영')
def apply_job(job_id: JobId, user: Manager, selected: list[BulkApplyItem] | None = None):
    """한 전송 건에 속한 반영 가능 항목만 처리합니다. 각 항목의 성공, 재대조 또는 실패 결과를 반환합니다."""
    service = PlayerDraftService()
    results = []
    items = sorted(service.items(job_id), key=lambda item: item['values']['effectiveDate'])
    versions = ({item.id: item.version for item in selected} if selected is not None else
                {item['id']: item['version'] for item in items if item['status'] in {'new', 'changed'}})
    for item in items:
        if item['id'] not in versions:
            continue
        try:
            results.append(service.apply(job_id, item['id'], versions.pop(item['id']), user))
        except BackendError as error:
            results.append({'id': item['id'], 'status': 'failed', 'reason': error.message})
        except GoogleAPICallError:
            results.append({'id': item['id'], 'status': 'failed', 'reason': 'Firestore 반영에 실패했습니다. 상태를 확인한 뒤 재시도하세요.'})
    results.extend({'id': item_id, 'status': 'failed', 'reason': '대조 항목을 찾을 수 없습니다.'}
                   for item_id in versions)
    return {'results': results}

