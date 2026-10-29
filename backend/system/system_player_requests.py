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


class PlayerRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    leagueId: str = Field(min_length=1, max_length=80)
    seasonId: str = Field(pattern=r'^\d{8}$')
    teamId: str = Field(min_length=1, max_length=80)
    matchId: str = Field(default='', max_length=100)
    kind: Literal['NEW', 'YOUTH'] = 'NEW'
    name: str = Field(min_length=1, max_length=100)
    nameEn: str = Field(default='', max_length=100)
    nameFull: str = Field(min_length=1, max_length=160)
    birth: str
    nation: str = Field(pattern=r'^[A-Z]{3}$')
    no: str = Field(pattern=r'^\d{1,3}$')
    pos: Literal['GK', 'DF', 'MF', 'FW']
    foot: Literal['', 'L', 'R', 'B'] = ''
    height: int | None = Field(default=None, ge=100, le=250)
    fromDate: str
    sourceUrl: str = Field(default='', max_length=2000)
    note: str = Field(default='', max_length=2000)

    @field_validator('birth', 'fromDate')
    @classmethod
    def valid_date(cls, value: str) -> str:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed > datetime.now(timezone(timedelta(hours=9))).date():
            raise ValueError('오늘까지의 날짜를 YYYY-MM-DD 형식으로 입력하세요.')
        return value

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


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    action: Literal['approve', 'edit']
    version: int = Field(ge=1)
    payload: PlayerRegistration | None = None
    existingPlayerId: str = Field(default='', max_length=100, pattern=r'^[^/]*$')


def is_manager(user: CurrentUser) -> bool:
    return str(user.claims.get('email', '')).strip().lower() == MANAGER_EMAIL


def require_manager(user: CurrentUser) -> None:
    if not is_manager(user):
        raise BackendError('관리자 계정만 검토할 수 있습니다.', 403, 'manager_required')


def check_references(db, payload: PlayerRegistration) -> dict:
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
    if payload.leagueId not in memberships and not (not entry and team_data.get('currentLeagueId') == payload.leagueId):
        raise BackendError('선택한 시즌·리그에 해당하는 팀을 선택하세요.', 422, 'team_season_mismatch')
    if payload.birth >= payload.fromDate:
        raise BackendError('명단 적용 시작일은 생년월일 이후여야 합니다.', 422, 'invalid_dates')
    if payload.matchId:
        match = db.collection('matches').document(payload.matchId).get()
        data = match.to_dict() or {}
        if not match.exists or payload.teamId not in {data.get('homeTeamId'), data.get('awayTeamId')} or data.get('leagueId') != payload.leagueId or data.get('seasonId') != payload.seasonId:
            raise BackendError('요청의 경기·리그·시즌·팀 정보가 일치하지 않습니다.', 422, 'match_mismatch')
        if payload.fromDate > str(data.get('date', '')).replace('.', '-'):
            raise BackendError('명단 적용 시작일은 해당 경기일 이하여야 합니다.', 422, 'after_match')
    return {'teamName': team_data.get('nameKr') or team_data.get('name') or payload.teamId,
            'leagueName': league_data.get('name') or payload.leagueId,
            'competitionType': league_data.get('competitionType') or 'league'}


def duplicate_players(db, payload: PlayerRegistration) -> list[dict]:
    names = {normalize(v) for v in (payload.name, payload.nameEn, payload.nameFull) if v}
    matches = []
    players = db.collection('players')
    queries = [players.where(filter=FieldFilter('birthKey', '==', normalize(payload.birth))),
               players.where(filter=FieldFilter('birth', 'in', [payload.birth, payload.birth.replace('-', '.'), normalize(payload.birth)]))]
    display_names = list({v for v in (payload.name, payload.nameEn, payload.nameFull) if v})
    queries.extend(players.where(filter=FieldFilter(field, 'in', display_names)) for field in ('name', 'nameEn', 'nameFull'))
    # Indexed lookups also cover legacy documents without normalized birthKey.
    # Never scan the full player DB for every request or approval.
    candidates = {s.id: s for query in queries for s in query.stream()}
    for snapshot in candidates.values():
        player = snapshot.to_dict() or {}
        same_name = bool(names & {normalize(str(player.get(key) or '')) for key in ('name', 'nameEn', 'nameFull')})
        same_birth = normalize(str(player.get('birth') or '')) == normalize(payload.birth)
        if same_name:
            matches.append({'id': snapshot.id, 'name': player.get('name'), 'nameFull': player.get('nameFull'),
                            'birth': player.get('birth'), 'currentTeamId': player.get('currentTeamId'), 'sameBirth': same_birth})
    return sorted(matches, key=lambda p: not p['sameBirth'])


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

    def submit(self, payload: PlayerRegistration, user: CurrentUser):
        labels = check_references(self.db, payload)
        ref = self.db.collection('playerRegistrationRequests').document()

        @firestore.transactional
        def save(tx):
            tx.create(ref, {'payload': payload.model_dump(), 'submittedPayload': payload.model_dump(), **labels, 'status': 'pending',
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
        if initial.get('status') == 'approved' and decision.action == 'approve':
            return {'id': request_id, 'status': 'approved', 'playerId': initial['playerId']}
        payload = decision.payload or PlayerRegistration.model_validate(initial['payload'])
        labels = check_references(self.db, payload)
        duplicates = duplicate_players(self.db, payload) if decision.action == 'approve' else []
        if decision.action == 'approve' and not decision.existingPlayerId and any(p['sameBirth'] for p in duplicates):
            raise BackendError('이름·생년월일이 일치하는 기존 선수를 선택하세요.', 409, 'duplicate_player')
        identity = sha256(f'{normalize(payload.nameFull)}:{normalize(payload.birth)}'.encode()).hexdigest()[:32]
        player_id = decision.existingPlayerId or f'PR-{identity}'
        player_ref = self.db.collection('players').document(player_id)
        contract_ref = player_ref.collection('contracts').document(f'request-{request_id}')

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
            player = player_ref.get(transaction=tx)
            if player.exists and not decision.existingPlayerId:
                raise BackendError('이미 등록된 선수입니다. 기존 선수를 선택하세요.', 409, 'duplicate_player')
            if decision.existingPlayerId and not player.exists:
                raise BackendError('기존 선수를 찾을 수 없습니다.', 422, 'invalid_player')
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
                tx.create(player_ref, {
                    'name': payload.name, 'nameEn': payload.nameEn or None, 'nameFull': payload.nameFull,
                    'birth': payload.birth.replace('-', '.'), 'birthKey': normalize(payload.birth),
                    'nation': payload.nation, 'foot': payload.foot or None, 'height': payload.height,
                    'active': True, 'searchTerms': search_terms(payload), 'createdAt': firestore.SERVER_TIMESTAMP,
                    'createdBy': user.uid, **audit_player,
                    **({'currentTeamId': payload.teamId, 'currentNo': payload.no, 'currentPos': payload.pos}
                       if labels['competitionType'] != 'national' else {}),
                })
            if not covering:
                tx.create(contract_ref, {'teamId': payload.teamId, 'leagueId': payload.leagueId,
                    'competitionType': labels['competitionType'], 'from': payload.fromDate.replace('-', '.'), 'to': None,
                    'no': payload.no, 'pos': payload.pos, 'transferType': 'YOUTH' if payload.kind == 'YOUTH' else 'TRANSFER',
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
