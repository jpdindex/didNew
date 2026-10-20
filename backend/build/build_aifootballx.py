"""AIfootballX local generation, SQL validation and read-only evidence capture.

Commands: mapping, aggregates, preview, season, sql-details, full-validation.
Firestore writes are not supported by this entry point.
"""
from __future__ import annotations

import argparse
import json
import re
import math
import sqlite3
from backend.system.system_aifootballx import (
    registered_player_projection,
    player_identity,
    AGGREGATES,
    bind_existing_documents,
    encode,
    match_target_scope,
    validate_aggregate_links,
    validate_sql_facts,
    PLAYER_DB,
    PLAYER_FIELDS,
    PLAYER_MST_KPI,
    SCORE_BASELINES,
    numeric_player_id,
    resolve_team_code,
    season_label,
    tactic_projection,
    select_contract,
    manager_projection,
    interval_name,
    grade,
    build_preview,
    stable_id,
    natural_key,
    bucket,
    dotted_date,
    LEGACY_2025_PREDICTION_BASELINES,
    LEGACY_PREDICTION_BASELINES,
    LEGACY_RANK_SLOT_OVERRIDES,
    aggregate_key,
    build_aggregates,
    rounded,
)
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from statistics import mean
from backend.temporary.build_legacy_import import _tuple_values
from backend.build.build_match_rating import BuildMatchRating
from backend.system.system_firestore import get_firestore_client, JpdDidData, BackendError
from backend.system.system_schema import PlayerKpi, RecordingKpi, TeamRatingSnapshot
from hashlib import sha256


# ==========================================================================
# mapping
# ==========================================================================


BUSINESS = {
    'dMST', 'TACTIC', 'Player', 'Manager', 'code', 'playerDB', 'playerMST',
    'vibePlayer', 'playerGoal', 'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank',
}
INPUTS = {'dMST', 'TACTIC', 'Player', 'Manager', 'code', 'playerDB', 'playerMST',
          'vibePlayer', 'playerGoal'}
MAPPING_AGGREGATES = {'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank'}
DOWNSTREAM = {'aiPHR', 'yPHR', 'prediction', 'pushMatches', 'forecastSnapshots',
              'AIAnalysis', 'shot_x', 'aiPodcast', 'aiShorts', 'analysis_history', 'snapshot_history'}
KPI = {'TAP', 'DAP', 'TTP', 'DTP', 'BAP', 'DTB', 'DTM', 'DTA', 'DTS', 'SHOT', 'ASR', 'SSR', 'GOAL', 'OG'}
TEAM_KPI = {'gi_tmp': 'TAP', 'B': 'DTB', 'M': 'DTM', 'A': 'DTA', 'S': 'DTS'}


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


def type_compatible(value: Any, types: list[str]) -> bool:
    name = type(value).__name__
    return name in types or (name in {'int', 'float'} and bool({'int', 'float'} & set(types)))


def field_rule(collection: str, field: str, label: str = '') -> tuple[str, str, str]:
    if collection == 'rank' and (field.startswith('WGR') or field in {'Season', 'S_Avg', 'R_Rate'}):
        return 'excluded', '', '사용자 확정: 미사용. 기존 값 삭제 없이 신규 생성 제외'
    if collection == 'sAVG' and field == 'AVG':
        return 'derived', '동일 시즌 sAVG.AI Rating', '사용자 확정: 여러 시즌 평균이 아닌 해당 시즌 평점과 동일'
    if collection in {'prediction', 'aiPHR', 'yPHR'} or (collection == 'pMST' and field.startswith(('I-', 'PH-', 'PA-', 'PI-'))):
        return 'excluded', '', '사용자 확정: 미사용. 추후 필요하면 별도 연동'
    if (collection == 'Manager' and field in {'Coach', 'Captain'}) or (collection == 'playerMST' and field in {'P-KPI40', 'P-KPI41'}):
        return 'manual', '팀원 관리 엑셀', '기존 직접 업로드 유지. DID 생성·누락 검증 대상 제외'
    if field.startswith('X-'):
        return 'excluded', '', '사용자 확정: 사용하지 않는 X-* 값은 매핑/신규 생성 제외; 기존 목적지 값 삭제하지 않음'
    if collection not in INPUTS | MAPPING_AGGREGATES and not collection.startswith(('multiSheet_', 'bmas_')):
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
    if field in common and collection not in MAPPING_AGGREGATES:
        return 'candidate', common[field], '대상 형식으로 변환; 공통 경기 실측 대조 필요'
    if field in {'t_code', 'IDX_CODE', 'IDX_NAME', 'TCODE', 'Code', 'T_Code', 'T-code', 'H-Code', 'A-Code'}:
        return 'crosswalk', 'teams 문서 ID + AIfootballX code 컬렉션', '컬렉션·시즌별 코드 체계 유지; 이름으로 추측 금지'
    if collection == 'TACTIC':
        if field in {'gr_pos_x', 'gr_pos_y'}:
            return 'derived', 'records.' + ('posX * 971/100' if field == 'gr_pos_x' else 'posY * 634/100'), 'DID 백분율을 기존 경기장 좌표로 복원; 터치라인 밖 좌표 보존, 슛 좌표는 별도 단위 유지'
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
        if field == 'BAP(AiBuildup)':
            return 'excluded', '', '사용자 확정: BAP 평가점수 미사용; 일반 BAP 및 multiSheet_BAP 집계는 유지'
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
            return 'derived', formulas[field], '기존 EPL 표본 20/20 일치; gm_id 순서 기준, 연기 시 gm_date만 변경하고 기존 순번 유지'
        if field == 'Point':
            return 'derived', 'matches.score + recording.side → 승3/무1/패0', '기존 footballindex 결과 처리와 일치; 미경기 값과 구분'
        if field == 't_Nm':
            return 'join', 'teams.nameKr / 기존 code 한국어 명칭', '목적지 기존 표시명 유지 우선'
        if field in {'PDF', 'T-code2__bak'}:
            return 'preserve', '기존 대상 자료/백업 필드', 'DID 기록으로 재생성하지 않음'
        if field in {'DAP(AiAttack)', 'DTP(AiTactic)'}:
            metric = 'DAP' if field == 'DAP(AiAttack)' else 'DTP'
            return 'derived', f'recordings.kpi.{metric} / {SCORE_BASELINES[metric]} * 100', '사용자 확정: NEW 기준값을 모든 시즌에 공통 적용; 보정 전 APX/TPX이며 99.9 상한 평점에서 원본 역산 금지'
    if collection.startswith('multiSheet_'):
        if re.fullmatch(r'(BAP|DAP|DTP|GOAL|JMX|SHOT|SSR)-H[12]-.+', field):
            if field.startswith('JMX-'):
                return 'candidate', 'recordings.teamRating.jmxSeries[0..19]', 'H1 10개→H2 10개; 누적 평점 직접 사용, 재계산 금지'
            if field.startswith('SSR-'):
                return 'derived', '자책골 제외 누적 GOAL / 누적 SHOT', 'NEW 엑셀 SSR-5 기준: 각 반 시작부터 해당 구간까지 누적, 분모 0은 0'
            return 'derived', 'records → KPI flags/구간 집계', '5:00은 5–10 구간; 분석 골은 자책골 제외. DTP 경기 총계는 구간 합과 일치'
        if re.fullmatch(r'GOAL\d+', field):
            return 'derived', '골 records.half/halfSeconds/seq → H1_초 또는 H2_초', '미지정은 문자열 \\N; 자책골 제외, 10골 초과 목적지 처리 확인 필요'
    if collection.startswith('bmas_') and field in {'gr_area_code', 'gr_half', 'gr_pos_x', 'gr_pos_y', 'gr_seconds', 'Type'}:
        return 'derived', 'records + flags.is_dt*', '역할 판정 이벤트만 추출; Type·시간 오프셋 대조 필요'
    if collection == 'Player':
        if field == 'p_id_old':
            return 'join', '기존 대상 이력 + players/{playerId}/contracts', '사용자 확정: 기존 이력 보존 + 계약 기반 갱신; league/cup/national/test 구분 유지, 대상 맥락별 투영; legacyPlayerId 복사 금지'
        if field in PLAYER_FIELDS:
            return 'candidate', 'players.' + PLAYER_FIELDS[field], '계약과 분리된 선수 기본 정보; 날짜·형식 변환'
        if field == 'p_id':
            return 'crosswalk', 'DID에서 수동 발급한 숫자 선수 ID', '기존 숫자 ID 유지; 자동 번호 발급 없이 DID 수동 등록값을 사용'
        if field in {'l_code', 'pt_begin', 'pt_end', 'pt_num', 'pt_pos'}:
            return 'join', 'players/{playerId}/contracts', 'DID 기존 선수 관리의 경기일·대회별 계약/이력 선택 기준 재사용'
    if collection == 'playerDB' and field in PLAYER_DB:
        if field == 'P-6':
            return 'candidate', 'seasonId 끝 2자리 결합 → 정수', '문자열 PlSeason 직접 복사 금지; 표본 P-6=2425'
        return 'join', 'Player 호환 투영.' + PLAYER_DB[field], 'admin/playerDB.vue 필드 제목으로 의미 확인; 코드·계약 정책 적용'
    if collection == 'playerDB' and field == 'P-17':
        return 'join', 'Player.p_id_old와 동일한 기존 이력 + DID contracts', '기존 이력 보존 + 대회 구분된 계약 기반 구성; legacyPlayerId 직접 복사 금지'
    if collection == 'playerMST':
        unused = re.fullmatch(r'P-KPI(\d+)', field)
        if unused and 50 <= int(unused[1]) <= 68:
            return 'excluded', '', '사용자 확정: 과거 잔재 P-KPI50~68 미사용; 매핑하지 않으며 기존 목적지 값 삭제 없음'
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
                   46:'playerStats.DTP',47:'playerStats.SHOT',48:'playerStats.SSR',49:'playerStats.GOAL'}
        match = re.fullmatch(r'P-KPI(\d+)',field)
        if match and int(match[1]) in aliases:
            return 'candidate', aliases[int(match[1])], '화면 레거시 제목 + 동일 문서 원자값 대조 후보; 가중점수 필드는 별도'
    if collection == 'vibePlayer':
        if field == 'P-BAP':
            return 'candidate', 'playerStats.DTB', '같은 AIfootballX 경기 선수 32명에서 P-BAP=P-DB29(gp_ctb) 32/32 일치; DID gp_ctb→DTB 가져오기 코드 확인'
        if field == 'gp_type':
            return 'derived', 'recording.lineup[playerId].type/slot/inHalf/inSeconds + pos', 'START GK→GK, 기타 START→KY, 출전한 BENCH→ST; 미출전 벤치는 vibePlayer 생성 제외'
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
        sources = {'m_id': 'coachRounds.coachId → 숫자', 'Manager_EN': 'coachRounds.coachNameEn',
                   'Manager_KR': 'coachRounds.coachName', 'Status_EN': 'coachRounds.status → Manager/Caretaker',
                   'Status_KR': 'coachRounds.status → 정식/감독대행', 'gm_date': 'coachRounds.from',
                   'e_date': 'coachRounds.to', 'Round': 'coachRounds.round'}
        return 'join', sources[field], 'coachRounds의 시즌/팀/라운드 기준; coaches의 이름형 문서 ID와 숫자 coachId는 별개이므로 이름 추정 조인 금지'
    if collection == 'mMST':
        match = re.fullmatch(r'(6)?([HA])-(TAP|DAP|TTP|DTP|BAP|SHOT|ASR|SSR|GOAL|B|M|A|S|FX|DWL|Code|IDX|Name)', field)
        if match:
            _, side, metric = match.groups()
            source = {'FX': 'teamRating.jmx', 'DWL': 'matches.score → W/D/L', 'TAP': 'kpi.TAP',
                      'B': 'kpi.DTB', 'M': 'kpi.DTM', 'A': 'kpi.DTA', 'S': 'kpi.DTS',
                      'Code': 'teamId', 'IDX': 'teamId → code.IDX_NAME', 'Name': 'teams.nameKr'}.get(metric, 'kpi.' + metric)
            return 'derived', f'recordings/{side}.' + source, '원천·결합 규칙 확인, 집계 미리보기 구현; 6H/6A는 같은 경기의 6개 원자 지표이며 최근 6경기 아님'
        if field == 'HA-avg':
            return 'derived', '(H.teamRating.jmx + A.teamRating.jmx) / 2', '집계 미리보기 구현; FPX 평균이 아님'
    if collection == 'pMST':
        if field in KPI | set(TEAM_KPI) | {'gi_score', 'Goal'}:
            source = '상대 recordings.kpi.GOAL' if field == 'Goal' else 'recordings.teamRating.jmx' if field == 'gi_score' else 'recordings.kpi.' + TEAM_KPI.get(field, field)
            return 'derived', source, '원천 확인, 집계 미리보기 구현; GOAL은 자기 팀, Goal은 상대 팀 득점'
        if re.fullmatch(r'(DAP|BAP|DTP|SHOT|SSR|GOAL)2', field):
            return 'derived', 'recordings.kpi.' + field[:-1], '동일 경기 원자값 별칭; 집계 미리보기 구현'
        if re.fullmatch(r'(I|PH|PA|PI)-.+', field):
            return 'decision', '기존 예측용 지수/예측 입력', '실제 평점과 별도: 표본 I-* 분모 DAP400/BAP65/DTP20/SHOT25/SSR0.2/GOAL3.7; NEW 팀 평점 분모로 임의 교체 금지'
    if collection == 'cAVG':
        match = re.fullmatch(r'C(\d+)-(AVG|WDL|S|C)', field)
        if match:
            source = {'AVG': 'teamRating.jmx', 'WDL': 'matches.score → 3/1/0',
                      'S': 'matches.score 해당 팀 득점', 'C': 'matches.score 상대 팀 득점'}[match[2]]
            return 'derived', source + ' → 시즌별 cavgSlot', '2024–25 1R 20팀×4필드 일치; 집계 미리보기 구현. cavgSlot과 Team Code는 별개'
    if collection == 'rAVG':
        match = re.fullmatch(r'M([HA])(\d+)[_-](NM|IDX|WDL)', field)
        if match:
            return 'derived', {'NM': '팀 IDX_CODE', 'IDX': 'teamRating.jmx', 'WDL': 'matches.score → W/D/L'}[match[3]], 'gm_id 경기 순서 + H/A로 10경기 투영; 집계 미리보기 구현'
    if collection == 'rank':
        match = re.fullmatch(r'(WDL|WTT|WRK|WGR|RAV|RRK)(\d+)', field)
        if match:
            sources = {'WDL': 'matches.score → 3/1/0', 'WTT': '해당 라운드까지 WDL 누적',
                       'WRK': '누적 승점 내림차순 RANK', 'WGR': 'floor(WRK/4)+1',
                       'RAV': 'teamRating.jmx', 'RRK': '라운드 JMX 내림차순 RANK'}
            return 'derived', sources[match[1]], '집계 미리보기 구현; 기본 code.POSITION, 2025–26 승격 3팀은 기존 rank와 38R 대조한 호환 슬롯 사용. WGR은 floor(WRK/4)+1'
    if collection in MAPPING_AGGREGATES:
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
        'basis': 'User confirmed equal totals; historical SQL extraction/upload errors explain discrepancies. MultiSheet interval sums are authoritative; no further historical investigation.',
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


def main_mapping() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly read live databases; never writes them')
    parser.add_argument('--footballx-root', type=Path, default=Path('C:/Users/user/footballx'))
    parser.add_argument('--snapshot', type=Path, help='Local read-only evidence snapshot (contains sports data, no credentials)')
    parser.add_argument('--output', type=Path, default=Path('backend/data/aifootballx'), help='Local JSON cache directory; no Markdown is generated')
    parser.add_argument('--sample-size', type=int, default=20)
    parser.add_argument('--self-test', action='store_true', help='Run built-in mapping tests offline')
    args = parser.parse_args()
    if args.self_test:
        from backend.check.check_aifootballx import run_self_tests
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


# ==========================================================================
# aggregates
# ==========================================================================


TEAM_COLUMNS = {'TAP': 'gi_tmp', 'DAP': 'gi_tap', 'TTP': 'gi_ttp', 'DTP': 'gi_ctp', 'BAP': 'gi_bap',
                'DTB': 'gi_ctb', 'DTM': 'gi_ctm', 'DTA': 'gi_cta', 'DTS': 'gi_cts',
                'SHOT': 'gi_sht', 'ASR': 'gi_asr', 'SSR': 'gi_ssr', 'GOAL': 'gi_gol'}


def collect_target_snapshot(footballx: Path, seasons: list[str]) -> dict:
    from firebase_admin import credentials, firestore, initialize_app
    from google.cloud.firestore_v1.base_query import FieldFilter
    ai = firestore.client(initialize_app(credentials.Certificate(str(footballx / 'server/keys/firebase-admin.json')),
                                        name='readonly-aggregate-target'))
    if ai.project != 'aifootballx':
        raise ValueError('Unexpected target project')

    def rows(query, bound):
        docs = list(query.limit(bound + 1).stream(timeout=60, retry=None))
        if len(docs) > bound:
            raise ValueError('Target read bound exceeded')
        return [{'id': d.id, 'data': json_value(d.to_dict())} for d in docs]

    out = {'code': rows(ai.collection('code'), 500), 'seasons': {}}
    for season in seasons:
        out['seasons'][season] = {}
        for col in ('dMST', *AGGREGATES):
            field, value = ('SEASON', season[2:4] + '/' + season[7:9]) if col == 'sAVG' else ('PlSeason', season)
            out['seasons'][season][col] = rows(ai.collection(col).where(filter=FieldFilter(field, '==', value)), 2000)
    return out


def read_sql(path: Path, season: str) -> dict:
    """Read fixtures and rebuild KPI from indexed detail, without SQL execution."""
    header = re.compile(r'INSERT\s+INTO\s+`([^`]+)`\s*\(([^)]*)\)\s*VALUES\s*(.*)', re.I)
    games, info, own_goals = {}, [], Counter()
    counts = Counter()
    player_scores = Counter()
    columns = {}
    # Supplied dumps have one complete INSERT per line. Reject a different format explicitly.
    with path.open(encoding='utf-8-sig', errors='strict') as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.startswith('INSERT INTO'):
                continue
            match = header.match(line)
            if not match:
                raise ValueError(f'Unsupported INSERT header at line {line_no}')
            table = match[1]
            counts[table] += 1
            columns.setdefault(table, [c.strip().strip('`') for c in match[2].split(',')])
            selected = table in ('ff_game', 'ff_game_info', 'ff_game_player') or table == 'ff_game_record' and "'OWN'" in match[3]
            if not selected:
                continue
            text = match[3].strip()
            if not text.startswith('(') or not text.endswith(');'):
                raise ValueError(f'Only complete single-row INSERTs are supported (line {line_no})')
            values = _tuple_values(text[1:-2])
            if len(values) != len(columns[table]):
                raise ValueError(f'Column/value length mismatch at line {line_no}')
            row = dict(zip(columns[table], values))
            if table == 'ff_game':
                if row['gm_id'] in games:
                    raise ValueError('Duplicate SQL gm_id')
                games[row['gm_id']] = row
            elif table == 'ff_game_info':
                info.append(row)
            elif table == 'ff_game_record':
                if row.get('p_id') == 'OWN' and row.get('gr_is_gol') == 1:
                    own_goals[row['gi_id']] += 1
            elif table == 'ff_game_player':
                for field in ('gp_score', 'gp_score_rel', 'gp_score_abs'):
                    player_scores[field + ('_nonzero' if row.get(field) else '_zero_or_null')] += 1
    season_id = season.replace('-', '')
    if not games or any(not ident.startswith(season_id) for ident in games):
        raise ValueError('SQL season does not match requested season')
    index_dir = Path('backend/data/aifootballx/sql-acceptance') / (season+'-details')
    index_dir.mkdir(parents=True, exist_ok=True)
    db, meta = index_dump(path, index_dir)
    normalized_info = {}
    for value in info:
        source = {table: rows(db, meta, table, value['gi_id'])
                  for table in ('ff_game_record', 'ff_game_path', 'ff_game_player', 'ff_game_bap')}
        normalized_info[value['gi_id']], _ = normalized_sql_source(value, source)
    db.close()
    items = []
    for raw_info in info:
        value = normalized_info[raw_info['gi_id']]
        game = games[value['gm_id']]
        if not game['gm_id'].startswith(season_id):
            raise ValueError('SQL season does not match requested season')
        side = value['gi_write_code']
        if side not in ('H', 'A'):
            raise ValueError('Unknown SQL recording side')
        # Importer stores gi_state END as final; do not promote a non-final fixture.
        status = 'final' if game['gm_state'] == 'END' and value['gi_state'] == 'END' else 'unfinished'
        match = {'seasonId': season_id, 'leagueId': game['gm_league'], 'round': game['gm_round'],
                 'date': game['gm_date'], 'homeTeamId': game['gm_h_t_code'], 'awayTeamId': game['gm_a_t_code'],
                 'score': {'home': game['gi_goal_home'], 'away': game['gi_goal_away']}}
        kpi = {field: value[column] for field, column in TEAM_COLUMNS.items()}
        kpi['OG'] = value['_valid_own_goals']
        items.append({'match_id': game['gm_id'], 'match': match, 'side': side,
                      'recording': {'status': status, 'teamId': match['homeTeamId' if side == 'H' else 'awayTeamId'],
                                    'kpi': kpi, 'kpiVersion': 1, 'ratingBasedOn': None, 'teamRating': None},
                      'source_evidence': {'gi_id': value['gi_id'], 'legacy_gi_score': value['gi_score']}})
    return {'source_path': str(path), 'season': season, 'table_counts': dict(counts),
            'columns': columns, 'player_score_presence': dict(player_scores), 'items': items,
            'missing_source': ['JPD-RATING teamRating/JMX/APX/TPX/FPX (rating checks skipped)',
                               'player/contract/coach master tables (not included in supplied dumps)']}


def from_target_dmst(rows: list[dict], sql_items: list[dict], precise_ratings: list[dict]) -> list[dict]:
    """Diagnostic replay only. This must never be called SQL/DID value validation."""
    by_team = {(i['match']['round'], i['recording']['teamId']): i for i in sql_items}
    items = []
    ratings = {(r['data']['gm_round'], r['data']['t_code']): r['data']['gi_score'] for r in precise_ratings}
    mapping = {'TAP': 'gi_tmp', 'DTB': 'B', 'DTM': 'M', 'DTA': 'A', 'DTS': 'S'}
    for row in rows:
        d = row['data']
        original = by_team.get((d['gm_round'], d['t_code']))
        if original is None:
            raise ValueError('Target dMST has no SQL fixture context')
        kpi = {k: d[mapping.get(k, k)] for k in (*TEAM_COLUMNS, 'OG')}
        precise = ratings.get((d['gm_round'], d['t_code']))
        if precise is None or abs(precise - d['JMX']) > .050001:
            raise ValueError('Precise target rating disagrees with its rounded dMST display')
        items.append({'match_id': original['match_id'], 'match': original['match'], 'side': d['H-A'],
                      'team_name': d.get('t_Nm'), 'recording': {'teamId': d['t_code'], 'status': 'final',
                       'kpiVersion': 1, 'ratingBasedOn': 1, 'kpi': kpi,
                       'teamRating': {'jmx': precise, 'apx': d.get('APX'), 'tpx': d.get('TPX'), 'fpx': d.get('FPX')}}})
    return items


def compare(generated: dict, existing: dict, *, replay: bool = False) -> dict:
    report = {'collections': {}, 'limitations': ['Target replay reuses target dMST as its input and is not independent SQL/DID validation.',
                                               'Unmapped fields and missing ratings are reported separately, never counted as passes.']}
    for col in AGGREGATES:
        targets = defaultdict(list)
        for r in existing.get(col, []):
            targets[aggregate_key(col, r['data'])].append(r)
        tally = Counter()
        differences = Counter()
        examples = []
        omitted = Counter()
        excluded = Counter()
        visited = set()
        for doc in generated['documents']:
            if doc['collection'] != col:
                continue
            key = aggregate_key(col, doc['data'])
            visited.add(key)
            rows = targets.get(key, [])
            if len(rows) != 1:
                tally['missing_target' if not rows else 'duplicate_target'] += 1
                continue
            target = rows[0]['data']
            for field, value in doc['data'].items():
                if field in ('uid', 'id', 'createAt', 'updatedAt'):
                    continue
                if (col == 'mMST' and field in ('H-Name', 'A-Name')) or (col == 'pMST' and field == 't_Nm'):
                    tally['reference_label_fields_not_counted'] += 1
                    continue
                if replay and col == 'pMST' and field == 'gi_score':
                    tally['reference_seed_fields_not_counted'] += 1
                    continue
                if field not in target:
                    tally['generated_field_not_in_target'] += 1
                    continue
                expected = target[field]
                tally['compared_fields'] += 1
                if value == expected or type(value) in (int, float) and type(expected) in (int, float) and abs(value - expected) < 1e-6:
                    tally['equal_fields'] += 1
                elif replay and col == 'mMST' and field in ('H-ASR', 'A-ASR', 'H-SSR', 'A-SSR') and abs(value - expected) < .000501:
                    tally['display_precision_equivalent_fields'] += 1
                else:
                    tally['different_fields'] += 1
                    differences[field] += 1
                    if len(examples) < 25:
                        examples.append({'key': key, 'field': field, 'generated': value, 'target': expected})
            for field in target.keys() - doc['data'].keys() - {'uid', 'id', 'createAt', 'updatedAt', '_round', '_season', '_isFuture'}:
                if (col == 'pMST' and field.startswith(('I-', 'PH-', 'PA-', 'PI-'))) or (col == 'rank' and
                        (field.startswith('WGR') or field in {'Season', 'S_Avg', 'R_Rate'})):
                    excluded[field] += 1
                else:
                    omitted[field] += 1
        tally['unmatched_target_documents'] = len(set(targets) - visited)
        report['collections'][col] = {**dict(tally), 'difference_counts': dict(differences),
                                       'omitted_field_counts': dict(omitted),
                                       'intentionally_excluded_field_counts': dict(excluded), 'examples': examples}
    report['all_compared_fields_equal'] = all(not v.get('different_fields') and not v.get('missing_target')
                                             and not v.get('duplicate_target') and not v.get('generated_field_not_in_target')
                                             and not v.get('unmatched_target_documents') for v in report['collections'].values())
    report['complete'] = report['all_compared_fields_equal'] and not generated['blocked'] and not generated['errors']
    return report


def compare_sql_team_fields(items, target):
    targets = {(r['data']['gm_round'], r['data']['t_code']): r['data'] for r in target}
    counts = Counter()
    examples = []
    for item in items:
        rec, match = item['recording'], item['match']
        row = targets.get((match['round'], rec['teamId']))
        if row is None:
            counts['missing_target_rows'] += 1
            continue
        for key, value in rec['kpi'].items():
            field = {'TAP': 'gi_tmp', 'DTB': 'B', 'DTM': 'M', 'DTA': 'A', 'DTS': 'S'}.get(key, key)
            expected = row.get(field)
            if type(value) in (int, float) and type(expected) in (int, float) and abs(value - expected) < 1e-6:
                counts['equal_fields'] += 1
            elif key in ('ASR', 'SSR') and type(expected) in (int, float) and abs(rounded(value, 3) - expected) < 1e-6:
                counts['display_precision_equivalent_fields'] += 1
            else:
                counts['different_fields'] += 1
                counts['different_' + key] += 1
                if len(examples) < 25:
                    examples.append({'gm_id': item['match_id'], 'side': item['side'], 'field': key,
                                     'sql': value, 'target': expected})
    return {'counts': dict(counts), 'examples': examples,
            'classification': 'source-value differences; display precision/upload/version cause not assigned without evidence'}


def main_aggregates():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sql', required=True, type=Path)
    parser.add_argument('--season', required=True)
    parser.add_argument('--calculation-mode', default='current', choices=('current', 'legacy-audit'))
    parser.add_argument('--prediction-index-recipe', choices=('epl-2024-legacy', 'epl-2025-workbook'),
                        help='Historical prediction index policy; never changes NEW team-rating baselines')
    parser.add_argument('--target-snapshot', required=True, type=Path)
    parser.add_argument('--refresh-target', action='store_true', help='Explicit read-only Firestore snapshot refresh')
    parser.add_argument('--legacy-target-replay', action='store_true', help='Optional historical target replay; never a SQL acceptance check')
    parser.add_argument('--footballx-root', type=Path, default=Path('C:/Users/user/footballx'))
    parser.add_argument('--slot-mapping', type=Path, default=Path('C:/Users/user/footballx/app/utils/team_slot_mapping.json'))
    parser.add_argument('--output-dir', type=Path, default=Path('backend/data/aifootballx'))
    args = parser.parse_args()
    if args.prediction_index_recipe and args.calculation_mode != 'legacy-audit':
        parser.error('--prediction-index-recipe requires --calculation-mode legacy-audit')
    if args.refresh_target:
        target = collect_target_snapshot(args.footballx_root, [args.season])
        args.target_snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.target_snapshot.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        target = json.loads(args.target_snapshot.read_text(encoding='utf-8'))
    existing = target['seasons'][args.season]
    slots = json.loads(args.slot_mapping.read_text(encoding='utf-8'))
    source_path = args.output_dir / f'{args.season}-sql-source.json'
    source = read_sql(args.sql, args.season)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf-8')
    names = {r['data']['t_code']: r['data'].get('t_Nm') for r in existing['dMST']}
    for item in source['items']:
        item['team_name'] = names.get(item['recording']['teamId'])
    recipe = {'epl-2024-legacy': LEGACY_PREDICTION_BASELINES,
              'epl-2025-workbook': LEGACY_2025_PREDICTION_BASELINES}.get(args.prediction_index_recipe)
    independent = build_aggregates(source['items'], target['code'], slots, prediction_index_baselines=recipe,
                                   calculation_mode=args.calculation_mode)
    independent['input_provenance'] = 'SQL match/KPI facts + existing code/slot reference and target team display labels. Labels are not independently counted as SQL validation.'
    bind_existing_documents(independent, existing)
    source_validation = validate_sql_facts(independent, source['items'], target['code'], slots)
    report = {'season': args.season, 'write_enabled': False,
              'calculation_mode': args.calculation_mode,
              'comparison_purpose': 'Historical values are diagnostic references, not current-formula acceptance criteria',
              'historical_comparison_is_current_acceptance': False,
              'rating_validation': 'Skipped for SQL input; production reads DID stored JPD-RATING values',
              'reference_policy': {'truth': 'SQL dump for historical match/KPI facts',
                                   'target_firestore': 'Schema/ID/code reference; historical values are diagnostic only',
                                   'current_ratings': 'DID saved JPD-RATING only; never substitute historical SQL score'},
              'source_missing': source['missing_source'], 'sql_source_validation': source_validation,
              'existing_target_diagnostics': compare(independent, existing),
              'prediction_index_recipe': args.prediction_index_recipe,
              'excluded': independent['excluded'],
              'independent_blocked': independent['blocked']}
    outputs = [('aggregate-preview', independent)]
    if args.legacy_target_replay:
        replay = build_aggregates(from_target_dmst(existing['dMST'], source['items'], existing['pMST']), target['code'], slots,
                                  prediction_index_baselines=recipe, calculation_mode=args.calculation_mode)
        replay['input_provenance'] = 'Historical target values; diagnostic only, not independent SQL validation'
        bind_existing_documents(replay, existing)
        report['historical_target_replay'] = compare(replay, existing, replay=True)
        outputs.append(('aggregate-replay', replay))
    compact = {'status': 'sql-facts-validated' if source_validation['passed'] else 'sql-facts-failed',
               'requested_sql_scope_passed': source_validation['passed'],
               'full_validation_passed': None,
               'full_validation_status': 'not evaluated; current rating values are outside SQL scope',
               'generated_documents_per_mode': len(independent['documents']),
               'sql_reference_checks': source_validation['checked'], 'sql_reference_failures': source_validation['failed'],
               'rating_validation': 'skipped', 'unused_predictions_are_blockers': False}
    report = {'summary': compact, **report}
    for suffix, value in [*outputs, ('aggregate-validation', report)]:
        if 'summary' in value:
            value = {'summary': value['summary'], **value}
        path = args.output_dir / f'{args.season}-{args.calculation_mode}-{suffix}.json'
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'season': args.season, 'sql_generation': independent['summary'],
                      'validation_summary': compact, 'sql_reference_collections': source_validation['collections'],
                      'validation': str((args.output_dir / f'{args.season}-{args.calculation_mode}-aggregate-validation.json').resolve())}, ensure_ascii=False))


# ==========================================================================
# full_validation
# ==========================================================================


def collect_masters(path):
    from google.cloud.firestore_v1.base_query import FieldFilter
    did = get_firestore_client()
    if did.project!='jpd-did': raise ValueError('Unexpected source project')
    def read(col, bound, season=None):
        query=did.collection(col)
        if season: query=query.where(filter=FieldFilter('plSeason','==',season))
        docs=list(query.limit(bound+1).stream(timeout=60,retry=None))
        if len(docs)>bound: raise ValueError('Master read truncated')
        return [{'id':s.id,'data':encode(s.to_dict())} for s in docs]
    result={'captured_at':datetime.now(timezone.utc).isoformat(),'project':did.project,'write_enabled':False}
    for col, bound in [('players',20000),('teams',1000),('coaches',1000),('coachContracts',5000)]:
        result[col]=read(col,bound)
    result['coachRounds']={s:read('coachRounds',2000,s) for s in ('2024-2025','2025-2026')}
    contracts=defaultdict(list)
    for number,snap in enumerate(did.collection_group('contracts').limit(50001).stream(timeout=60,retry=None),1):
        if number>50000: raise ValueError('Contract read truncated')
        if snap.reference.parent.parent.parent.id=='players':
            contracts[snap.reference.parent.parent.id].append(dict(encode(snap.to_dict()),_id=snap.id))
    result['player_contracts']=dict(contracts)
    save(path,result)
    print(json.dumps({'masters':{k:len(v) for k,v in result.items() if isinstance(v,(list,dict))},
                      'coach_rounds':{s:len(v) for s,v in result['coachRounds'].items()}}),flush=True)


def rating_input(game,info,players,detail):
    side=info['gi_write_code']
    target='HOME' if side=='H' else 'AWAY'
    wide=next(d['data'] for d in detail['documents'] if d['collection']=='multiSheet_ALL' and d['data']['team_type']==target)
    dmst=next(d['data'] for d in detail['documents'] if d['collection']=='dMST' and d['data']['H-A']==side)
    kpi={k:dmst['gi_tmp' if k=='TAP' else {'DTB':'B','DTM':'M','DTA':'A','DTS':'S'}.get(k,k)]
         for k in TEAM_COLUMNS}; kpi['OG']=dmst['OG']
    cumulative=[]; totals=Counter()
    for n in range(20):
        for metric in ('DAP','DTP','SHOT','GOAL'): totals[metric]+=wide[metric+'-'+interval_name(n)]
        cumulative.append({**dict(totals),'SSR':totals['GOAL']/totals['SHOT'] if totals['SHOT'] else 0})
    aliases={field:source for n,source in PLAYER_COLUMNS.items() for field in [{8:'TAP',12:'DAP',16:'UTP',17:'DTP',18:'TTP',23:'SHOT',26:'AST',27:'GOAL',29:'DTB',30:'DTM',31:'DTA',32:'DTS',33:'GTB',34:'GTM',35:'ASR',36:'SSR'}[n]]}
    body={'gm_id':game['gm_id'],'side':side,'leagueId':'EPL','seasonId':game['gm_id'][:8],'matchType':'league','round':game['gm_round'],
          'teamId':game['gm_h_t_code' if side=='H' else 'gm_a_t_code'],
          'opponentTeamId':game['gm_a_t_code' if side=='H' else 'gm_h_t_code'],'kpiVersion':1,
          'teamKpi':RecordingKpi.model_validate(kpi).model_dump(),'teamKpi5min':cumulative,
          'playerKpis':[PlayerKpi.model_validate({'playerId':r['p_id'],**{f:r[col] for f,col in aliases.items()}}).model_dump() for r in players]}
    return body


def validate_rating(body, response):
    checks=[]
    def check(name,passed,**details): checks.append({'name':name,'passed':bool(passed),**details})
    try: team=TeamRatingSnapshot.model_validate(response.get('teamRating'))
    except Exception: return [{'name':'team-rating-schema','passed':False}]
    check('rating-version',response.get('ratingBasedOn')==body['kpiVersion'])
    for field in ('gm_id','side','teamId','opponentTeamId','kpiVersion'):
        check('response-link-'+field,response.get(field)==body[field],expected=body[field],actual=response.get(field))
    check('twenty-JMX-intervals',len(team.jmxSeries)==20)
    check('finite-JMX-intervals',all(math.isfinite(v) for v in team.jmxSeries))
    check('terminal-JMX',len(team.jmxSeries)==20 and abs(team.jmxSeries[-1]-team.jmx)<1e-6)
    for metric in ('apx','tpx','fpx'):
        number=getattr(team,metric)
        check('team-'+metric+'-finite',math.isfinite(number) and 0<=number<=99.9)
        expected_grade=grade(number) if math.isfinite(number) and number>=0 else None
        check('team-'+metric+'-grade',expected_grade is not None and getattr(team,metric+'Grade')==expected_grade,
              score=number if math.isfinite(number) else None,actual=getattr(team,metric+'Grade'),expected=expected_grade)
    check('team-JMX-finite',math.isfinite(team.jmx) and 0<=team.jmx<=99.9)
    kpi=body['teamKpi']
    expected={'apx':min(kpi['DAP']/400*100,99.9),'tpx':min(kpi['DTP']/12*100,99.9),
              'fpx':(min(kpi['SHOT']/15*100,99.9)+min(kpi['GOAL']/3*100,99.9))/2}
    for metric,value in expected.items():
        actual=getattr(team,metric)
        check('team-'+metric+'-NEW-baseline',abs(actual-value)<0.00011,expected=value,actual=actual)
    received=response.get('playerRatings',[])
    if not isinstance(received,list) or not all(isinstance(p,dict) for p in received):
        return checks+[{'name':'player-rating-schema','passed':False}]
    ids=[str(p.get('playerId')) for p in received]
    expected={p['playerId'] for p in body['playerKpis']}
    check('all-player-identities',len(ids)==len(set(ids)) and set(ids)==expected,expected=len(expected),actual=len(ids))
    for player in received:
        for metric in ('apx','tpx','fpx','jmx'):
            number=player.get(metric)
            check('player-'+str(player.get('playerId'))+'-'+metric,type(number) in (int,float) and math.isfinite(number) and 0<=number<=99.9)
            if metric!='jmx' and type(number) in (int,float) and math.isfinite(number) and number>=0:
                check('player-grade-'+str(player.get('playerId'))+'-'+metric,player.get(metric+'Grade')==grade(number))
    return checks


def run(season,root,masters,ratings,workers,cached_only=False):
    directory=root/(season+'-details'); db=sqlite3.connect(directory/'sql-index.sqlite3'); meta=load(directory/'index-meta.json')
    profiles={r['id']:r['data'] for r in masters['players']}; coaches=masters['coachRounds'][season]
    team_names={r['id']:r['data'].get('nameKr') or r['data'].get('name') or r['id'] for r in masters['teams']}
    manager_targets=defaultdict(list)
    for row in masters.get('target_Manager',{}).get(season,[]):
        manager_targets[(row['data'].get('T_Code'),row['data'].get('Round'))].append(row)
    codes=load(Path('backend/data/aifootballx/aggregate-target-evidence.json'))['code']
    out=root/'full-validation'/season; out.mkdir(parents=True,exist_ok=True)
    checks=Counter(); issues=[]; rating_jobs=[]; player_ids=set(); coach_generated=[]
    for game in rows(db,meta,'ff_game'):
        mid=game['gm_id']; detail=load(directory/'matches'/f'{mid}.json')
        for info in rows(db,meta,'ff_game_info',mid):
            side=info['gi_write_code']; team=game['gm_h_t_code' if side=='H' else 'gm_a_t_code']
            source = {table: rows(db,meta,table,info['gi_id'])
                      for table in ('ff_game_record','ff_game_path','ff_game_player','ff_game_bap')}
            info, source = normalized_sql_source(info,source)
            players=source['ff_game_player']
            entries=rows(db,meta,'ff_game_player_log',info['gi_id'])
            for row in players:
                pid=row['p_id']; player_ids.add(pid); checks['player_appearances']+=1
                profile=profiles.get(pid)
                if not profile or not profile.get('name'):
                    issues.append({'kind':'player-master','match_id':mid,'side':side,'player_id':pid,'reason':'DID 선수 프로필 또는 이름 누락'})
                inactive=row.get('gp_type')=='ST' and not any(row.get(c) for c in PLAYER_COLUMNS.values()) and not any(e.get('pl_in_p_id')==pid for e in entries)
                if inactive:
                    checks['registered_unplayed_players']+=1
                    continue
                try:
                    contract=select_contract(masters['player_contracts'].get(pid,[]),{'date':game['gm_date'],'seasonId':mid[:8],'leagueId':'EPL'},team,JpdDidData._contract_covers_match_date)
                except ValueError as exc:
                    contract=None
                    issues.append({'kind':'player-contract-ambiguity','match_id':mid,'side':side,'player_id':pid,'reason':str(exc)})
                if contract is None:
                    issues.append({'kind':'player-contract','match_id':mid,'side':side,'player_id':pid,'team':team,'date':game['gm_date'],'reason':'출전 선수의 경기일·팀 계약 없음'})
                elif not contract.get('pos') or not str(contract.get('no') or '').isdigit():
                    issues.append({'kind':'player-contract-fields','match_id':mid,'side':side,'player_id':pid,'reason':'DID 계약의 포지션·등번호 누락'})
            selected=[r['data'] for r in coaches if r['data'].get('teamId')==team and r['data'].get('round')==game['gm_round']]
            checks['coach_team_rounds']+=1
            if len(selected)!=1:
                issues.append({'kind':'coach-round','match_id':mid,'side':side,'team':team,'round':game['gm_round'],'reason':f'DID coachRounds가 {len(selected)}개 (정확히 1개 필요)'})
            else:
                coach=selected[0]
                try:
                    projected=manager_projection(coach,resolve_team_code(codes,season,team),team_names.get(team,team))
                    old=manager_targets.get((team,game['gm_round']),[])
                    if len(old)!=1:
                        issues.append({'kind':'coach-target-identity','match_id':mid,'side':side,'reason':f'기존 Manager 자연키에 {len(old)}개 문서 (1개 필요)'})
                    else:
                        projected['uid']=old[0]['data'].get('uid') or old[0]['id']
                        checks['manager_existing_id_preserved']+=1
                        checks['manager_manual_fields_preserved']+=sum(k in old[0]['data'] and k not in projected for k in ('Coach','Captain'))
                    coach_generated.append({'collection':'Manager','document_id':old[0]['id'] if len(old)==1 else None,'data':projected})
                    contracts=[r['data'] for r in masters['coachContracts'] if str(r['data'].get('coachId'))==str(coach.get('coachId')) and r['data'].get('teamId')==team and JpdDidData._contract_covers_match_date(r['data'],game['gm_date'])]
                    if not contracts:
                        issues.append({'kind':'coach-contract','match_id':mid,'side':side,'coach_id':coach.get('coachId'),'reason':'선택 감독의 경기일 계약이 coachContracts에 없음'})
                    elif not any(c.get('coachName')==coach.get('coachName') and c.get('coachNameEn')==coach.get('coachNameEn')
                                 and c.get('status')==coach.get('status') for c in contracts):
                        issues.append({'kind':'coach-name','match_id':mid,'side':side,'reason':'coachRounds와 coachContracts의 한글·영문 이름 불일치'})
                except (ValueError,KeyError) as exc:
                    issues.append({'kind':'coach-fields','match_id':mid,'side':side,'reason':str(exc)})
            # Only genuine validation failures block a rating request.
            side_failed=any(not c['passed'] and (c['name'].startswith(side+':') or ':' not in c['name']) for c in detail['checks'])
            side_failed |= any(e.startswith(side+':') or ':' not in e for e in detail['errors'])
            if side_failed:
                checks['ratings_blocked_by_sql_details']+=1
                continue
            rating_jobs.append(rating_input(game,info,players,detail))
    matches=[{'id':g['gm_id'],'data':{'seasonId':g['gm_id'][:8],'date':g['gm_date'],
               'homeTeamId':g['gm_h_t_code'],'awayTeamId':g['gm_a_t_code']}} for g in rows(db,meta,'ff_game')]
    evidence={'player_profiles':profiles,'player_contracts':masters['player_contracts'],
              'ai_samples':{'code':codes},'target_existing':{}}
    teams={r['data'][k] for r in matches for k in ('homeTeamId','awayTeamId')}
    registered_ids=player_ids|{pid for pid,profile in profiles.items() if profile.get('currentTeamId') in teams}
    player_candidates=[]
    for pid in sorted(registered_ids):
        try:
            mapped=registered_player_projection(evidence,pid,season,matches)
            if mapped is None:
                issues.append({'kind':'player-registration-team','player_id':pid,'reason':'DID 선수의 등록 팀 연결 없음'})
            else:
                player_candidates.append({'collection':'Player','data':mapped})
        except (ValueError,KeyError,TypeError) as exc:
            issues.append({'kind':'player-registration','player_id':pid,'reason':str(exc)})
    checks['registered_player_candidates']=len(player_candidates)
    db.close()
    save(out/'registered-players.json',{'season':season,'write_enabled':False,'documents':player_candidates})
    save(out/'master-validation.json',{'season':season,'write_enabled':False,'passed':not issues,'counts':dict(checks),
         'unique_players':len(player_ids),'master_capture':masters['captured_at'],'issues':issues,'manager_candidates':coach_generated},pretty=True)
    print(json.dumps({'phase':'masters-validated','season':season,'issues':len(issues),'groups':dict(Counter(i['kind'] for i in issues)),'rating_targets':len(rating_jobs)}),flush=True)
    save(out/'rating-requests.json',{'season':season,'write_enabled':False,'requests':rating_jobs})
    rating_summaries=[]
    def calculate(body):
        path=out/'ratings'/f'{body["gm_id"]}-{body["side"]}.json'
        if path.exists():
            cached=load(path)
            if cached.get('request')==body and cached.get('current_run_date')==datetime.now(timezone.utc).date().isoformat() and cached.get('response'):
                cached['checks']=validate_rating(body,cached['response'])
                cached['passed']=all(c['passed'] for c in cached['checks'])
                save(path,cached)
                return cached
        result={'request':body,'write_enabled':False,'current_run_date':datetime.now(timezone.utc).date().isoformat()}
        if cached_only:
            return {**result,'error':'current_rating_capture_missing','reason':'현재 날짜의 실제 JPD 응답 캡처가 없습니다.','checks':[],'passed':False}
        try:
            response=BuildMatchRating._request_rating(body)
            result.update(response=response,checks=validate_rating(body,response))
        except BackendError as exc:
            result.update(error=exc.code,reason=exc.message,checks=[])
        result['passed']=not result.get('error') and bool(result['checks']) and all(c['passed'] for c in result['checks'])
        save(path,result)
        return result
    if ratings and rating_jobs:
        first=calculate(rating_jobs[0]); rating_summaries.append(first)
        if first.get('error') and not cached_only:
            print(json.dumps({'phase':'rating-service-unavailable','season':season,'error':first['error'],'reason':first['reason']}),flush=True)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for number,result in enumerate(pool.map(calculate,rating_jobs[1:]),2):
                    rating_summaries.append(result)
                    if number%50==0: print(json.dumps({'phase':'JPD-current-ratings','season':season,'processed':number,'total':len(rating_jobs)}),flush=True)
    summary={'season':season,'write_enabled':False,'master_passed':not issues,'master_issues':len(issues),
             'master_issue_groups':dict(Counter(i['kind'] for i in issues)),'rating_expected':760,
             'rating_sql_blocked':checks['ratings_blocked_by_sql_details'],
             'rating_evaluated':len(rating_summaries),
             'rating_requested':sum(bool(r.get('response')) for r in rating_summaries),
             'rating_pending':sum(r.get('error')=='current_rating_capture_missing' for r in rating_summaries),
             'rating_passed':sum(r['passed'] for r in rating_summaries),
             'rating_service_errors':[{'match_id':r['request']['gm_id'],'side':r['request']['side'],'code':r.get('error'),'reason':r.get('reason')} for r in rating_summaries if r.get('error') and r.get('error')!='current_rating_capture_missing'],
             'rating_failed_checks':[{'match_id':r['request']['gm_id'],'side':r['request']['side'],**c} for r in rating_summaries for c in r['checks'] if not c['passed']],
             'full_validation_passed':not issues and len(rating_summaries)==760 and all(r['passed'] for r in rating_summaries)}
    save(out/'full-validation.json',summary,pretty=True)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('rating_failed_checks','rating_service_errors')}),flush=True)


def main_full_validation():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('backend/data/aifootballx/sql-acceptance'))
    parser.add_argument('--refresh-masters',action='store_true')
    parser.add_argument('--ratings',action='store_true')
    parser.add_argument('--cached-ratings-only',action='store_true',help='Revalidate current-day real responses without network calls')
    parser.add_argument('--workers',type=int,choices=range(1,5),default=4)
    args=parser.parse_args(); path=args.root/'full-validation'/'masters.json'
    if args.refresh_masters: collect_masters(path)
    masters=load(path)
    for season in ('2024-2025','2025-2026'): run(season,args.root,masters,args.ratings or args.cached_ratings_only,args.workers,args.cached_ratings_only)


# ==========================================================================
# preview
# ==========================================================================


def collect_match(match_id: str, footballx: Path) -> dict:
    from firebase_admin import credentials, firestore, initialize_app
    from google.cloud.firestore_v1.base_query import FieldFilter
    from google.cloud.firestore_v1.field_path import FieldPath
    from backend.system.system_firestore import get_firestore_client

    did = get_firestore_client()
    ai = firestore.client(initialize_app(credentials.Certificate(str(footballx / 'server/keys/firebase-admin.json')),
                                        name='readonly-aifootballx-preview'))
    if (did.project, ai.project) != ('jpd-did', 'aifootballx'):
        raise ValueError('Unexpected source/destination project')

    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: encode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [encode(v) for v in value]
        return value

    def rows(query, limit=10000):
        docs = list(query.limit(limit + 1).stream(timeout=60, retry=None))
        if len(docs) > limit:
            raise ValueError('Read bound exceeded; refusing incomplete preview')
        return [{'id': d.id, 'data': encode(d.to_dict())} for d in docs]

    ref = did.collection('matches').document(match_id)
    snap = ref.get(timeout=30, retry=None)
    if not snap.exists:
        raise ValueError('Match does not exist')
    match = encode(snap.to_dict())
    season = season_label(match['seasonId'])
    evidence = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source_recordings': [],
                'did_matches': rows(did.collection('matches').where(filter=FieldFilter('seasonId', '==', match['seasonId']))),
                'match_scan_truncated': False, 'ai_samples': {'code': rows(ai.collection('code'), 500)},
                'target_existing': {}, 'player_profiles': {}, 'player_contracts': {},
                'teams': {}, 'stadiums': {}, 'leagues': {}, 'projects': {'aifootballx': {}}}
    for side in ('H', 'A'):
        recording = ref.collection('recordings').document(side)
        doc = recording.get(timeout=30, retry=None)
        if not doc.exists:
            raise ValueError(f'Missing {side} recording')
        item = {'match_id': match_id, 'match': match, 'side': side,
                'recording': encode(doc.to_dict()), 'children': {}}
        for collection in ('records', 'paths', 'playerStats', 'cards'):
            item['children'][collection] = rows(recording.collection(collection))
        evidence['source_recordings'].append(item)

    for collection, ids in (('teams', {match['homeTeamId'], match['awayTeamId']}),
                            ('stadiums', {match.get('stadiumId')})):
        for ident in ids - {None, ''}:
            doc = did.collection(collection).document(ident).get(timeout=30, retry=None)
            if doc.exists:
                evidence[collection][ident] = encode(doc.to_dict())
    evidence['leagues'] = {r['id']: r['data'] for r in rows(did.collection('leagues'), 100)}
    evidence['coach_rounds'] = [r for r in rows(did.collection('coachRounds').where(filter=FieldFilter('plSeason', '==', season)), 2000)
                               if r['data'].get('round') == match['round']
                               and r['data'].get('teamId') in (match['homeTeamId'], match['awayTeamId'])]
    player_ids = sorted({r['id'] for i in evidence['source_recordings'] for r in i['children']['playerStats']}
                        | {r['data']['playerId'] for i in evidence['source_recordings'] for r in i['children']['records']
                           if r['data'].get('playerId') and r['data']['playerId'] != 'OWN'})

    player_ids=set(player_ids)
    evidence['player_team_links']={}
    for team in (match['homeTeamId'],match['awayTeamId']):
        player_ids.update(r['id'] for r in rows(did.collection('players').where(filter=FieldFilter('currentTeamId','==',team)),2000))
        for row in rows(did.collection('teams').document(team).collection('squad'),2000):
            player_ids.add(row['id'])
            evidence['player_team_links'].setdefault(row['id'],[]).append(team)
    for item in evidence['source_recordings']:
        player_ids.update(item['recording'].get('lineup',{}))
    player_ids=sorted(player_ids)
    evidence['registration_player_ids']=player_ids

    def player_read(player_id):
        ref = did.collection('players').document(player_id)
        doc = ref.get(timeout=30, retry=None)
        contracts = rows(ref.collection('contracts'), 500)
        target = {}
        for collection, field in (('Player', 'p_id'), ('playerDB', 'P-1')):
            target[collection] = rows(ai.collection(collection).where(filter=FieldFilter(FieldPath(field).to_api_repr(), '==', player_identity(player_id,doc.to_dict() if doc.exists else None))), 100)
        return player_id, encode(doc.to_dict()) if doc.exists else None, contracts, target

    with ThreadPoolExecutor(max_workers=4) as pool:
        for player_id, profile, contracts, targets in pool.map(player_read, player_ids):
            if profile is not None:
                evidence['player_profiles'][player_id] = profile
            evidence['player_contracts'][player_id] = [dict(r['data'], _id=r['id']) for r in contracts]
            for collection, found in targets.items():
                evidence['target_existing'].setdefault(collection, []).extend(found)

    # dMST and playerMST legacy documents do not necessarily contain gm_id.
    collections = ['dMST', 'TACTIC', 'multiSheet_ALL', 'multiSheet_BAP', 'multiSheet_DAP',
                   'multiSheet_DTP', 'multiSheet_GOAL', 'multiSheet_JMX', 'multiSheet_SHOT',
                   'multiSheet_GOAL-시간', 'playerGoal', 'playerMST', 'vibePlayer',
                   'bmas_DTA', 'bmas_DTB', 'bmas_DTM', 'bmas_DTS', 'Manager']

    def target_read(collection):
        query = ai.collection(collection)
        if collection == 'dMST':
            query = query.where(filter=FieldFilter('PlSeason', '==', season)).where(filter=FieldFilter('gm_round', '==', match['round']))
        elif collection == 'Manager':
            query = query.where(filter=FieldFilter('PlSeason', '==', season)).where(filter=FieldFilter('Round', '==', match['round']))
        else:
            query = query.where(filter=FieldFilter(FieldPath('P-DB1' if collection == 'playerMST' else 'gm_id').to_api_repr(), '==', match_id))
        fields = {}
        for doc in ai.collection(collection).limit(5).stream(timeout=30, retry=None):
            for field, value in encode(doc.to_dict()).items():
                fields.setdefault(field, set()).add(type(value).__name__)
        if collection == 'TACTIC' and fields.get('gr_act_code') == {'NoneType'}:
            # The lexicographically first documents can all be actionless follow-up records.
            actions = ai.collection(collection).where(filter=FieldFilter('gr_act_code', 'in', ['P', 'C', 'K', 'F', 'S', 'H', 'R']))
            for doc in actions.limit(5).stream(timeout=30, retry=None):
                for field, value in encode(doc.to_dict()).items():
                    fields.setdefault(field, set()).add(type(value).__name__)
        return collection, rows(query), {'fields': {k: sorted(v) for k, v in fields.items()}}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for collection, found, schema in pool.map(target_read, collections):
            evidence['target_existing'][collection] = found
            evidence['projects']['aifootballx'][collection] = schema
    for collection in ('Player', 'playerDB'):
        fields = {}
        for row in evidence['target_existing'][collection]:
            for field, value in row['data'].items():
                fields.setdefault(field, set()).add(type(value).__name__)
        evidence['projects']['aifootballx'][collection] = {'fields': {k: sorted(v) for k, v in fields.items()}}
    # Reject a torn source read rather than validating rows against a different KPI version.
    for item in evidence['source_recordings']:
        latest = ref.collection('recordings').document(item['side']).get(timeout=30, retry=None)
        if encode(latest.to_dict()) != item['recording']:
            raise ValueError('Recording changed during preview collection; retry required')
    if encode(ref.get(timeout=30, retry=None).to_dict()) != match:
        raise ValueError('Match metadata changed during preview collection; retry required')
    return evidence


def main_preview():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--match-id', required=True)
    parser.add_argument('--snapshot', required=True, type=Path, help='Local source snapshot; written only with --live')
    parser.add_argument('--live', action='store_true', help='Read both Firestore projects; never writes them')
    parser.add_argument('--footballx-root', type=Path, default=Path('C:/Users/user/footballx'))
    parser.add_argument('--output-dir', type=Path, default=Path('backend/data/aifootballx'))
    args = parser.parse_args()
    if args.live:
        evidence = collect_match(args.match_id, args.footballx_root)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        evidence = json.loads(args.snapshot.read_text(encoding='utf-8'))
    from backend.system.system_firestore import JpdDidData
    report = build_preview(evidence, args.match_id, covers=JpdDidData._contract_covers_match_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f'{args.match_id}-preview.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'output': str(output.resolve()), 'validation_passed': report['validation_passed'],
                      **report['summary'], 'error_details': report['errors'][:12],
                      'failed_checks': [c for c in report['checks'] if not c['passed']][:12]}, ensure_ascii=False))
    raise SystemExit(0 if report['validation_passed'] else 1)


# ==========================================================================
# season
# ==========================================================================


MATCH_COLLECTIONS = ('dMST', 'TACTIC', 'multiSheet_ALL', 'multiSheet_BAP', 'multiSheet_DAP',
                     'multiSheet_DTP', 'multiSheet_GOAL', 'multiSheet_JMX', 'multiSheet_SHOT',
                     'multiSheet_GOAL-시간', 'playerGoal', 'vibePlayer',
                     'bmas_DTA', 'bmas_DTB', 'bmas_DTM', 'bmas_DTS', 'Manager')


def save(path, value, *, pretty=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None), encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def collect(season_id, footballx, output, workers):
    from firebase_admin import credentials, firestore, initialize_app
    from google.cloud.firestore_v1.base_query import FieldFilter
    from google.cloud.firestore_v1.field_path import FieldPath
    from backend.system.system_firestore import get_firestore_client

    did = get_firestore_client()
    ai = firestore.client(initialize_app(credentials.Certificate(str(footballx / 'server/keys/firebase-admin.json')),
                                        name='readonly-season-acceptance'))
    if (did.project, ai.project) != ('jpd-did', 'aifootballx'):
        raise ValueError('Unexpected Firestore projects')

    def rows(query, bound=50000):
        docs = list(query.limit(bound + 1).stream(timeout=60, retry=None))
        if len(docs) > bound:
            raise ValueError('Read bound exceeded; refusing truncated evidence')
        return [{'id': d.id, 'data': encode(d.to_dict())} for d in docs]

    season = season_label(season_id)
    matches = rows(did.collection('matches').where(filter=FieldFilter('seasonId', '==', season_id)), 2000)
    matches = [m for m in matches if m['data'].get('leagueId') == 'EPL']
    if not matches:
        raise ValueError('No EPL matches for requested DID season')
    common = {'captured_at': datetime.now(timezone.utc).isoformat(), 'season_id': season_id,
              'source_project': did.project, 'target_project': ai.project, 'write_enabled': False,
              'did_matches': matches, 'match_scan_truncated': False, 'target_existing': {},
              'ai_samples': {'code': rows(ai.collection('code'), 500)}, 'projects': {'aifootballx': {}}}
    for source, dest in [('players', 'player_profiles'), ('teams', 'teams'), ('stadiums', 'stadiums'), ('leagues', 'leagues')]:
        common[dest] = {r['id']: r['data'] for r in rows(did.collection(source))}
    common['player_contracts'] = defaultdict(list)
    for doc in did.collection_group('contracts').limit(50001).stream(timeout=60, retry=None):
        if sum(map(len, common['player_contracts'].values())) >= 50000:
            raise ValueError('Contract read bound exceeded')
        if doc.reference.parent.parent.parent.id == 'players':
            common['player_contracts'][doc.reference.parent.parent.id].append(dict(encode(doc.to_dict()), _id=doc.id))
    common['coach_rounds'] = rows(did.collection('coachRounds').where(filter=FieldFilter('plSeason', '==', season)), 2000)

    def target_read(col):
        query = ai.collection(col)
        if col == 'playerMST':
            field = FieldPath('P-DB1').to_api_repr()
            query = query.where(filter=FieldFilter(field, '>=', season_id)).where(filter=FieldFilter(field, '<', season_id + '\uf8ff'))
        elif col not in ('Player', 'playerDB'):
            field, value = ('SEASON', season[2:4] + '/' + season[7:9]) if col == 'sAVG' else ('PlSeason', season)
            query = query.where(filter=FieldFilter(field, '==', value))
        found = rows(query, 1000000 if col in ('TACTIC', 'bmas_DTA', 'bmas_DTB', 'bmas_DTM', 'bmas_DTS') else 50000)
        fields = defaultdict(set)
        for sample in ai.collection(col).limit(5).stream(timeout=30, retry=None):
            for k, v in encode(sample.to_dict()).items():
                fields[k].add(type(v).__name__)
        # Actionless rows do not establish a non-null TACTIC action type.
        if col == 'TACTIC' and fields.get('gr_act_code') == {'NoneType'}:
            for sample in ai.collection(col).where(filter=FieldFilter('gr_act_code', 'in', ['P', 'C', 'K', 'F', 'S', 'H', 'R'])).limit(5).stream(timeout=30, retry=None):
                for k, v in encode(sample.to_dict()).items():
                    fields[k].add(type(v).__name__)
        return col, found, {'fields': {k: sorted(v) for k, v in fields.items()}}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for col, found, schema in pool.map(target_read, (*MATCH_COLLECTIONS, 'Player', 'playerDB', 'playerMST', *AGGREGATES)):
            common['target_existing'][col] = found
            common['projects']['aifootballx'][col] = schema
    save(output / 'source-common.json', common)
    print(json.dumps({'phase': 'masters-collected', 'matches': len(matches), 'players': len(common['player_profiles']),
                      'coaches': len(common['coach_rounds']), 'target_counts': {c: len(r) for c, r in common['target_existing'].items()}}), flush=True)

    def match_read(row):
        mid, match = row['id'], row['data']
        ref = did.collection('matches').document(mid)
        items = []
        for side in ('H', 'A'):
            rec = ref.collection('recordings').document(side)
            snap = rec.get(timeout=30, retry=None)
            item = {'match_id': mid, 'match': match, 'side': side, 'recording': encode(snap.to_dict()) if snap.exists else {}, 'children': {}}
            for col in ('records', 'paths', 'playerStats', 'cards'):
                item['children'][col] = rows(rec.collection(col), 20000)
            latest = rec.get(timeout=30, retry=None)
            if encode(latest.to_dict()) != (item['recording'] if snap.exists else None):
                raise ValueError(f'Recording changed during read: {mid}/{side}')
            items.append(item)
        if encode(ref.get(timeout=30, retry=None).to_dict()) != match:
            raise ValueError(f'Match changed during read: {mid}')
        save(output / 'source-matches' / f'{mid}.json', items)
        return mid

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for count, mid in enumerate(pool.map(match_read, sorted(matches, key=lambda r: r['id'])), 1):
            if count % 10 == 0:
                print(json.dumps({'phase': 'source-collected', 'matches': count, 'total': len(matches)}), flush=True)
    common['collection_completed_at'] = datetime.now(timezone.utc).isoformat()
    save(output / 'source-common.json', common)


def generate(output, slot_path):
    from backend.system.system_firestore import JpdDidData
    from backend.system.system_aifootballx import natural_key

    common = load(output / 'source-common.json')
    season = season_label(common['season_id'])
    totals, errors, warnings, checks, failed_checks = Counter(), [], [], 0, []
    aggregate_items, match_summaries = [], []
    identities, shared, shared_origins, shared_conflicts = {}, {}, {}, {}
    cross_checks = []
    existing_by_id = {c: {r['id']: r['data'] for r in rows} for c, rows in common['target_existing'].items()}
    preservation = Counter()

    def check(name, passed, **details):
        cross_checks.append({'name': name, 'passed': bool(passed), **details})

    for count, fixture in enumerate(sorted(common['did_matches'], key=lambda r: r['id']), 1):
        mid = fixture['id']
        items = load(output / 'source-matches' / f'{mid}.json')
        evidence = {**common, 'source_recordings': items}
        # Legacy dMST Match is unique within a round, not across the season.
        evidence['target_existing'] = match_target_scope(common['target_existing'], fixture['data'])
        report = build_preview(evidence, mid, covers=JpdDidData._contract_covers_match_date)
        save(output / 'match-previews' / f'{mid}.json', report)
        checks += report['summary']['checks']
        failed_checks.extend({'match_id': mid, **c} for c in report['checks'] if not c['passed'])
        errors.extend({'match_id': mid, 'error': e} for e in report['errors'])
        warnings.extend({'match_id': mid, 'warning': e} for e in report['warnings'])
        by_col = defaultdict(list)
        for doc in report['documents']:
            by_col[doc['collection']].append(doc)
            old = existing_by_id.get(doc['collection'], {}).get(doc['document_id'])
            if doc['action'] == 'merge-existing':
                preservation['existing_id_checks'] += 1
                if old is None or (old.get('uid') and doc['data'].get('uid') != old['uid']):
                    errors.append({'match_id': mid, 'error': f'Existing document ID/UID not preserved: {doc["collection"]}/{doc["document_id"]}'})
            manual = {'Manager': {'Coach', 'Captain'}, 'playerMST': {'P-KPI40', 'P-KPI41'}}.get(doc['collection'], set())
            if manual:
                preservation['manual_field_checks'] += 1
                if manual.intersection(doc['data']):
                    errors.append({'match_id': mid, 'error': f'Manual fields included in merge payload: {doc["collection"]}'})
            identity = (doc['collection'], doc['document_id'])
            if identity in identities and doc['collection'] not in ('Player', 'playerDB'):
                errors.append({'match_id': mid, 'error': f'Duplicate destination across fixtures: {identity}'})
            identities[identity] = mid
            if doc['collection'] in ('Player', 'playerDB'):
                key = (doc['collection'], natural_key(doc['collection'], doc['data']))
                if key in shared and shared[key]['data'] != doc['data']:
                    if key not in shared_conflicts:
                        shared_conflicts[key] = [{'match_id': shared_origins[key], 'data': shared[key]['data']}]
                    if not any(c['data'] == doc['data'] for c in shared_conflicts[key]):
                        shared_conflicts[key].append({'match_id': mid, 'data': doc['data']})
                        errors.append({'match_id': mid, 'error': f'Conflicting shared player-season identity: {key}'})
                elif key not in shared:
                    shared[key] = doc
                    shared_origins[key] = mid
            else:
                totals[doc['collection']] += 1
        check(mid + ':team-manager-coverage', len(by_col['Manager']) == 2, actual=len(by_col['Manager']), expected=2)
        check(mid + ':team-coverage', len(by_col['dMST']) == 2, actual=len(by_col['dMST']), expected=2)
        for item in items:
            team = common['teams'].get(item['recording'].get('teamId'), {})
            aggregate_items.append({k: v for k, v in {**item, 'team_name': team.get('nameKr') or team.get('name')}.items() if k != 'children'})
        match_summaries.append({'match_id': mid, 'validation_passed': report['validation_passed'], **report['summary']})
        if count % 10 == 0:
            print(json.dumps({'phase': 'generated', 'matches': count, 'errors': len(errors), 'failed_checks': len(failed_checks)}), flush=True)
    for key in shared:
        if key not in shared_conflicts:
            totals[key[0]] += 1
    save(output / 'shared-player-preview.json', {'write_enabled': False, 'ready_for_write': False,
         'documents': [doc for key, doc in shared.items() if key not in shared_conflicts],
         'conflicts': [{'collection': key[0], 'identity': key[1], 'candidates': candidates}
                       for key, candidates in shared_conflicts.items()],
         'policy': 'Ambiguous shared season identities excluded from combined candidates; never choose a team by processing order.'})
    save(output / 'shared-player-conflicts.json', {'write_enabled': False,
         'conflicts': [{'collection': key[0], 'identity': key[1], 'candidates': candidates}
                       for key, candidates in shared_conflicts.items()]}, pretty=True)
    slots = load(slot_path)
    aggregate = build_aggregates(aggregate_items, common['ai_samples']['code'], slots)
    bind_existing_documents(aggregate, common['target_existing'])
    save(output / 'aggregate-preview.json', aggregate)
    aggregate_validation = validate_aggregate_links(aggregate, aggregate_items, common['ai_samples']['code'], slots)
    save(output / 'aggregate-validation.json', aggregate_validation, pretty=True)
    check('aggregate-source-and-rating-links', aggregate_validation['passed'])
    for col in AGGREGATES:
        check('aggregate-generated:' + col, any(d['collection'] == col for d in aggregate['documents']))
    check('season-slot-map', season in slots.get('seasonSlotMappings', {}), season=season)
    check('source-capture-completed', bool(common.get('collection_completed_at')))
    check('all-match-previews-passed', all(r['validation_passed'] for r in match_summaries))
    check('aggregates-complete', aggregate['complete'], errors=len(aggregate['errors']), blocked=aggregate['blocked'])
    totals.update(aggregate['summary']['collections'])
    validation = {'season': season, 'write_enabled': False, 'ready_for_write': False,
                  'preservation_checks': dict(preservation),
                  'reference_policy': 'DID stored source is the oracle. Target values are schema/ID references only.',
                  'source_capture': {'started': common['captured_at'], 'completed': common.get('collection_completed_at'),
                                     'consistency': 'Each fixture and recording reread after child collection reads; not a season-wide transactional snapshot.'},
                  'summary': {'matches': len(match_summaries), 'matches_passed': sum(r['validation_passed'] for r in match_summaries),
                              'unique_documents': sum(totals.values()), 'collections': dict(sorted(totals.items())),
                              'match_checks': checks, 'match_failed_checks': len(failed_checks), 'errors': len(errors),
                              'warnings': len(warnings), 'aggregate_errors': len(aggregate['errors']),
                              'shared_player_identity_conflicts': len(shared_conflicts),
                              'aggregate_blocked_groups': len(aggregate['blocked'])},
                  'checks': cross_checks, 'failed_match_checks': failed_checks, 'errors': errors, 'warnings': warnings,
                  'aggregate_errors': aggregate['errors'], 'aggregate_blocked': aggregate['blocked'], 'matches': match_summaries}
    validation['validation_passed'] = not errors and not failed_checks and all(c['passed'] for c in cross_checks)
    save(output / 'season-validation.json', validation, pretty=True)
    print(json.dumps({'validation_passed': validation['validation_passed'], **validation['summary'], 'output': str(output.resolve())}, ensure_ascii=False), flush=True)
    return validation['validation_passed']


def main_season():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season-id', required=True)
    parser.add_argument('--live', action='store_true', help='Read Firestore only; no write support exists')
    parser.add_argument('--footballx-root', type=Path, default=Path('C:/Users/user/footballx'))
    parser.add_argument('--slot-mapping', type=Path, default=Path('C:/Users/user/footballx/app/utils/team_slot_mapping.json'))
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=6, choices=range(1, 9))
    args = parser.parse_args()
    season_label(args.season_id)
    if args.live:
        collect(args.season_id, args.footballx_root, args.output_dir, args.workers)
    if load(args.output_dir / 'source-common.json')['season_id'] != args.season_id:
        raise ValueError('Snapshot season differs from requested season')
    raise SystemExit(0 if generate(args.output_dir, args.slot_mapping) else 1)


# ==========================================================================
# sql_details
# ==========================================================================


TABLES = {'ff_game', 'ff_game_info', 'ff_game_player', 'ff_game_player_log', 'ff_game_record', 'ff_game_path', 'ff_game_bap', 'ff_game_card'}
PLAYER_COLUMNS = {8:'gp_tmp',12:'gp_tap',16:'gp_utp',17:'gp_ctp',18:'gp_ttp',23:'gp_sht',26:'gp_ast',27:'gp_gol',
                  29:'gp_ctb',30:'gp_ctm',31:'gp_cta',32:'gp_cts',33:'gp_gtb',34:'gp_gtm',35:'gp_asr',36:'gp_ssr'}


def index_dump(path, output):
    stat = path.stat()
    signature = {'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    meta_path = output / 'index-meta.json'
    db = sqlite3.connect(output / 'sql-index.sqlite3')
    if meta_path.exists():
        meta = load(meta_path)
        if meta['signature'] == signature and meta.get('complete'):
            return db, meta
    db.executescript('DROP TABLE IF EXISTS facts; CREATE TABLE facts(table_name TEXT, scope TEXT, payload TEXT);')
    header = re.compile(r'INSERT\s+INTO\s+`([^`]+)`\s*\(([^)]*)\)\s*VALUES\s*(.*)', re.I)
    columns, counts, pending = {}, Counter(), []
    digest = sha256()
    with path.open('rb') as handle:
        for n, raw in enumerate(handle, 1):
            digest.update(raw)
            line = raw.decode('utf-8-sig')
            if not line.startswith('INSERT INTO'): continue
            match = header.match(line)
            if not match: raise ValueError(f'Unsupported INSERT header at line {n}')
            table = match[1]
            if table not in TABLES: continue
            fields = [c.strip().strip('`') for c in match[2].split(',')]
            if table in columns and fields != columns[table]: raise ValueError('Changing SQL column order')
            columns[table] = fields
            text = match[3].strip()
            if not text.startswith('(') or not text.endswith(');'): raise ValueError('Expected complete single-row INSERT')
            values = _tuple_values(text[1:-2])
            if len(values) != len(fields): raise ValueError(f'Column/value mismatch: {n}')
            scope_field = 'gm_id' if table in ('ff_game', 'ff_game_info') else 'gi_id'
            scope = str(values[fields.index(scope_field)])
            pending.append((table, scope, json.dumps(values, ensure_ascii=False, allow_nan=False)))
            counts[table] += 1
            if len(pending) >= 5000:
                db.executemany('INSERT INTO facts VALUES (?,?,?)', pending)
                db.commit()
                pending.clear()
            if sum(counts.values()) % 100000 == 0:
                print(json.dumps({'phase': 'sql-index', 'rows': sum(counts.values()), 'source': path.name}), flush=True)
    db.executemany('INSERT INTO facts VALUES (?,?,?)', pending)
    db.execute('CREATE INDEX facts_scope ON facts(table_name,scope)')
    db.commit()
    if path.stat().st_size != stat.st_size or path.stat().st_mtime_ns != stat.st_mtime_ns:
        raise ValueError('SQL dump changed during indexing')
    meta = {'signature': signature, 'sha256': digest.hexdigest(), 'columns': columns, 'counts': dict(counts), 'complete': True}
    save(meta_path, meta, pretty=True)
    return db, meta


def rows(db, meta, table, scope=None):
    query, args = ('SELECT payload FROM facts WHERE table_name=?', [table])
    if scope is not None:
        query += ' AND scope=?'; args.append(scope)
    return [dict(zip(meta['columns'].get(table, []), json.loads(r[0]))) for r in db.execute(query + ' ORDER BY rowid', args)]


def normalized_sql_source(info, source):
    """Rebuild facts from events with an explicit half; never infer a missing half.

    Orphan path summaries are not events. Raw SQL/index contents stay intact.
    Unsupported nonempty clocks are retained so validation can reject them.
    """
    from backend.system.system_kpi_rules import KpiRecord, calculate_kpis

    source = {**source, 'ff_game_record': [r for r in source.get('ff_game_record', [])
                                         if r.get('gr_half') not in ('', None)]}
    records = source['ff_game_record']
    linked = {r.get('gt_id') for r in records if r.get('gt_id')}
    paths = [p for p in source.get('ff_game_path', []) if p['gt_id'] in linked]
    source['ff_game_path'] = paths
    by_path = {p['gt_id']: p for p in paths}
    flag_fields = {'tap': 'tmp', 'tapSuccess': 'tmp_s', 'dap': 'tap', 'dapSuccess': 'tap_s',
                   'shot': 'sht', 'shotSuccess': 'sht_s', 'goal': 'gol', 'ast': 'ast',
                   'dtb': 'ctb', 'dtm': 'ctm', 'dta': 'cta', 'dts': 'cts', 'gtb': 'gtb', 'gtm': 'gtm'}
    projected = []
    for seq, row in enumerate(records):
        path = by_path.get(row.get('gt_id'), {})
        path_type = next((kind for field, kind in (('gt_upp','UPP'), ('gt_utp','UTP'),
                          ('gt_ctp','DTP'), ('gt_stp','STP')) if path.get(field)), None)
        projected.append(KpiRecord(id=row['gr_id'], half=str(row['gr_half']),
            seconds=row['gr_half_seconds'], seq=seq, act=row.get('gr_act_code') or '',
            res=row.get('gr_res_code') or '', area=row.get('gr_area_code'), player_id=row.get('p_id'),
            legacy_path_id=row.get('gt_id'), legacy_path_type=path_type,
            legacy_path_ttp=bool(path.get('gt_ttp')),
            legacy_flags={k: bool(row.get('gr_is_'+field)) for k, field in flag_fields.items()}))
    calculated = calculate_kpis(projected)
    kpi = dict(calculated.team_kpi)
    kpi['BAP'] = len(source.get('ff_game_bap', []))
    normalized = {**info, **{column: kpi[metric] for metric, column in TEAM_COLUMNS.items()},
                  '_valid_own_goals': kpi['OG']}
    player_metrics = {8:'TAP',12:'DAP',16:'UTP',17:'DTP',18:'TTP',23:'SHOT',26:'AST',27:'GOAL',
                      29:'DTB',30:'DTM',31:'DTA',32:'DTS',33:'GTB',34:'GTM',35:'ASR',36:'SSR'}
    source['ff_game_player'] = [
        {**row, **{column: calculated.player_kpis.get(row['p_id'], {}).get(player_metrics[n], 0)
                    for n, column in PLAYER_COLUMNS.items()}}
        for row in source.get('ff_game_player', [])]
    return normalized, source


def build_details(game, infos, children, codes):
    season = season_label(game['gm_id'][:8])
    documents, checks, errors, diagnostics = [], [], [], []
    mid, round_no = game['gm_id'], game['gm_round']
    ids = set()

    def check(name, actual, expected):
        equal = actual == expected
        if type(actual) in (int, float) and type(expected) in (int, float): equal = abs(actual - expected) < 1e-6
        checks.append({'name': name, 'passed': equal, 'actual': actual, 'expected_sql': expected})

    def emit(col, data, key=None):
        ident = stable_id(col, key or natural_key(col, data))
        if (col, ident) in ids:
            errors.append(f'Duplicate source identity: {col}/{ident}')
        ids.add((col, ident))
        documents.append({'collection': col, 'source_candidate_id': ident, 'target_id_binding': 'required-at-DID-preview', 'data': data})

    check('both-sides', sorted(i['gi_write_code'] for i in infos), ['A','H'])
    for raw_info in infos:
        info, source = normalized_sql_source(raw_info, children[raw_info['gi_id']])
        gi, side = info['gi_id'], info['gi_write_code']
        team = game['gm_h_t_code' if side == 'H' else 'gm_a_t_code']
        code = resolve_team_code(codes, season, team)
        idx = code['IDX_CODE']
        common = {'gm_id': mid, 'gm_round': round_no, 'PlSeason': season}
        wide = {**common, 'gm_date': dotted_date(game['gm_date']), 't_code': idx, 'team_type': 'HOME' if side == 'H' else 'AWAY'}
        base_wide = dict(wide)
        records, paths = source.get('ff_game_record', []), source.get('ff_game_path', [])
        members = defaultdict(list)
        bins = {m: [0] * 20 for m in ('DAP','DTP','BAP','SHOT','GOAL','SSR')}
        own_goals, goal_times = 0, []
        for seq, row in enumerate(records):
            half = {'1':'H1','2':'H2','H1':'H1','H2':'H2'}.get(str(row['gr_half']))
            seconds = row['gr_half_seconds']
            active = any(row.get(k) for k in ('gr_is_tap','gr_is_sht','gr_is_gol','gr_is_ctb','gr_is_ctm','gr_is_cta','gr_is_cts'))
            slot = None
            if half and type(seconds) is int and seconds >= 0:
                slot = bucket(half, seconds)
                if row.get('gt_id'):
                    members[str(row['gt_id'])].append((slot, seconds, seq))
            elif active:
                errors.append(f'{side}: KPI event outside supported H1/H2 clock: {row["gr_id"]}')
            else:
                diagnostics.append({'type': 'clockless-non-KPI-record', 'record_id': row['gr_id']})
            for metric, field in [('DAP','gr_is_tap'),('SHOT','gr_is_sht'),('GOAL','gr_is_gol')]:
                if slot is not None and row.get(field) and (metric != 'GOAL' or row.get('p_id') != 'OWN'):
                    bins[metric][slot] += 1
            if row.get('gr_is_gol') and row.get('p_id') == 'OWN': own_goals += 1
            projected = {**common, 't_code': idx, 'gr_half': half or 'H' + str(row['gr_half']), 'gr_half_seconds': seconds,
                         'gr_seconds': seconds + (info['gi_h1_seconds'] if half == 'H2' else 0),
                         'gr_act_code': row.get('gr_act_code') or None, 'gr_res_code': 'G' if row.get('gr_res_code') == 'GOAL' else row.get('gr_res_code'),
                         'gr_area_code': row['gr_area_code'], 'gr_pos_x': row.get('gr_pos_x') if row.get('gr_pos_x') is not None else '\\N',
                         'gr_pos_y': row.get('gr_pos_y') if row.get('gr_pos_y') is not None else '\\N',
                         'gr_shoot_pos_x': row.get('gr_shoot_pos_x') if row.get('gr_shoot_pos_x') is not None else '\\N',
                         'gr_shoot_pos_y': row.get('gr_shoot_pos_y') if row.get('gr_shoot_pos_y') is not None else '\\N',
                         'p_id': str(int(row['p_id'])) if str(row.get('p_id') or '').isdigit() else '\\N', 'gt_id': row.get('gt_id') or '\\N'}
            emit('TACTIC', projected, (mid, side, row['gr_id']))
            check(side + ':record-position-' + row['gr_id'], projected['gr_pos_x'], row.get('gr_pos_x') if row.get('gr_pos_x') is not None else '\\N')
            for role, field in [('DTA','gr_is_cta'),('DTB','gr_is_ctb'),('DTM','gr_is_ctm'),('DTS','gr_is_cts')]:
                if row.get(field):
                    emit('bmas_' + role, {**common,'team_type':wide['team_type'],'t_code':idx,'Type':role,
                        **{k:projected[k] for k in ('gr_half','gr_seconds','gr_area_code','gr_pos_x','gr_pos_y')}}, (mid,side,row['gr_id'],role))
            if row.get('gr_is_gol') and row.get('p_id') != 'OWN':
                goal_times.append((half or str(row['gr_half']), seconds))
                emit('playerGoal',{**common,'gm_date':wide['gm_date'],'IDX_CODE':idx,'t_code':team,'team_type':wide['team_type'],
                    'HA':side,'gr_res_code':'G','gr_half':projected['gr_half'],'gr_half_seconds':seconds,'p_id':row.get('p_id') or '\\N'},(mid,side,row['gr_id']))
        for path in paths:
            if not path.get('gt_ctp'): continue
            times = members.get(str(path['gt_id']), [])
            if not times:
                errors.append(f'{side}: DTP path has no supported timestamp: {path["gt_id"]}')
            else:
                terminal = max(times, key=lambda t: (t[0] // 10, t[1], t[2]))
                bins['DTP'][terminal[0]] += 1
        for row in source.get('ff_game_bap', []):
            half = {'1':'H1','2':'H2','H1':'H1','H2':'H2'}.get(str(row['gr_half']))
            if half and type(row['gr_half_seconds']) is int and row['gr_half_seconds'] >= 0:
                bins['BAP'][bucket(half,row['gr_half_seconds'])] += 1
            else: errors.append(f'{side}: BAP outside supported clock')
        for offset in (0,10):
            goals = shots = 0
            for n in range(offset,offset+10):
                goals += bins['GOAL'][n]; shots += bins['SHOT'][n]
                bins['SSR'][n] = goals/shots if shots else 0
        for metric in ('DAP','DTP','BAP','SHOT','GOAL'):
            expected = info[TEAM_COLUMNS[metric]] - (own_goals if metric == 'GOAL' else 0)
            check(side + ':interval-total-' + metric, sum(bins[metric]), expected)
        for metric, values in bins.items():
            fields = {metric+'-'+interval_name(n):v for n,v in enumerate(values)}
            if metric != 'SSR': emit('multiSheet_'+metric, {**base_wide, **fields})
            wide.update(fields)
        emit('multiSheet_ALL',wide)
        if len(goal_times)>10: errors.append(f'{side}: More than ten goals')
        emit('multiSheet_GOAL-시간', {**common,'gm_date':wide['gm_date'],'t_code':idx,'team_type':wide['team_type'],
             **{f'GOAL{n+1}':f'{sorted(goal_times)[n][0]}_{sorted(goal_times)[n][1]}' if n<len(goal_times) else '\\N' for n in range(10)}})
        for row in source.get('ff_game_player', []):
            if not str(row.get('p_id') or '').isdigit():
                errors.append(f'{side}: Non-numeric SQL player ID')
                continue
            data = {'P-DB1':mid,'P-DB2':round_no,'P-DB3':side,'P-DB4':team,'P-DB7':int(row['p_id'])}
            data.update({f'P-DB{n}':row[field] for n,field in PLAYER_COLUMNS.items() if row.get(field) is not None})
            emit('playerMST',data)
            for n, field in PLAYER_COLUMNS.items():
                if row.get(field) is not None: check(side+f':player-{row["p_id"]}-{field}',data[f'P-DB{n}'],row[field])
        # dMST source facts. Current JPD ratings are deliberately absent.
        data = {**common,'gm_date':wide['gm_date'],'t_code':team,'IDX_CODE':idx,'H-A':side,
                **{('gi_tmp' if k=='TAP' else {'DTB':'B','DTM':'M','DTA':'A','DTS':'S'}.get(k,k)):info[v] for k,v in TEAM_COLUMNS.items()},'OG':own_goals}
        emit('dMST',data)
        check(side+':player-count',sum(d['collection']=='playerMST' and d['data']['P-DB3']==side for d in documents),len(source.get('ff_game_player',[])))
        check(side+':record-count',sum(d['collection']=='TACTIC' and d['data']['t_code']==idx for d in documents),len(records))
    raw_records = [r for source in children.values() for r in source.get('ff_game_record', [])]
    selection = {'raw_records':len(raw_records),
                 'selected_records':sum(d['collection']=='TACTIC' for d in documents),
                 'excluded_incomplete_records':sum(r.get('gr_half') in ('',None) for r in raw_records)}
    return {'write_enabled':False,'ready_for_write':False,'documents':documents,'checks':checks,'errors':errors,'diagnostics':diagnostics,
            'source_selection':selection,
            'available_source_validation_passed':not errors and all(c['passed'] for c in checks)}


def main_sql_details():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sql',required=True,type=Path)
    parser.add_argument('--season',required=True)
    parser.add_argument('--target-snapshot',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    db, meta = index_dump(args.sql,args.output_dir)
    codes = load(args.target_snapshot)['code']
    counts, checked, failed, errors, diagnostics, summaries = Counter(),0,Counter(),[],[],[]
    consumed, selection = Counter(), Counter()
    games = rows(db,meta,'ff_game')
    if not games or any(g['gm_id'][:8] != args.season.replace('-','') for g in games): raise ValueError('SQL season mismatch')
    for number, game in enumerate(games,1):
        infos = rows(db,meta,'ff_game_info',game['gm_id'])
        children = {i['gi_id']:{t:rows(db,meta,t,i['gi_id']) for t in TABLES-{'ff_game','ff_game_info'}} for i in infos}
        consumed['ff_game'] += 1
        consumed['ff_game_info'] += len(infos)
        for tables in children.values():
            for table, values in tables.items(): consumed[table] += len(values)
        report = build_details(game,infos,children,codes)
        selection.update(report['source_selection'])
        save(args.output_dir/'matches'/f'{game["gm_id"]}.json',report)
        counts.update(d['collection'] for d in report['documents']); checked += len(report['checks'])
        for c in report['checks']:
            if not c['passed']: failed[c['name'].split(':',1)[1]] += 1
        errors.extend({'match_id':game['gm_id'],'error':e} for e in report['errors'])
        diagnostics.extend({'match_id':game['gm_id'],**d} for d in report['diagnostics'])
        summaries.append({'match_id':game['gm_id'],'passed':report['available_source_validation_passed'],
                          'failed_checks':[c for c in report['checks'] if not c['passed']]})
        if number%25==0: print(json.dumps({'phase':'sql-details','matches':number,'errors':len(errors),'failed_checks':sum(failed.values())}),flush=True)
    for table, expected in meta['counts'].items():
        if consumed[table] != expected:
            errors.append({'table':table,'error':'Unlinked or multiply consumed SQL rows','consumed':consumed[table],'expected':expected})
    result = {'season':args.season,'write_enabled':False,'ready_for_write':False,'source_sha256':meta['sha256'],
        'source_table_coverage':{'expected':meta['counts'],'consumed':dict(consumed)},
        'source_selection':dict(selection),
        'summary':{'matches':len(games),'matches_passed':sum(s['passed'] for s in summaries),'documents':sum(counts.values()),
                   'collections':dict(counts),'checks':checked,'failed_checks':sum(failed.values()),'errors':len(errors)},
        'available_source_validation_passed':not errors and not failed,
        'full_validation_passed':None,'not_evaluated':['DID saved current team/player ratings and JMX intervals',
            'Player/playerDB/Manager names and match-date contracts: master tables absent from supplied SQL',
            'Target legacy document ID binding: performed by DID API preview after import'],
        'failed_check_groups':dict(failed),'errors':errors,'diagnostics':diagnostics,'matches':summaries}
    save(args.output_dir/'details-validation.json',result,pretty=True)
    print(json.dumps(result['summary']),flush=True)
    raise SystemExit(0 if result['available_source_validation_passed'] else 1)


def main():
    import sys
    commands = {'mapping': main_mapping, 'aggregates': main_aggregates,
                'preview': main_preview, 'season': main_season,
                'sql-details': main_sql_details, 'full-validation': main_full_validation}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=commands)
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        parser.parse_args()
    command = sys.argv.pop(1)
    if command not in commands:
        parser.error('unknown command: ' + command)
    commands[command]()


if __name__ == '__main__':
    main()
