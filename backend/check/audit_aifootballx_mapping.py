"""Read-only Firestore schema/value audit. No destination writes or rating calls.

Run live collection once, then replay the evidence snapshot entirely offline.
The field inventory covers observed fields, not a Firestore-enforced schema.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

BUSINESS = {
    'dMST', 'TACTIC', 'Player', 'Manager', 'code', 'playerDB', 'playerMST',
    'vibePlayer', 'playerGoal', 'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank',
    'aiPHR', 'yPHR', 'prediction', 'pushMatches',
}
INPUTS = {'dMST', 'TACTIC', 'Player', 'Manager', 'code', 'playerDB', 'playerMST',
          'vibePlayer', 'playerGoal'}
AGGREGATES = {'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank'}
DOWNSTREAM = {'aiPHR', 'yPHR', 'prediction', 'pushMatches', 'forecastSnapshots',
              'AIAnalysis', 'shot_x', 'aiPodcast', 'aiShorts', 'analysis_history', 'snapshot_history'}
KPI = {'TAP', 'DAP', 'TTP', 'DTP', 'BAP', 'DTB', 'DTM', 'DTA', 'DTS', 'SHOT', 'ASR', 'SSR', 'GOAL', 'OG'}
TEAM_KPI = {'gi_tmp': 'TAP', 'B': 'DTB', 'M': 'DTM', 'A': 'DTA', 'S': 'DTS'}
PLAYER_FIELDS = {'p_name': 'name', 'p_name_en': 'nameEn', 'p_name_full': 'nameFull',
                 'p_birth': 'birth', 'p_height': 'height', 'p_nation': 'nation',
                 'p_foot': 'foot'}
PLAYER_DB = {'P-1': 'p_id', 'P-2': 'p_name', 'P-3': 'p_name_en', 'P-4': 'p_name_full',
             'P-5': 'l_code', 'P-6': 'PlSeason', 'P-7': 't_code', 'P-8': 'IDX_CODE',
             'P-9': 'pt_num', 'P-10': 'pt_pos', 'P-11': 'p_birth', 'P-12': 'p_height',
             'P-13': 'p_nation', 'P-14': 'pt_begin', 'P-15': 'pt_end', 'P-16': 'p_foot'}
PLAYER_MST_KPI = {8: 'TAP', 12: 'DAP', 16: 'UTP', 17: 'DTP', 18: 'TTP', 23: 'SHOT',
                  26: 'AST', 27: 'GOAL', 29: 'DTB', 30: 'DTM', 31: 'DTA', 32: 'DTS',
                  33: 'GTB', 34: 'GTM', 35: 'ASR', 36: 'SSR', 37: 'scoreRel', 38: 'scoreAbs', 39: 'score'}


def season_label(value: str) -> str:
    value = str(value)
    if re.fullmatch(r'\d{8}', value):
        first, second = int(value[:4]), int(value[4:])
        if second == first + 1:
            return f'{first}-{second}'
    raise ValueError(f'Unsupported season ID: {value}')


def numeric_player_id(value: Any) -> int:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError('Non-numeric player ID requires explicit crosswalk')
    return int(text)


def resolve_team_code(rows: list[dict[str, Any]], season: str, team_id: str) -> dict[str, Any]:
    candidates = [row['data'] for row in rows if (row['data'].get('TCODE') or row['data'].get('Code')) == team_id]
    exact = [row for row in candidates if row.get('PlSeason') == season]
    selected = exact or [row for row in candidates if not row.get('PlSeason')]
    signatures = {(row.get('IDX_NAME'), row.get('Team Code'), row.get('No')) for row in selected}
    if not selected or len(signatures) != 1 or not next(iter(signatures))[0]:
        raise ValueError('Missing or ambiguous season-specific team code')
    idx, number, no = next(iter(signatures))
    return {'team_id': team_id, 'season': season, 'IDX_CODE': idx, 'team_code_number': number,
            'No': no, 'basis': 'exact-season' if exact else 'global-fallback'}


def json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return {'_type': type(value).__name__}


def schema_rows(data: Any, prefix: str = '') -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    if isinstance(data, dict):
        for key, value in data.items():
            output.update(schema_rows(value, f'{prefix}.{key}' if prefix else str(key)))
    elif isinstance(data, list):
        output[prefix].add('array')
        for value in data[:3]:
            for key, types in schema_rows(value, prefix + '[]').items():
                output[key].update(types)
    else:
        output[prefix].add(type(data).__name__)
    return output


def collect_live(footballx: Path, sample_size: int) -> dict[str, Any]:
    from firebase_admin import credentials, firestore, initialize_app
    from google.cloud.firestore_v1.base_query import FieldFilter
    from backend.system.system_firestore import get_firestore_client

    did = get_firestore_client()
    app = initialize_app(credentials.Certificate(str(footballx / 'server/keys/firebase-admin.json')),
                         name='readonly-aifootballx-mapping')
    ai = firestore.client(app)
    if (did.project, ai.project) != ('jpd-did', 'aifootballx'):
        raise RuntimeError('Unexpected projects; audit aborted')
    clients = {'jpd-did': did, 'aifootballx': ai}
    evidence: dict[str, Any] = {'captured_at': datetime.now(timezone.utc).isoformat(),
                              'projects': {}, 'ai_samples': {}, 'source_recordings': [], 'player_profiles': {}}

    def scan(project, col):
        relevant = col.id in BUSINESS or col.id.startswith(('multiSheet_', 'bmas_'))
        size = sample_size if project == 'aifootballx' and relevant else 3
        if col.id == 'code':
            size = 500
        docs = list(col.limit(size).stream(timeout=30, retry=None))
        schema: dict[str, set[str]] = defaultdict(set)
        for doc in docs:
            for field, types in schema_rows(doc.to_dict()).items():
                schema[field].update(types)
        rows = [{'id': doc.id, 'data': json_value(doc.to_dict())} for doc in docs] if relevant else []
        return project, col.id, {'sampled': len(docs), 'limit': size,
                                'fields': {k: sorted(v) for k, v in sorted(schema.items())}}, rows

    jobs = [(name, col) for name, db in clients.items() for col in db.collections(timeout=20, retry=None)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(scan, name, col) for name, col in jobs]
        for future in futures:
            name, col, inventory, rows = future.result()
            evidence['projects'].setdefault(name, {})[col] = inventory
            if name == 'aifootballx' and rows:
                evidence['ai_samples'][col] = rows

    match_docs = list(did.collection('matches').select([
        'seasonId', 'date', 'round', 'homeTeamId', 'awayTeamId', 'score', 'leagueId', 'stadiumId',
    ]).limit(2001).stream(timeout=30, retry=None))
    evidence['did_matches'] = [{'id': doc.id, 'data': json_value(doc.to_dict())} for doc in match_docs]
    evidence['match_scan_truncated'] = len(match_docs) == 2001
    evidence['ai_season_presence'] = {}
    seasons = sorted({season_label(doc.to_dict()['seasonId']) for doc in match_docs})
    seasons += ['2024-2025', '2025-2026']
    for season in sorted(set(seasons)):
        evidence['ai_season_presence'][season] = {}
        for collection in ('dMST', 'multiSheet_ALL', 'TACTIC'):
            rows = list(ai.collection(collection).where(filter=FieldFilter('PlSeason', '==', season))
                        .limit(1).stream(timeout=20, retry=None))
            evidence['ai_season_presence'][season][collection] = bool(rows)
    evidence['ai_reference_rounds'] = []
    rounds = sorted({(row['data']['PlSeason'], row['data']['gm_round'])
                     for row in evidence['ai_samples'].get('multiSheet_ALL', [])})[:3]
    for season, round_no in rounds:
        rows = list(ai.collection('dMST').where(filter=FieldFilter('PlSeason', '==', season))
                    .where(filter=FieldFilter('gm_round', '==', round_no)).limit(101).stream(timeout=30, retry=None))
        evidence['ai_reference_rounds'].append({'season': season, 'round': round_no, 'truncated': len(rows)==101,
            'rows':[{'id':doc.id,'data':json_value(doc.to_dict())} for doc in rows]})

    # Include contemporary fixtures to distinguish scheduled data from final RAW.
    chosen = [doc for doc in match_docs if doc.to_dict().get('seasonId') == '20232024'][:1]
    chosen += [doc for doc in match_docs if doc.to_dict().get('seasonId', '') >= '20262027'][:3]
    for match in chosen:
        for doc in match.reference.collection('recordings').stream(timeout=20, retry=None):
            item = {'match_id': match.id, 'match': json_value(match.to_dict()),
                    'side': doc.id, 'recording': json_value(doc.to_dict()), 'children': {}}
            for collection in ('records', 'cards', 'paths', 'playerStats'):
                rows = list(doc.reference.collection(collection).limit(5001).stream(timeout=30, retry=None))
                if len(rows) == 5001:
                    raise RuntimeError('Recording exceeds audit bound; refusing incomplete calculation')
                item['children'][collection] = [{'id': row.id, 'data': json_value(row.to_dict())} for row in rows]
            evidence['source_recordings'].append(item)

    # Stable numeric identity/profile comparison; season-specific affiliation is not assumed equal.
    ids = set()
    for row in evidence['ai_samples'].get('Player', []):
        try:
            ids.add(str(numeric_player_id(row['data']['p_id'])).zfill(10))
        except (KeyError, ValueError):
            pass
    for doc in did.get_all([did.collection('players').document(value) for value in sorted(ids)], timeout=30, retry=None):
        if doc.exists:
            evidence['player_profiles'][doc.id] = json_value(doc.to_dict())
    return evidence


def headers(footballx: Path) -> dict[str, dict[str, str]]:
    labels = {}
    for path in (footballx / 'app/pages/admin').glob('*.vue'):
        found = dict((key, title) for title, key in re.findall(
            r"title:\s*['\"]([^'\"]*)['\"],\s*key:\s*['\"]([^'\"]*)['\"]", path.read_text(encoding='utf-8')))
        if found:
            labels[path.stem] = found
    return labels


def tactic_projection(item: dict[str, Any], row: dict[str, Any], path_id: str | None) -> dict[str, Any]:
    value, match, recording = row['data'], item['match'], item['recording']
    halves = ['H1', 'H2', 'H3', 'H4']
    half = value['half']
    if half not in halves:
        raise ValueError('Unknown half')
    previous = halves[:halves.index(half)]
    timings = recording.get('halves', {})
    if any(h not in timings or timings[h].get('seconds') is None for h in previous):
        raise ValueError('Previous half duration is missing; no fixed offset fallback')
    player = value.get('playerId')
    return {'gm_id': item['match_id'], 'gm_round': match['round'], 'PlSeason': season_label(match['seasonId']),
            'gr_half': half, 'gr_half_seconds': value['halfSeconds'],
            'gr_seconds': value['halfSeconds'] + sum(timings[h]['seconds'] for h in previous),
            'gr_act_code': value['act'], 'gr_res_code': 'G' if value['res'] == 'GOAL' else value['res'],
            'gr_area_code': value['area'], 'gr_pos_x': value.get('posX') if value.get('posX') is not None else '\\N',
            'gr_pos_y': value.get('posY') if value.get('posY') is not None else '\\N',
            'gr_shoot_pos_x': value.get('shootPosX') if value.get('shootPosX') is not None else '\\N',
            'gr_shoot_pos_y': value.get('shootPosY') if value.get('shootPosY') is not None else '\\N',
            'p_id': str(numeric_player_id(player)) if player else '\\N', 'gt_id': path_id or '\\N'}


def type_compatible(value: Any, types: list[str]) -> bool:
    name = type(value).__name__
    return name in types or (name in {'int', 'float'} and bool({'int', 'float'} & set(types)))


def field_rule(collection: str, field: str, label: str = '') -> tuple[str, str, str]:
    if field.startswith('X-'):
        return 'excluded', '', '사용자 확정: 사용하지 않는 X-* 값은 매핑/신규 생성 제외; 기존 목적지 값 삭제하지 않음'
    if collection not in INPUTS | AGGREGATES and not collection.startswith(('multiSheet_', 'bmas_')):
        return ('downstream' if collection in DOWNSTREAM else 'out_of_scope', '', '기존 생성 주체 유지; 원본 직접 덮어쓰기 대상 아님')
    if field in {'uid', 'id', 'createAt', 'updatedAt'}:
        return 'preserve', '기존 대상 문서/전송 메타데이터', '기존 문서 ID 유지; 신규 ID·전송 정책은 별도 확정'
    if collection == 'code':
        if field == 'Full Name':
            return 'join', 'teams.nameFull', '팀 코드 대응 후 명칭만 갱신 후보'
        return 'preserve', '기존 AIfootballX code 기준표', '시즌별 번호·슬롯·별칭은 기준표 유지; 자동 재채번 금지'
    if collection == 'Manager' and field in {'gm_date','e_date'}:
        return 'join', 'coachRounds.from/to 또는 coachContracts.from/to', '경기 날짜로 대입 금지; 재임 기간 날짜 표현 유지'
    common = {'gm_id': 'matches/{gmId} 문서 ID', 'gm_round': 'matches.round', 'gm_date': 'matches.date',
              'PlSeason': 'matches.seasonId / seasons', 'team_type': 'recordings 문서 ID H→HOME, A→AWAY'}
    if field in common and collection not in AGGREGATES:
        return 'candidate', common[field], '대상 형식으로 변환; 공통 경기 실측 대조 필요'
    if field in {'t_code', 'IDX_CODE', 'IDX_NAME', 'TCODE', 'Code', 'T_Code', 'T-code', 'H-Code', 'A-Code'}:
        return 'crosswalk', 'teams 문서 ID + AIfootballX code 컬렉션', '컬렉션·시즌별 코드 체계 유지; 이름으로 추측 금지'
    if collection == 'TACTIC':
        direct = {'gr_half': 'records.half', 'gr_half_seconds': 'records.halfSeconds', 'gr_area_code': 'records.area',
                  'gr_act_code': 'records.act', 'gr_res_code': 'records.res', 'gr_pos_x': 'records.posX',
                  'gr_pos_y': 'records.posY', 'gr_shoot_pos_x': 'records.shootPosX', 'gr_shoot_pos_y': 'records.shootPosY'}
        if field in direct:
            return 'candidate', direct[field], 'G/GOAL·null/空欄·좌표 방향은 실측 대조'
        if field == 'p_id':
            return 'crosswalk', 'records.playerId', '대상 문자열·미지정 \\N 규칙 유지; 신규 ID는 대응표 필요'
        if field == 'gt_id':
            return 'derived', 'KPI 계산 flags.path_id / paths.gtId', '신규 기록도 경로 생성; 시간순·분리된 후속 X/B 의미 대조'
        if field == 'gr_seconds':
            return 'candidate', 'halfSeconds + 앞선 halves의 실제 seconds 합계', '후반에 고정 2700초 사용 금지; 표본 오프셋 2883/2890초, 같은 경기 검증은 별도'
    if collection == 'dMST':
        if field in KPI | set(TEAM_KPI):
            return 'candidate', 'recordings.kpi.' + TEAM_KPI.get(field, field), '숫자 값·비율 단위 보존; 평점 재계산 없음'
        if field in {'JMX', 'JMX2', 'gi_score2', 'APX', 'TPX', 'FPX', 'FPX(AiFPX)', 'G-APX', 'G-TPX', 'G-FPX'}:
            source = {'JMX': 'jmx', 'JMX2': 'jmx', 'gi_score2': 'jmx', 'APX': 'apx', 'TPX': 'tpx', 'FPX': 'fpx', 'FPX(AiFPX)': 'fpx',
                      'G-APX': 'apxGrade', 'G-TPX': 'tpxGrade', 'G-FPX': 'fpxGrade'}[field]
            return 'candidate', 'recordings.teamRating.' + source, 'ratingBasedOn=kpiVersion 확인; 저장된 반환값만 사용'
        if field == 'H-A':
            return 'candidate', 'recordings 문서 ID H/A', '대상은 HOME/AWAY 대신 H/A 사용'
        if field == 'Season':
            return 'candidate', 'seasonId 두 연도의 끝 2자리 결합 → 정수', '20232024→2324; 현재는 연속 연도 시즌만 검증'
        formulas = {'M_No2': 'round*100 + Match', 'S-Round': 'Season*10000 + round*100 + Match',
                    'M_No1': 'Season*10000 + (round-1)*20 + Match', 'T-Round': 'code.No + round',
                    'M-Code': 'Season*10000 + code.No + round'}
        if field in formulas:
            return 'derived', formulas[field], '기존 EPL 표본 20/20 일치; Match 순번·연기 경기 정책 확정 전 신규 생성 금지'
        if field == 'Point':
            return 'derived', 'matches.score + recording.side → 승3/무1/패0', '기존 footballindex 결과 처리와 일치; 미경기 값과 구분'
        if field == 't_Nm':
            return 'join', 'teams.nameKr / 기존 code 한국어 명칭', '목적지 기존 표시명 유지 우선'
        if field in {'PDF', 'T-code2__bak'}:
            return 'preserve', '기존 대상 자료/백업 필드', 'DID 기록으로 재생성하지 않음'
        if field in {'DAP(AiAttack)', 'DTP(AiTactic)'}:
            return 'decision', '기존 업로드 산출값', 'APX/TPX와 같지 않음: 표본 20개 중 8/9개 불일치; 단순 평점 복사 금지'
    if collection.startswith('multiSheet_'):
        if re.fullmatch(r'(BAP|DAP|DTP|GOAL|JMX|SHOT|SSR)-H[12]-.+', field):
            if field.startswith('JMX-'):
                return 'candidate', 'recordings.teamRating.jmxSeries[0..19]', 'H1 10개→H2 10개; 누적 평점 직접 사용, 재계산 금지'
            return 'derived', 'records → KPI flags/구간 집계', '구간값/누적값·300초 경계·45-Plus·SSR 분모 규칙 확인 필요'
        if re.fullmatch(r'GOAL\d+', field):
            return 'derived', '골 records.half/halfSeconds/seq → H1_초 또는 H2_초', '미지정은 문자열 \\N; 자책골·10골 초과 정책 확인 필요'
    if collection.startswith('bmas_') and field in {'gr_area_code', 'gr_half', 'gr_pos_x', 'gr_pos_y', 'gr_seconds', 'Type'}:
        return 'derived', 'records + flags.is_dt*', '역할 판정 이벤트만 추출; Type·시간 오프셋 대조 필요'
    if collection == 'Player':
        if field == 'p_id_old':
            return 'join', '기존 대상 이력 + players/{playerId}/contracts', '사용자 확정: 기존 이력 보존 + 계약 기반 갱신; league/cup/national/test 구분 유지, 대상 맥락별 투영; legacyPlayerId 복사 금지'
        if field in PLAYER_FIELDS:
            return 'candidate', 'players.' + PLAYER_FIELDS[field], '계약과 분리된 선수 기본 정보; 날짜·형식 변환'
        if field == 'p_id':
            return 'crosswalk', 'players 문서 ID', '숫자 레거시 ID만 안전 변환; 신규 비숫자 ID는 결정 필요'
        if field in {'l_code', 'pt_begin', 'pt_end', 'pt_num', 'pt_pos'}:
            return 'join', 'players/{playerId}/contracts', '시즌/경기일 기준 계약 선택; 이적·임대·중복 기간 정책 확인'
    if collection == 'playerDB' and field in PLAYER_DB:
        if field == 'P-6':
            return 'candidate', 'seasonId 끝 2자리 결합 → 정수', '문자열 PlSeason 직접 복사 금지; 표본 P-6=2425'
        return 'join', 'Player 호환 투영.' + PLAYER_DB[field], 'admin/playerDB.vue 필드 제목으로 의미 확인; 코드·계약 정책 적용'
    if collection == 'playerDB' and field == 'P-17':
        return 'join', 'Player.p_id_old와 동일한 기존 이력 + DID contracts', '기존 이력 보존 + 대회 구분된 계약 기반 구성; legacyPlayerId 직접 복사 금지'
    if collection == 'playerMST':
        match = re.fullmatch(r'P-DB(\d+)', field)
        if match and int(match[1]) in PLAYER_MST_KPI:
            return 'candidate', 'playerStats.' + PLAYER_MST_KPI[int(match[1])], 'admin/playerMST.vue의 레거시 제목 + DID SQL 매핑표 근거'
        known = {1: 'matches 문서 ID', 2: 'matches.round', 4: 'recordings.teamId',
                 5: 'players.name', 6: 'lineup.pos', 7: 'playerStats.playerId'}
        if match and int(match[1]) in known:
            return 'candidate', known[int(match[1])], '기존 코드·자료형 변환 필요'
        if field == 'P-DB3':
            return 'candidate', 'recordings 문서 ID H/A', '표본 값 H/A; 분석관 UID를 넣지 않음'
        ratios = {9: 'flags.is_tap_success 합계',10:'TAP성공/TAP',11:'선수TAP/팀TAP',13:'flags.is_dap_success 합계',
                  14:'DAP성공/DAP',15:'선수DAP/팀DAP',19:'선수DTP',20:'선수DTP/선수TTP',21:'선수TTP/팀TTP',
                  22:'선수TTP/선수DAP',24:'flags.is_shot_success 합계',25:'선수SHOT성공/팀SHOT',
                  28:'(선수GTB*0.5+GTM+AST+GOAL)/팀GOAL'}
        if match and int(match[1]) in ratios:
            return 'derived', ratios[int(match[1])], 'lib/extra.lib.php 2990~3050의 레거시 비율식; 분모0은 0, 평점 재계산 없음'
        aliases = {42:'playerStats.playerId',43:'playerStats.TAP',44:'playerStats.DAP',45:'playerStats.DTB',
                   46:'playerStats.DTP',47:'playerStats.SHOT',48:'playerStats.SSR',49:'playerStats.GOAL',67:'playerStats.GOAL'}
        match = re.fullmatch(r'P-KPI(\d+)',field)
        if match and int(match[1]) in aliases:
            return 'candidate', aliases[int(match[1])], '화면 레거시 제목 + 동일 문서 원자값 대조 후보; 가중점수 필드는 별도'
    if collection == 'vibePlayer':
        if field == 'P-BAP':
            return 'decision', '선수별 Build-up 정의 확인 필요', 'playerStats에는 BAP 필드 없음; DTB/UTP를 임의 대입 금지'
        known = {'gi_write_code':'recordings 문서 ID H/A','p_id':'playerStats.playerId 숫자형 변환',
                 'p_name':'players.name','pt_pos':'lineup.pos','l_code':'matches.leagueId',
                 'G-APX':'playerStats.apxGrade','G-TPX':'playerStats.tpxGrade','G-FPX':'playerStats.fpxGrade'}
        if field in known:
            return 'candidate', known[field], '기존 선수 ID 대응·소속 기준 적용; 시즌 집계 등급과 경기 등급은 구분'
        match = re.fullmatch(r'P-(APX|TPX|FPX|JMX|DAP|DTP|SHOT|SSR|GOAL)', field)
        if match:
            return 'candidate', 'playerStats.' + (match[1].lower() if match[1] in {'APX','TPX','FPX','JMX'} else match[1]), '저장된 선수 평점/KPI; 결측 시 임의 0 금지'
    if collection == 'playerGoal' and field in {'gr_half','gr_half_seconds','gr_res_code','p_id','p_name','p_name_en','HA'}:
        return 'join', '골 records + players + recording.side', '시간순 정렬·자책골 주체·G/GOAL 표현 확인'
    if collection == 'playerGoal' and field == 's_name':
        return 'join', 'matches.stadiumId → stadiums.name', '경기 당시 경기장 명칭으로 구성'
    if collection == 'Manager' and field == 'T_Name':
        return 'join', 'teams.nameKr / 목적지 기존 표시명', '팀 ID 대응 후 표시명 구성'
    if collection == 'Manager' and field in {'m_id','Manager_EN','Manager_KR','Status_EN','Status_KR','gm_date','Round','e_date'}:
        return 'join', 'coaches + coachContracts + coachRounds', '시즌/라운드 감독 재임 정보; 감독 상태 영문/한글 enum 대조'
    if collection in AGGREGATES:
        return 'decision', '기존 SQL/엑셀의 경기 결합·누적·예측 산출물', '기존 공식/조회 소비 규칙 필요; 이름만으로 계산·복사 금지'
    return 'decision', '', '기존 SQL/엑셀 산출식 또는 업무 의미 확인 필요' + (f' (화면 제목: {label})' if label else '')


def validate(evidence: dict[str, Any]) -> dict[str, Any]:
    from backend.system.system_kpi_rules import KpiRecord, calculate_kpis
    result: dict[str, Any] = {'source_seasons': dict(Counter(row['data']['seasonId'] for row in evidence['did_matches'])),
        'season_presence': evidence['ai_season_presence'], 'recordings': [], 'profile_comparisons': [],
        'target_identity_types': {}, 'projection_checks': [], 'target_internal_checks': {}, 'team_crosswalk_checks': [],
        'limitations': ['Target samples are bounded, not a full database scan.',
        'No Firestore writes and no JPD-RATING requests are performed.',
        'Field candidates are not declared value-equivalent without a common match.']}
    sources = {row['id']: row['data'] for row in evidence['did_matches']}
    ai_ids = {row['data'].get('gm_id') for rows in evidence['ai_samples'].values() for row in rows if row['data'].get('gm_id')}
    result['sampled_common_match_ids'] = sorted(set(sources) & ai_ids)
    for collection, rows in evidence['ai_samples'].items():
        result['target_identity_types'][collection] = {key: dict(Counter(type(row['data'][key]).__name__ for row in rows if key in row['data']))
            for key in ('gm_id', 'p_id', 't_code', 'IDX_CODE', 'team_type', 'PlSeason') if any(key in row['data'] for row in rows)}
    for item in evidence['source_recordings']:
        recording = item['recording']
        raws = item['children']['records']
        records = []
        for row in raws:
            value = row['data']
            records.append(KpiRecord(id=row['id'], half=value['half'], seconds=value['halfSeconds'], seq=value['seq'],
                act=value['act'], res=value['res'], area=value['area'], player_id=value.get('playerId'),
                shoot_pos_x=value.get('shootPosX'), shoot_pos_y=value.get('shootPosY'),
                shoot_dsp_range=value.get('shootDspRange'), is_shot=value.get('isShot'),
                created_by=value.get('createdBy',''), created_at=value.get('createdAt',''),
                legacy_path_id=value.get('legacyPathId'), legacy_path_type=value.get('legacyPathType'),
                legacy_path_ttp=value.get('legacyPathTtp'), legacy_flags=value.get('legacyKpiFlags'), bap_reason=value.get('bapReason')))
        computed = calculate_kpis(records)
        expected = recording.get('kpi') or {}
        differences = {key: {'stored': expected[key], 'recomputed': computed.team_kpi[key]}
            for key in expected.keys() & computed.team_kpi.keys()
            if abs(float(expected[key]) - float(computed.team_kpi[key])) > 0.000001}
        rating = recording.get('teamRating') or {}
        result['recordings'].append({'match_id': item['match_id'], 'side': item['side'],
            'status': recording.get('status'), 'record_count': len(records), 'kpi_differences': differences,
            'rating_current': bool(rating) and recording.get('ratingBasedOn') == recording.get('kpiVersion'),
            'jmx_series_length': len(rating.get('jmxSeries') or []),
            'jmx_last_matches_final': bool(rating.get('jmxSeries')) and abs(rating['jmxSeries'][-1] - rating['jmx']) < .000001,
            'target_same_match_present_in_sample': item['match_id'] in ai_ids})
        type_errors, projection_errors = [], []
        target_schema = evidence['projects']['aifootballx']['TACTIC']['fields']
        checked = Counter()
        for row in raws:
            try:
                flag = computed.flags.get(row['id'])
                projection = tactic_projection(item, row, flag.path_id if flag else row['data'].get('legacyPathId'))
                for field, value in projection.items():
                    if field not in target_schema:
                        type_errors.append({'field': field, 'reason': 'not observed in target'})
                    elif not type_compatible(value, target_schema[field]):
                        type_errors.append({'record_id': row['id'], 'field': field, 'value_type': type(value).__name__, 'target_types': target_schema[field]})
                    checked[field] += 1
            except ValueError as error:
                projection_errors.append({'record_id': row['id'], 'error': str(error)})
        result['projection_checks'].append({'collection': 'TACTIC', 'match_id': item['match_id'], 'side': item['side'],
            'records': len(raws), 'field_checks': sum(checked.values()), 'fields': dict(checked),
            'type_errors': type_errors, 'projection_errors': projection_errors,
            'not_checked': ['t_code crosswalk (source season not represented)', 'target UID matching', 'same-match value equality']})
    for row in evidence['ai_samples'].get('Player', []):
        target = row['data']
        try:
            player_id = str(numeric_player_id(target['p_id'])).zfill(10)
        except (KeyError, ValueError):
            continue
        source = evidence['player_profiles'].get(player_id)
        if not source:
            continue
        comparisons = {}
        for target_key, source_key in PLAYER_FIELDS.items():
            if target_key not in target or source_key not in source:
                continue
            a, b = source[source_key], target[target_key]
            if target_key == 'p_birth':
                a, b = str(a).replace('-','.').replace('/','.'), str(b).replace('-','.').replace('/','.')
            comparisons[target_key] = {'source': a, 'target': b, 'equal': str(a).strip() == str(b).strip()}
        result['profile_comparisons'].append({'player_id': player_id, 'fields': comparisons,
            'history_is_not_legacy_id': {'source_legacyPlayerId': source.get('legacyPlayerId'), 'target_p_id_old': target.get('p_id_old')}})
    targets = evidence['ai_samples']
    alias_pairs = [('JMX','JMX2'), ('JMX','gi_score2'), ('FPX','FPX(AiFPX)'),
                   ('APX','DAP(AiAttack)'), ('TPX','DTP(AiTactic)')]
    result['target_internal_checks']['dmst_aliases'] = []
    for a,b in alias_pairs:
        rows = [r['data'] for r in targets.get('dMST',[]) if a in r['data'] and b in r['data']]
        result['target_internal_checks']['dmst_aliases'].append({'fields': [a,b], 'checked': len(rows),
            'equal': sum(r[a] == r[b] for r in rows), 'difference_examples': [{a:r[a],b:r[b]} for r in rows if r[a] != r[b]][:3]})
    for row in targets.get('dMST', []):
        data = row['data']
        try:
            code = resolve_team_code(targets.get('code', []), data['PlSeason'], data['t_code'])
            result['team_crosswalk_checks'].append({'season': data['PlSeason'], 'team_id': data['t_code'],
                'resolved': code, 'IDX_CODE_matches': data.get('IDX_CODE') == code['IDX_CODE'],
                'T_code_matches': data.get('T-code') == code['No']})
        except ValueError as error:
            result['team_crosswalk_checks'].append({'season': data['PlSeason'], 'team_id': data['t_code'], 'error': str(error)})
    pattern_functions = {
        'Season': lambda d: int(d['PlSeason'][2:4]+d['PlSeason'][7:9]),
        'M_No2': lambda d: d['gm_round']*100+d['Match'],
        'S-Round': lambda d: d['Season']*10000+d['gm_round']*100+d['Match'],
        'M_No1': lambda d: d['Season']*10000+(d['gm_round']-1)*20+d['Match'],
        'T-Round': lambda d: d['T-code']+d['gm_round'],
        'M-Code': lambda d: d['Season']*10000+d['T-code']+d['gm_round'],
    }
    result['target_internal_checks']['dmst_number_patterns'] = []
    for field,fn in pattern_functions.items():
        values=[]
        for row in targets.get('dMST', []):
            d=row['data']
            try: values.append((d[field],fn(d)))
            except (KeyError,TypeError,ValueError): pass
        result['target_internal_checks']['dmst_number_patterns'].append({'field': field,'checked':len(values),
            'equal':sum(a==b for a,b in values),'scope':'observed EPL rows only; not a new-fixture numbering policy'})
    result['target_internal_checks']['tactic_offsets'] = [
        {'half': h, 'offset': o, 'count': count} for (h,o),count in Counter(
            (r['data']['gr_half'],r['data']['gr_seconds']-r['data']['gr_half_seconds']) for r in targets.get('TACTIC',[])).items()]
    result['target_internal_checks']['goal_encoding'] = {
        'nonempty': sorted({str(value) for row in targets.get('multiSheet_GOAL-시간',[]) for field,value in row['data'].items()
                            if re.fullmatch(r'GOAL\d+',field) and value != '\\N'}), 'empty_sentinel': '\\N'}
    interval_checks = []
    def key(row):
        return tuple(row.get(field) for field in ('gm_id','gm_round','gm_date','t_code','team_type'))
    all_rows = {key(r['data']):r['data'] for r in targets.get('multiSheet_ALL',[])}
    for metric in ['BAP','DAP','DTP','GOAL','JMX','SHOT']:
        checked = 0; differences = []
        for row in targets.get('multiSheet_'+metric,[]):
            data = row['data']; combined = all_rows.get(key(data))
            if combined is None:
                continue
            for field,value in data.items():
                if field.startswith(metric+'-H'):
                    checked += 1
                    if value != combined.get(field):
                        differences.append({'identity': key(data), 'field': field, 'individual': value, 'combined': combined.get(field)})
        interval_checks.append({'metric': metric, 'checked_fields': checked, 'differences': differences})
    result['target_internal_checks']['multisheet_individual_vs_all'] = interval_checks
    sums=[]
    dmst_rows=[row['data'] for block in evidence.get('ai_reference_rounds',[]) if not block['truncated'] for row in block['rows']]
    for wrapped in targets.get('multiSheet_ALL',[]):
        data=wrapped['data']
        code_candidates=[row['data'] for row in targets.get('code',[]) if row['data'].get('IDX_NAME')==data['t_code']]
        codes={row.get('TCODE') or row.get('Code') for row in code_candidates}
        candidates=[row for row in dmst_rows if row.get('PlSeason')==data['PlSeason'] and row.get('gm_round')==data['gm_round']
                    and row.get('gm_date')==data['gm_date'] and row.get('t_code') in codes
                    and row.get('H-A')=={'HOME':'H','AWAY':'A'}.get(data['team_type'])]
        if len(candidates)!=1:
            sums.append({'match_id':data['gm_id'],'team_type':data['team_type'],'join_candidates':len(candidates)})
            continue
        differences={}
        totals={}
        for metric in ['BAP','DAP','DTP','GOAL','SHOT']:
            actual=sum(float(value) for key,value in data.items() if key.startswith(metric+'-H'))
            expected=candidates[0].get(metric)
            totals[metric]={'interval_sum':actual,'dMST':expected}
            if expected is None or abs(actual-float(expected))>.000001:
                differences[metric]=totals[metric]
        own_goals=float(candidates[0].get('OG') or 0)
        goal_minus_og=float(candidates[0].get('GOAL') or 0)-own_goals
        sums.append({'match_id':data['gm_id'],'team_type':data['team_type'],'join_candidates':1,'totals':totals,'differences':differences,
            'goal_excluding_own_goals': {'OG':own_goals,'GOAL_minus_OG':goal_minus_og,
                'interval_sum_matches':abs(totals['GOAL']['interval_sum']-goal_minus_og)<.000001}})
    result['target_internal_checks']['multisheet_totals_vs_dmst']=sums
    result['target_internal_checks']['dtp_total_contract'] = {
        'requires_equal_totals': True,
        'basis': 'User confirmed that interval DTP and whole-match DTP represent the same total.',
        'violations': [{'match_id':row['match_id'], 'team_type':row['team_type'], **row['differences']['DTP']}
                       for row in sums if row.get('differences',{}).get('DTP')],
        'action': 'Block future inconsistent exports; do not redistribute or pad counts to conceal the discrepancy.'}
    consumers = {season: any(values.values()) for season,values in evidence['ai_season_presence'].items()}
    result['final_raw_season_overlap'] = sorted({season_label(item['match']['seasonId']) for item in evidence['source_recordings'] if item['recording'].get('status')=='final' and consumers.get(season_label(item['match']['seasonId']))})
    return result


def write_audit_cache(evidence: dict[str, Any], footballx: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    labels = headers(footballx)
    mappings = []
    for collection, schema in sorted(evidence['projects']['aifootballx'].items()):
        for field, types in schema['fields'].items():
            label = labels.get(collection, {}).get(field, '')
            status, source, rule = field_rule(collection, field, label)
            mappings.append({'collection': collection, 'field': field, 'observed_types': types,
                             'label': label, 'status': status, 'source': source, 'rule': rule})
    validation = validate(evidence)
    validation.update({'captured_at': evidence['captured_at'], 'collection_counts': {name: len(cols) for name,cols in evidence['projects'].items()},
                       'mapping_status_counts': dict(Counter(row['status'] for row in mappings)), 'field_count': len(mappings)})
    for filename, payload in [('inventory.json', evidence['projects']), ('field_mapping.json', mappings), ('validation.json', validation)]:
        (output / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return validation


def run_self_tests() -> int:
    """Check mapping rules in this file without credentials or network access."""
    import unittest

    def fixture():
        return {'match_id':'m','match':{'seasonId':'20232024','round':1},
                'recording':{'halves':{'H1':{'seconds':2890},'H2':{'seconds':3000}}}}, {
                    'id':'r','data':{'half':'H2','halfSeconds':12,'act':'S','res':'GOAL','area':1,
                                    'playerId':'0000000571','posX':0,'posY':0}}

    class MappingTests(unittest.TestCase):
        def test_season_conversion(self):
            self.assertEqual(season_label('20232024'), '2023-2024')
            with self.assertRaises(ValueError):
                season_label('20232025')

        def test_new_player_id_requires_crosswalk(self):
            with self.assertRaises(ValueError):
                numeric_player_id('player-new')

        def test_decimal_player_id_is_rejected(self):
            with self.assertRaises(ValueError):
                numeric_player_id('571.0')

        def test_missing_player_id_is_rejected(self):
            with self.assertRaises(ValueError):
                numeric_player_id('\\N')

        def test_numeric_legacy_identity(self):
            self.assertEqual(numeric_player_id('0000000571'), 571)

        def test_season_specific_team_code(self):
            rows = [
                {'data': {'TCODE':'E-MU','PlSeason':None,'IDX_NAME':'OLD','Team Code':1,'No':100}},
                {'data': {'TCODE':'E-MU','PlSeason':'2025-2026','IDX_NAME':'MUNX','Team Code':16,'No':1600}},
            ]
            self.assertEqual(resolve_team_code(rows,'2025-2026','E-MU')['IDX_CODE'], 'MUNX')
            self.assertEqual(resolve_team_code(rows,'2023-2024','E-MU')['basis'], 'global-fallback')

        def test_ambiguous_team_codes(self):
            rows = [{'data': {'TCODE':'E-MU','PlSeason':None,'IDX_NAME':name}} for name in ['MUNX','OTHER']]
            with self.assertRaises(ValueError):
                resolve_team_code(rows,'2025-2026','E-MU')

        def test_actual_half_duration_and_zero_coordinates(self):
            item,row = fixture()
            result = tactic_projection(item,row,'path')
            self.assertEqual(result['gr_seconds'], 2902)
            self.assertEqual(result['gr_half_seconds'], 12)
            self.assertEqual(result['p_id'], '571')
            self.assertEqual(result['gr_res_code'], 'G')
            self.assertEqual(result['gr_pos_x'], 0)
            self.assertEqual(result['gr_shoot_pos_x'], '\\N')

        def test_missing_half_duration(self):
            item,row = fixture()
            item['recording']['halves'] = {}
            with self.assertRaises(ValueError):
                tactic_projection(item,row,'path')

        def test_history_is_not_legacy_numeric_id(self):
            status,source,_ = field_rule('Player','p_id_old')
            self.assertEqual(status, 'join')
            self.assertNotEqual(source, 'players.legacyPlayerId')

        def test_raw_scores_are_not_returned_ratings(self):
            self.assertEqual(field_rule('dMST','DTP(AiTactic)')[0], 'decision')
            self.assertEqual(field_rule('dMST','DAP(AiAttack)')[0], 'decision')
            self.assertEqual(field_rule('dMST','JMX2')[1], 'recordings.teamRating.jmx')

        def test_numeric_type_compatibility(self):
            self.assertTrue(type_compatible(1.5,['int','float']))
            self.assertFalse(type_compatible(True,['int']))
            self.assertFalse(type_compatible('1',['int']))

        def test_manager_date_is_not_match_date(self):
            self.assertNotEqual(field_rule('Manager','gm_date')[1], 'matches.date')

        def test_player_db_short_season(self):
            self.assertIn('정수', field_rule('playerDB','P-6')[1])

        def test_existing_collection_ownership(self):
            self.assertEqual(field_rule('prediction','actualWdlHome')[0], 'downstream')
            self.assertEqual(field_rule('user','uid')[0], 'out_of_scope')
            self.assertEqual(field_rule('code','Team Code')[0], 'preserve')

        def test_x_fields_are_excluded(self):
            self.assertEqual(field_rule('dMST','X-DAP')[0], 'excluded')
            self.assertEqual(field_rule('dMST','X-SSR')[0], 'excluded')
            self.assertEqual(field_rule('dMST','DAP(AiAttack)')[0], 'decision')

        def test_history_competition_scope(self):
            status,source,rule = field_rule('Player','p_id_old')
            self.assertEqual(status, 'join')
            self.assertIn('contracts', source)
            self.assertIn('league/cup/national/test', rule)
            self.assertEqual(field_rule('playerDB','P-17')[0], 'join')

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MappingTests))
    return 0 if result.wasSuccessful() else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly read live databases; never writes them')
    parser.add_argument('--footballx-root', type=Path, default=Path('C:/Users/user/footballx'))
    parser.add_argument('--snapshot', type=Path, help='Local read-only evidence snapshot (contains sports data, no credentials)')
    parser.add_argument('--output', type=Path, default=Path('backend/data/aifootballx'), help='Local JSON cache directory; no Markdown is generated')
    parser.add_argument('--sample-size', type=int, default=20)
    parser.add_argument('--self-test', action='store_true', help='Run built-in mapping tests offline')
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(run_self_tests())
    if args.snapshot is None:
        parser.error("--snapshot is required for an audit; use --self-test for offline tests")
    if not 1 <= args.sample_size <= 100:
        parser.error('sample-size must be between 1 and 100')
    if args.live:
        evidence = collect_live(args.footballx_root, args.sample_size)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        evidence = json.loads(args.snapshot.read_text(encoding='utf-8'))
    result = write_audit_cache(evidence, args.footballx_root, args.output)
    print(json.dumps({key: result[key] for key in ('collection_counts','field_count','mapping_status_counts','source_seasons','sampled_common_match_ids','recordings')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
