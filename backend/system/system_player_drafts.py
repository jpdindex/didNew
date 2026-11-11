
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from time import perf_counter
from typing import Literal
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import re
from zipfile import BadZipFile

from firebase_admin import firestore, storage
from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.system.system_firestore import BackendError, ensure_firebase_app, get_firestore_client, get_settings
from backend.system.system_player_requests import (
    DirectPlayerRegistration, check_references, format_english_short_name, normalize, profile_values,
    registration_identity, reserve_player_id,
)

logger = logging.getLogger(__name__)


PROFILE = ('name', 'nameEn', 'nameFull', 'birth', 'nation', 'foot', 'height')
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_TOTAL_FILE_SIZE = 50 * 1024 * 1024
MAX_REQUEST_SIZE = MAX_TOTAL_FILE_SIZE + 1024 * 1024
MAX_FILES = 64


class DeliveryFile(BaseModel):
    name: str = Field(min_length=6, max_length=255)
    team: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class DeliveryTarget(BaseModel):
    id: str = Field(pattern=r'^(?:transfer|roster)-[0-9]+$')
    action: Literal['team', 'number', 'depart', 'retire']
    kind: str | None = Field(default=None, max_length=80)
    effectiveDate: str | None = Field(default=None, max_length=16)
    team: str = Field(min_length=1, max_length=160)
    teamCode: str | None = Field(default=None, max_length=100)
    fromTeam: str | None = Field(default=None, max_length=160)
    toTeam: str | None = Field(default=None, max_length=160)
    fromTeamCode: str | None = Field(default=None, max_length=100)
    code: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=160)
    nameFull: str | None = Field(default=None, max_length=160)
    birth: str | None = Field(default=None, max_length=16)
    number: str | int | None = None
    sheet: str | None = Field(default=None, max_length=80)


class DeliveryManifest(BaseModel):
    schemaVersion: Literal[2]
    source: Literal['transfer-ops']
    runId: int = Field(gt=0)
    group: str | None = Field(default=None, max_length=80)
    confirmedAt: str | None = Field(default=None, max_length=64)
    memo: str = ''
    files: list[DeliveryFile] = Field(min_length=1, max_length=MAX_FILES)
    targets: list[DeliveryTarget] = Field(min_length=1)


class DraftValues(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(default='', max_length=100)
    nameEn: str = Field(default='', max_length=100)
    nameFull: str = Field(default='', max_length=160)
    birth: str = Field(default='', max_length=10)
    nation: str = Field(default='', pattern=r'^(?:[A-Z]{3})?$')
    foot: str = Field(default='', pattern=r'^[LRB]?$')
    height: int | None = Field(default=None, ge=100, le=250)
    no: str = Field(default='', pattern=r'^\d{0,3}$')
    pos: str = Field(default='', pattern=r'^(?:GK|DF|MF|FW)?$')
    effectiveDate: str = Field(max_length=10)
    teamId: str = Field(default='', max_length=80, pattern=r'^[^/]*$')


    @field_validator('nameEn')
    @classmethod
    def english_short_name(cls, value: str) -> str:
        return format_english_short_name(value)


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    values: DraftValues
    playerId: str = Field(default='', max_length=100, pattern=r'^[^/]*$')
    registerNew: bool = False


def _text(value):
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() if value is not None else ''


def _iso(value):
    text = _text(value).replace('.', '-')
    for pattern in ('%Y-%m-%d', '%y-%m-%d'):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return ''


def _number(value):
    text = _text(value)
    return str(int(text)) if re.fullmatch(r'\d{1,3}', text) else ''


def _position(value):
    text = _text(value).upper()
    return text if text in {'GK', 'DF', 'MF', 'FW'} else ''


def _excel_values(book, target):
    """Use the same season and half that Transfer Ops writes."""
    when = date.fromisoformat(_iso(target.get('effectiveDate')))
    year = when.year if when.month >= 6 else when.year - 1
    season = f'{year % 100:02d}-{(year + 1) % 100:02d}'
    half = '상반기' if when.month >= 6 else '하반기'
    names = [name for name in book.sheetnames if season in name and half in name]
    if target.get('sheet'):
        names = [name for name in book.sheetnames if name == target['sheet']]
    elif not names:
        names = [name for name in book.sheetnames if season in name and '상반기' not in name and '하반기' not in name]
    if len(names) != 1:
        raise ValueError('변경일에 해당하는 엑셀 기간 시트를 확정할 수 없습니다.')
    rows = iter(book[names[0]].values)
    headers = {}
    for _, cells in zip(range(15), rows):
        headers = {_text(value).lower(): index for index, value in enumerate(cells) if value is not None}
        if {'p_id', 'p_name', 'full-name'} <= headers.keys():
            break
    if not {'p_id', 'p_name', 'full-name'} <= headers.keys():
        raise ValueError('엑셀 선수 헤더가 없습니다.')

    def get(cells, key):
        index = headers.get(key)
        return _text(cells[index]) if index is not None and index < len(cells) else ''

    code = _text(target.get('code')).casefold()
    name = normalize(_text(target.get('nameFull') or target.get('name')))
    matches = []
    for cells in rows:
        match = get(cells, 'p_id').casefold() == code if code else (
            name and name in {normalize(get(cells, 'full-name')), normalize(get(cells, 'p_name'))})
        if match:
            matches.append(cells)
    if len(matches) != 1:
        raise ValueError('확정 엑셀에서 선수 행을 하나로 특정할 수 없습니다.')
    cells = matches[0]
    nation = get(cells, 'country_code').upper()
    foot = {'left': 'L', 'right': 'R', 'both': 'B', '왼발': 'L', '오른발': 'R', '양발': 'B'}.get(
        get(cells, 'p_foot').lower(), get(cells, 'p_foot').upper())
    no = _number(get(cells, 'p_number'))
    if target['action'] == 'number':
        expected = _number(target.get('number'))
        history_entry = f'No.{expected}-{when:%y.%m.%d}'
        if expected and any(history_entry in [part.strip() for part in _text(value).split('/')] for value in cells):
            no = expected
        elif expected and no != expected:
            raise ValueError('변경일의 등번호를 확정 엑셀에서 확인할 수 없습니다.')
    raw_height = get(cells, 'p_height') or get(cells, 'height') or get(cells, '키')
    return {
        'name': get(cells, 'p_name'), 'nameEn': format_english_short_name(get(cells, 'e-name')), 'nameFull': get(cells, 'full-name'),
        'birth': _iso(get(cells, 'p_birth')), 'nation': nation if re.fullmatch('[A-Z]{3}', nation) else '',
        'foot': foot if foot in {'L', 'R', 'B'} else '', 'height': int(raw_height) if raw_height.isdigit() and 100 <= int(raw_height) <= 250 else None,
        'no': no, 'pos': _position(get(cells, 'p_position')),
        'effectiveDate': when.isoformat(),
    }


def _matching_snapshots(collection, filters):
    queries = [collection.where(filter=FieldFilter(field, operator, value))
               for field, operator, value in filters if value]
    with ThreadPoolExecutor(max_workers=min(5, len(queries) or 1)) as workers:
        results = workers.map(lambda query: list(query.stream()), queries)
        return {snapshot.id: snapshot for snapshots in results for snapshot in snapshots}


def _lookup_team(teams, name, code):
    name, code = _text(name), _text(code)
    for key in dict.fromkeys((code, name)):
        if key and '/' not in key and key not in {'.', '..'}:
            snapshot = teams.document(key).get()
            if snapshot.exists:
                return snapshot.id
    matches = _matching_snapshots(teams, [
        ('teamCode', '==', code),
        *((field, '==', name) for field in ('name', 'nameKr', 'nameEn', 'nameFull', 'nameShort')),
    ])
    return next(iter(matches)) if len(matches) == 1 else ''


def _active_draft(ref, transaction=None):
    draft = ref.get(transaction=transaction).to_dict()
    if not draft:
        raise BackendError('전송 건을 찾을 수 없습니다.', 404, 'draft_not_found')
    if draft.get('status') == 'deleting':
        raise BackendError('삭제 중인 전송 건입니다.', 409, 'draft_deleting')
    return draft


def _snapshot_values(player, contracts):
    values = player.to_dict() or {}
    current = next((contract.to_dict() or {} for contract in contracts), {})
    data = {key: values.get(key) for key in PROFILE}
    data['nameEn'] = format_english_short_name(_text(data['nameEn']))
    data.update(birth=_iso(data['birth']), teamId=current.get('teamId') or values.get('currentTeamId'),
                no=_number(current.get('no', values.get('currentNo'))), pos=_position(current.get('pos', values.get('currentPos'))),
                effectiveDate=_iso(current.get('from')), active=values.get('active', True))
    version = {'player': str(player.update_time), 'contracts': {c.id: str(c.update_time) for c in contracts}}
    return data, version


def _league_contracts(player_ref, transaction=None):
    return [contract for contract in player_ref.collection('contracts').stream(transaction=transaction)
            if contract.to_dict().get('competitionType') != 'national']


def _open_contracts(player_ref, transaction=None):
    return [contract for contract in _league_contracts(player_ref, transaction)
            if contract.to_dict().get('to') is None]


def _today():
    return datetime.now(timezone(timedelta(hours=9))).date()


def _season_id(day):
    year = day.year - (day.month < 6)
    return f'{year}{year + 1}'


def _date_correction(target, values, contracts, open_contracts, source_team):
    if (target['action'] != 'team' or target.get('kind') == 'callup' or len(open_contracts) != 1
            or not source_team or source_team == values.get('teamId')):
        return None
    active = open_contracts[0]
    data = active.to_dict()
    old_start, new_start = _iso(data.get('from')), _iso(values.get('effectiveDate'))
    if not old_start or not new_start or old_start == new_start or data.get('teamId') != values.get('teamId'):
        return None
    if data.get('fromTeamId') and data['fromTeamId'] != source_team:
        return None
    old_previous_day = (date.fromisoformat(old_start) - timedelta(days=1)).isoformat()
    previous = [contract for contract in contracts if contract.id != active.id
                and contract.to_dict().get('teamId') == source_team
                and _iso(contract.to_dict().get('to')) in {old_start, old_previous_day}]
    if len(previous) > 1 or (not previous and data.get('fromTeamId') != source_team):
        return None
    excluded = {active.id, *(contract.id for contract in previous)}
    for contract in contracts:
        if contract.id not in excluded:
            end = _iso(contract.to_dict().get('to'))
            if not end or end >= new_start:
                return None
    correction = {'contractId': active.id}
    if previous:
        prior_start = _iso(previous[0].to_dict().get('from'))
        if not prior_start or prior_start >= new_start:
            return None
        correction.update(previousId=previous[0].id,
                          previousEnd=(date.fromisoformat(new_start) - timedelta(days=1)).isoformat())
    return correction



def _player_candidates(players, name_full, name):
    full_key, name_key = normalize(name_full), normalize(name)
    filters = [
        ('nameFull', '==', name_full),
        ('name', '==', name),
        ('nameFullKey', '==', full_key),
    ]
    filters.extend(('searchTerms', 'array_contains', key) for key in sorted({full_key, name_key} - {''}))
    candidates = _matching_snapshots(players, filters)
    return [
        {'id': player_id, **{key: data.get(key) for key in ('name', 'nameFull', 'birth', 'currentTeamId')}}
        for player_id in sorted(candidates) if (data := candidates[player_id].to_dict())
    ]


def _matches_contract_alias(player_ref, code, team_id):
    if not code or not team_id:
        return False
    query = player_ref.collection('contracts').where(filter=FieldFilter('teamId', '==', team_id))
    for contract in query.stream():
        aliases = _text((contract.to_dict() or {}).get('alias')).split(',')
        if any(alias.strip().casefold() == code.casefold() for alias in aliases):
            return True
    return False


def _changes(action, excel, edited, current):
    if action == 'number':
        return {'no': edited['no']}
    if action in {'depart', 'retire'}:
        return {'teamId': None, 'no': '', 'pos': '', **({'active': False} if action == 'retire' else {})}
    changes = {'teamId': edited['teamId'], 'active': True}
    changes.update({key: edited[key] for key in ('no', 'pos') if edited[key] or current.get('teamId') != edited['teamId']})
    changes.update({key: edited[key] for key in PROFILE if edited.get(key)})
    return changes


def _classify(target, excel, values, current, contracts, register_new, source_team, correction=None):
    action = target['action']
    effective = _iso(values.get('effectiveDate'))
    today = _today().isoformat()
    if _iso(target.get('effectiveDate')) > today:
        return 'review', '원본이 미래 예정 이적입니다. 실제 이적을 OPS에서 다시 수집해 전송하세요.'
    if not effective or effective > today:
        return 'review', '유효한 적용일을 입력하세요.'
    if action in {'team', 'number'} and not values.get('teamId'):
        return 'review', 'JPD-DID 팀 ID를 선택하세요.'
    if action == 'number' and not values.get('no'):
        return 'review', '등번호를 입력하세요.'
    if action in {'depart', 'retire'} and not source_team:
        return 'review', '이적 전 JPD-DID 팀 ID를 확인하세요.'
    if register_new:
        if action == 'team' and values.get('name') and values.get('nameFull'):
            return 'new', ''
        return 'review', '팀 소속과 선수 이름을 확인하세요.'
    if not current:
        return 'review', '기존 선수를 선택하거나 신규 등록을 지정하세요.'
    if len(contracts) > 1:
        return 'review', '진행 중인 구단 계약이 여러 건입니다.'
    if action == 'number' and (not contracts or current.get('teamId') != values.get('teamId')):
        return 'review', '현재 소속 계약이 엑셀 팀과 일치하지 않습니다.'
    changes = _changes(action, excel, values, current)
    date_differs = (action == 'team' and target.get('kind') != 'callup'
                    and current.get('teamId') == values.get('teamId')
                    and current.get('effectiveDate') and current['effectiveDate'] != effective)
    if date_differs and not correction:
        return 'review', '기존 계약과 같은 이적인지 확인할 수 없어 적용일을 자동으로 교정할 수 없습니다.'
    if 'nameEn' in changes:
        changes['nameEn'] = format_english_short_name(changes['nameEn'])
    changed = [key for key, value in changes.items() if (current.get(key) or '') != (value or '')]
    if not changed and not correction:
        return 'same', ''
    if not correction and current.get('effectiveDate') and current['effectiveDate'] > effective:
        return 'review', '적용일 이후에 시작된 계약이 있어 자동 반영할 수 없습니다.'
    if source_team and current.get('teamId') not in {None, source_team, values.get('teamId') if action == 'team' else source_team}:
        return 'review', '현재 소속이 이적 기록의 출발 팀과 다릅니다.'
    labels = {'teamId': '소속팀', 'no': '등번호', 'pos': '포지션', 'active': '활동 상태',
              'name': '한글명', 'nameEn': '영문 약칭', 'nameFull': '영문 전체 이름',
              'birth': '생년월일', 'nation': '국가 코드', 'foot': '주발', 'height': '키'}
    reasons = [labels[key] for key in changed]
    if correction:
        reasons.append(f"적용일 {current['effectiveDate']} → {effective}")
    return 'changed', ', '.join(reasons)


def _read_target(book, target):
    try:
        return {'values': _excel_values(book, target), 'error': ''}
    except (KeyError, ValueError, BadZipFile, InvalidFileException) as exc:
        return {'values': {}, 'error': str(exc)}


def _read_legacy_draft(job_id, draft):
    """Retain comparison support for previously received Storage deliveries."""
    bucket_name = get_settings().jpd_did_storage_bucket.strip()
    if not bucket_name or draft.get('bucket') != bucket_name:
        raise BackendError('이전 방식으로 받은 건입니다. Transfer Ops에서 다시 전송하세요.', 503, 'draft_bucket')
    bucket = storage.bucket(bucket_name, app=ensure_firebase_app())
    files = {file['team']: file for file in draft['files']}
    books, extracted = {}, {}
    try:
        for target in draft['targets']:
            try:
                file = files[target['team']]
                if target['team'] not in books:
                    path = file['path']
                    if not path.startswith(f'playerDrafts/{job_id}/'):
                        raise ValueError('전송 건의 엑셀 경로가 올바르지 않습니다.')
                    blob = bucket.get_blob(path)
                    if blob is None or blob.size > MAX_FILE_SIZE:
                        raise ValueError('확정 엑셀을 찾을 수 없거나 허용 용량을 초과했습니다.')
                    contents = blob.download_as_bytes(if_generation_match=blob.generation)
                    if sha256(contents).hexdigest() != file['sha256']:
                        raise ValueError('확정 엑셀의 검증값이 일치하지 않습니다.')
                    books[target['team']] = load_workbook(BytesIO(contents), read_only=True, data_only=True)
                extracted[target['id']] = _read_target(books[target['team']], target)
            except (KeyError, ValueError, BadZipFile, InvalidFileException) as exc:
                extracted[target['id']] = {'values': {}, 'error': str(exc)}
    finally:
        for book in books.values():
            book.close()
    return extracted


class PlayerDraftService:
    def __init__(self):
        self.db = get_firestore_client()
        self.drafts = self.db.collection('playerDrafts')

    def _draft(self, job_id):
        ref = self.drafts.document(job_id)
        return ref, _active_draft(ref)

    def list(self):
        jobs = []
        for snapshot in self.drafts.order_by('createdAt', direction=firestore.Query.DESCENDING).limit(100).stream():
            data = snapshot.to_dict() or {}
            jobs.append({'id': snapshot.id, **{key: data.get(key) for key in ('runId', 'group', 'status', 'confirmedAt')},
                         'targetCount': len(data.get('targets') or [])})
        return jobs

    def _items(self, ref):
        return [{'id': snapshot.id, **(snapshot.to_dict() or {})} for snapshot in ref.collection('items').stream()]

    def items(self, job_id):
        ref, _ = self._draft(job_id)
        return self._items(ref)

    def details(self, job_id):
        ref = self.drafts.document(job_id)
        draft = ref.get().to_dict()
        if not draft:
            raise BackendError('전송 건을 찾을 수 없습니다.', 404, 'draft_not_found')
        items = [] if draft.get('status') == 'deleting' else self._items(ref)
        return {'memo': draft.get('memo') or '', 'items': items}

    def delete_item(self, job_id, item_id):
        ref = self.drafts.document(job_id)

        @firestore.transactional
        def remove(tx):
            draft = _active_draft(ref, tx)
            targets = [target for target in draft['targets'] if target['id'] != item_id]
            if len(targets) == len(draft['targets']):
                raise BackendError('전송 항목을 찾을 수 없습니다.', 404, 'item_not_found')
            item_ref = ref.collection('items').document(item_id)
            tx.delete(item_ref)
            if not targets:
                tx.delete(ref)
                return
            memo = '\n'.join(' · '.join(filter(None, (
                target.get('effectiveDate'), target.get('nameFull') or target.get('name'),
                target.get('code'), target.get('kind'), target.get('team'),
            ))) for target in targets)
            patch = {'targets': targets, 'memo': memo}
            if 'extracted' in draft:
                patch['extracted'] = {key: value for key, value in draft['extracted'].items() if key != item_id}
            tx.update(ref, patch)

        remove(self.db.transaction())

    def delete_job(self, job_id):
        ref = self.drafts.document(job_id)

        @firestore.transactional
        def mark_deleting(tx):
            if ref.get(transaction=tx).exists:
                tx.update(ref, {'status': 'deleting'})

        @firestore.transactional
        def remove_page(tx):
            draft = ref.get(transaction=tx).to_dict() or {}
            if draft.get('status') != 'deleting':
                return False
            children = list(ref.collection('items').limit(200).stream(transaction=tx))
            if not children:
                tx.delete(ref)
                return False
            for child in children:
                tx.delete(child.reference)
            return True

        mark_deleting(self.db.transaction())
        while remove_page(self.db.transaction()):
            pass

    def receive(self, raw_manifest, uploads):
        try:
            manifest = DeliveryManifest.model_validate_json(raw_manifest)
        except ValidationError as exc:
            raise BackendError('전송 메타데이터의 형식이 올바르지 않습니다.', 422, 'invalid_delivery') from exc
        if len(uploads) != len(manifest.files) or sum(file.size or 0 for file in uploads) > MAX_TOTAL_FILE_SIZE:
            raise BackendError('전송 파일 수 또는 용량을 확인하세요.', 422, 'invalid_delivery')
        teams = {file.team for file in manifest.files}
        target_ids = {target.id for target in manifest.targets}
        if len(teams) != len(manifest.files) or len(target_ids) != len(manifest.targets) or {target.team for target in manifest.targets} - teams:
            raise BackendError('전송 파일과 변경 대상의 팀이 일치해야 합니다.', 422, 'invalid_delivery')
        fingerprint = sha256(raw_manifest.encode()).hexdigest()[:20]
        job_id = f'transfer-ops-{manifest.runId}-{fingerprint}'
        ref = self.drafts.document(job_id)
        if ref.get().exists:
            _active_draft(ref)
            return {'job_id': job_id}
        draft = manifest.model_dump()
        targets_by_team = defaultdict(list)
        for target in draft['targets']:
            targets_by_team[target['team']].append(target)
        extracted = {}
        for file, upload in zip(manifest.files, uploads, strict=True):
            if file.name != upload.filename or not file.name.lower().endswith('.xlsx') or not upload.size or upload.size > MAX_FILE_SIZE:
                raise BackendError('확정 엑셀의 파일 이름 또는 용량을 확인하세요.', 422, 'invalid_workbook')
            digest = sha256()
            for chunk in iter(lambda: upload.file.read(1024 * 1024), b''):
                digest.update(chunk)
            if digest.hexdigest() != file.sha256:
                raise BackendError('확정 엑셀의 검증값이 일치하지 않습니다.', 422, 'invalid_workbook')
            if file.team not in targets_by_team:
                continue
            upload.file.seek(0)
            try:
                book = load_workbook(upload.file, read_only=True, data_only=True)
            except (ValueError, BadZipFile, InvalidFileException, KeyError) as exc:
                raise BackendError('전송된 확정 엑셀을 열 수 없습니다.', 422, 'invalid_workbook') from exc
            try:
                for target in targets_by_team[file.team]:
                    extracted[target['id']] = _read_target(book, target)
            finally:
                book.close()
        draft.update(extracted=extracted, status='pending')
        if len(json.dumps(draft, ensure_ascii=False).encode()) > 750_000:
            raise BackendError('변경 대상이 Firestore 문서 저장 한도를 초과했습니다.', 413, 'draft_too_large')
        try:
            ref.create({**draft, 'createdAt': firestore.SERVER_TIMESTAMP})
        except AlreadyExists:
            pass
        return {'job_id': job_id}

    def compare(self, job_id):
        for _ in self.compare_steps(job_id):
            pass
        return self.items(job_id)

    def compare_steps(self, job_id):
        started = perf_counter()
        ref, draft = self._draft(job_id)
        total = len(draft['targets'])
        yield {'done': 0, 'total': total}
        if draft.get('schemaVersion') == 1:
            extracted = _read_legacy_draft(job_id, draft)
        elif draft.get('schemaVersion') == 2:
            extracted = draft['extracted']
        else:
            raise BackendError('지원하지 않는 전송 형식입니다.', 422, 'unsupported_draft')
        teams = self.db.collection('teams')
        players = self.db.collection('players')
        candidate_cache, team_cache = {}, {}
        existing = {item['id']: item for item in self._items(ref)}
        prepared = perf_counter()

        def resolve_team(name, code):
            key = (name, code)
            if key not in team_cache:
                team_cache[key] = _lookup_team(teams, name, code)
            return team_cache[key]
        for done, target in enumerate(draft['targets'], start=1):
            item_id = target['id']
            previous = existing.get(item_id)
            if previous and previous['status'] == 'applied':
                original_values = previous['values']
                original_current = previous['current']
                if (not previous.get('dateCorrection') and previous['target']['action'] == 'team'
                        and original_values.get('teamId') == original_current.get('teamId')
                        and original_values.get('effectiveDate') != original_current.get('effectiveDate')):
                    source_team = previous.get('sourceTeamId') or resolve_team(target.get('fromTeam'), target.get('fromTeamCode'))
                    retry = {**previous, 'sourceTeamId': source_team}
                    fresh = self._evaluate(retry)
                    if fresh['status'] == 'changed' and fresh['dateCorrection']:
                        item_ref = ref.collection('items').document(item_id)
                        self._save_comparison(ref, item_ref, previous, {
                            **retry, **fresh, 'version': previous['version'] + 1,
                        }, reopen_applied=True)
                yield {'done': done, 'total': total}
                continue
            team_id = resolve_team(target.get('team'), target.get('teamCode'))
            source_team = (team_id if target['action'] in {'depart', 'retire'} else
                           resolve_team(target.get('fromTeam'), target.get('fromTeamCode')))
            original = extracted[item_id]
            excel = {**DraftValues(effectiveDate=_iso(target.get('effectiveDate')), teamId=team_id).model_dump(),
                     **{key: _text(target.get(key)) for key in ('name', 'nameFull')}, 'birth': _iso(target.get('birth')),
                     **original['values']}
            excel['nameEn'] = format_english_short_name(excel['nameEn'])
            error = original['error']
            names = (excel['nameFull'], excel['name'])
            if names not in candidate_cache:
                candidate_cache[names] = _player_candidates(players, *names)
            candidates = candidate_cache[names]
            birth = excel.get('birth')
            exact = [candidate for candidate in candidates if
                     (excel['nameFull'] and normalize(_text(candidate.get('nameFull'))) == normalize(excel['nameFull'])
                      and (not birth or not candidate.get('birth') or _iso(candidate['birth']) == birth))
                     or (not excel['nameFull'] and birth and _iso(candidate.get('birth')) == birth)]
            if len(exact) > 1:
                alias_matches = [
                    candidate for candidate in exact
                    if _matches_contract_alias(players.document(candidate['id']), _text(target.get('code')), team_id)
                ]
                if alias_matches:
                    exact = alias_matches
            selected = exact[0]['id'] if len(exact) == 1 else ''
            # A legacy spelling may lack a search key. Leave unmatched players for review.
            register_new = False
            values = previous['values'] if previous and previous.get('editedBy') else excel
            if previous and previous.get('editedBy'):
                selected, register_new = previous['playerId'], previous['registerNew']
            item_ref = ref.collection('items').document(item_id)
            item = {'target': target, 'excel': excel, 'values': values, 'sourceTeamId': source_team,
                    'candidates': candidates[:20], 'excelError': error, 'playerId': selected, 'registerNew': register_new,
                    'version': (previous or {}).get('version', 0) + 1}
            if previous and previous.get('editedBy'):
                item['editedBy'] = previous['editedBy']
            item.update(self._evaluate(item))
            self._save_comparison(ref, item_ref, previous, item)
            yield {'done': done, 'total': total}

        @firestore.transactional
        def finish(tx):
            _active_draft(ref, tx)
            tx.update(ref, {'status': 'compared', 'comparedAt': firestore.SERVER_TIMESTAMP})

        finish(self.db.transaction())
        logger.info('Compared player draft %s (%d targets): setup %.2fs, compare %.2fs',
                    job_id, total, prepared - started, perf_counter() - prepared)

    def _save_comparison(self, ref, item_ref, previous, item, *, reopen_applied=False):
        @firestore.transactional
        def save(tx):
            draft = _active_draft(ref, tx)
            if not any(target['id'] == item_ref.id for target in draft['targets']):
                raise BackendError('전송 항목이 삭제됐습니다. 다시 대조하세요.', 409, 'draft_conflict')
            current = item_ref.get(transaction=tx).to_dict() or {}
            if previous is None:
                if not current:
                    tx.create(item_ref, item)
            elif current.get('version') != previous['version'] or (current.get('status') == 'applied' and not reopen_applied):
                raise BackendError('항목이 변경됐습니다. 다시 불러오세요.', 409, 'draft_conflict')
            else:
                tx.update(item_ref, item)

        save(self.db.transaction())

    def _evaluate(self, item, transaction=None):
        player_id = item.get('playerId')
        current, revision, contracts, open_contracts = {}, None, [], []
        if player_id:
            ref = self.db.collection('players').document(player_id)
            player = ref.get(transaction=transaction)
            if not player.exists:
                raise BackendError('지정한 선수를 찾을 수 없습니다.', 422, 'player_not_found')
            contracts = _league_contracts(ref, transaction)
            open_contracts = [contract for contract in contracts if contract.to_dict().get('to') is None]
            current, revision = _snapshot_values(player, open_contracts)
        target, values = item['target'], item['values']
        source = values.get('teamId') if target['action'] in {'depart', 'retire'} else item.get('sourceTeamId')
        correction = _date_correction(target, values, contracts, open_contracts, source)
        if correction:
            revision['contracts'].update({contract.id: str(contract.update_time) for contract in contracts})
        status, reason = _classify(target, item['excel'], values, current, open_contracts,
                                   item['registerNew'], source, correction)
        if status in {'new', 'changed'}:
            effective = date.fromisoformat(_iso(values['effectiveDate']))
            if not correction and any(_iso(contract.to_dict().get('from')) > effective.isoformat() for contract in contracts):
                status, reason = 'review', '적용일 이후의 소속 이력이 있어 최신 소속을 자동으로 바꿀 수 없습니다.'
            elif target['action'] == 'team' and values.get('teamId'):
                team = self.db.collection('teams').document(values['teamId'])
                season = team.collection('seasons').document(_season_id(effective)).get(transaction=transaction).to_dict() or {}
                if not season.get('leagueId'):
                    current_team = team.get(transaction=transaction).to_dict() or {}
                    if _season_id(effective) != _season_id(_today()) or not current_team.get('currentLeagueId'):
                        status, reason = 'review', '해당 적용일의 시즌에서 JPD-DID 팀 소속을 확인하세요.'
        if item.get('excelError') and not item.get('editedBy'):
            status, reason = 'review', item['excelError']
        return {'status': status, 'reason': reason, 'current': current, 'playerRevision': revision, 'dateCorrection': correction}

    def _replace(self, ref, item_ref, version, patch):
        @firestore.transactional
        def update(tx):
            _active_draft(ref, tx)
            current = item_ref.get(transaction=tx).to_dict() or {}
            if current.get('version') != version or current.get('status') == 'applied':
                raise BackendError('항목이 변경됐습니다. 다시 불러오세요.', 409, 'draft_conflict')
            tx.update(item_ref, patch)
        update(self.db.transaction())

    def edit(self, job_id, item_id, edit, user):
        ref, _ = self._draft(job_id)
        item_ref = ref.collection('items').document(item_id)
        current = item_ref.get().to_dict() or {}
        if not current:
            raise BackendError('대조 항목을 찾을 수 없습니다.', 404, 'item_not_found')
        if edit.playerId and edit.registerNew:
            raise BackendError('기존 선수와 신규 등록 중 하나를 지정하세요.', 422, 'invalid_selection')
        if edit.values.teamId in {'.', '..'} or edit.playerId in {'.', '..'}:
            raise BackendError('올바른 팀 또는 선수 ID를 입력하세요.', 422, 'invalid_selection')
        if edit.values.birth and not _iso(edit.values.birth):
            raise BackendError('생년월일을 YYYY-MM-DD 형식으로 입력하세요.', 422, 'invalid_birth')
        patch = {'values': edit.values.model_dump(), 'playerId': edit.playerId, 'registerNew': edit.registerNew,
                 'editedBy': user.uid, 'version': edit.version + 1}
        patch['values']['birth'] = _iso(patch['values']['birth'])
        patch.update(self._evaluate({**current, **patch}))
        self._replace(ref, item_ref, edit.version, patch)
        return {'id': item_id, **current, **patch}

    def apply(self, job_id, item_id, version, user):
        ref, _ = self._draft(job_id)
        item_ref = ref.collection('items').document(item_id)

        @firestore.transactional
        def publish(tx):
            _active_draft(ref, tx)
            item = item_ref.get(transaction=tx).to_dict()
            if not item:
                raise BackendError('대조 항목을 찾을 수 없습니다.', 404, 'item_not_found')
            status = item['status']
            if status == 'applied':
                return {'id': item_id, 'status': status, 'playerId': item.get('appliedPlayerId')}
            if item['version'] != version:
                raise BackendError('항목이 변경됐습니다. 다시 불러오세요.', 409, 'draft_conflict')
            if status not in {'new', 'changed'}:
                return {'id': item_id, 'status': status, 'reason': item.get('reason', '')}
            fresh = self._evaluate(item, transaction=tx)
            if fresh['playerRevision'] != item['playerRevision'] or fresh['status'] != status:
                reason = '대조 후 선수 DB가 변경됐습니다. 다시 대조하세요.'
                tx.update(item_ref, {'status': 'stale', 'reason': reason, 'version': item['version'] + 1})
                return {'id': item_id, 'status': 'stale', 'reason': reason}
            player_id = self._write_player(tx, {**item, 'dateCorrection': fresh['dateCorrection']}, user, job_id, item_id)
            tx.update(item_ref, {'status': 'applied', 'appliedBy': user.uid, 'appliedAt': firestore.SERVER_TIMESTAMP,
                                 'appliedPlayerId': player_id, 'version': item['version'] + 1})
            return {'id': item_id, 'status': 'applied', 'playerId': player_id}

        return publish(self.db.transaction())

    def _write_player(self, tx, item, user, job_id, item_id):
        values, target, current = item['values'], item['target'], item['current']
        action = target['action']
        effective = _iso(values['effectiveDate'])
        when = effective.replace('-', '.')
        destination = values['teamId'] if action in {'team', 'number'} else None
        team = self.db.collection('teams').document(destination).get(transaction=tx).to_dict() if destination else {}
        if destination and not team:
            raise BackendError('JPD-DID 팀 ID를 확인하세요.', 422, 'invalid_team')
        season_id = _season_id(date.fromisoformat(effective))
        season = self.db.collection('teams').document(destination).collection('seasons').document(season_id).get(transaction=tx).to_dict() if destination else {}
        league_id = (season or {}).get('leagueId') or team.get('currentLeagueId') or ''
        audit = {'updatedAt': firestore.SERVER_TIMESTAMP, 'updatedBy': user.uid}
        created = {'createdAt': firestore.SERVER_TIMESTAMP, 'createdBy': user.uid}
        receipt = sha256(f'{job_id}:{item_id}'.encode()).hexdigest()[:32]
        transfer_kind = _text(target.get('kind'))
        kind = ('YOUTH' if transfer_kind == 'callup' else 'LOAN_RETURN' if '임대' in transfer_kind and '복귀' in transfer_kind
                else 'LOAN' if '임대' in transfer_kind else 'FREE' if '자유' in transfer_kind else 'TRANSFER')
        if destination and action == 'team':
            league = self.db.collection('leagues').document(league_id).get(transaction=tx).to_dict() if league_id else None
            if not league or league.get('competitionType') == 'national':
                raise BackendError('구단 팀의 리그를 확인하세요.', 422, 'invalid_league')
        old_team = current.get('teamId')
        old_contract = None
        contract_data = {}
        player_updates = {}
        if item['status'] == 'new':
            try:
                payload = DirectPlayerRegistration(**{key: values[key] for key in (*PROFILE, 'no', 'pos')},
                    requestId=receipt, teamId=destination, leagueId=league_id, seasonId=season_id, fromDate=effective, kind='TRANSFER' if kind == 'LOAN_RETURN' else kind)
            except ValidationError as exc:
                raise BackendError('신규 등록에 필요한 선수 정보와 팀·시즌을 확인하세요.', 422, 'invalid_player') from exc
            labels = check_references(self.db, payload)
            if labels['competitionType'] == 'national':
                raise BackendError('구단 팀만 등록할 수 있습니다.', 422, 'invalid_team')
            identity = self.db.collection('playerRegistrationState').document('numbering').collection('identities').document(registration_identity(payload))
            queries = [self.db.collection('players').where(filter=FieldFilter(key, '==', value)).limit(1) for key, value in
                       (('nameFull', payload.nameFull), ('nameFullKey', normalize(payload.nameFull)))]
            if identity.get(transaction=tx).exists or any(next(query.stream(transaction=tx), None) for query in queries):
                raise BackendError('같은 선수가 등록되어 있습니다. 기존 선수를 선택하세요.', 409, 'duplicate_player')
            player_id, counter, number = reserve_player_id(self.db, tx)
            player_updates = profile_values(payload, user.uid)
        else:
            player_id = item['playerId']
            changes = _changes(action, item['excel'], values, current)
            if 'nameEn' in changes:
                changes['nameEn'] = format_english_short_name(changes['nameEn'])
            player_updates = {key: (value.replace('-', '.') if key == 'birth' and value else value or None)
                              for key, value in changes.items() if key in PROFILE}
            if 'nameFull' in player_updates:
                player_updates['nameFullKey'] = normalize(player_updates['nameFull'] or '')
                player_updates['searchTerms'] = list(dict.fromkeys(normalize(values.get(key) or '') for key in ('nameFull', 'name', 'nameEn')))
            if 'birth' in player_updates:
                player_updates['birthKey'] = normalize(values['birth'])
            old_contract = next(iter(_open_contracts(self.db.collection('players').document(player_id), tx)), None)
            contract_data = old_contract.to_dict() if old_contract else {}
        player_ref = self.db.collection('players').document(player_id)
        no = values['no'] or (current.get('no') if destination == old_team else None)
        pos = (current.get('pos') if action == 'number' else values['pos']) or (current.get('pos') if destination == old_team else None)
        correction = item.get('dateCorrection')
        next_contract = None
        split_number_contract = action == 'number' and old_contract and when > _text(contract_data.get('from')).replace('-', '.')
        if split_number_contract:
            next_contract = player_ref.collection('contracts').document(f'transfer-draft-{receipt}')
        elif destination and old_contract and destination == old_team:
            next_contract = old_contract.reference
        elif destination:
            if not league_id:
                raise BackendError('이 팀의 리그를 확인하세요.', 422, 'invalid_league')
            next_contract = player_ref.collection('contracts').document(f'transfer-draft-{receipt}')
        affected_teams = {team_id for team_id in (old_team, destination) if team_id}
        revisions = []
        for team_id in affected_teams:
            revision = self.db.collection('playerRegistrationRosterVersions').document(team_id)
            version = (revision.get(transaction=tx).to_dict() or {}).get('version', 0)
            revisions.append((revision, version))

        if split_number_contract:
            previous_day = (date.fromisoformat(effective) - timedelta(days=1)).isoformat().replace('-', '.')
            tx.update(old_contract.reference, {'to': previous_day, **audit})
        elif old_contract and old_team != destination:
            end = (date.fromisoformat(effective) - timedelta(days=1)).isoformat().replace('-', '.') if destination else when
            tx.update(old_contract.reference, {'to': end, **audit})
        if old_team and old_team != destination:
            tx.delete(self.db.collection('teams').document(old_team).collection('squad').document(player_id))
        if next_contract:
            if old_contract and next_contract == old_contract.reference:
                dates = {'from': when, 'leagueId': league_id} if correction else {}
                tx.update(next_contract, {'no': no, 'pos': pos, **dates, **audit})
                if correction and correction.get('previousId'):
                    previous_ref = player_ref.collection('contracts').document(correction['previousId'])
                    tx.update(previous_ref, {'to': correction['previousEnd'].replace('-', '.'), **audit})
            elif split_number_contract:
                tx.create(next_contract, {**contract_data, 'from': when, 'no': no, **created, **audit})
            else:
                tx.create(next_contract, {'teamId': destination, 'leagueId': league_id, 'competitionType': 'league',
                    'from': when, 'to': None, 'no': no, 'pos': pos, 'fromTeamId': old_team,
                    'transferType': kind, 'fee': None, 'currency': None, **created, **audit})
            tx.set(self.db.collection('teams').document(destination).collection('squad').document(player_id),
                   {'name': player_updates.get('name') or current.get('name') or values['name'],
                    'no': no or '', 'pos': pos or '', 'contractId': next_contract.id,
                    'since': contract_data.get('from') if old_contract and next_contract == old_contract.reference and not correction else when})
        player_updates.update(currentTeamId=destination, currentNo=no if destination else None, currentPos=pos if destination else None)
        if action in {'team', 'retire'}:
            player_updates['active'] = action == 'team'
        if item['status'] == 'new':
            tx.create(player_ref, {**player_updates, **audit})
            tx.create(identity, {'playerId': player_id})
            tx.set(counter, {'lastAssigned': number}, merge=True)
        else:
            tx.update(player_ref, {**player_updates, **audit})
        for revision, version in revisions:
            tx.set(revision, {'version': version + 1})
        return player_id

