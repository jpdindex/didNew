"""Structured analyst requests. Only an authenticated manager can publish players."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import re
import unicodedata
from typing import Literal
from urllib.parse import urlparse

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.system.system_firestore import BackendError, CurrentUser, get_firestore_client

MANAGER_EMAIL = 'jpdindex@gmail.com'


def normalize(value: str) -> str:
    text = unicodedata.normalize('NFD', value).lower()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9가-힣]', '', unicodedata.normalize('NFC', text))


def format_english_short_name(value: str) -> str:
    return re.sub(r'^([A-Za-z])\.\s*(?=\S)', r'\1. ', value.strip())


class PlayerRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    leagueId: str = Field(min_length=1, max_length=80)
    seasonId: str = Field(pattern=r'^\d{8}$')
    teamId: str = Field(min_length=1, max_length=80)
    matchId: str = Field(default='', max_length=100)
    # Keep NEW readable for requests submitted before the form changed.
    kind: Literal['NEW', 'YOUTH', 'TRANSFER', 'LOAN'] = 'YOUTH'
    name: str = Field(min_length=1, max_length=100)
    nameEn: str = Field(default='', max_length=100)
    nameFull: str = Field(min_length=1, max_length=160)
    birth: str
    nation: str = Field(pattern=r'^[A-Z]{3}$')
    no: str = Field(pattern=r'^\d{1,3}$')
    pos: Literal['GK', 'DF', 'MF', 'FW']
    foot: Literal['', 'L', 'R', 'B'] = ''
    height: int | None = Field(default=None, ge=100, le=250)
    fromDate: str = ''
    sourceUrl: str = Field(default='', max_length=2000)
    note: str = Field(default='', max_length=2000)

    @field_validator('nameEn')
    @classmethod
    def english_short_name(cls, value: str) -> str:
        return format_english_short_name(value)

    @field_validator('birth')
    @classmethod
    def valid_date(cls, value: str) -> str:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed > datetime.now(timezone(timedelta(hours=9))).date():
            raise ValueError('오늘까지의 날짜를 YYYY-MM-DD 형식으로 입력하세요.')
        return value

    @field_validator('fromDate')
    @classmethod
    def optional_start_date(cls, value: str) -> str:
        return cls.valid_date(value) if value else ''

    @field_validator('leagueId', 'teamId', 'matchId')
    @classmethod
    def document_id(cls, value: str) -> str:
        if '/' in value or value in {'.', '..'}:
            raise ValueError('올바른 DB 항목을 선택하세요.')
        return value

    @field_validator('seasonId')
    @classmethod
    def valid_season(cls, value: str) -> str:
        if int(value[4:]) != int(value[:4]) + 1:
            raise ValueError('올바른 시즌을 선택하세요.')
        return value

    @field_validator('sourceUrl')
    @classmethod
    def valid_url(cls, value: str) -> str:
        if value and (urlparse(value).scheme not in {'http', 'https'} or not urlparse(value).netloc):
            raise ValueError('참고 링크는 http 또는 https 주소여야 합니다.')
        return value


class PlayerDuplicateLookup(PlayerRegistration):
    lookupPlayerId: str = Field(default='', max_length=100, pattern=r'^[^/]*$')


class PlayerNumberChange(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    leagueId: str = Field(min_length=1, max_length=80)
    seasonId: str = Field(pattern=r'^\d{8}$')
    teamId: str = Field(min_length=1, max_length=80)
    playerId: str = Field(min_length=1, max_length=100)
    contractId: str = Field(min_length=1, max_length=100)
    currentNo: str = Field(pattern=r'^\d{0,3}$')
    newNo: str = Field(pattern=r'^\d{1,3}$')
    changeDate: str = ''

    @field_validator('leagueId', 'teamId', 'playerId', 'contractId')
    @classmethod
    def document_id(cls, value):
        return PlayerRegistration.document_id(value)

    @field_validator('seasonId')
    @classmethod
    def valid_season(cls, value):
        return PlayerRegistration.valid_season(value)

    @field_validator('changeDate')
    @classmethod
    def valid_date(cls, value):
        return PlayerRegistration.valid_date(value) if value else ''

    # Share reference/date checks without adding registration fields to the form.
    @property
    def fromDate(self):
        return self.changeDate

    @property
    def birth(self):
        return ''

    @property
    def matchId(self):
        return ''


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    action: Literal['approve', 'edit']
    version: int = Field(ge=1)
    payload: PlayerRegistration | PlayerNumberChange | None = None
    existingPlayerId: str = Field(default='', max_length=100, pattern=r'^[^/]*$')


class DirectPlayerRegistration(PlayerRegistration):
    requestId: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    nameFull: str = Field(default='', max_length=160)
    birth: str = ''
    nation: str = Field(default='', pattern=r'^(?:[A-Z]{3})?$')
    no: str = Field(default='', pattern=r'^\d{0,3}$')
    pos: Literal['', 'GK', 'DF', 'MF', 'FW'] = ''
    kind: Literal['TRANSFER', 'LOAN', 'FREE', 'YOUTH'] = 'TRANSFER'
    fee: float | None = Field(default=None, ge=0)
    currency: str = Field(default='', max_length=10)

    @field_validator('birth')
    @classmethod
    def valid_date(cls, value: str) -> str:
        return PlayerRegistration.valid_date(value.replace('.', '-')) if value else ''


def registration_identity(payload: PlayerRegistration) -> str:
    identity = f'fullname:{normalize(payload.nameFull)}' if payload.nameFull else f'{normalize(payload.name)}:{normalize(payload.birth)}'
    return sha256(identity.encode()).hexdigest()[:32]


def reserve_player_id(db, tx):
    """Read the numeric ceiling and counter; callers commit the reservation atomically.

    Project only document IDs. PR/random IDs never become numeric candidates.
    Recheck the ceiling to also see later numeric legacy imports.
    """
    state = db.collection('playerRegistrationState').document('numbering')
    counter = state.get(transaction=tx).to_dict() or {}
    latest = int(counter.get('lastAssigned') or 0)
    players = db.collection('players')
    query = players.where(filter=FieldFilter('__name__', '<=', players.document('9999999999')))
    query = query.order_by('__name__', direction='DESCENDING').select([])
    for snapshot in query.stream(transaction=tx):
        if re.fullmatch(r'[0-9]{1,10}', snapshot.id):
            latest = max(latest, int(snapshot.id))
            if len(snapshot.id) == 10:
                break
    if latest >= 9999999999:
        raise BackendError('선수 번호 범위를 초과했습니다.', 409, 'player_id_exhausted')
    return f'{latest + 1:010d}', state, latest + 1


def profile_values(payload: PlayerRegistration, user_id: str) -> dict:
    return {'name': payload.name, 'nameEn': payload.nameEn or None, 'nameFull': payload.nameFull or None,
            'birth': payload.birth.replace('-', '.') or None, 'birthKey': normalize(payload.birth),
            'nation': payload.nation or None, 'foot': payload.foot or None, 'height': payload.height,
            'active': True, 'nameFullKey': normalize(payload.nameFull), 'searchTerms': search_terms(payload), 'createdAt': firestore.SERVER_TIMESTAMP,
            'createdBy': user_id, 'updatedAt': firestore.SERVER_TIMESTAMP, 'updatedBy': user_id}


def is_manager(user: CurrentUser) -> bool:
    return str(user.claims.get('email', '')).strip().lower() == MANAGER_EMAIL


def require_manager(user: CurrentUser) -> None:
    if not is_manager(user):
        raise BackendError('관리자 계정만 검토할 수 있습니다.', 403, 'manager_required')


def check_references(db, payload: PlayerRegistration, *, first_division_only: bool = False) -> dict:
    team = db.collection('teams').document(payload.teamId).get()
    league = db.collection('leagues').document(payload.leagueId).get()
    if not team.exists or not league.exists:
        raise BackendError('DB에 등록된 리그와 팀을 선택하세요.', 422, 'invalid_reference')
    team_data, league_data = team.to_dict() or {}, league.to_dict() or {}
    season = db.collection('teams').document(payload.teamId).collection('seasons').document(payload.seasonId).get()
    entry = season.to_dict() or {}
    if not entry and not payload.matchId and not db.collection('seasons').document(payload.seasonId).get().exists:
        raise BackendError('DB에 등록된 시즌을 선택하세요.', 422, 'invalid_season')
    memberships = [entry.get('leagueId'), *(entry.get('competitionIds') or []), *(entry.get('leagueIds') or []), *(entry.get('cupIds') or [])]
    if first_division_only and payload.leagueId in {'EPL', 'LALIGA'}:
        league_ids = entry.get('leagueIds') or [entry.get('leagueId')]
        belongs = entry.get('division') == 'D1' and payload.leagueId in league_ids
    else:
        belongs = payload.leagueId in memberships or (not entry and team_data.get('currentLeagueId') == payload.leagueId)
    if not belongs:
        raise BackendError('선택한 시즌·리그에 해당하는 팀을 선택하세요.', 422, 'team_season_mismatch')
    if payload.fromDate and payload.birth >= payload.fromDate:
        raise BackendError('명단 적용 시작일은 생년월일 이후여야 합니다.', 422, 'invalid_dates')
    if payload.matchId:
        match = db.collection('matches').document(payload.matchId).get()
        data = match.to_dict() or {}
        if not match.exists or payload.teamId not in {data.get('homeTeamId'), data.get('awayTeamId')} or data.get('leagueId') != payload.leagueId or data.get('seasonId') != payload.seasonId:
            raise BackendError('요청의 경기·리그·시즌·팀 정보가 일치하지 않습니다.', 422, 'match_mismatch')
        if payload.fromDate and payload.fromDate > str(data.get('date', '')).replace('.', '-'):
            raise BackendError('명단 적용 시작일은 해당 경기일 이하여야 합니다.', 422, 'after_match')
    return {'teamName': team_data.get('nameKr') or team_data.get('name') or payload.teamId,
            'leagueName': league_data.get('name') or payload.leagueId,
            'competitionType': league_data.get('competitionType') or 'league'}


def duplicate_players(db, payload: PlayerRegistration, player_id: str = '') -> list[dict]:
    fullname = normalize(payload.nameFull)
    matches = []
    players = db.collection('players')
    if not fullname:
        return []
    queries = [players.where(filter=FieldFilter('nameFull', '==', payload.nameFull)),
               players.where(filter=FieldFilter('nameFullKey', '==', fullname)),
               players.where(filter=FieldFilter('searchTerms', 'array_contains', fullname))]
    # Birth/display names never qualify a candidate. Legacy exact full names
    # remain searchable without a backfill; normalized indexes cover newer data.
    candidates = {s.id: s for query in queries for s in query.stream()}
    if player_id:
        ids = {player_id}
        if player_id.isascii() and player_id.isdigit() and len(player_id) < 10:
            ids.add(player_id.zfill(10))
        snapshots = [players.document(key).get() for key in ids]
        found = [snapshot for snapshot in snapshots if snapshot.exists]
        if not found:
            raise BackendError('해당 p_id의 선수를 찾을 수 없습니다.', 422, 'invalid_player')
        matching = [snapshot for snapshot in found if normalize(str((snapshot.to_dict() or {}).get('nameFull') or '')) == fullname]
        if not matching:
            raise BackendError('입력한 p_id의 영문 FULLNAME이 요청과 다릅니다.', 422, 'player_fullname_mismatch')
        candidates.update({snapshot.id: snapshot for snapshot in matching})
    for snapshot in candidates.values():
        player = snapshot.to_dict() or {}
        if normalize(str(player.get('nameFull') or '')) == fullname:
            matches.append({'id': snapshot.id, 'name': player.get('name'), 'nameFull': player.get('nameFull'),
                            'birth': player.get('birth'), 'currentTeamId': player.get('currentTeamId')})
    return sorted(matches, key=lambda p: p['id'])


def search_terms(payload: PlayerRegistration) -> list[str]:
    terms = set()
    for value in (payload.name, payload.nameEn, payload.nameFull):
        for part in [value, *re.split(r'[\s.,()\-_/]+', value)]:
            text = normalize(part)
            terms.update(text[:length] for length in range(min(2, len(text)), len(text) + 1) if text)
    birth = normalize(payload.birth)
    terms.update(birth[:length] for length in range(4, len(birth) + 1))
    return sorted(terms)


class PlayerRequestService:
    def __init__(self, db=None):
        self.db = db if db is not None else get_firestore_client()

    def submit(self, payload: PlayerRegistration | PlayerNumberChange, user: CurrentUser):
        if isinstance(payload, PlayerNumberChange):
            return self.submit_number_change(payload, user)
        labels = check_references(self.db, payload, first_division_only=True)
        ref = self.db.collection('playerRegistrationRequests').document()

        @firestore.transactional
        def save(tx):
            tx.create(ref, {'requestType': 'REGISTRATION', 'payload': payload.model_dump(), 'submittedPayload': payload.model_dump(), **labels, 'status': 'pending',
                         'requestedBy': user.uid, 'requestedName': user.claims.get('name') or user.claims.get('email') or user.uid,
                         'createdAt': firestore.SERVER_TIMESTAMP, 'updatedAt': firestore.SERVER_TIMESTAMP,
                         'version': 1})
            return {'id': ref.id, 'status': 'pending'}
        return save(self.db.transaction())

    def list(self, user: CurrentUser):
        query = self.db.collection('playerRegistrationRequests')
        if not is_manager(user):
            query = query.where(filter=FieldFilter('requestedBy', '==', user.uid))
        return sorted([{'id': s.id, **(s.to_dict() or {})} for s in query.stream()],
                      key=lambda r: str(r.get('createdAt', '')), reverse=True)

    def review(self, request_id: str, decision: ReviewDecision, user: CurrentUser):
        require_manager(user)
        ref = self.db.collection('playerRegistrationRequests').document(request_id)
        initial = ref.get().to_dict() or {}
        if not initial:
            raise BackendError('요청을 찾을 수 없습니다.', 404, 'not_found')
        if initial.get('requestType') == 'NUMBER_CHANGE':
            return self.review_number_change(request_id, decision, user)
        if isinstance(decision.payload, PlayerNumberChange):
            raise BackendError('요청 종류를 변경할 수 없습니다.', 422, 'request_type_mismatch')
        if initial.get('status') == 'approved' and decision.action == 'approve':
            return {'id': request_id, 'status': 'approved', 'playerId': initial['playerId']}
        payload = decision.payload or PlayerRegistration.model_validate(initial['payload'])
        labels = check_references(self.db, payload, first_division_only=True)
        if decision.action == 'approve' and not payload.fromDate:
            raise BackendError('승인 전에 명단 적용 시작일을 확인해 입력하세요.', 422, 'start_date_required')
        duplicates = duplicate_players(self.db, payload) if decision.action == 'approve' else []
        if decision.action == 'approve' and not decision.existingPlayerId and duplicates:
            raise BackendError('영문 FULLNAME이 일치하는 기존 선수의 p_id를 확인하고 연결하세요.', 409, 'duplicate_player')
        identity = registration_identity(payload)

        @firestore.transactional
        def publish(tx):
            current = ref.get(transaction=tx).to_dict() or {}
            if current.get('status') == 'approved' and decision.action == 'approve':
                return {'id': request_id, 'status': 'approved', 'playerId': current['playerId']}
            if current.get('status') != 'pending' or current.get('version') != decision.version:
                raise BackendError('다른 검토자가 변경했습니다. 목록을 다시 불러오세요.', 409, 'request_conflict')
            audit = {'reviewedBy': user.uid, 'reviewedAt': firestore.SERVER_TIMESTAMP,
                     'updatedAt': firestore.SERVER_TIMESTAMP, 'version': decision.version + 1}
            if decision.action == 'edit':
                tx.update(ref, {**audit, **labels, 'payload': payload.model_dump()})
                return {'id': request_id, 'status': 'pending'}
            allocation = None
            identity_ref = self.db.collection('playerRegistrationState').document('numbering').collection('identities').document(identity)
            if decision.existingPlayerId:
                player_id = decision.existingPlayerId
            else:
                if identity_ref.get(transaction=tx).exists:
                    raise BackendError('이미 등록된 선수입니다. 기존 선수를 선택하세요.', 409, 'duplicate_player')
                player_id, counter_ref, number = reserve_player_id(self.db, tx)
                allocation = (counter_ref, number)
            player_ref = self.db.collection('players').document(player_id)
            contract_ref = player_ref.collection('contracts').document(f'request-{request_id}')
            player = player_ref.get(transaction=tx)
            if player.exists and not decision.existingPlayerId:
                raise BackendError('이미 등록된 선수입니다. 기존 선수를 선택하세요.', 409, 'duplicate_player')
            if decision.existingPlayerId and not player.exists:
                raise BackendError('기존 선수를 찾을 수 없습니다.', 422, 'invalid_player')
            if decision.existingPlayerId and normalize(str((player.to_dict() or {}).get('nameFull') or '')) != normalize(payload.nameFull):
                raise BackendError('선택한 p_id의 영문 FULLNAME이 요청과 다릅니다.', 422, 'player_fullname_mismatch')
            contracts = list(player_ref.collection('contracts').stream(transaction=tx)) if player.exists else []
            target = normalize(payload.fromDate)
            covering = []
            for snapshot in contracts:
                contract = snapshot.to_dict() or {}
                start, end = normalize(str(contract.get('from') or '')), normalize(str(contract.get('to') or ''))
                if (not end or end >= target) and labels['competitionType'] != 'national' and contract.get('competitionType') != 'national':
                    if contract.get('teamId') != payload.teamId or start > target:
                        raise BackendError('소속 기간이 겹치는 기존 계약이 있습니다. 선수 관리에서 소속을 먼저 확인하세요.', 409, 'affiliation_conflict')
                if start <= target and (not end or end >= target):
                    if contract.get('teamId') == payload.teamId:
                        covering.append(contract)
                    elif labels['competitionType'] != 'national' and contract.get('competitionType') != 'national':
                        raise BackendError('다른 팀 소속 이력이 있습니다. 선수 관리에서 이적·콜업 소속을 먼저 확인하세요.', 409, 'affiliation_conflict')
            # Invalidate only cached squad lists, on the next normal entry.
            # Final recordings and saved lineup placements are never changed.
            revision_ref = self.db.collection('playerRegistrationRosterVersions').document(payload.teamId)
            revision = revision_ref.get(transaction=tx).to_dict() or {}
            audit_player = {'updatedAt': firestore.SERVER_TIMESTAMP, 'updatedBy': user.uid}
            if not player.exists:
                tx.create(player_ref, {**profile_values(payload, user.uid),
                    **({'currentTeamId': payload.teamId, 'currentNo': payload.no, 'currentPos': payload.pos}
                       if labels['competitionType'] != 'national' else {}),
                })
                if allocation:
                    tx.set(allocation[0], {'lastAssigned': allocation[1]}, merge=True)
                    tx.create(identity_ref, {'playerId': player_id})
            if not covering:
                tx.create(contract_ref, {'teamId': payload.teamId, 'leagueId': payload.leagueId,
                    'competitionType': labels['competitionType'], 'from': payload.fromDate.replace('-', '.'), 'to': None,
                    'no': payload.no, 'pos': payload.pos,
                    'transferType': 'YOUTH' if payload.kind == 'YOUTH' else ('LOAN' if payload.kind == 'LOAN' else 'TRANSFER'),
                    'registrationRequestId': request_id, 'sourceUrl': payload.sourceUrl,
                    'createdAt': firestore.SERVER_TIMESTAMP, 'createdBy': user.uid, **audit_player})
                if labels['competitionType'] != 'national':
                    tx.set(player_ref, {'currentTeamId': payload.teamId, 'currentNo': payload.no, 'currentPos': payload.pos, **audit_player}, merge=True)
                    tx.set(self.db.collection('teams').document(payload.teamId).collection('squad').document(player_id),
                           {'name': (player.to_dict() or {}).get('name') or payload.name, 'no': payload.no, 'pos': payload.pos,
                            'contractId': contract_ref.id, 'since': payload.fromDate.replace('-', '.')})
                tx.set(revision_ref, {'version': revision.get('version', 0) + 1})
            tx.update(ref, {**audit, **labels, 'status': 'approved', 'payload': payload.model_dump(), 'playerId': player_id})
            return {'id': request_id, 'status': 'approved', 'playerId': player_id}
        return publish(self.db.transaction())

    def number_change_source(self, payload: PlayerNumberChange, tx=None):
        target = payload.changeDate.replace('-', '.')
        year = payload.seasonId[:4]
        season_start, season_end = f'{year}.08.01', f'{payload.seasonId[4:]}.05.31'
        if target and not season_start <= target <= season_end:
            raise BackendError('변경 날짜는 선택한 시즌 기간 안이어야 합니다.', 422, 'outside_season')
        player_ref = self.db.collection('players').document(payload.playerId)
        player = player_ref.get(transaction=tx)
        contracts = list(player_ref.collection('contracts').stream(transaction=tx))
        covering = [snapshot for snapshot in contracts if (entry := snapshot.to_dict() or {}).get('teamId') == payload.teamId
                    and entry.get('competitionType') != 'national'
                    and str(entry.get('from') or '').replace('-', '.') <= (target or season_end)
                    and (not entry.get('to') or str(entry['to']).replace('-', '.') >= (target or season_start))
                    and (bool(target) or snapshot.id == payload.contractId)]
        if not player.exists or len(covering) != 1 or covering[0].id != payload.contractId:
            raise BackendError('변경 날짜의 팀 소속 이력을 다시 선택하세요.', 409, 'number_contract_mismatch')
        contract = covering[0]
        source_no = str((contract.to_dict() or {}).get('no') if (contract.to_dict() or {}).get('no') is not None else '')
        if source_no != payload.currentNo:
            raise BackendError('현재 등번호가 변경됐습니다. 선수 목록을 다시 불러오세요.', 409, 'number_changed')
        if source_no and int(source_no) == int(payload.newNo):
            raise BackendError('변경할 등번호가 현재 등번호와 같습니다.', 422, 'same_number')
        return player, contract

    def submit_number_change(self, payload: PlayerNumberChange, user: CurrentUser):
        labels = check_references(self.db, payload, first_division_only=True)
        ref = self.db.collection('playerRegistrationRequests').document()

        @firestore.transactional
        def save(tx):
            player, _ = self.number_change_source(payload, tx)
            tx.create(ref, {'requestType': 'NUMBER_CHANGE', 'payload': payload.model_dump(),
                'submittedPayload': payload.model_dump(), **labels, 'playerName': (player.to_dict() or {}).get('name') or payload.playerId,
                'status': 'pending', 'requestedBy': user.uid,
                'requestedName': user.claims.get('name') or user.claims.get('email') or user.uid,
                'createdAt': firestore.SERVER_TIMESTAMP, 'updatedAt': firestore.SERVER_TIMESTAMP, 'version': 1})
            return {'id': ref.id, 'status': 'pending'}
        return save(self.db.transaction())

    def review_number_change(self, request_id: str, decision: ReviewDecision, user: CurrentUser):
        require_manager(user)
        ref = self.db.collection('playerRegistrationRequests').document(request_id)

        @firestore.transactional
        def publish(tx):
            current = ref.get(transaction=tx).to_dict() or {}
            if current.get('requestType') != 'NUMBER_CHANGE':
                raise BackendError('요청 종류를 확인하세요.', 422, 'request_type_mismatch')
            if current.get('status') == 'approved' and decision.action == 'approve':
                return {'id': request_id, 'status': 'approved', 'playerId': current['playerId']}
            if current.get('status') != 'pending' or current.get('version') != decision.version:
                raise BackendError('다른 검토자가 변경했습니다. 목록을 다시 불러오세요.', 409, 'request_conflict')
            payload = decision.payload or PlayerNumberChange.model_validate(current['payload'])
            if not isinstance(payload, PlayerNumberChange) or decision.existingPlayerId:
                raise BackendError('등번호 변경 요청 내용을 확인하세요.', 422, 'request_type_mismatch')
            if decision.action == 'approve' and not payload.changeDate:
                raise BackendError('승인 전에 등번호 변경 날짜를 확인해 입력하세요.', 422, 'change_date_required')
            player, source = self.number_change_source(payload, tx)
            revision_ref = self.db.collection('playerRegistrationRosterVersions').document(payload.teamId)
            revision = revision_ref.get(transaction=tx).to_dict() or {}
            squad_ref = self.db.collection('teams').document(payload.teamId).collection('squad').document(payload.playerId)
            squad = squad_ref.get(transaction=tx).to_dict() or {}
            audit = {'updatedAt': firestore.SERVER_TIMESTAMP, 'reviewedAt': firestore.SERVER_TIMESTAMP,
                     'reviewedBy': user.uid, 'version': decision.version + 1}
            if decision.action == 'approve':
                contract = source.to_dict() or {}
                target = payload.changeDate.replace('-', '.')
                today = datetime.now(timezone(timedelta(hours=9))).date().isoformat().replace('-', '.')
                active = str(contract.get('from') or '').replace('-', '.') <= today and (not contract.get('to') or str(contract['to']).replace('-', '.') >= today)
                player_audit = {'updatedAt': firestore.SERVER_TIMESTAMP, 'updatedBy': user.uid}
                if target == str(contract.get('from') or '').replace('-', '.'):
                    next_ref = source.reference
                    tx.update(next_ref, {'no': payload.newNo, **player_audit})
                else:
                    next_ref = player.reference.collection('contracts').document('number-request-' + request_id)
                    previous_day = (date.fromisoformat(payload.changeDate) - timedelta(days=1)).isoformat().replace('-', '.')
                    tx.update(source.reference, {'to': previous_day, **player_audit})
                    tx.create(next_ref, {**contract, 'from': target, 'no': payload.newNo,
                        'numberChangeRequestId': request_id, 'createdAt': firestore.SERVER_TIMESTAMP,
                        'createdBy': user.uid, **player_audit})
                if active and (player.to_dict() or {}).get('currentTeamId') == payload.teamId:
                    tx.update(player.reference, {'currentNo': payload.newNo, **player_audit})
                if active and squad.get('contractId') == source.id:
                    tx.set(squad_ref, {'no': payload.newNo, 'contractId': next_ref.id}, merge=True)
                tx.set(revision_ref, {'version': revision.get('version', 0) + 1})
            tx.update(ref, {**audit, 'payload': payload.model_dump(),
                'playerName': (player.to_dict() or {}).get('name') or payload.playerId,
                **({'status': 'approved', 'playerId': payload.playerId} if decision.action == 'approve' else {})})
            return {'id': request_id, 'status': 'approved' if decision.action == 'approve' else 'pending', 'playerId': payload.playerId}
        # Revalidate season membership when reviewing, just as on initial submission.
        stored = ref.get().to_dict() or {}
        payload = decision.payload or PlayerNumberChange.model_validate(stored.get('payload'))
        if not isinstance(payload, PlayerNumberChange):
            raise BackendError('요청 종류를 변경할 수 없습니다.', 422, 'request_type_mismatch')
        if stored.get('status') != 'approved':
            check_references(self.db, payload, first_division_only=True)
        return publish(self.db.transaction())

    def create_player(self, payload: DirectPlayerRegistration, user: CurrentUser):
        """Manager's direct registration shares approval's numeric ID allocator."""
        require_manager(user)
        if not payload.fromDate:
            raise BackendError('입단일을 입력하세요.', 422, 'start_date_required')
        labels = check_references(self.db, payload)
        state = self.db.collection('playerRegistrationState').document('numbering')
        receipt = state.collection('directRequests').document(payload.requestId)
        identity_ref = state.collection('identities').document(registration_identity(payload))

        @firestore.transactional
        def publish(tx):
            previous = receipt.get(transaction=tx).to_dict() or {}
            if previous:
                if previous.get('payload') != payload.model_dump() or previous.get('createdBy') != user.uid:
                    raise BackendError('등록 요청 번호가 다른 내용에 사용됐습니다.', 409, 'request_conflict')
                return {'playerId': previous['playerId']}
            if identity_ref.get(transaction=tx).exists:
                raise BackendError('영문 FULLNAME이 같은 선수가 이미 등록됐습니다.', 409, 'duplicate_player')
            player_id, counter_ref, number = reserve_player_id(self.db, tx)
            player_ref = self.db.collection('players').document(player_id)
            if player_ref.get(transaction=tx).exists:
                raise BackendError('선수 번호가 이미 사용 중입니다. 다시 시도하세요.', 409, 'player_id_conflict')
            contract_ref = player_ref.collection('contracts').document('manual-' + payload.requestId)
            revision_ref = self.db.collection('playerRegistrationRosterVersions').document(payload.teamId)
            revision = revision_ref.get(transaction=tx).to_dict() or {}
            audit = {'updatedAt': firestore.SERVER_TIMESTAMP, 'updatedBy': user.uid,
                     'createdAt': firestore.SERVER_TIMESTAMP, 'createdBy': user.uid}
            tx.create(player_ref, {**profile_values(payload, user.uid),
                **({'currentTeamId': payload.teamId, 'currentNo': payload.no or None, 'currentPos': payload.pos or None}
                   if labels['competitionType'] != 'national' else {})})
            tx.create(contract_ref, {**audit, 'teamId': payload.teamId, 'leagueId': payload.leagueId,
                'competitionType': labels['competitionType'], 'from': payload.fromDate.replace('-', '.'), 'to': None,
                'no': payload.no or None, 'pos': payload.pos or None, 'transferType': payload.kind,
                'fee': payload.fee, 'currency': payload.currency or None})
            if labels['competitionType'] != 'national':
                tx.set(self.db.collection('teams').document(payload.teamId).collection('squad').document(player_id),
                    {'name': payload.name, 'no': payload.no, 'pos': payload.pos,
                     'contractId': contract_ref.id, 'since': payload.fromDate.replace('-', '.')})
            tx.set(revision_ref, {'version': revision.get('version', 0) + 1})
            tx.set(counter_ref, {'lastAssigned': number}, merge=True)
            tx.create(identity_ref, {'playerId': player_id})
            tx.create(receipt, {'playerId': player_id, 'payload': payload.model_dump(), 'createdBy': user.uid})
            return {'playerId': player_id}
        return publish(self.db.transaction())
