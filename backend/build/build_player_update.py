"""Synchronize EPL player and contract changes from aifootballx Player."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import logging
import math
import re
from time import monotonic
from types import SimpleNamespace
from typing import Callable

from firebase_admin import firestore
from google.api_core.exceptions import FailedPrecondition, GoogleAPICallError, RetryError
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.build.build_legacy_import import normalize_legacy_contract_end, normalize_legacy_date
from backend.system.system_aifootballx import target_client
from backend.system.system_firestore import BackendError, get_firestore_client
from backend.system.system_player_requests import normalize, search_terms

PROFILE_FIELDS = {
    'p_name': 'name', 'p_name_en': 'nameEn', 'p_name_full': 'nameFull',
    'p_birth': 'birth', 'p_foot': 'foot', 'p_height': 'height', 'p_nation': 'nation',
}
SOURCE_FIELDS = [*PROFILE_FIELDS, 'p_id', 'DID_PLAYER_ID', 't_code', 'pt_begin', 'pt_end', 'pt_num', 'pt_pos']
COUNTERS = ('playersCreated', 'playersUpdated', 'contractsCreated', 'contractsUpdated', 'squadsUpdated')
KST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)
PROFILE_BATCH_SIZE = 150
TEAM_BATCH_SIZE = 25
PREFETCH_MIN_PLAYERS = 32
UPDATE_WORKERS = 8


def _problem(message: str) -> BackendError:
    return BackendError(message, 422, 'invalid_player_update')


def _text(value) -> str:
    return '' if value is None else str(value).strip()


def _date(value, *, open_end=False) -> str | None:
    value = normalize_legacy_contract_end(value) if open_end else normalize_legacy_date(value)
    if value in (None, '', '0000-00-00'):
        return value
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise _problem(f'날짜 형식이 잘못되었습니다: {value}') from exc


def player_update_season(value: str | None) -> str:
    if not value:
        now = datetime.now(KST)
        year = now.year if now.month >= 8 else now.year - 1
        return f'{year}-{year + 1}'
    match = re.fullmatch(r'(\d{4})-(\d{4})', value.strip())
    if not match or not 1900 <= int(match[1]) < 9999 or int(match[2]) != int(match[1]) + 1:
        raise _problem('시즌을 YYYY-YYYY 형식으로 입력하세요.')
    return match[0]


def _player_id(row: dict) -> tuple[str, str]:
    numeric = _text(row.get('p_id'))
    if not numeric.isascii() or not numeric.isdigit() or not 0 < int(numeric) < 2**53:
        raise _problem('Player.p_id가 유효한 숫자 ID가 아닙니다.')
    canonical = str(int(numeric)).zfill(10)
    explicit = _text(row.get('DID_PLAYER_ID'))
    if explicit:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', explicit):
            raise _problem('DID_PLAYER_ID 형식이 잘못되었습니다.')
        if explicit.isdigit() and explicit.zfill(10) != canonical:
            raise _problem('p_id와 DID_PLAYER_ID가 서로 다릅니다.')
        return canonical, explicit.zfill(10) if explicit.isdigit() else explicit
    if len(canonical) != 10:
        raise _problem('10자리 범위를 벗어난 p_id에는 DID_PLAYER_ID가 필요합니다.')
    return canonical, canonical


def _profile(row: dict) -> dict:
    result = {}
    for source, field in PROFILE_FIELDS.items():
        value = _text(row.get(source))
        if not value:
            continue
        if field == 'birth':
            parsed = _date(value)
            if not parsed or parsed == '0000-00-00':
                continue
            value = parsed.replace('-', '.')
        elif field == 'foot':
            value = value.upper()
            if value not in {'L', 'R', 'B'}:
                raise _problem('주발 값이 L/R/B 형식과 다릅니다.')
        elif field == 'height':
            try:
                value = float(value)
                if value == 0:
                    continue
                if not math.isfinite(value) or value < 0:
                    raise ValueError
            except ValueError as exc:
                raise _problem('키가 유효한 숫자가 아닙니다.') from exc
        result[field] = value
    return result


def _source_contract(row: dict, season: str) -> dict | None:
    start, end = _date(row.get('pt_begin')), _date(row.get('pt_end'), open_end=True)
    if (end and end < f'{season[:4]}-08-01') or (start and start > f'{season[-4:]}-05-31'):
        return None
    team = _text(row.get('t_code'))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', team) or not start or 'pt_end' not in row:
        raise _problem('팀 코드, 계약 시작일 또는 종료일 필드가 빠졌습니다.')
    if end and end < start:
        raise _problem('계약 종료일이 시작일보다 앞섭니다.')
    fields = {'teamId': team, 'leagueId': 'EPL', 'competitionType': 'league', 'from': start, 'to': end}
    number, pos = _text(row.get('pt_num')), _text(row.get('pt_pos')).upper()
    if number and number != '0':
        if not re.fullmatch(r'[0-9]{1,3}', number):
            raise _problem('등번호 형식이 잘못되었습니다.')
        fields['no'] = number
    if pos:
        fields['pos'] = pos
    return fields


def _same(field, first, second):
    if field in {'from', 'birth'}:
        return normalize_legacy_date(first) == normalize_legacy_date(second)
    if field == 'to':
        return normalize_legacy_contract_end(first) == normalize_legacy_contract_end(second)
    if field == 'no':
        return _text(first) == _text(second)
    return first == second


def _merge(target: dict, values: dict, label: str) -> None:
    for field, value in values.items():
        if field in target and not _same(field, target[field], value):
            raise _problem(f'{label}의 {field} 값이 Player 문서 간에 다릅니다.')
        target[field] = value


def _is_club(contract: dict) -> bool:
    return contract.get('competitionType') not in {'national', 'cup', 'test'} and bool(contract.get('teamId'))


def _period(contract: dict) -> tuple[str, str]:
    return normalize_legacy_date(contract.get('from')), normalize_legacy_contract_end(contract.get('to')) or '9999-12-31'


def _current(contracts: dict, today: str) -> list[tuple[str, dict]]:
    return [(key, c) for key, c in contracts.items() if _is_club(c) and (period := _period(c))[0] and period[0] <= today <= period[1]]


def _identity_fields(profile: dict) -> dict:
    identity = SimpleNamespace(**{key: _text(profile.get(key)) for key in ('name', 'nameEn', 'nameFull', 'birth')})
    return dict(nameFullKey=normalize(identity.nameFull), birthKey=normalize(identity.birth), searchTerms=search_terms(identity))


def _unchanged_players(db, plans: dict, season: str) -> set[str]:
    """Use projected batch reads to skip no-op player transactions."""
    if len(plans) < PREFETCH_MIN_PLAYERS:
        return set()
    ids = sorted(plans)
    players = db.collection('players')
    profiles = {}
    for offset in range(0, len(ids), PROFILE_BATCH_SIZE):
        refs = [players.document(pid) for pid in ids[offset:offset + PROFILE_BATCH_SIZE]]
        for snap in db.get_all(refs, field_paths=list(PROFILE_FIELDS.values()), timeout=45):
            if snap.exists:
                profiles[snap.id] = snap.to_dict() or {}
    candidates = {
        pid for pid, data in profiles.items()
        if all(_same(field, data.get(field), value) for field, value in plans[pid][0].items())
    }
    if len(candidates) < PREFETCH_MIN_PLAYERS:
        return set()
    wanted = {(pid, *key) for pid in candidates for key in plans[pid][1]}
    teams = sorted({team for _, team, _ in wanted})
    contracts = defaultdict(list)
    # Use the same collection-group indexes as the team/season view. These
    # bounds include both dot and dash dates; identity is checked after reading.
    lower, upper = f'{season[:4]}-08-01', f'{season[-4:]}.05.31'
    try:
        for offset in range(0, len(teams), TEAM_BATCH_SIZE):
            base = db.collection_group('contracts').where(filter=FieldFilter('teamId', 'in', teams[offset:offset + TEAM_BATCH_SIZE]))
            fields = ['teamId', 'leagueId', 'competitionType', 'from', 'to', 'no', 'pos']
            for operation, end in [('==', None), ('>=', lower)]:
                query = base.where(filter=FieldFilter('to', operation, end)).where(filter=FieldFilter('from', '<=', upper)).select(fields)
                for snap in query.stream(timeout=60):
                    parent = snap.reference.parent.parent
                    if parent is None or parent.parent.id != 'players' or parent.id not in candidates:
                        continue
                    data = snap.to_dict() or {}
                    key = (parent.id, data.get('teamId'), normalize_legacy_date(data.get('from')))
                    if key in wanted and _is_club(data) and data.get('leagueId') in {None, '', 'EPL'}:
                        contracts[key].append(data)
    except FailedPrecondition:
        logger.warning('Player update contract prefetch index unavailable; using per-player transactions')
        return set()

    def matches(pid, key, values):
        current = contracts.get((pid, *key), ())
        return len(current) == 1 and all(_same(field, current[0].get(field), value) for field, value in values.items())

    return {pid for pid in candidates if all(matches(pid, key, values) for key, values in plans[pid][1].items())}


def _apply_player(db, pid: str, profile: dict, source_contracts: dict, actor: str, today: str) -> dict:
    audit = {'updatedAt': firestore.SERVER_TIMESTAMP, 'updatedBy': actor}
    created = {'createdAt': firestore.SERVER_TIMESTAMP, 'createdBy': actor}
    ref = db.collection('players').document(pid)

    @firestore.transactional
    def apply(tx):
        counts = dict.fromkeys(COUNTERS, 0)
        writes = []
        snapshot = ref.get(transaction=tx)
        player = snapshot.to_dict() or {}
        if not snapshot.exists:
            if not all(profile.get(key) for key in ('name', 'nameFull', 'birth')):
                raise _problem('신규 선수의 이름, 영문 전체 이름, 생년월일이 필요합니다.')
            if pid.isdigit() and db.collection('players').document(str(int(pid))).get(transaction=tx).exists:
                raise _problem('짧은 ID로 저장된 기존 선수 문서가 있습니다.')
            for field, value in [('nameFull', profile['nameFull']), ('nameFullKey', normalize(profile['nameFull']))]:
                query = db.collection('players').where(filter=FieldFilter(field, '==', value))
                if any(_same('birth', doc.to_dict().get('birth'), profile['birth']) for doc in query.stream(transaction=tx)):
                    raise _problem('영문 전체 이름과 생년월일이 같은 다른 선수 ID가 있습니다.')
        patch = {key: value for key, value in profile.items() if not _same(key, player.get(key), value)}
        if not snapshot.exists or {'name', 'nameEn', 'nameFull', 'birth'} & patch.keys():
            patch.update(_identity_fields(player | patch))

        collection = ref.collection('contracts')
        contracts = {doc.id: doc.to_dict() or {} for doc in collection.stream(transaction=tx)}
        old_current = _current(contracts, today)
        new_keys = set()
        for (team, start), fields in source_contracts.items():
            matches = [key for key, c in contracts.items() if c.get('teamId') == team and _is_club(c)
                       and c.get('leagueId') in {None, '', 'EPL'} and _same('from', c.get('from'), start)]
            if len(matches) > 1:
                raise _problem(f'동일한 팀·시작일의 didNEW 계약이 여러 개입니다: {team} / {start}')
            if matches:
                key = matches[0]
                existing = contracts[key]
                delta = {k: v for k, v in fields.items() if not _same(k, existing.get(k), v)}
                if not delta:
                    continue
                if delta.get('to') and '.' in _text(existing.get('to') or existing.get('from')):
                    delta['to'] = delta['to'].replace('-', '.')
                contracts[key] = existing | delta
                writes.append(('set', collection.document(key), delta | audit))
                counts['contractsUpdated'] += 1
            else:
                team_ref = db.collection('teams').document(team)
                if start == '0000-00-00' or not team_ref.get(transaction=tx).exists:
                    raise _problem(f'신규 계약의 시작일 또는 didNEW 팀을 확인할 수 없습니다: {team} / {start}')
                key = f'{team}_{start}'
                if key in contracts:
                    raise _problem(f'생성할 계약 ID가 기존 문서와 충돌합니다: {key}')
                contracts[key] = fields
                new_keys.add(key)
                writes.append(('set', collection.document(key), fields | created | audit))
                counts['contractsCreated'] += 1
        for key in new_keys:
            start, end = _period(contracts[key])
            for other, existing in contracts.items():
                if other == key or not _is_club(existing):
                    continue
                other_start, other_end = _period(existing)
                if other_start and start < other_end and other_start < end:
                    raise _problem(f'신규 계약 기간이 기존 계약과 겹칩니다: {key} / {other}')

        changed = bool(counts['contractsCreated'] or counts['contractsUpdated'])
        active = _current(contracts, today)
        teams = {c['teamId'] for _, c in active}
        if changed and len(teams) > 1:
            raise _problem('기준일에 유효한 클럽 계약이 여러 팀에 있습니다.')
        current = max(active, key=lambda item: (_period(item[1])[0], item[0]), default=None)
        if changed:
            value = current[1] if current else {}
            fields = {'currentTeamId': value.get('teamId'), 'currentNo': value.get('no'), 'currentPos': value.get('pos')}
            patch.update({k: v for k, v in fields.items() if not _same(k, player.get(k), v)})
            for team in {c['teamId'] for _, c in old_current} - teams:
                squad = db.collection('teams').document(team).collection('squad').document(pid)
                if squad.get(transaction=tx).exists:
                    writes.append(('delete', squad, {}))
                    counts['squadsUpdated'] += 1
        if current and (changed or ('name' in patch and len(teams) == 1)):
            key, c = current
            squad = db.collection('teams').document(c['teamId']).collection('squad').document(pid)
            previous = squad.get(transaction=tx).to_dict() or {}
            fields = dict(name=patch.get('name', player.get('name', '')), no=c.get('no') or '', pos=c.get('pos') or '', contractId=key, since=c['from'])
            if any(not _same(k, previous.get(k), v) for k, v in fields.items()):
                writes.append(('set', squad, fields))
                counts['squadsUpdated'] += 1
        if not snapshot.exists:
            patch.update(active=True, **created)
            counts['playersCreated'] += 1
        elif patch:
            counts['playersUpdated'] += 1
        if patch:
            writes.append(('set', ref, patch | audit))
        for action, document, values in writes:
            if action == 'delete':
                tx.delete(document)
            else:
                tx.set(document, values, merge=True)
        return counts

    return apply(db.transaction())


def apply_player_update(season: str, actor: str, *, on_progress: Callable[[dict], None] | None = None, source=None, db=None) -> dict:
    season = player_update_season(season)
    source = source if source is not None else target_client()
    db = db if db is not None else get_firestore_client()
    if source.project != 'aifootballx' or db.project != 'jpd-did':
        raise _problem('aifootballx와 jpd-did 프로젝트 설정을 확인하세요.')
    query = source.collection('Player').where(filter=FieldFilter('PlSeason', '==', season)).where(filter=FieldFilter('l_code', '==', 'EPL')).select(SOURCE_FIELDS)
    groups = defaultdict(list)
    ids = defaultdict(set)
    source_ids = defaultdict(set)
    source_errors = {}
    result = dict(rowsChecked=0, playersTotal=0, playersProcessed=0, playersSkipped=0, unchanged=0,
                  fastPathUnchanged=0, issues=[], **dict.fromkeys(COUNTERS, 0))

    def issue(identity, error):
        result['playersSkipped'] += 1
        if len(result['issues']) < 100:
            result['issues'].append({'playerId': identity, 'reason': error.message})

    for document in query.stream(timeout=120):
        result['rowsChecked'] += 1
        row = document.to_dict() or {}
        try:
            canonical, pid = _player_id(row)
        except BackendError as exc:
            issue(document.id, exc)
            continue
        ids[canonical].add(pid)
        source_ids[pid].add(canonical)
        try:
            contract = _source_contract(row, season)
            if contract:
                groups[pid].append((row, contract))
        except BackendError as exc:
            source_errors[pid] = exc
    if not result['rowsChecked']:
        raise _problem(f'{season} EPL Player 문서가 없습니다.')
    for aliases in ids.values():
        if len(aliases) > 1:
            for pid in aliases:
                source_errors[pid] = _problem('동일 p_id가 여러 didNEW ID에 연결되어 있습니다.')
    for pid, aliases in source_ids.items():
        if len(aliases) > 1:
            source_errors[pid] = _problem('여러 p_id가 같은 didNEW ID에 연결되어 있습니다.')
    players = groups.keys() | source_errors.keys()
    result['playersTotal'] = len(players)
    if on_progress:
        on_progress(result)
    plans = {}
    for pid in sorted(players):
        try:
            if pid in source_errors:
                raise source_errors[pid]
            profile = {}
            contracts = {}
            for row, fields in groups[pid]:
                _merge(profile, _profile(row), '선수 프로필')
                _merge(contracts.setdefault((fields['teamId'], fields['from']), {}), fields, '계약')
            plans[pid] = (profile, contracts)
        except BackendError as exc:
            issue(pid, exc)
            result['playersProcessed'] += 1
    unchanged = _unchanged_players(db, plans, season)
    result['fastPathUnchanged'] = len(unchanged)
    result['unchanged'] += len(unchanged)
    result['playersProcessed'] += len(unchanged)
    pending = [pid for pid in plans if pid not in unchanged]
    if on_progress:
        on_progress(result)
    if not pending:
        return result

    today = datetime.now(KST).date().isoformat()
    last_progress = monotonic()
    with ThreadPoolExecutor(max_workers=min(UPDATE_WORKERS, len(pending))) as pool:
        futures = {pool.submit(_apply_player, db, pid, *plans[pid], actor, today): pid for pid in pending}
        for task in as_completed(futures):
            pid = futures[task]
            try:
                changes = task.result()
                for name, count in changes.items():
                    result[name] += count
                if not any(changes.values()):
                    result['unchanged'] += 1
            except BackendError as exc:
                issue(pid, exc)
            except (GoogleAPICallError, RetryError, ValueError):
                logger.exception('Player update transaction failed for %s', pid)
                issue(pid, _problem('Firestore 갱신에 실패했습니다. 상태를 확인한 뒤 다시 실행하세요.'))
            result['playersProcessed'] += 1
            now = monotonic()
            if on_progress and now - last_progress >= 0.5:
                on_progress(result)
                last_progress = now
    return result
