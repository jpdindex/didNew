"""AIfootballX conversion, aggregates, source capture and asynchronous delivery.

Clients and workers are created only when the delivery service is requested.
"""

from __future__ import annotations
import math
import re
import json
import sqlite3
import time
from datetime import datetime, timezone
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any
from backend.system.system_kpi_rules import (
    KpiRecord,
    calculate_kpis,
    confirmed_dtp_results,
    reviewed_records,
    approved_path_difference,
    approved_apx_difference,
    rating_two_decimals,
    APPROVED_RAW_EXCLUSIONS,
    APPROVED_PATH_DELTAS,
)
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from statistics import mean
from copy import deepcopy
from pathlib import Path
from backend.system.system_firestore import (
    BackendError,
    JpdDidData,
    PROJECT_ROOT,
    get_settings,
    get_firestore_client,
    resolve_contract_duplicates,
)
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

# ==========================================================================
# Conversion
# ==========================================================================


SCORE_BASELINES = {'DAP': 400, 'DTP': 12, 'SHOT': 15, 'SSR': 0.18, 'GOAL': 3}
NULL = '\\N'
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

# User-approved exclusions, shared by generation, comparison and transmission.
EXCLUDED_COLLECTIONS = frozenset(('playerMST', 'aiPHR', 'yPHR', 'prediction', 'pushMatches',
                                  'bmas_DTA', 'bmas_DTB', 'bmas_DTM', 'bmas_DTS'))

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


def registration_team_index(evidence, season, team_id):
    """Resolve a historical contract's identity without allocating a match slot.

    Historical clubs need no current-season No/POSITION. Player has its own
    index namespace (e.g. LTNX vs code.LUTX); never weaken match lookup.
    """
    codes = evidence['ai_samples']['code']
    references = {r['data']['IDX_CODE'] for r in evidence.get('target_existing', {}).get('Player', [])
                  if r['data'].get('PlSeason') == season and r['data'].get('t_code') == team_id
                  and r['data'].get('IDX_CODE')}
    if len(references) > 1:
        raise ValueError(f'Ambiguous Player team index: {team_id}')
    if references:
        return references.pop()
    try:
        return resolve_team_code(codes, season, team_id)['IDX_CODE']
    except ValueError:
        values = {r['data']['IDX_NAME'] for r in codes
                  if (r['data'].get('TCODE') or r['data'].get('Code')) == team_id
                  and r['data'].get('IDX_NAME')}
        values.update(r['data']['IDX_CODE'] for r in evidence.get('target_existing', {}).get('Player', [])
                      if r['data'].get('t_code') == team_id and r['data'].get('IDX_CODE'))
        # Cross-season Player rows are reference-only, never destination candidates.
        values.update(evidence.get('historical_team_indexes', {}).get(team_id, []))
        if len(values) != 1:
            raise ValueError(f'Missing or ambiguous historical team index: {team_id}')
        return values.pop()


def registration_alias(evidence, player_id, season, contract):
    """Bind an old registration identifier by DID team/start, never by shirt no.

    The old suffix is an operating registration number, not the player's kit
    number. New DID registrations have no legacy alias until one is supplied.
    """
    team = contract['teamId']
    identity = (season, player_identity(player_id, evidence.get('player_profiles', {}).get(player_id)),
                'EPL', team, dotted_date(contract.get('from') or '0000.00.00'))
    rows = evidence.get('_registration_reference_index', {}).get(identity)
    if rows is None:
        rows = [r for r in evidence.get('target_existing', {}).get('Player', [])
                if natural_key('Player', r['data']) == identity]
    # A verified legacy value may contain the whole career, including foreign
    # clubs or national teams. Do not erase that bundle or rebuild it from kits.
    aliases = {r['data']['p_id_old'] for r in rows
               if isinstance(r['data'].get('p_id_old'), str) and r['data']['p_id_old']}
    if len(aliases) > 1:
        raise ValueError(f'Ambiguous registration alias: {identity}')
    return next(iter(aliases), '')


def registration_history(evidence, player_id, season):
    contracts = evidence.get('player_contracts', {}).get(player_id, [])
    aliases = [registration_alias(evidence, player_id, season, c)
               for c in sorted(contracts, key=lambda c: (c.get('from') or '', c.get('teamId') or ''))
               if c.get('leagueId') in (None, '', 'EPL') and c.get('teamId')
               and c.get('competitionType', 'league') == 'league']
    return ','.join(dict.fromkeys(token.strip() for alias in aliases
                                  for token in alias.split(',') if token.strip()))


def with_registration_references(evidence):
    references = defaultdict(list)
    for row in evidence.get('target_existing', {}).get('Player', []):
        references[natural_key('Player', row['data'])].append(row)
    return {**evidence, '_registration_reference_index': references}


def player_db_projection(evidence, mapped, season):
    """playerDB P-8 uses the club abbreviation; Player uses IDX_CODE."""
    db = {dest: int(season[2:4]+season[7:9]) if dest == 'P-6' else mapped[src]
          for dest, src in {**PLAYER_DB, 'P-17': 'p_id_old'}.items() if src in mapped}
    team = mapped['t_code']
    rows = [r['data'] for r in evidence['ai_samples']['code']
            if (r['data'].get('TCODE') or r['data'].get('Code')) == team]
    exact = [r for r in rows if r.get('PlSeason') == season]
    names = {r['구단(약자)'] for r in exact or rows if r.get('구단(약자)')}
    if len(names) > 1:
        raise ValueError(f'Ambiguous playerDB team abbreviation: {team}')
    existing_names = {r['data']['P-8'] for r in evidence.get('target_existing', {}).get('playerDB', [])
                      if r['data'].get('P-6') == int(season[2:4]+season[7:9])
                      and r['data'].get('P-7') == team and r['data'].get('P-8')}
    if len(existing_names) > 1:
        raise ValueError(f'Ambiguous playerDB team abbreviation reference: {team}')
    if existing_names:
        db['P-8'] = existing_names.pop()
    elif names:
        db['P-8'] = names.pop()
    elif evidence.get('teams', {}).get(team, {}).get('nameShort'):
        db['P-8'] = evidence['teams'][team]['nameShort']
    db.setdefault('P-14', '0000.00.00')
    db.setdefault('P-15', '9999.99.99')
    if 'DID_PLAYER_ID' in mapped:
        db['DID_PLAYER_ID'] = mapped['DID_PLAYER_ID']
    return db


def team_delivery_name(code_rows, season, team_id, profile):
    """Use the operating season's configured label, rather than another namespace."""
    overrides = {'E-EV': '에버턴', 'E-LE': '레스터'}
    if team_id in overrides:
        return overrides[team_id]
    names = {r['data']['FC Name(한글)'] for r in code_rows
             if (r['data'].get('TCODE') or r['data'].get('Code')) == team_id
             and r['data'].get('PlSeason') == season and r['data'].get('FC Name(한글)')}
    if len(names) > 1:
        raise ValueError(f'Ambiguous season team label: {team_id}')
    return next(iter(names), None) or profile.get('nameKr') or profile.get('name') or team_id


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
            'gr_act_code': value['act'] or None, 'gr_res_code': 'G' if value['res'] == 'GOAL' else value['res'],
            'gr_area_code': value['area'], 'gr_pos_x': pitch_coordinate(value.get('posX'), 971),
            'gr_pos_y': pitch_coordinate(value.get('posY'), 634),
            'gr_shoot_pos_x': value.get('shootPosX') if value.get('shootPosX') is not None else '\\N',
            'gr_shoot_pos_y': value.get('shootPosY') if value.get('shootPosY') is not None else '\\N',
            'p_id': ('OWN' if player == 'OWN' else
                     str(item.get('player_identities', {}).get(player, player_identity(player))) if player else '\\N'),
            'gt_id': str(path_id) if path_id else '\\N'}


def pitch_coordinate(value: Any, extent: int) -> float | str:
    # Reverse backend/build/build_legacy_import.py's 971x634 -> percent conversion.
    if value is None:
        return NULL
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Pitch coordinate must be finite')
    # Historic touchline events can lie just outside the pitch; preserve them, do not clamp.
    return round(number * extent / 100, 6)


def grade(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        raise ValueError('Grade requires a finite nonnegative score')
    return next(label for minimum, label in ((90, 'A+'), (80, 'A'), (70, 'B'),
                                            (60, 'C'), (50, 'D'), (0, 'F')) if value >= minimum)


def truncate_rating(value, digits=1):
    """Trim presentation precision without promoting a score across a grade boundary."""
    number=Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Rating must be finite')
    return float(number.quantize(Decimal(10) ** -digits,rounding=ROUND_DOWN))


def precise_mean(values):
    """Average decimal source values before applying presentation precision."""
    values = list(values)
    if not values:
        raise ValueError('Cannot average empty values')
    return float(sum(Decimal(str(v)) for v in values) / len(values))


def clean_player_name(value):
    if not isinstance(value, str):
        return value
    return re.sub(r'(?:\\r|\\n|\s)+$', '', value).strip()


def rating_display(values):
    """Separate display strings from the original numeric calculation/storage payload."""
    if values is None:
        return None
    result=dict(values)
    for field in ('apx','tpx','fpx','jmx','score','scoreRel','scoreAbs'):
        if result.get(field) is not None:
            result[field]=format(truncate_rating(result[field]),'.1f')
    if isinstance(result.get('jmxSeries'),list):
        result['jmxSeries']=[format(truncate_rating(value),'.1f') for value in result['jmxSeries']]
    return result


def bucket(half: str, seconds: int) -> int:
    if half not in ('H1', 'H2') or isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        raise ValueError('Only H1/H2 and nonnegative integer halfSeconds are supported')
    return (0 if half == 'H1' else 10) + min(seconds // 300, 9)


def interval_name(index: int) -> str:
    slot = index % 10
    return f"H{index // 10 + 1}-" + (f'{slot * 5}-{(slot + 1) * 5}' if slot < 9 else '45-Plus')


def source_records(item: dict[str, Any], *, include_extra_halves=False, include_reviewed_exclusions=False) -> list[KpiRecord]:
    records = [KpiRecord(id=row['id'], half=v['half'], seconds=v['halfSeconds'], seq=v['seq'],
                     act=v['act'], res=v['res'], area=v['area'], player_id=v.get('playerId'),
                     shoot_pos_x=v.get('shootPosX'), shoot_pos_y=v.get('shootPosY'),
                     shoot_dsp_range=v.get('shootDspRange'), is_shot=v.get('isShot'),
                     created_by=v.get('createdBy', ''), created_at=v.get('createdAt', ''),
                     legacy_path_id=v.get('legacyPathId'), legacy_path_type=v.get('legacyPathType'),
                     legacy_path_ttp=v.get('legacyPathTtp'), legacy_flags=v.get('legacyKpiFlags'),
                     bap_reason=v.get('bapReason'))
            for row in item['children']['records'] for v in [row['data']]
            if v.get('half') in (('H1', 'H2', 'H3', 'H4') if include_extra_halves else ('H1', 'H2'))]
    return records if include_reviewed_exclusions else reviewed_records(item['match_id'], item['side'], records)


def tactic_record_ids(records: list[KpiRecord], computed: Any) -> set[str]:
    """Keep the whole recorded sequence containing a shot or goal, including OWN.

    Legacy DTP flags alone omit STP shots and sequences whose path master was
    lost. Ordinary possession sequences must not enter the target TACTIC.
    """
    paths = {computed.flags[r.id].path_id for r in records
             if computed.flags[r.id].is_shot or computed.flags[r.id].is_goal}
    return {r.id for r in records if computed.flags[r.id].path_id in paths}


def intervals(records: list[KpiRecord], computed: Any) -> dict[str, list[float]]:
    values = {key: [0] * 20 for key in ('BAP', 'DAP', 'DTP', 'SHOT', 'GOAL', 'SSR')}
    ordered = sorted(records, key=lambda r: (r.half, r.seconds, r.created_at, r.seq, r.id))
    paths = defaultdict(list)
    ordinary_goals = [0] * 20
    for record in ordered:
        slot = bucket(record.half, record.seconds)
        flag = computed.flags[record.id]
        ordinary_goals[slot] += int(flag.is_goal and record.player_id != 'OWN')
        paths[flag.path_id].append(record)
        for metric, enabled in (('DAP', flag.is_dap), ('SHOT', flag.is_shot),
                                ('GOAL', flag.is_goal)):
            values[metric][slot] += int(enabled)
        if record.legacy_flags is not None and record.bap_reason:
            values['BAP'][slot] += 1
    for event in computed.bap_events:
        record = next(r for r in ordered if r.id == event.record_id)
        values['BAP'][bucket(record.half, record.seconds)] += 1
    if any(path.path_type == 'DTP' and path.id not in paths for path in computed.paths):
        raise ValueError('DTP path has no timestamped record; cannot assign an interval')
    for terminal in confirmed_dtp_results(records, computed).values():
        values['DTP'][bucket(terminal.half, terminal.seconds)] += 1
    goals = shots = 0
    for index in range(20):
        goals += ordinary_goals[index]
        shots += values['SHOT'][index]
        values['SSR'][index] = goals / shots if shots else 0
    return values


def stable_id(collection: str, key: tuple[Any, ...]) -> str:
    # No dates: rescheduling must not produce a second destination document.
    return 'did-' + sha256((collection + '|' + '|'.join(map(str, key))).encode()).hexdigest()[:40]


def dotted_date(value: str) -> str:
    digits = re.sub(r'\D', '', value)
    if len(digits) != 8:
        raise ValueError('Expected an eight-digit destination date')
    return f'{digits[:4]}.{digits[4:6]}.{digits[6:]}'


def player_game_type(lineup: dict[str, Any], position: str) -> str | None:
    if lineup.get('type') == 'START':
        return 'GK' if lineup.get('slot') == 'gk' or position == 'GK' else 'KY'
    if lineup.get('type') == 'BENCH':
        # ST in vibePlayer means an actual substitute appearance, not an unused bench place.
        return 'ST' if lineup.get('inHalf') and lineup.get('inSeconds') is not None else None
    raise ValueError('Missing/unknown lineup type for gp_type')


def natural_key(collection: str, data: dict[str, Any]) -> tuple[Any, ...]:
    if collection == 'Manager':
        return data.get('PlSeason'), data.get('T_Code'), data.get('Round')
    if collection == 'dMST':
        return data.get('PlSeason'), data.get('gm_round'), data.get('t_code')
    if collection == 'Player':
        # DID contract documents are keyed by team + start; closing an open
        # contract must update its document rather than create a second one.
        return (data.get('PlSeason'), data.get('p_id'), data.get('l_code') or 'EPL',
                data.get('t_code'), dotted_date(data.get('pt_begin') or '0000.00.00'))
    if collection == 'playerDB':
        return data.get('P-6'), data.get('P-1')
    if collection == 'playerMST':
        return data.get('P-DB1'), data.get('P-DB3'), data.get('P-DB7')
    if collection == 'vibePlayer':
        return data.get('gm_id'), data.get('IDX_CODE'), data.get('p_id')
    if collection == 'TACTIC':
        return tuple(data.get(k) for k in ('gm_id', 't_code', 'gr_half', 'gr_half_seconds',
                                         'gt_id', 'p_id', 'gr_act_code', 'gr_res_code',
                                         'gr_pos_x', 'gr_pos_y', 'gr_shoot_pos_x', 'gr_shoot_pos_y'))
    if collection == 'playerGoal':
        return tuple(data.get(k) for k in ('gm_id', 'IDX_CODE', 'gr_half', 'gr_half_seconds', 'p_id'))
    if collection.startswith('bmas_'):
        return tuple(data.get(k) for k in ('gm_id', 't_code', 'gr_half', 'gr_seconds'))
    return data.get('gm_id'), data.get('t_code'), data.get('team_type')


def path_ids_equivalent(left, right):
    """Match a legacy scientific display to its original ID, in either direction.

    Precision is taken from the actual display. Callers must also match the
    event identity and reject multiple matches; rounded IDs are not unique.
    """
    if str(left) == str(right):
        return True
    scientific = re.compile(r'^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$')
    left_sci, right_sci = bool(scientific.fullmatch(str(left))), bool(scientific.fullmatch(str(right)))
    if left_sci == right_sci:
        return False
    display, original = (str(left), str(right)) if left_sci else (str(right), str(left))
    if not re.fullmatch(r'\d+(?:\.\d+)?', original):
        return False
    try:
        number, rounded_id = Decimal(original), Decimal(display)
        return number.quantize(Decimal(1).scaleb(rounded_id.as_tuple().exponent), rounding=ROUND_HALF_UP) == rounded_id
    except ArithmeticError:
        return False


def matching_rows(collection, data, rows):
    key = natural_key(collection, data)
    exact = [row for row in rows if natural_key(collection, row['data']) == key]
    if exact or collection != 'TACTIC':
        return exact
    # Only gt_id may differ. Coordinates, player, clock and action still bind.
    return [row for row in rows if
            all(a == b for n, (a, b) in enumerate(zip(key, natural_key(collection, row['data']))) if n != 4)
            and path_ids_equivalent(data.get('gt_id'), row['data'].get('gt_id'))]


def select_contract(contracts: list[dict[str, Any]], match: dict[str, Any], team_id: str,
                    covers: Any) -> dict[str, Any] | None:
    # covers is the existing DID date policy, injected to keep conversion free of database access.
    selected = [c for c in resolve_contract_duplicates(contracts) if c.get('teamId') == team_id
                and c.get('leagueId') in (None, '', match['leagueId'])
                and c.get('seasonId') in (None, '', match['seasonId'])
                and covers(c, match['date'])]
    selected.sort(key=lambda c: (str(c.get('from') or ''), str(c.get('_id') or '')), reverse=True)
    if len(selected) > 1 and selected[0].get('from') == selected[1].get('from'):
        signatures = {(c.get('no'), c.get('pos')) for c in selected if c.get('from') == selected[0].get('from')}
        if len(signatures) > 1:
            raise ValueError('Equally recent contracts disagree on number/position')
    return selected[0] if selected else None


def history(existing: str, contracts: list[dict[str, Any]], league_types: dict[str, str],
            competition_type: str) -> str:
    tokens = [token.strip() for token in existing.split(',') if token.strip()]
    for c in sorted(contracts, key=lambda c: (str(c.get('from') or ''), str(c.get('_id') or ''))):
        kind = c.get('competitionType') or league_types.get(c.get('leagueId'), 'league')
        if kind != competition_type or not c.get('teamId') or c.get('no') in (None, ''):
            continue
        tokens.append(str(c['teamId']) + str(c['no']))
    return ','.join(dict.fromkeys(tokens))


def build_preview(evidence: dict[str, Any], match_id: str, *, covers: Any, match_only=False) -> dict[str, Any]:
    """Return reviewable merge candidates and checks; never writes a destination."""
    evidence = with_registration_references(evidence)
    report: dict[str, Any] = {'mode': 'preview-only', 'match_id': match_id, 'documents': [],
                              'source_captured_at': evidence.get('captured_at'),
                              'checks': [], 'errors': [], 'warnings': [],
                              'scope': ['dMST', 'TACTIC', 'multiSheet_*', 'playerGoal',
                                        'Player', 'playerDB', 'vibePlayer'],
                              'not_generated': ['Manager', 'code (existing lookup only)',
                                                'mMST/pMST/cAVG/rAVG/sAVG/rank (aggregate stage)']}
    report['limitations'] = [
        'No same-match SQL/AIfootballX value-equivalence claim; checks use DID source and observed target types.',
        'DTP paths are assigned once at their final shot result; SSR is cumulative and excludes OWN; GOAL/FPX include OWN.',
        'New candidate IDs are stable, but deletion reconciliation and a write manifest belong to the transmission stage.',
        'Some target fields have no confirmed definition; these are omitted, not zero-filled.',
        'Destination-only fields are preserved; generated fields and nested calculation maps are replaced from the reviewed payload.',
    ]
    report['excluded'] = {'unused': ['pMST.I-*/PH-*/PA-*/PI-*', 'prediction', 'aiPHR', 'yPHR',
                                    'rank.WGR*/Season/S_Avg/R_Rate'],
                          'manual_upload': ['Manager.Coach/Captain', 'playerMST residual score fields']}
    report['excluded']['collections'] = sorted(EXCLUDED_COLLECTIONS)
    report['excluded']['halves'] = ['H3', 'H4']
    report['type_checks'] = {'checked': 0, 'unobserved_fields': [], 'null_only_fields': []}
    target = evidence.get('target_existing', {})
    index: dict[str, dict[tuple[Any, ...], list[dict[str, Any]]]] = {}
    for collection, rows in target.items():
        index[collection] = defaultdict(list)
        for row in rows:
            index[collection][natural_key(collection, row['data'])].append(row)
    identities = set()
    destination_ids = set()
    unchecked, null_only = set(), set()

    def check(name, passed, **details):
        report['checks'].append({'name': name, 'passed': bool(passed), **details})

    def emit(collection, data, source_key=None):
        if collection in EXCLUDED_COLLECTIONS:
            return
        key = natural_key(collection, data)
        rows = matching_rows(collection, data, target.get(collection, []))
        if len(rows) > 1:
            report['errors'].append(f'{collection}: ambiguous existing identity {key}')
            return
        identity = (collection, source_key if source_key is not None else key)
        if identity in identities:
            report['errors'].append(f'{collection}: duplicate candidate identity {identity[1]}')
            return
        identities.add(identity)
        old = rows[0] if rows else None
        candidate_id = stable_id(collection, identity[1])
        own_rows = [r for r in target.get(collection, []) if r['id'] == candidate_id]
        if own_rows and old and own_rows[0]['id'] != old['id']:
            report['errors'].append(f'{collection}: stable ID conflicts with legacy document')
            return
        old = own_rows[0] if own_rows else old
        # A legacy natural key cannot safely distinguish simultaneous identical actions.
        document_id = old['id'] if old else stable_id(collection, identity[1])
        if (collection, document_id) in destination_ids:
            report['errors'].append(f'{collection}: multiple source records resolve to {document_id}')
            return
        destination_ids.add((collection, document_id))
        if old and old['data'].get('uid'):
            data['uid'] = old['data']['uid']
        elif collection not in ('Player', 'playerDB', 'playerMST'):
            data['uid'] = document_id
        schema = evidence.get('projects', {}).get('aifootballx', {}).get(collection, {}).get('fields', {})
        for field, value in data.items():
            types = schema.get(field, [])
            if not types:
                unchecked.add(f'{collection}.{field}')
            elif set(types) == {'NoneType'}:
                null_only.add(f'{collection}.{field}')
            else:
                report['type_checks']['checked'] += 1
            # Null-only samples provide no evidence of the field's non-null type.
            if types and set(types) != {'NoneType'} and type(value).__name__ not in types and not (type(value) in (int, float) and set(types) & {'int', 'float'}):
                report['errors'].append(f'{collection}.{field}: {type(value).__name__} incompatible with {types}')
        report['documents'].append({'collection': collection, 'document_id': document_id,
                                    'action': 'replace-existing' if old else 'create-candidate',
                                    'data': delivery_payload(collection, data, old['data'] if old else None), 'changed_fields': sorted(k for k, v in data.items()
                                                                          if not old or old['data'].get(k) != v)})

    items = sorted((i for i in evidence['source_recordings'] if i['match_id'] == match_id), key=lambda i: i['side'])
    if {i['side'] for i in items} != {'H', 'A'} or len(items) != 2:
        report['errors'].append('Exactly one H and one A recording are required')
    if len(items) == 2 and items[0]['match'] != items[1]['match']:
        report['errors'].append('H/A recording match metadata differ')
    if items and not match_only:
        registered_players(evidence, items[0]['match'], emit, report)
    for original_item in items:
        item = {**original_item, 'player_identities': {pid:player_identity(pid,profile) for pid,profile in evidence.get('player_profiles',{}).items()}}
        side, match, recording = item['side'], item['match'], item['recording']
        prefix = f'{side}:'
        try:
            if recording.get('status') != 'final':
                raise ValueError('Recording is not final')
            if recording.get('ratingBasedOn') != recording.get('kpiVersion') or not recording.get('teamRating'):
                raise ValueError('Rating missing or stale')
            season = season_label(match['seasonId'])
            short_season = int(season[2:4] + season[7:9])
            team_id = match['homeTeamId' if side == 'H' else 'awayTeamId']
            if recording['teamId'] != team_id:
                raise ValueError('Recording team does not match fixture side')
            code = resolve_team_code(evidence['ai_samples']['code'], season, team_id)
            idx = code['IDX_CODE']
            round_ids = sorted({r['id'] for r in evidence['did_matches']
                                if r['data']['seasonId'] == match['seasonId']
                                and r['data']['leagueId'] == match['leagueId'] and r['data']['round'] == match['round']})
            if evidence.get('match_scan_truncated'):
                raise ValueError('Fixture context truncated; cannot allocate Match')
            position = round_ids.index(match_id) * 2 + (1 if side == 'H' else 2)
            existing_dmst = index.get('dMST', {}).get((season, match['round'], team_id), [])
            if len(existing_dmst) == 1:
                position = existing_dmst[0]['data']['Match']
            collisions = [r for r in target.get('dMST', []) if r['data'].get('Match') == position
                          and r['data'].get('t_code') != team_id]
            if collisions:
                raise ValueError('Match allocation collides with existing team row')
            records = source_records(item)
            if not records:
                raise ValueError('No records')
            computed = calculate_kpis(records)
            kpi, rating = recording['kpi'], recording['teamRating']
            unfiltered = source_records(item, include_reviewed_exclusions=True)
            has_reviewed_exclusions = len(unfiltered) != len(records)
            before_exclusions = calculate_kpis(unfiltered) if has_reviewed_exclusions else computed
            if has_reviewed_exclusions:
                report['warnings'].append(prefix + f'{len(unfiltered) - len(records)} reviewed SQL RAW event excluded; source unchanged')
            for metric in ('apx','tpx','fpx'):
                if metric+'Grade' in rating:
                    consistent = rating[metric+'Grade']==grade(rating[metric])
                    # User-approved delivery exception for this exact legacy snapshot.
                    # Keep the source unchanged and retain the failed comparison in the audit.
                    waived = (match_only and match_id=='20232024eplj2rgn13na28rd2b8q0009'
                              and side=='H' and team_id=='E-CH' and match.get('awayTeamId')=='E-LI'
                              and metric=='fpx' and rating[metric]==50.0 and rating[metric+'Grade']=='F')
                    check(prefix+'source-'+metric.upper()+'-grade', consistent or waived,
                          score=rating[metric], stored=rating[metric+'Grade'], expected=grade(rating[metric]),
                          **({'waived':True,'actual_passed':False,
                              'waiver_reason':'User-approved legacy Chelsea home FPX 50.0/F; delivery only'} if waived else {}))
            excluded_records = len(source_records(item, include_extra_halves=True)) - len(records)
            all_computed = calculate_kpis(source_records(item, include_extra_halves=True)) if excluded_records else computed
            if excluded_records:
                report['warnings'].append(prefix + f'{excluded_records} H3/H4 records excluded from delivery eligibility')
            imported = bool(recording.get('legacyGiId')) and all(r.legacy_flags is not None for r in records)
            for key, value in kpi.items():
                if key in computed.team_kpi:
                    legacy_og = (imported and key == 'OG' and value == 0 and computed.team_kpi['OG'] > 0
                        and all(abs(kpi[field] - computed.team_kpi[field]) < 1e-6
                                for field in ('GOAL', 'SHOT', 'SSR')))
                    check(prefix + 'source-KPI-' + key, abs(value - computed.team_kpi[key]) < 1e-6
                          or (excluded_records and abs(value - all_computed.team_kpi[key]) < 1e-6)
                          or (has_reviewed_exclusions and abs(value - before_exclusions.team_kpi[key]) < 1e-6)
                          or approved_path_difference(match_id, side, key, value, computed.team_kpi[key])
                          or legacy_og,
                          stored=value, calculated=computed.team_kpi[key],
                          **({'legacy_og_placeholder': True, 'actual_passed': False,
                              'reason': 'Imported OG placeholder; GOAL/SHOT/OWN-exclusive SSR verified against RAW'} if legacy_og else {}))
            # Use the reviewed RAW result, never reintroduce obsolete stored totals.
            kpi = computed.team_kpi
            reviewed_change = has_reviewed_exclusions or any(
                approved_path_difference(match_id, side, key, value, computed.team_kpi[key])
                for key, value in recording['kpi'].items() if key in computed.team_kpi)
            if reviewed_change:
                # Imported data does not run the new-input KPI POST. Exact
                # reviewed deltas are audited above; rating value checks below
                # remain independent and are never silently waived.
                check(prefix + ('reviewed-import-KPI-difference' if imported else 'reviewed-KPI-rebuild'), imported or all(
                    abs(value - computed.team_kpi[key]) < 1e-6
                    for key, value in recording['kpi'].items() if key in computed.team_kpi),
                    reason=('Reviewed import difference; no new-input KPI POST required; saved ratings checked separately'
                            if imported else 'New-input stored KPI differs from reviewed RAW'))
            bins = intervals(records, computed)
            for metric in ('BAP', 'DAP', 'DTP', 'SHOT', 'GOAL'):
                expected = computed.team_kpi[metric]
                check(prefix + 'interval-total-' + metric, sum(bins[metric]) == expected,
                      intervals=sum(bins[metric]), total=expected)
            common = {'gm_id': match_id, 'gm_round': match['round'], 'PlSeason': season}
            wide = dict(common, gm_date=dotted_date(match['date']), t_code=idx, team_type='HOME' if side == 'H' else 'AWAY')
            series = rating.get('jmxSeries') or []
            if len(series) != 20:
                raise ValueError('Expected 20 stored JMX intervals')
            check(prefix + 'JMX-final', abs(series[-1] - rating['jmx']) < 1e-6)
            bins['JMX'] = series
            # Only the standalone GOAL sheet excludes OWN; ALL and team totals retain it.
            sheet_goals = list(bins['GOAL'])
            for record in records:
                if computed.flags[record.id].is_goal and record.player_id == 'OWN':
                    sheet_goals[bucket(record.half, record.seconds)] -= 1
            expected_sheet_goals = computed.team_kpi['GOAL'] - computed.team_kpi['OG']
            check(prefix + 'multiSheet_GOAL-total', sum(sheet_goals) == expected_sheet_goals,
                  intervals=sum(sheet_goals), total=expected_sheet_goals)
            for metric, values in bins.items():
                fields = {f'{metric}-{interval_name(i)}': v for i, v in enumerate(values)}
                wide.update(fields)
                if metric != 'SSR':
                    sheet_fields = ({f'GOAL-{interval_name(i)}': v for i, v in enumerate(sheet_goals)}
                                    if metric == 'GOAL' else fields)
                    emit('multiSheet_' + metric, {**{k: v for k, v in wide.items() if '-H' not in k}, **sheet_fields})
            emit('multiSheet_ALL', wide)
            shotscore = min(kpi['SHOT'] / SCORE_BASELINES['SHOT'] * 100, 99.9)
            goalscore = min(kpi['GOAL'] / SCORE_BASELINES['GOAL'] * 100, 99.9)
            ssrscore = float(Decimal(str(kpi['SSR'])) / Decimal(str(SCORE_BASELINES['SSR'])) * 100)
            dmst = {**common, 'gm_date': dotted_date(match['date']), 't_code': team_id, 'IDX_CODE': idx,
                    'H-A': side, 'Season': short_season, 'Match': position,
                    **{key: kpi[key] for key in ('BAP', 'DAP', 'DTP', 'TTP', 'SHOT', 'ASR', 'SSR', 'GOAL', 'OG')},
                    **{dest: kpi[src] for dest, src in {'gi_tmp': 'TAP', 'B': 'DTB', 'M': 'DTM', 'A': 'DTA', 'S': 'DTS'}.items()},
                    'DAP(AiAttack)': float(Decimal(str(kpi['DAP'])) / SCORE_BASELINES['DAP'] * 100),
                    'DTP(AiTactic)': float(Decimal(str(kpi['DTP'])) / SCORE_BASELINES['DTP'] * 100),
                    'SHOTScore': shotscore, 'SHOT(AiShot)': shotscore, 'GOALScore': goalscore,
                    'GOAL(AiGoal)': goalscore, 'SSRScore': ssrscore, 'SSR(AiSSR)': ssrscore,
                    'JMX': rating['jmx'], 'JMX2': rating['jmx'], 'gi_score2': rating['jmx'],
                    'APX': rating['apx'], 'TPX': rating['tpx'], 'FPX': rating['fpx'], 'FPX(AiFPX)': rating['fpx'],
                    'Power3X(AVG)': precise_mean(rating[k] for k in ('apx', 'tpx', 'fpx')),
                    'T-code': code['No'], 'T-code2': code['No'] + match['round'], 'T-code3': 0}
            for metric in ('APX', 'TPX', 'FPX', 'JMX'):
                dmst['G-' + metric] = grade(dmst[metric])
            for metric, value in (('SHOT', shotscore), ('GOAL', goalscore), ('SSR', ssrscore)):
                dmst['G-' + metric] = grade(value)
            check(prefix + 'FPX-components', abs((shotscore + goalscore) / 2 - rating['fpx']) < .11)
            for metric, source, baseline in (('APX', 'DAP', 'DAP'), ('TPX', 'DTP', 'DTP')):
                equal = abs(min(kpi[source] / SCORE_BASELINES[baseline] * 100, 99.9) - dmst[metric]) < .11
                excluded_only = (excluded_records
                    and abs(recording['kpi'][source] - all_computed.team_kpi[source]) < 1e-6
                    and abs(min(recording['kpi'][source] / SCORE_BASELINES[baseline] * 100, 99.9) - dmst[metric]) < .11)
                waived = approved_apx_difference(
                    match_id, side, team_id, match.get('awayTeamId'), metric,
                    imported, has_reviewed_exclusions, recording['kpi'].get('DAP'),
                    before_exclusions.team_kpi['DAP'], kpi['DAP'], dmst[metric], SCORE_BASELINES[baseline])
                check(prefix + metric + '-baseline', equal or excluded_only or waived,
                      **({'excluded_extra_halves': True} if excluded_only and not equal else {}),
                      **({'waived': True, 'actual_passed': False,
                          'stored': dmst[metric], 'calculated': 67.50,
                          'waiver_reason': 'User-approved 0087H APX 67.75/67.50 after exact reviewed RAW exclusion; saved rating preserved'}
                         if waived else {}))
            dmst.update({'M_No2': match['round'] * 100 + position,
                         'S-Round': short_season * 10000 + match['round'] * 100 + position,
                         'M_No1': short_season * 10000 + (match['round'] - 1) * 20 + position,
                         'T-Round': code['No'] + match['round'],
                         'M-Code': short_season * 10000 + code['No'] + match['round']})
            # The numbering formula is EPL-specific; do not silently use it for a cup.
            if match['leagueId'] != 'EPL':
                raise ValueError('Legacy numbering formula is only verified for EPL')
            own, other = ('home', 'away') if side == 'H' else ('away', 'home')
            dmst['Point'] = 3 if match['score'][own] > match['score'][other] else 1 if match['score'][own] == match['score'][other] else 0
            team = evidence.get('teams', {}).get(team_id, {})
            dmst['t_Nm'] = team_delivery_name(evidence['ai_samples']['code'], season, team_id, team)
            emit('dMST', dmst)
            if 'coach_rounds' in evidence and not match_only:
                managers = [r['data'] for r in evidence['coach_rounds'] if r['data'].get('plSeason') == season
                            and r['data'].get('round') == match['round'] and r['data'].get('teamId') == team_id]
                if len(managers) != 1:
                    raise ValueError('Expected one coachRounds row per season/team/round')
                emit('Manager', manager_projection(managers[0], code, dmst.get('t_Nm', team_id)))
            goals = []
            tactic_ids = tactic_record_ids(records, computed)
            for row in sorted((r for r in item['children']['records'] if r['id'] in computed.flags),
                              key=lambda r: (r['data']['half'], r['data']['halfSeconds'],
                                                                         r['data'].get('createdAt', ''), r['id'])):
                value = row['data']
                flag = computed.flags[row['id']]
                projection = tactic_projection(item, row, flag.path_id)
                if row['id'] in tactic_ids:
                    emit('TACTIC', dict(projection, t_code=idx), (match_id, side, row['id']))
                if not flag.is_goal:
                    continue
                own_goal = value.get('playerId') == 'OWN'
                goals.append(f"{value['half']}_{'OG_' if own_goal else ''}{value['halfSeconds']}")
                if own_goal:
                    continue
                player = evidence.get('player_profiles', {}).get(value.get('playerId'), {})
                goal = {**common, 'gm_date': dotted_date(match['date']), 'IDX_CODE': idx, 't_code': team_id,
                        'team_type': wide['team_type'], 'HA': side, 'gr_res_code': 'G',
                        'gr_half': value['half'], 'gr_half_seconds': value['halfSeconds'],
                        'p_id': (value['playerId'] if str(value.get('playerId')).isdigit() else str(player_identity(value['playerId'], player))) if value.get('playerId') else NULL}
                for dest, src in (('p_name', 'name'), ('p_name_en', 'nameEn')):
                    if src in player:
                        goal[dest] = player[src]
                stadium = evidence.get('stadiums', {}).get(match.get('stadiumId'), {})
                if stadium.get('name'):
                    goal['s_name'] = stadium['name']
                emit('playerGoal', goal, (match_id, side, row['id']))
            actual_tactic = [d for d in report['documents'] if d['collection'] == 'TACTIC'
                             and d['data']['gm_id'] == match_id and d['data']['t_code'] == idx]
            check(prefix + 'TACTIC-selected-records', len(actual_tactic) == len(tactic_ids),
                  expected=len(tactic_ids), actual=len(actual_tactic),
                  excluded=len(computed.flags) - len(tactic_ids))
            if len(goals) > 10:
                raise ValueError('More than ten goals; refusing truncation')
            emit('multiSheet_GOAL-시간', {**{k: v for k, v in wide.items() if '-H' not in k},
                                         **{f'GOAL{i + 1}': goals[i] if i < len(goals) else NULL for i in range(10)}})
            build_players(evidence, item, computed, code, short_season, emit, report, covers, check)
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            report['errors'].append(prefix + str(exc))
    failed = [c for c in report['checks'] if not c['passed']]
    if 'coach_rounds' in evidence:
        report['scope'].append('Manager')
        report['not_generated'].remove('Manager')
    report['type_checks']['unobserved_fields'] = sorted(unchecked)
    report['type_checks']['null_only_fields'] = sorted(null_only)
    report['summary'] = {'documents': len(report['documents']),
                         'collections': dict(sorted(Counter(d['collection'] for d in report['documents']).items())),
                         'checks': len(report['checks']), 'failed_checks': len(failed),
                         'errors': len(report['errors']), 'warnings': len(report['warnings'])}
    report['validation_passed'] = bool(items) and not failed and not report['errors']
    report['write_enabled'] = False
    report['ready_for_write'] = False
    return report



def player_identity(player_id, profile=None):
    """Keep legacy numbers; allocate a stable safe-integer identity for DID auto IDs."""
    if str(player_id).isdigit():
        return numeric_player_id(player_id)
    legacy = (profile or {}).get('legacyPlayerId')
    if legacy is not None:
        return numeric_player_id(legacy)
    return 10**12 + int.from_bytes(sha256(('DID-player:'+str(player_id)).encode()).digest()[:6], 'big')


def registration_link(evidence, player_id, season, matches):
    """Identity membership is independent of a particular match's appearance/date."""
    teams = {r['data'][k] for r in matches for k in ('homeTeamId','awayTeamId')}
    profile = evidence.get('player_profiles', {}).get(player_id, {})
    all_contracts = resolve_contract_duplicates(evidence.get('player_contracts', {}).get(player_id, []))
    dates = sorted(re.sub(r'\D','',r['data']['date']) for r in matches)
    dated_contracts = [c for c in all_contracts if c.get('leagueId') in (None, '', 'EPL')
                       and c.get('competitionType', 'league') == 'league'
                       and re.sub(r'\D', '', c.get('from') or '') <= dates[-1]]
    contracts = [c for c in dated_contracts
                 if c.get('teamId') in teams and c.get('leagueId') in (None,'EPL')
                 and c.get('competitionType','league')=='league']
    identity = player_identity(player_id, profile)
    has_destination_registration = any(
        r['data'].get('P-1') == identity and r['data'].get('P-6') == int(season[2:4]+season[7:9])
        for r in evidence.get('target_existing', {}).get('playerDB', []))
    # Existing registrations for a relegated club still need an identity. The
    # destination supplies membership only; contract values always come from DID.
    if has_destination_registration:
        # An existing registration can belong to a relegated/foreign club.
        # Its newer DID contract must not lose to an older current-season club.
        contracts = dated_contracts
    if not dated_contracts and any(c.get('leagueId') == 'EPL' for c in all_contracts):
        return None  # A later transfer is not a registration in an earlier season.
    overlapping = [c for c in contracts if re.sub(r'\D','',c.get('from') or '')<=dates[-1]
                   and re.sub(r'\D','',c.get('to') or '99999999')>=dates[0]]
    if overlapping:
        contracts = overlapping
    elif dated_contracts and not has_destination_registration:
        # Historical Player coverage must not register every retired player in
        # the active playerDB merely because his old club is still in the EPL.
        return None
    elif profile.get('currentTeamId') in teams:
        return {'teamId':profile['currentTeamId'], 'no':profile.get('currentNo'), 'pos':profile.get('currentPos')}
    if not contracts:
        links = evidence.get('player_team_links', {}).get(player_id, [])
        links = [team for team in links if team in teams]
        if len(set(links))==1:
            return {'teamId':links[0]}
        return None
    latest = max(re.sub(r'\D','',c.get('from') or '') for c in contracts)
    chosen = [c for c in contracts if re.sub(r'\D','',c.get('from') or '')==latest]
    if len({c['teamId'] for c in chosen})!=1:
        raise ValueError(f'Ambiguous latest season contract for player {player_id}')
    # Identity needs the team, not a fabricated choice between conflicting optional dates/attributes.
    result = {key:chosen[0][key] for key in ('teamId','from','to','no','pos')
              if key in chosen[0] and all(c.get(key)==chosen[0][key] for c in chosen)}
    if str(result.get('no') or '0') == '0':
        known = [c for c in dated_contracts if c.get('teamId') == result['teamId']
                 and str(c.get('no') or '').isdigit() and int(c['no']) > 0]
        if known:
            latest_known = max(re.sub(r'\D', '', c.get('from') or '') for c in known)
            numbers = {int(c['no']) for c in known if re.sub(r'\D', '', c.get('from') or '') == latest_known}
            if len(numbers) == 1:
                result['no'] = numbers.pop()
    return result


def registered_player_projection(evidence, player_id, season, matches, *, contract=None):
    profile = evidence.get('player_profiles', {}).get(player_id, {})
    link = contract if contract is not None else registration_link(evidence,player_id,season,matches)
    if link is None:
        return None
    if not profile.get('name'):
        raise ValueError(f'Missing player name/profile {player_id}')
    pid = player_identity(player_id,profile)
    mapped = {dest:clean_player_name(profile[src]) if dest in ('p_name','p_name_en','p_name_full') else profile[src]
              for dest,src in PLAYER_FIELDS.items() if profile.get(src) is not None}
    if mapped.get('p_birth'): mapped['p_birth']=dotted_date(mapped['p_birth'])
    mapped.update(p_id=pid,PlSeason=season,l_code='EPL',t_code=link['teamId'],
                  IDX_CODE=registration_team_index(evidence,season,link['teamId']))
    if not str(player_id).isdigit():
        mapped['DID_PLAYER_ID']=player_id
        for col,field in (('Player','p_id'),('playerDB','P-1')):
            for old in evidence.get('target_existing',{}).get(col,[]):
                if old['data'].get(field)==pid and old['data'].get('DID_PLAYER_ID')!=player_id:
                    raise ValueError(f'Player identity collision: {player_id}/{pid}')
    for source,dest in (('from','pt_begin'),('to','pt_end')):
        if link.get(source): mapped[dest]=dotted_date(link[source])
    if link.get('pos'): mapped['pt_pos']=link['pos']
    if str(link.get('no') or '').isdigit(): mapped['pt_num']=int(link['no'])
    mapped['p_id_old'] = registration_history(evidence, player_id, season)
    return mapped


def registered_player_projections(evidence, player_id, season, matches):
    """Preserve DID team/start contract identities, including unknown/open dates.

    playerDB remains the latest season registration; Player stores the history.
    Contract ends are values, not identity: an eventual transfer closes the same
    team/start document. No historical source document is changed here.
    """
    contracts = [c for c in resolve_contract_duplicates(evidence.get('player_contracts', {}).get(player_id, []))
                 if c.get('leagueId') in (None, '', 'EPL')
                 and c.get('competitionType', 'league') == 'league' and c.get('teamId')]
    if not contracts:
        mapped = registered_player_projection(evidence, player_id, season, matches)
        if mapped is None:
            return []
        mapped.setdefault('pt_begin', '0000.00.00')
        mapped.setdefault('pt_end', '9999.99.99')
        return [mapped]
    projections = {}
    for contract in sorted(contracts, key=lambda c: (c['teamId'], c.get('from') or '', c.get('_id') or '')):
        mapped = registered_player_projection(evidence, player_id, season, matches, contract=contract)
        for dest, src in PLAYER_FIELDS.items():
            if src in evidence['player_profiles'][player_id] and evidence['player_profiles'][player_id][src] is None:
                mapped[dest] = None
        mapped['pt_begin'] = dotted_date(contract.get('from') or '0000.00.00')
        mapped['pt_end'] = dotted_date(contract.get('to') or '9999.99.99')
        mapped['p_id_old'] = registration_alias(evidence, player_id, season, contract)
        for src, dest in (('no', 'pt_num'), ('pos', 'pt_pos')):
            if src in contract and contract[src] is None:
                mapped[dest] = None
        identity = natural_key('Player', mapped)
        if identity in projections and projections[identity] != mapped:
            raise ValueError(f'Conflicting DID contract identity for player {player_id}: {identity}')
        projections[identity] = mapped
    return list(projections.values())


def registered_players(evidence, match, emit, report):
    matches=evidence.get('did_matches') or [{'id':'scope','data':match}]
    matches=[r for r in matches if r['data']['seasonId']==match['seasonId']]
    season=season_label(match['seasonId']); teams={r['data'][k] for r in matches for k in ('homeTeamId','awayTeamId')}
    referenced={r['id'] for item in evidence['source_recordings'] for r in item['children']['playerStats']}
    referenced.update(pid for item in evidence['source_recordings'] for pid in item['recording'].get('lineup',{}))
    selected=set(evidence.get('registration_player_ids',[]))|referenced
    selected.update(pid for pid,p in evidence.get('player_profiles',{}).items() if p.get('currentTeamId') in teams)
    identities={}
    for pid in sorted(selected):
        try:
            mapped=registered_player_projection(evidence,pid,season,matches)
            registrations = registered_player_projections(evidence, pid, season, matches)
            if mapped is None and not registrations: continue
            identity=player_identity(pid, evidence.get('player_profiles', {}).get(pid))
            if identity in identities and identities[identity]!=pid:
                raise ValueError(f'Player identity collision: {pid}/{identities[identity]}')
            identities[identity]=pid
            for registration in registrations:
                emit('Player', registration)
            if mapped is not None:
                emit('playerDB',player_db_projection(evidence,mapped,season))
        except (KeyError,ValueError,TypeError) as exc:
            report['errors'].append(str(exc))

def player_tactical_participation(kpi):
    """vibePlayer P-DTP is maker + assister contribution (DTM + DTA).

    It is not the distinct DTP path participation count used by playerStats.DTP.
    Build-up and shooting contributions are separate metrics, not P-DTP.
    """
    return kpi['DTM'] + kpi['DTA']


def build_players(evidence, item, computed, code, short_season, emit, report, covers, check):
    match, rec, side = item['match'], item['recording'], item['side']
    season, team_id, idx = season_label(match['seasonId']), rec['teamId'], code['IDX_CODE']
    league_types = {key: value.get('competitionType', 'league') for key, value in evidence.get('leagues', {}).items()}
    kind = league_types.get(match['leagueId'], 'league')
    stats_ids = {r['id'] for r in item['children']['playerStats']}
    missing = set(computed.player_kpis) - stats_ids
    if missing:
        report['errors'].append(f'{side}: playerStats missing active record players {sorted(missing)}')
    records_with_extra_halves = source_records(item, include_extra_halves=True)
    has_extra_halves = len(records_with_extra_halves) != len(source_records(item))
    full_computed = calculate_kpis(records_with_extra_halves) if has_extra_halves else computed
    for row in item['children']['playerStats']:
        value, player_id = row['data'], row['id']
        profile = evidence.get('player_profiles', {}).get(player_id)
        if not profile:
            report['errors'].append(f'{side}: missing player profile {player_id}')
            continue
        pid = player_identity(player_id,profile)
        lineup = rec.get('lineup', {}).get(player_id, {})
        gp_type = player_game_type(lineup, lineup.get('pos') or '') if lineup.get('type') else None
        active = gp_type is not None or any(computed.player_kpis.get(player_id, {}).values())
        if not active:
            continue
        for field, actual in computed.player_kpis.get(player_id, {}).items():
            stored = value.get(field)
            equal = stored is not None and abs(stored - actual) < 1e-6
            # Only waive a discrepancy explained by the excluded H3/H4 records.
            # An unrelated mismatch in H1/H2 must still fail validation.
            with_extras = full_computed.player_kpis.get(player_id, {}).get(field)
            excluded_only = (has_extra_halves and with_extras is not None and stored is not None
                             and abs(stored - with_extras) < 1e-6)
            check(f'{side}:player-{pid}-{field}', equal or excluded_only,
                  stored=stored, calculated=actual,
                  **({'excluded_extra_halves': True, 'calculated_with_extra_halves': with_extras}
                     if excluded_only and not equal else {}))
        contracts = evidence.get('player_contracts', {}).get(player_id, [])
        scoped = [c for c in contracts if (c.get('competitionType') or league_types.get(c.get('leagueId'), 'league')) == kind]
        contract = select_contract(scoped, match, team_id, covers)
        # Approved legacy date-boundary exceptions: preserve actual appearances,
        # without extending a contract or borrowing another team's contract.
        if contract is None and (item['match_id'], side, player_id) in {
            ('20232024eplj2rgn13na28rd2b8q0220', 'A', '0000003668'),
            ('20232024eplj2rgn13na28rd2b8q0220', 'A', '0000005560'),
        }:
            contract = {}
            report['warnings'].append(f'{side}: approved legacy contract date boundary {player_id}')
        if contract is None:
            report['errors'].append(f'{side}: no match-date contract {player_id}')
            continue
        # Match appearances need the profile name, not a season registration or
        # every historical alias. Match-date contract validation is above.
        mapped = {'p_name': profile.get('name', '')}
        lineup = rec.get('lineup', {}).get(player_id, {})
        position = lineup.get('pos') or contract.get('pos') or ''
        gp_type = player_game_type(lineup, position) if lineup.get('type') else None
        if gp_type is None:
            if any(computed.player_kpis.get(player_id, {}).get(k, 0) for k in ('TAP', 'SHOT', 'GOAL')):
                report['errors'].append(f'{side}: active player lacks substitute entry time {player_id}')
            continue
        if value.get('ratingBasedOn') != rec.get('kpiVersion'):
            report['errors'].append(f'{side}: stale player rating {player_id}')
        vibe = {'gm_id': item['match_id'], 'gm_round': match['round'], 'PlSeason': season, 'p_id': pid,
                'gi_write_code': side, 'l_code': match['leagueId'], 'IDX_CODE': idx,
                'p_name': mapped.get('p_name', ''), 'pt_pos': position,
                'P-BAP': value['DTB'], 'gp_type': gp_type}
        for metric in ('apx', 'tpx', 'fpx', 'jmx', 'DAP', 'SHOT', 'SSR', 'GOAL'):
            if value.get(metric) is None:
                report['errors'].append(f'{side}: missing player rating/KPI {player_id}.{metric}')
            else:
                vibe['P-' + metric.upper()] = rating_two_decimals(value[metric]) if metric in ('apx', 'tpx', 'fpx', 'jmx') else value[metric]
        vibe['P-DTP'] = player_tactical_participation(value)
        for metric in ('apx', 'tpx', 'fpx'):
            if value.get(metric + 'Grade'):
                check(f'{side}:player-{pid}-{metric}-grade', value[metric+'Grade']==grade(value[metric]),
                      score=value[metric], stored=value[metric+'Grade'], expected=grade(value[metric]))
                vibe['G-' + metric.upper()] = value[metric + 'Grade']
        emit('vibePlayer', vibe)


def manager_projection(row: dict[str, Any], code: dict[str, Any], team_name: str) -> dict[str, Any]:
    """coachRounds retains the legacy numeric coach identity and bilingual names."""
    status = {'MANAGER': ('Manager', '정식'), 'CARETAKER': ('Caretaker', '감독대행')}
    if row.get('status') not in status:
        raise ValueError('Unknown manager status')
    if not row.get('coachName') or not row.get('coachNameEn'):
        raise ValueError('Manager needs Korean and English names')
    en, kr = status[row['status']]
    return {'PlSeason': row['plSeason'], 'Round': row['round'],
            'T_Code': row['teamId'], 'IDX_CODE': code['IDX_CODE'], 'T_Name': team_name,
            'm_id': (numeric_player_id(row.get('legacyCoachId') or row['coachId'])
                     if str(row.get('legacyCoachId') or row['coachId']).isdigit() else player_identity('coach:'+str(row['coachId']))),
            'Manager_EN': row['coachNameEn'],
            'Manager_KR': row['coachName'], 'Status_EN': en, 'Status_KR': kr,
            'gm_date': dotted_date(row['from']), 'e_date': dotted_date(row.get('to') or '9999.99.99')}


# ==========================================================================
# Season aggregates
# ==========================================================================


METRICS = ('DAP', 'BAP', 'DTP', 'SHOT', 'SSR', 'GOAL')
KPI_ALIASES = {'gi_tmp': 'TAP', 'DAP': 'DAP', 'TTP': 'TTP', 'DTP': 'DTP', 'BAP': 'BAP',
               'B': 'DTB', 'M': 'DTM', 'A': 'DTA', 'S': 'DTS', 'SHOT': 'SHOT',
               'ASR': 'ASR', 'SSR': 'SSR', 'GOAL': 'GOAL'}
# Prediction index recipe observed in the legacy target. This is NOT the NEW 3PX recipe.
LEGACY_PREDICTION_BASELINES = {'DAP': 400, 'BAP': 65, 'DTP': 20, 'SHOT': 25, 'SSR': .2, 'GOAL': 3.7}
LEGACY_2025_PREDICTION_BASELINES = {'DAP': 450, 'BAP': 60, 'DTP': 18, 'SHOT': 20, 'SSR': .18, 'GOAL': 4}
# Existing rank uses these legacy slots, not the three promoted teams' code.POSITION.
# Each correspondence was checked against all 38 RAV rounds, with a unique match.
LEGACY_RANK_SLOT_OVERRIDES = {'2025-2026': {'E-BU': 27, 'E-LU': 12, 'E-SU': 21}}


def rounded(value: float, digits: int = 1) -> float:
    return float(Decimal(str(value)).quantize(Decimal(10) ** -digits, rounding=ROUND_HALF_UP))


def rank_competition(value: float, values: list[float]) -> int:
    return 1 + sum(other > value for other in values)


def aggregate_key(collection: str, data: dict[str, Any]) -> tuple:
    if collection == 'mMST':
        return data.get('PlSeason'), data.get('Round'), data.get('H-Code'), data.get('A-Code')
    if collection == 'pMST':
        return data.get('PlSeason'), data.get('gm_round'), data.get('t_code')
    if collection == 'sAVG':
        return (data.get('SEASON'),)
    return data.get('PlSeason'), data.get('Round')


def build_aggregates(items: list[dict], code_rows: list[dict], slot_definitions: dict,
                     *, prediction_inputs: dict | None = None,
                     prediction_index_baselines: dict | None = None,
                     calculation_mode: str = 'current') -> dict:
    if calculation_mode not in ('current', 'legacy-audit'):
        raise ValueError('Unknown calculation mode')
    legacy = calculation_mode == 'legacy-audit'
    rating_precision = rounded if legacy else truncate_rating
    if prediction_index_baselines is not None and not legacy:
        raise ValueError('Historical I-* recipes require calculation_mode=legacy-audit')
    if prediction_inputs is not None and not legacy:
        raise ValueError('Unused prediction fields are disabled in current mode')
    result = {'mode': 'aggregate-preview-only', 'write_enabled': False, 'documents': [],
              'calculation_mode': calculation_mode,
              'excluded': {'unused': ['pMST.I-*', 'pMST.PH-*', 'pMST.PA-*', 'pMST.PI-*',
                                      'prediction', 'aiPHR', 'yPHR', 'rank.WGR*',
                                      'rank.Season', 'rank.S_Avg', 'rank.R_Rate'],
                           'manual_upload': ['Manager.Coach/Captain', 'playerMST residual score fields']},
              'errors': [], 'blocked': [], 'policies': {
                  'rating': 'DID stored teamRating only; no fallback to SQL gi_score',
                  'prediction_index': prediction_index_baselines or 'unused; no generation or missing-input warning',
                  'rank': ('Historical Excel competition RANK; WGR=floor(WRK/4)+1' if legacy else
                           'AIfootballX ai_chart/build_ranking.py: points/GD/GF/team; JMX/growth/team; unique ranks'),
                  'legacy_fields': ('diagnostic only; not approved for delivery' if legacy else
                                    'I-*, WGR*, Season, S_Avg, R_Rate unused by user decision; omitted'),
                  'season_reference_average': 'current: AVG equals same-season AI Rating; never a multi-season average',
                  'unplayed': 'non-final recordings excluded, never padded with a fabricated rating',
              }}
    result['summary'] = {'documents': 0, 'errors': 0, 'collections': {}}
    final = [i for i in items if i['recording'].get('status') == 'final']
    seasons = {i['match']['seasonId'] for i in final}
    if len(seasons) != 1:
        result['errors'].append('Exactly one season is required')
        result['summary']['errors'] = 1
        result['complete'] = False
        return result
    season_id = seasons.pop()
    season = season_label(season_id)
    short = int(season[2:4] + season[7:9])
    grouped = defaultdict(dict)
    rows_by_round = defaultdict(list)
    keys = set()
    all_rows = []
    blocked = defaultdict(set)

    def emit(collection, data):
        key = aggregate_key(collection, data)
        if (collection, key) in keys:
            result['errors'].append(f'{collection}: duplicate identity {key}')
            return
        keys.add((collection, key))
        result['documents'].append({'collection': collection, 'document_id': stable_id(collection, key),
                                    'data': data if legacy else delivery_payload(collection, data)})

    def avg_known(values, collection, field, scope):
        # Never average a partial subset and present it as the complete league.
        if values and all(v is not None for v in values):
            return precise_mean(values)
        blocked[collection].add(f'{field}: missing stored JMX ({scope})')
        return None

    for item in final:
        match, rec, side = item['match'], item['recording'], item['side']
        if match.get('leagueId') != 'EPL':
            result['errors'].append('Numbering/20-team slots are verified only for EPL')
            continue
        if type(match.get('round')) is not int or match['round'] < 1:
            result['errors'].append('Round must be a positive integer')
            continue
        required_kpi = set(KPI_ALIASES.values())
        if any(type(rec.get('kpi', {}).get(k)) not in (int, float)
               or not math.isfinite(rec['kpi'][k]) for k in required_kpi):
            result['errors'].append(f'Missing/non-finite KPI at {item["match_id"]}/{side}')
            continue
        key = item['match_id']
        if side not in ('H', 'A') or side in grouped[key]:
            result['errors'].append(f'Duplicate/unknown recording side: {key}/{side}')
            continue
        team = match['homeTeamId' if side == 'H' else 'awayTeamId']
        try:
            code = resolve_team_code(code_rows, season, team)
        except ValueError as exc:
            result['errors'].append(f'{team}: {exc}')
            continue
        rating = rec.get('teamRating') or {}
        if rating and (rec.get('ratingBasedOn') is None or rec.get('ratingBasedOn') != rec.get('kpiVersion')):
            result['errors'].append(f'Stale rating {key}/{side}')
            rating = {}
        if rating and (type(rating.get('jmx')) not in (int, float) or not math.isfinite(rating['jmx'])):
            result['errors'].append(f'Invalid stored JMX {key}/{side}')
            rating = {}
        own, other = ('home', 'away') if side == 'H' else ('away', 'home')
        score, against = match['score'][own], match['score'][other]
        row = {'item': item, 'team': team, 'idx': code['IDX_CODE'], 'no': code['No'],
               'team_number': code['team_code_number'], 'side': side, 'round': match['round'],
               'kpi': rec['kpi'], 'jmx': rating.get('jmx'), 'goals': score, 'against': against,
               'point': 3 if score > against else 1 if score == against else 0,
               'wdl': 'W' if score > against else 'D' if score == against else 'L',
               'name': team_delivery_name(code_rows, season, team, {'name': item.get('team_name')})}
        matching_codes = [r['data'] for r in code_rows
                          if (r['data'].get('TCODE') or r['data'].get('Code')) == team
                          and r['data'].get('PlSeason') == season]
        positions = {r.get('POSITION') for r in matching_codes}
        if len(positions) != 1 or None in positions:
            result['errors'].append(f'Missing/ambiguous season rank POSITION for {team}')
            blocked['rank'].add(f'{team}: season rank POSITION missing/ambiguous')
            row['rank_slot'] = None
        else:
            row['rank_slot'] = LEGACY_RANK_SLOT_OVERRIDES.get(season, {}).get(team, int(next(iter(positions))))
        grouped[key][side] = row
        rows_by_round[match['round']].append(row)
        all_rows.append(row)

    if not all_rows:
        result['errors'].append('No valid final recordings')
        result['summary']['errors'] = len(result['errors'])
        result['complete'] = False
        return result
    result['policies']['rank_slot_overrides'] = LEGACY_RANK_SLOT_OVERRIDES.get(season, {})

    # Allocation is independent of dates. Rescheduled matches keep their gm_id order.
    for round_no, round_rows in sorted(rows_by_round.items()):
        mids = sorted({row['item']['match_id'] for row in round_rows})
        for row in round_rows:
            fixture_no = mids.index(row['item']['match_id']) + 1
            row['fixture_no'] = fixture_no
            row['match_position'] = (fixture_no - 1) * 2 + (1 if row['side'] == 'H' else 2)
    for mid, pair in sorted(grouped.items()):
        if set(pair) != {'H', 'A'}:
            result['errors'].append(f'Incomplete fixture: {mid}')
            continue
        h, a = pair['H'], pair['A']
        if h['item']['match'] != a['item']['match']:
            result['errors'].append(f'Fixture metadata mismatch: {mid}')
            continue
        match, round_no, n = h['item']['match'], h['round'], h['fixture_no']
        mmst = {'PlSeason': season, 'Round': round_no, 'Match-No': n, 'RM-NO': round_no * 1000 + n,
                'M-Home': h['match_position'], 'M-Away': a['match_position'],
                'M-No1': round_no * 100 + h['match_position'], 'M-No2': round_no * 100 + a['match_position'],
                'M_date': dotted_date(match['date'])}
        for side, row in pair.items():
            mmst.update({f'{side}-{dest if dest != "gi_tmp" else "TAP"}': row['kpi'][src]
                         for dest, src in KPI_ALIASES.items()})
            mmst.update({f'{side}-Code': row['team'], f'{side}-IDX': row['idx'],
                         f'{side}-Name': row['name'], f'{side}-DWL': row['wdl']})
            if row['jmx'] is not None:
                mmst[f'{side}-FX'] = rating_precision(row['jmx'], 2)
            else:
                blocked['mMST'].add(f'{side}-FX: missing stored JMX')
            mmst.update({f'6{side}-{m}': rounded(row['kpi'][m], 3 if m == 'SSR' else 1) for m in METRICS})
            pos = row['match_position']
            pmst = {'PlSeason': season, 'Season': short, 'gm_round': round_no, 'ROUND2': round_no,
                    'gm_date': dotted_date(match['date']), 't_code': row['team'], 'IDX_CODE': row['idx'], 't_Nm': row['name'],
                    'Match': pos, 'M_No1': short * 10000 + (round_no - 1) * 20 + pos,
                    'M_No2': round_no * 100 + pos, 'M_No3': round_no * 100 + pos,
                    'T-code': row['no'], 'T-Round': row['no'] + round_no,
                    'M-Code': short * 10000 + row['no'] + round_no,
                    'S-Round': short * 10000 + round_no * 100 + pos,
                    'Goal': row['against'], **{dest: row['kpi'][src] for dest, src in KPI_ALIASES.items()},
                    **{m + '2': row['kpi'][m] for m in METRICS}}
            # Legacy pMST uploads retain three decimals for success rates; mMST retains raw rates.
            pmst['ASR'] = rounded(pmst['ASR'], 3)
            pmst['SSR'] = rounded(pmst['SSR'], 3)
            pmst['SSR2'] = rounded(pmst['SSR2'], 3)
            if row['jmx'] is not None:
                pmst['gi_score'] = row['jmx']
            else:
                blocked['pMST'].add('gi_score: missing stored JMX')
            for m in METRICS:
                if not legacy:
                    continue
                if not prediction_index_baselines:
                    blocked['pMST'].add('I-' + m + ': explicit prediction index recipe absent')
                    continue
                raw = row['kpi'][m]
                if m == 'SSR':
                    if row['kpi'].get('OG', 0) > row['kpi']['GOAL']:
                        blocked['pMST'].add(f'I-SSR: inconsistent GOAL/OG at {mid}/{side}; not generated as a negative score')
                        continue
                    raw = (row['kpi']['GOAL'] - row['kpi'].get('OG', 0)) / row['kpi']['SHOT'] if row['kpi']['SHOT'] else 0
                pmst['I-' + m] = rounded(Decimal(str(raw)) * 100 / Decimal(str(prediction_index_baselines[m])))
            supplied = (prediction_inputs or {}).get((mid, side), {})
            for prefix in ('PH', 'PA', 'PI'):
                if not legacy:
                    continue
                for m in METRICS:
                    field = prefix + '-' + m
                    if field in supplied:
                        pmst[field] = supplied[field]
                    else:
                        blocked['pMST'].add(field + ': forecast input/recipe absent')
            emit('pMST', pmst)
        avg = avg_known([h['jmx'], a['jmx']], 'mMST', 'HA-avg', mid)
        if avg is not None:
            mmst['HA-avg'] = rating_precision(avg, 2)
        emit('mMST', mmst)

    block = slot_definitions.get('seasonSlotMappings', {}).get(season, {})
    slots = block.get('cavgSlotToCodeTeamCodeMapping', {})
    cumulative_points = Counter()
    cumulative_goals = Counter()
    cumulative_against = Counter()
    previous_ratings = {}
    round_means = []
    previous_mean = None
    previous_round_no = None
    for round_no, round_rows in sorted(rows_by_round.items()):
        scope = f'{season}/R{round_no}'
        history_available = all(previous in rows_by_round for previous in range(1, round_no + 1))
        if not history_available:
            blocked['rank'].add(f'R{round_no}: earlier round context missing; cumulative points/rank omitted')
            blocked['rAVG'].add(f'R{round_no}: earlier round context missing; S_AVG omitted')
        by_number = {r['team_number']: r for r in round_rows}
        if len(by_number) != len(round_rows):
            result['errors'].append(f'Duplicate team in round {round_no}; explicit double-fixture rule required')
        complete = len(round_rows) == 20
        if not complete:
            blocked['cAVG'].add(f'R{round_no}: incomplete round ({len(round_rows)}/20 teams)')
            blocked['rank'].add(f'R{round_no}: incomplete round')
        cavg = {'PlSeason': season, 'Round': round_no}
        if not slots:
            blocked['cAVG'].add('Season slot mapping missing')
        for slot, slot_info in slots.items():
            row = by_number.get(slot_info['codeTeamCode'])
            if not row:
                blocked['cAVG'].add(f'C{slot}: team missing in R{round_no}')
                continue
            cavg.update({f'C{slot}-WDL': row['point'], f'C{slot}-S': row['goals'], f'C{slot}-C': row['against']})
            if row['jmx'] is not None:
                cavg[f'C{slot}-AVG'] = rating_precision(row['jmx'])
            else:
                blocked['cAVG'].add(f'C{slot}-AVG: missing stored JMX')
        cavg.update({'C-W': sum(r['point'] == 3 for r in round_rows),
                     'C-D': sum(r['point'] == 1 for r in round_rows), 'C-L': sum(r['point'] == 0 for r in round_rows)})
        if complete:
            cavg['RS-AVG'] = rounded(mean(r['goals'] for r in round_rows))
            cavg['RC-AVG'] = rounded(mean(r['against'] for r in round_rows))
        round_avg = avg_known([r['jmx'] for r in round_rows] if complete else [], 'cAVG', 'R-AVG', scope)
        if round_avg is not None:
            cavg['R-AVG'] = rating_precision(round_avg)
        emit('cAVG', cavg)
        ravg = {'PlSeason': season, 'Round': round_no}
        fixture_means = []
        for pair in grouped.values():
            if set(pair) != {'H', 'A'} or pair['H']['round'] != round_no:
                continue
            n = pair['H']['fixture_no']
            for side, row in pair.items():
                ravg[f'M{side}{n}_NM'] = row['idx']
                ravg[f'M{side}{n}-WDL'] = row['wdl']
                if row['jmx'] is not None:
                    ravg[f'M{side}{n}-IDX'] = rating_precision(row['jmx'])
                else:
                    blocked['rAVG'].add(f'M{side}{n}-IDX: missing stored JMX')
            avg = avg_known([pair['H']['jmx'], pair['A']['jmx']], 'rAVG', f'M{n}-AVG', scope)
            if avg is not None:
                ravg[f'M{n}-AVG'] = rating_precision(avg)
                fixture_means.append(avg)
        if round_avg is not None:
            ravg['M-AVG'] = rating_precision(round_avg)
        round_means.append(round_avg)
        if history_available and all(v is not None for v in round_means):
            ravg['S_AVG'] = rating_precision(precise_mean(round_means))
        else:
            blocked['rAVG'].add('S_AVG: missing prior round ratings')
        emit('rAVG', ravg)
        ranks = {'PlSeason': season, 'Round': round_no}
        for row in round_rows:
            cumulative_points[row['team']] += row['point']
            cumulative_goals[row['team']] += row['goals']
            cumulative_against[row['team']] += row['against']
        points = [cumulative_points[r['team']] for r in round_rows]
        ratings = [r['jmx'] for r in round_rows]
        # Current chart policy reads the truncated rAVG rating, caps it at 99.9,
        # and breaks ties using rounded growth over the previous played round.
        wrk_map = {r['team']: n for n, r in enumerate(sorted(round_rows, key=lambda r: (
            -cumulative_points[r['team']],
            -(cumulative_goals[r['team']] - cumulative_against[r['team']]),
            -cumulative_goals[r['team']], r['idx'])), 1)}
        current_ratings = {r['team']: min(rating_precision(r['jmx']), 99.9)
                           for r in round_rows if r['jmx'] is not None}
        rating_history_complete = all(r['team'] not in previous_ratings or
                                      previous_ratings[r['team']] is not None for r in round_rows)
        growth = {}
        for team, value in current_ratings.items():
            previous = previous_ratings.get(team)
            growth[team] = round((value - previous) / previous * 100, 1) if previous else 0.0
        rrk_map = {r['team']: n for n, r in enumerate(sorted(
            [r for r in round_rows if r['team'] in current_ratings], key=lambda r: (
                -int(round(current_ratings[r['team']] * 1000)),
                -int(round(growth[r['team']] * 1000)), r['idx'])), 1)}
        for row in round_rows:
            number = row['rank_slot']
            if number is None:
                continue
            total = cumulative_points[row['team']]
            wrk = rank_competition(total, points)
            ranks[f'WDL{number}'] = row['point']
            if history_available:
                ranks[f'WTT{number}'] = total
            if complete and history_available:
                ranks[f'WRK{number}'] = wrk if legacy else wrk_map[row['team']]
                if legacy:
                    ranks[f'WGR{number}'] = wrk // 4 + 1
            if row['jmx'] is not None:
                ranks[f'RAV{number}'] = rating_precision(row['jmx'])
                if complete and all(v is not None for v in ratings) and (legacy or (history_available and rating_history_complete)):
                    ranks[f'RRK{number}'] = rank_competition(row['jmx'], ratings) if legacy else rrk_map[row['team']]
                elif not legacy:
                    blocked['rank'].add(f'RRK{number}: complete rating/previous round context required')
            else:
                blocked['rank'].add(f'RAV{number}/RRK{number}: missing stored JMX')
        if round_avg is not None:
            ranks.update({'R_Avg': rating_precision(round_avg), 'Max': rating_precision(max(ratings)), 'Min': rating_precision(min(ratings))})
            if legacy:
                ranks.update(Season=rating_precision(round_avg / round_no), S_Avg=rating_precision(round_avg / round_no ** 2))
            if previous_mean is not None and (legacy or previous_round_no == round_no - 1):
                ranks['R_Gap'] = rating_precision(round_avg - previous_mean, 2)
                if legacy:
                    ranks['R_Rate'] = rounded((round_avg - previous_mean) / round_avg, 3) if round_avg else 0
            elif round_no == 1:
                ranks['R_Gap'] = 0
                if legacy:
                    ranks['R_Rate'] = 0
            elif not legacy:
                blocked['rank'].add(f'R{round_no} R_Gap: previous round mean absent')
        # Missing intervening ratings must not silently select an older rating as
        # the previous played round. A missing previous value blocks RRK above.
        previous_ratings.update({r['team']: current_ratings.get(r['team']) for r in round_rows})
        previous_mean = round_avg
        previous_round_no = round_no
        emit('rank', ranks)
    savg = {'SEASON': season[2:4] + '/' + season[7:9]}
    for dest, source in KPI_ALIASES.items():
        dest = 'TAP' if dest == 'gi_tmp' else dest
        digits = 3 if dest in ('ASR', 'SSR') else 2 if dest == 'GOAL' else 1
        savg[dest] = rounded(mean(r['kpi'][source] for r in all_rows), digits)
    rating_avg = avg_known([r['jmx'] for r in all_rows], 'sAVG', 'AI Rating', season)
    if rating_avg is not None:
        savg['AI Rating'] = rating_precision(rating_avg)
        if not legacy:
            savg['AVG'] = savg['AI Rating']
    if legacy:
        blocked['sAVG'].add('AVG: historical multi-season/override definition is diagnostic only')
    emit('sAVG', savg)
    result['blocked'] = [{'collection': c, 'fields_or_scopes': sorted(fields)} for c, fields in sorted(blocked.items())]
    result['summary'] = {'season': season, 'final_recordings': len(all_rows),
                         'documents': len(result['documents']),
                         'collections': dict(Counter(d['collection'] for d in result['documents'])),
                         'errors': len(result['errors']), 'blocked_groups': len(blocked)}
    result['complete'] = not result['errors'] and not result['blocked']
    return result


# ==========================================================================
# Shared source validation
# ==========================================================================
AGGREGATES = ('mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank')


def bind_existing_documents(generated: dict, existing: dict) -> None:
    """Keep legacy random IDs/UIDs without enabling a writer."""
    for doc in generated['documents']:
        col = doc['collection']
        key = aggregate_key(col, doc['data'])
        rows = [r for r in existing.get(col, []) if aggregate_key(col, r['data']) == key]
        if len(rows) > 1:
            generated['errors'].append(f'{col}: ambiguous existing identity {key}')
            generated['complete'] = False
            continue
        doc['action'] = 'replace-existing' if rows else 'create-candidate'
        if rows:
            doc['document_id'] = rows[0]['id']
            if rows[0]['data'].get('uid'):
                doc['data']['uid'] = rows[0]['data']['uid']
        doc['data'] = delivery_payload(col, doc['data'], rows[0]['data'] if rows else None)
    generated['summary']['errors'] = len(generated['errors'])


def validate_sql_facts(generated: dict, items: list[dict], codes: list[dict], slots: dict) -> dict:
    """Validate output against SQL facts, not against existing Firestore values.

    Ratings are explicitly skipped: SQL gi_score is not current JPD-RATING JMX.
    The oracle uses source team/fixture identities and source-level sums/means.
    """

    final = [i for i in items if i['recording'].get('status') == 'final']
    tallies = defaultdict(Counter)
    failures = []

    def check(col, key, field, actual, expected):
        tally = tallies[col]
        tally['checked'] += 1
        equal = actual == expected
        if type(actual) in (int, float) and type(expected) in (int, float):
            equal = abs(actual - expected) < 1e-6
        tally['passed' if equal else 'failed'] += 1
        if not equal and len(failures) < 25:
            failures.append({'collection': col, 'key': key, 'field': field,
                             'generated': actual, 'expected_sql': expected})

    if not final:
        return {'passed': False, 'collections': {}, 'failures': ['No final SQL input'], 'checked': 0}
    season = season_label(final[0]['match']['seasonId'])
    index = defaultdict(list)
    for doc in generated['documents']:
        index[(doc['collection'], aggregate_key(doc['collection'], doc['data']))].append(doc['data'])

    def expect(col, data):
        key = aggregate_key(col, data)
        rows = index.get((col, key), [])
        check(col, key, '__unique_document__', len(rows), 1)
        actual = rows[0] if len(rows) == 1 else {}
        for field, value in data.items():
            check(col, key, field, actual.get(field), value)

    # Contract is listed independently of the generator's KPI alias constants.
    mapping = {'gi_tmp': 'TAP', 'DAP': 'DAP', 'TTP': 'TTP', 'DTP': 'DTP', 'BAP': 'BAP',
               'B': 'DTB', 'M': 'DTM', 'A': 'DTA', 'S': 'DTS', 'SHOT': 'SHOT',
               'ASR': 'ASR', 'SSR': 'SSR', 'GOAL': 'GOAL'}
    rounds, fixtures = defaultdict(list), defaultdict(dict)
    references = {}
    positions = {}
    for item in final:
        match, rec, side = item['match'], item['recording'], item['side']
        team = rec['teamId']
        references[team] = resolve_team_code(codes, season, team)
        candidates = {r['data'].get('POSITION') for r in codes
                      if r['data'].get('PlSeason') == season and
                      (r['data'].get('TCODE') or r['data'].get('Code')) == team}
        position = next(iter(candidates)) if len(candidates) == 1 else None
        positions[team] = (LEGACY_RANK_SLOT_OVERRIDES.get(season, {}).get(team, int(position))
                           if position is not None else None)
        if positions[team] is None:
            check('rank', team, '__season_rank_position_available__', False, True)
        rounds[match['round']].append(item)
        fixtures[item['match_id']][side] = item
        expected = {'PlSeason': season, 'gm_round': match['round'], 't_code': team,
                    'gm_date': dotted_date(match['date'])}
        for dest, source in mapping.items():
            expected[dest] = rounded(rec['kpi'][source], 3) if dest in ('ASR', 'SSR') else rec['kpi'][source]
        for field in ('DAP', 'BAP', 'DTP', 'SHOT', 'SSR', 'GOAL'):
            expected[field + '2'] = expected[field]
        expected['Goal'] = match['score']['away' if side == 'H' else 'home']
        expect('pMST', expected)

    for mid, pair in fixtures.items():
        if set(pair) != {'H', 'A'}:
            check('mMST', mid, '__both_sides__', sorted(pair), ['A', 'H'])
            continue
        match = pair['H']['match']
        expected = {'PlSeason': season, 'Round': match['round'],
                    'H-Code': match['homeTeamId'], 'A-Code': match['awayTeamId'],
                    'M_date': dotted_date(match['date'])}
        for side, item in pair.items():
            own, other = ('home', 'away') if side == 'H' else ('away', 'home')
            score, against = match['score'][own], match['score'][other]
            expected[side + '-DWL'] = 'W' if score > against else 'D' if score == against else 'L'
            for dest, source in mapping.items():
                expected[side + '-' + ('TAP' if dest == 'gi_tmp' else dest)] = item['recording']['kpi'][source]
        expect('mMST', expected)

    points, gf, ga = Counter(), Counter(), Counter()
    season_slots = slots.get('seasonSlotMappings', {}).get(season, {}).get('cavgSlotToCodeTeamCodeMapping', {})
    check('cAVG', season, '__season_slots__', len(season_slots), 20)
    for number, values in sorted(rounds.items()):
        cavg = {'PlSeason': season, 'Round': number}
        wins = draws = losses = 0
        ranks = {'PlSeason': season, 'Round': number}
        for item in values:
            match, rec, side = item['match'], item['recording'], item['side']
            team = rec['teamId']
            own, other = ('home', 'away') if side == 'H' else ('away', 'home')
            score, against = match['score'][own], match['score'][other]
            point = 3 if score > against else 1 if score == against else 0
            wins += point == 3
            draws += point == 1
            losses += point == 0
            points[team] += point
            gf[team] += score
            ga[team] += against
            code = references[team]
            position = positions[team]
            if position is not None:
                ranks[f'WDL{position}'] = point
                ranks[f'WTT{position}'] = points[team]
            for slot, info in season_slots.items():
                if info['codeTeamCode'] == code['team_code_number']:
                    cavg.update({f'C{slot}-WDL': point, f'C{slot}-S': score, f'C{slot}-C': against})
        cavg.update({'C-W': wins, 'C-D': draws, 'C-L': losses})
        if len(values) == 20:
            scores = [i['match']['score']['home' if i['side'] == 'H' else 'away'] for i in values]
            conceded = [i['match']['score']['away' if i['side'] == 'H' else 'home'] for i in values]
            cavg.update({'RS-AVG': rounded(mean(scores)), 'RC-AVG': rounded(mean(conceded))})
            teams = sorted((i['recording']['teamId'] for i in values),
                           key=lambda team: (-points[team], -(gf[team]-ga[team]), -gf[team], references[team]['IDX_CODE']))
            if generated.get('calculation_mode') == 'current':
                for rank_no, team in enumerate(teams, 1):
                    position = positions[team]
                    if position is not None:
                        ranks[f'WRK{position}'] = rank_no
        expect('cAVG', cavg)
        expect('rank', ranks)
        ravg = {'PlSeason': season, 'Round': number}
        mids = sorted(mid for mid, pair in fixtures.items() if next(iter(pair.values()))['match']['round'] == number)
        for n, mid in enumerate(mids, 1):
            for side, item in fixtures[mid].items():
                match, team = item['match'], item['recording']['teamId']
                own, other = ('home', 'away') if side == 'H' else ('away', 'home')
                score, against = match['score'][own], match['score'][other]
                ravg[f'M{side}{n}_NM'] = references[team]['IDX_CODE']
                ravg[f'M{side}{n}-WDL'] = 'W' if score > against else 'D' if score == against else 'L'
        expect('rAVG', ravg)
    savg = {'SEASON': season[2:4] + '/' + season[7:9]}
    for dest, source in mapping.items():
        dest = 'TAP' if dest == 'gi_tmp' else dest
        digits = 3 if dest in ('ASR', 'SSR') else 2 if dest == 'GOAL' else 1
        savg[dest] = rounded(mean(i['recording']['kpi'][source] for i in final), digits)
    expect('sAVG', savg)
    expected_counts = {'mMST': len(fixtures), 'pMST': len(final),
                       'cAVG': len(rounds), 'rAVG': len(rounds), 'rank': len(rounds), 'sAVG': 1}
    for col, count in expected_counts.items():
        check(col, season, '__document_count__', sum(d['collection'] == col for d in generated['documents']), count)
    checked = sum(t['checked'] for t in tallies.values())
    failed = sum(t['failed'] for t in tallies.values())
    return {'reference': 'provided SQL facts; existing Firestore values are not the oracle',
            'scope': 'match/KPI facts, six aggregate structures, official-score points/current standings',
            'passed': not failed and not generated['errors'], 'checked': checked, 'failed': failed,
            'collections': {c: dict(t) for c, t in tallies.items()}, 'failures': failures,
            'ratings': {'status': 'skipped', 'reason': 'SQL gi_score is historical; current source is DID saved JPD-RATING'},
            'excluded': generated.get('excluded', {})}


def encode(value):
    # Keep invalid legacy values visible while producing strict JSON. A tagged
    # value cannot accidentally pass any numeric source validation.
    if isinstance(value, float) and not math.isfinite(value):
        return {'__nonfinite_float__': str(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


def match_target_scope(existing, match):
    season = season_label(match['seasonId'])
    short = int(season[2:4] + season[7:9])
    result = {}
    for col, rows in existing.items():
        if col in AGGREGATES:
            continue
        if col == 'dMST':
            rows = [r for r in rows if r['data'].get('gm_round') == match['round']]
        elif col == 'Player':
            rows = [r for r in rows if r['data'].get('PlSeason') == season]
        elif col == 'playerDB':
            rows = [r for r in rows if r['data'].get('P-6') == short]
        result[col] = rows
    return result


def validate_aggregate_links(generated, items, codes, slots):
    """Independent source-fact checks plus the saved-rating links SQL cannot check."""

    try:
        facts = validate_sql_facts(generated, items, codes, slots)
    except (StopIteration, ValueError, KeyError, TypeError) as exc:
        facts = {'passed': False, 'checked': 0, 'status': 'blocked',
                 'failures': [f'Source fact validation requires valid fixture/code/season POSITION context: {type(exc).__name__}: {exc}']}
    # The existing fact checker is source-agnostic despite its historical name.
    facts['reference'] = 'DID fixture and stored KPI facts; no SQL or target-value oracle'
    facts['ratings'] = {'status': 'checked separately against DID saved JMX'}
    for failure in facts['failures']:
        if isinstance(failure, dict) and 'expected_sql' in failure:
            failure['expected_did'] = failure.pop('expected_sql')
    final = [i for i in items if i['recording'].get('status') == 'final']
    if not final:
        return {'passed': False, 'source_facts': facts, 'rating_checks': []}
    season = season_label(final[0]['match']['seasonId'])
    docs = {(d['collection'], aggregate_key(d['collection'], d['data'])): d['data'] for d in generated['documents']}
    rounds = defaultdict(list)
    for item in final:
        rounds[item['match']['round']].append(item)
    checks = []

    def check(col, key, field, expected):
        actual = docs.get((col, key), {}).get(field)
        passed = type(actual) in (int, float) and abs(actual - expected) < 1e-6
        checks.append({'collection': col, 'key': key, 'field': field, 'passed': passed,
                       'actual': actual, 'expected_did': expected})

    ratings = []
    cumulative = []
    previous = {}
    for number, values in sorted(rounds.items()):
        mids = sorted({i['match_id'] for i in values})
        current = []
        for item in values:
            jmx = (item['recording'].get('teamRating') or {}).get('jmx')
            if type(jmx) not in (int, float) or not math.isfinite(jmx):
                checks.append({'passed': False, 'match_id': item['match_id'], 'side': item['side'], 'field': 'saved-JMX'})
                continue
            current.append(jmx)
            ratings.append(jmx)
            n = mids.index(item['match_id']) + 1
            team = item['recording']['teamId']
            match = item['match']
            check('mMST', (season, number, match['homeTeamId'], match['awayTeamId']), item['side'] + '-FX', truncate_rating(jmx, 2))
            check('pMST', (season, number, team), 'gi_score', rating_two_decimals(jmx))
            check('rAVG', (season, number), f'M{item["side"]}{n}-IDX', truncate_rating(jmx))
            try:
                code = resolve_team_code(codes, season, team)
            except ValueError as exc:
                checks.append({'passed': False, 'match_id': item['match_id'], 'field': 'team-code', 'error': str(exc)})
                continue
            for slot, info in slots.get('seasonSlotMappings', {}).get(season, {}).get('cavgSlotToCodeTeamCodeMapping', {}).items():
                if info['codeTeamCode'] == code['team_code_number']:
                    check('cAVG', (season, number), f'C{slot}-AVG', truncate_rating(jmx))
        rank_values = []
        for item in values:
            team = item['recording']['teamId']
            jmx = (item['recording'].get('teamRating') or {}).get('jmx')
            if type(jmx) not in (int, float) or not math.isfinite(jmx):
                continue
            positions = {r['data'].get('POSITION') for r in codes if r['data'].get('PlSeason') == season
                         and (r['data'].get('TCODE') or r['data'].get('Code')) == team}
            if len(positions) != 1 or None in positions:
                checks.append({'passed': False, 'collection': 'rank', 'team': team, 'round': number,
                               'field': 'season-POSITION'})
                continue
            position = LEGACY_RANK_SLOT_OVERRIDES.get(season, {}).get(team, int(next(iter(positions))))
            check('rank', (season, number), f'RAV{position}', truncate_rating(jmx))
            current_rating = min(truncate_rating(jmx), 99.9)
            growth = round((current_rating - previous[team]) / previous[team] * 100, 1) if previous.get(team) else 0.0
            idx = resolve_team_code(codes, season, team)['IDX_CODE']
            rank_values.append((team, position, current_rating, growth, idx))
        for rank_no, (_, position, _, _, _) in enumerate(sorted(rank_values, key=lambda r: (
                -int(round(r[2] * 1000)), -int(round(r[3] * 1000)), r[4])), 1):
            check('rank', (season, number), f'RRK{position}', rank_no)
        previous.update({team: rating for team, _, rating, _, _ in rank_values})
        pairs = defaultdict(list)
        for item in values:
            value = (item['recording'].get('teamRating') or {}).get('jmx')
            if type(value) in (int, float) and math.isfinite(value):
                pairs[item['match_id']].append(value)
        for mid, pair in pairs.items():
            if len(pair) == 2:
                match = next(i['match'] for i in values if i['match_id'] == mid)
                check('mMST', (season, number, match['homeTeamId'], match['awayTeamId']), 'HA-avg', truncate_rating(precise_mean(pair), 2))
        if len(current) == len(values):
            cumulative.append(precise_mean(current))
            check('cAVG', (season, number), 'R-AVG', truncate_rating(precise_mean(current)))
            check('rAVG', (season, number), 'M-AVG', truncate_rating(precise_mean(current)))
            check('rAVG', (season, number), 'S_AVG', truncate_rating(precise_mean(cumulative)))
    if len(ratings) == len(final):
        for field in ('AI Rating', 'AVG'):
            check('sAVG', (season[2:4] + '/' + season[7:9],), field, truncate_rating(precise_mean(ratings)))
    return {'passed': facts['passed'] and all(c['passed'] for c in checks), 'source_facts': facts,
            'rating_checks': checks, 'rating_checks_count': len(checks),
            'rating_checks_failed': sum(not c['passed'] for c in checks)}


# ==========================================================================
# Source capture and delivery planning
# ==========================================================================


MATCH_TARGETS = ('TACTIC', 'multiSheet_ALL', 'multiSheet_BAP', 'multiSheet_DAP', 'multiSheet_DTP',
                 'multiSheet_GOAL', 'multiSheet_JMX', 'multiSheet_SHOT', 'multiSheet_GOAL-시간',
                 'playerGoal', 'vibePlayer')

IDENTITY_COLLECTIONS = frozenset(('Player', 'playerDB', 'Manager', 'code'))
MASTER_COLLECTIONS = IDENTITY_COLLECTIONS | frozenset(AGGREGATES)
MATCH_COLLECTIONS = frozenset(('dMST', *MATCH_TARGETS))


def delivery_mode(scope):
    mode = scope.get('mode', 'matches')
    # Keep old stored job scopes readable; only the three new modes have HTTP routes.
    if mode not in ('matches', 'masters', 'aggregates', 'all', 'latest'):
        fail('Unknown delivery mode')
    if (mode in ('masters', 'latest') or (mode == 'all' and not scope.get('unified'))) and any(scope.get(k) is not None for k in ('roundFrom', 'roundTo', 'matchId')):
        fail('Masters accept a season only; round and match filters are not allowed')
    return mode


def fail(message, code='aifootballx_validation_failed'):
    raise BackendError(message, status_code=409, code=code)


class SourceCapture:
    def __init__(self, did, target):
        self.did, self.target = did, target
        if did.project != 'jpd-did' or target.project != 'aifootballx':
            fail('Unexpected source or destination project')
        self.documents = {}
        self.queries = []

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def report_progress(self, percent):
        callback = getattr(self, '_progress_callback', None)
        if callback:
            callback(max(0, min(100, percent)))

    @staticmethod
    def version(snap):
        if not snap.exists:
            return None
        value = snap.update_time
        # Collection-group SDK results can carry protobuf Timestamp whereas
        # document reads carry DatetimeWithNanoseconds. Keep one exact format.
        if hasattr(value, 'ToJsonString'):
            return value.ToJsonString()
        if hasattr(value, 'timestamp_pb'):
            return value.timestamp_pb().ToJsonString()
        if isinstance(value, datetime):
            from google.protobuf.timestamp_pb2 import Timestamp
            stamp = Timestamp()
            stamp.FromDatetime(value)
            return stamp.ToJsonString()
        return value.isoformat()

    def get(self, path):
        snap = self.did.document(path).get(timeout=30, retry=None)
        self.documents[path] = self.version(snap)
        return encode(snap.to_dict()) if snap.exists else None

    @staticmethod
    def query(client, path, filters):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = client.collection(path)
        for field, op, value in filters:
            q = q.where(filter=FieldFilter(field, op, value))
        return q

    def rows(self, client, path, filters=(), *, source=False, bound=20000, season_range=None):
        snaps = list(self.query(client, path, filters).limit(bound + 1).stream(timeout=60, retry=None))
        if len(snaps) > bound:
            fail(f'Read limit exceeded: {path}')
        if source:
            guard = {'path':path,'filters':list(filters),'bound':bound,
                     **({'season_range':season_range} if season_range else {})}
            guard['versions'] = self.guard_versions(snaps,guard)
            self.queries.append(guard)
            if season_range: snaps=[s for s in snaps if s.id in guard['versions']]
        return [{'id': s.id, 'data': encode(s.to_dict())} for s in snaps]

    def get_many(self, paths):
        paths = sorted(set(paths))
        result = {}
        for start in range(0,len(paths),100):
            refs = [self.did.document(p) for p in paths[start:start+100]]
            for snap in self.did.get_all(refs,timeout=60,retry=None):
                self.documents[snap.reference.path] = self.version(snap)
                if snap.exists: result[snap.reference.path] = encode(snap.to_dict())
        return result

    @staticmethod
    def guard_versions(snaps, guard):
        result = {}
        for snap in snaps:
            if guard.get('season_range'):
                value = snap.to_dict()
                start,end = guard['season_range']
                if (re.sub(r'\D','',value.get('from') or '')>end
                        or re.sub(r'\D','',value.get('to') or '99999999')<start):
                    continue
            key = snap.reference.path if guard.get('group') else snap.id
            result[key] = None if guard.get('membership_only') else SourceCapture.version(snap)
        return result

    def team_contracts(self, team_ids, start, end):
        from google.cloud.firestore_v1.base_query import FieldFilter
        result = defaultdict(list)
        for first in range(0,len(team_ids),30):
            filters = [('teamId','in',team_ids[first:first+30])]
            query = self.did.collection_group('contracts').where(filter=FieldFilter(*filters[0]))
            snaps = list(query.limit(50001).stream(timeout=60,retry=None))
            if len(snaps)>50000: fail('Team contract read limit exceeded')
            guard = {'group':'contracts','filters':filters,'bound':50000,'season_range':[start,end],
                     'membership_only':True}
            guard['versions'] = self.guard_versions(snaps,guard)
            self.queries.append(guard)
            for snap in snaps:
                parts = snap.reference.path.split('/')
                if snap.reference.path in guard['versions'] and len(parts)==4 and parts[0]=='players':
                    result[parts[1]].append(dict(encode(snap.to_dict()),_id=snap.id))
        return result

    def historical_league_players(self, league_id):
        """Read historical registration membership, separately from season squads."""
        from google.cloud.firestore_v1.base_query import FieldFilter
        from google.api_core.exceptions import FailedPrecondition
        filters = [('leagueId', '==', league_id)]
        membership_only = True
        try:
            query = self.did.collection_group('contracts').where(filter=FieldFilter(*filters[0]))
            snaps = list(query.limit(50001).stream(timeout=60, retry=None))
        except FailedPrecondition as exc:
            if 'index' not in str(exc).lower():
                raise
            # Existing installations may not have the leagueId group index yet.
            # Read only league membership, with an explicit bound. Guard every
            # document version so league changes cannot evade the unfiltered query.
            query = self.did.collection_group('contracts').select(['leagueId'])
            snaps = list(query.limit(50001).stream(timeout=60, retry=None))
            filters, membership_only = [], False
        if len(snaps) > 50000:
            fail('Historical contract read limit exceeded')
        guard = {'group': 'contracts', 'filters': filters, 'bound': 50000, 'membership_only': membership_only}
        guard['versions'] = self.guard_versions(snaps, guard)
        self.queries.append(guard)
        return {s.reference.path.split('/')[1] for s in snaps
                if len(s.reference.path.split('/')) == 4 and s.reference.path.startswith('players/')
                and (membership_only or s.to_dict().get('leagueId') == league_id)}

    def historical_team_indexes(self, team_ids):
        """Read only team/index pairs across seasons, without expanding write scope."""
        team_ids = sorted(set(team_ids))
        values = {team_id: set() for team_id in team_ids}
        for start in range(0, len(team_ids), 30):
            rows = self.rows(self.target, 'Player', [('t_code', 'in', team_ids[start:start + 30])], bound=20000)
            for row in rows:
                team, index = row['data'].get('t_code'), row['data'].get('IDX_CODE')
                if team in values and index:
                    values[team].add(index)
        return {team: sorted(indexes) for team, indexes in values.items()}

    def assert_fresh(self, guards):
        self.report_progress(0)
        document_batches = (len(guards['documents']) + 99) // 100
        work_total = max(1, document_batches + len(guards['queries']))
        work_done = 0
        slot_guard = guards.get('slot_file')
        if slot_guard and (not Path(slot_guard['path']).is_file() or
                           sha256(Path(slot_guard['path']).read_bytes()).hexdigest() != slot_guard['sha256']):
            fail('Season slot configuration changed; create a new preview', 'aifootballx_source_changed')
        for start in range(0, len(guards['documents']), 100):
            paths = list(guards['documents'])[start:start + 100]
            for snap in self.did.get_all([self.did.document(p) for p in paths], timeout=60, retry=None):
                if self.version(snap) != guards['documents'][snap.reference.path]:
                    fail('DID source changed; create a new preview', 'aifootballx_source_changed')
            work_done += 1
            self.report_progress(95 * work_done / work_total)
        for guard in guards['queries']:
            query = self.did.collection_group(guard['group']) if guard.get('group') else self.query(self.did, guard['path'], guard['filters'])
            if guard.get('group'):
                from google.cloud.firestore_v1.base_query import FieldFilter
                for field,op,value in guard.get('filters',[]): query=query.where(filter=FieldFilter(field,op,value))
            # Version/membership checks need no full RAW/player payloads.
            if hasattr(query,'select'):
                query=query.select(['from','to'] if guard.get('season_range') else [])
            snaps = list(query.limit(guard['bound'] + 1).stream(timeout=60, retry=None))
            if self.guard_versions(snaps,guard) != guard['versions']:
                fail('DID source collection changed; create a new preview', 'aifootballx_source_changed')
            work_done += 1
            self.report_progress(95 * work_done / work_total)
        if 'reference_code' in guards:
            code = self.rows(self.target, 'code', bound=500)
            actual = code_reference(code,guards.get('code_scope'))
            expected = guards['reference_code']
            additions = guards.get('new_code', {})
            if any(actual.get(k) != v for k, v in expected.items()) or any(
                    k not in expected and (k not in additions or v != additions[k]) for k, v in actual.items()):
                fail('Destination team code configuration changed; create a new preview', 'aifootballx_source_changed')
        if 'registration_references' in guards:
            reference = guards['registration_references']
            rows = self.rows(self.target, 'Player', [('PlSeason', '==', reference['season'])], bound=20000)
            if registration_reference_values(rows, reference['season']) != reference['values']:
                fail('Registration identity references changed; create a new preview', 'aifootballx_source_changed')
            if 'player_db_values' in reference:
                from google.cloud.firestore_v1.field_path import FieldPath
                season = reference['season']
                rows = self.rows(self.target, 'playerDB', [(FieldPath('P-6').to_api_repr(), '==',
                    int(season[2:4]+season[7:9]))], bound=20000)
                if player_db_reference_values(rows, season) != reference['player_db_values']:
                    fail('playerDB code references changed; create a new preview', 'aifootballx_source_changed')
        if 'historical_team_indexes' in guards:
            reference = guards['historical_team_indexes']
            if self.historical_team_indexes(reference['team_ids']) != reference['values']:
                fail('Historical team index references changed; create a new preview', 'aifootballx_source_changed')
        groups = guards.get('delivery_groups', [])
        if groups:
            callback = getattr(self, '_progress_callback', None)
            try:
                for number, group in enumerate(groups):
                    if callback:
                        self.set_progress_callback(lambda value, index=number:
                            callback(95 + 5 * (index + value / 100) / len(groups)))
                    self.assert_fresh({'documents': {}, 'queries': [], **group})
            finally:
                self.set_progress_callback(callback)
        self.report_progress(100)

    def split_scopes(self, scope):
        if delivery_mode(scope) == 'all' and scope.get('unified'):
            masters = {**scope, 'mode': 'masters', 'roundFrom': None, 'roundTo': None, 'matchId': None}
            matches = self.split_scopes({**scope, 'mode': 'matches'})
            return [masters, *matches, {**scope, 'mode': 'aggregates'}]
        if delivery_mode(scope) != 'matches':
            return [scope]
        if scope.get('matchId'):
            return [scope]
        matches = self.rows(self.did, 'matches', [('seasonId', '==', scope['seasonId']), ('leagueId', '==', 'EPL')], bound=2000)
        rounds = sorted({r['data']['round'] for r in matches
                         if (scope.get('roundFrom') is None or r['data']['round'] >= scope['roundFrom'])
                         and (scope.get('roundTo') is None or r['data']['round'] <= scope['roundTo'])})
        if not rounds:
            fail('No DID matches in this scope')
        return [{**scope, 'roundFrom': n, 'roundTo': n} for n in rounds]

    def capture(self, scope, slot_path):
        if delivery_mode(scope) == 'aggregates':
            return self.capture_aggregates(scope, slot_path)
        if delivery_mode(scope) != 'matches':
            return self.capture_masters(scope, slot_path)
        from google.cloud.firestore_v1.field_path import FieldPath
        self.report_progress(0)
        season_id, season = scope['seasonId'], season_label(scope['seasonId'])
        all_matches = self.rows(self.did, 'matches', [('seasonId', '==', season_id), ('leagueId', '==', 'EPL')], source=True, bound=2000)
        selected = [r for r in all_matches if (not scope.get('matchId') or r['id'] == scope['matchId'])
                    and (scope.get('roundFrom') is None or r['data']['round'] >= scope['roundFrom'])
                    and (scope.get('roundTo') is None or r['data']['round'] <= scope['roundTo'])]
        if not selected:
            fail('No DID matches in this scope')
        self.report_progress(5)
        evidence = {'did_matches': all_matches, 'match_scan_truncated': False, 'source_recordings': [],
                    'captured_at': datetime.now(timezone.utc).isoformat(), 'player_profiles': {}, 'player_contracts': {},
                    'teams': {}, 'stadiums': {}, 'target_existing': {}, 'projects': {},
                    'leagues': {r['id']: r['data'] for r in self.rows(self.did, 'leagues', source=True, bound=100)},
                    'ai_samples': {'code': self.rows(self.target, 'code', bound=500)}}
        target = evidence['target_existing']
        for col in ('dMST',):
            field, value = ('SEASON', season[2:4] + '/' + season[7:9]) if col == 'sAVG' else ('PlSeason', season)
            target[col] = self.rows(self.target, col, [(field, '==', value)], bound=5000)
        # Match-only delivery resolves existing/global team codes directly.
        # Season code creation and aggregate slots belong to ALL/LATEST.
        slots = {}
        setup = {'documents': [], 'code_rows': [], 'slots': slots, 'new_season': False}
        # Match delivery never reads unrelated recordings or sends season masters.
        refs = [self.did.document(f'matches/{r["id"]}/recordings/{side}') for r in selected for side in ('H', 'A')]
        recording_by_path = {}
        for start in range(0, len(refs), 100):
            for snap in self.did.get_all(refs[start:start + 100], timeout=60, retry=None):
                self.documents[snap.reference.path] = self.version(snap)
                recording_by_path[snap.reference.path] = encode(snap.to_dict()) if snap.exists else {}
        aggregate_items = []
        for row in selected:
            match = row['data']
            for side in ('H', 'A'):
                rec = recording_by_path[f'matches/{row["id"]}/recordings/{side}']
                aggregate_items.append({'match_id': row['id'], 'match': match, 'side': side, 'recording': rec})
        player_ids = set()
        for match_number, row in enumerate(selected, 1):
            match, mid = row['data'], row['id']
            for side in ('H', 'A'):
                path = f'matches/{mid}/recordings/{side}'
                children = {col: self.rows(self.did, path + '/' + col, source=True) for col in ('records', 'paths', 'playerStats', 'cards')}
                player_ids.update(r['id'] for r in children['playerStats'])
                player_ids.update(r['data']['playerId'] for r in children['records'] if r['data'].get('playerId') not in (None, '', 'OWN'))
                evidence['source_recordings'].append({'match_id': mid, 'match': match, 'side': side,
                    'recording': recording_by_path[path], 'children': children})
            for col in MATCH_TARGETS:
                target.setdefault(col, []).extend(self.rows(self.target, col, [('gm_id', '==', mid)]))
            self.report_progress(10 + 45 * match_number / len(selected))
        evidence['player_team_links']={}
        for item in evidence['source_recordings']:
            player_ids.update(item['recording'].get('lineup',{}))
        evidence['registration_player_ids']=sorted(player_ids)
        for player_number, pid in enumerate(sorted(player_ids), 1):
            profile = self.get('players/' + pid)
            if profile:
                evidence['player_profiles'][pid] = profile
            evidence['player_contracts'][pid] = [dict(r['data'], _id=r['id']) for r in self.rows(self.did, f'players/{pid}/contracts', source=True, bound=500)]
            for col, field in [('Player', 'p_id'), ('playerDB', 'P-1')]:
                identity=player_identity(pid,profile)
                target.setdefault(col, []).extend(self.rows(self.target, col, [(FieldPath(field).to_api_repr(), '==', identity)], bound=200))
            self.report_progress(55 + 35 * player_number / len(player_ids))
        for row in all_matches:
            for tid in (row['data']['homeTeamId'], row['data']['awayTeamId']):
                if tid not in evidence['teams']:
                    evidence['teams'][tid] = self.get('teams/' + tid) or {}
        for row in selected:
            sid = row['data'].get('stadiumId')
            if sid and sid not in evidence['stadiums']:
                evidence['stadiums'][sid] = self.get('stadiums/' + sid) or {}
        for item in aggregate_items:
            team = evidence['teams'].get(item['recording'].get('teamId'), {})
            item['team_name'] = team.get('nameKr') or team.get('name')
        schemas = {}
        for col in target:
            fields = defaultdict(set)
            for snap in self.target.collection(col).limit(5).stream(timeout=30, retry=None):
                for key, value in encode(snap.to_dict()).items():
                    fields[key].add(type(value).__name__)
            if col == 'TACTIC' and fields.get('gr_act_code') == {'NoneType'}:
                query = self.query(self.target, col, [('gr_act_code', 'in', ['P','C','K','F','S','H','R'])])
                for snap in query.limit(5).stream(timeout=30, retry=None):
                    for key, value in encode(snap.to_dict()).items():
                        fields[key].add(type(value).__name__)
            schemas[col] = {'fields': {key: sorted(types) for key, types in fields.items()}}
        evidence['projects'] = {'aifootballx': schemas}
        self.report_progress(95)
        guards = {'documents': self.documents, 'queries': self.queries,
                  'code_scope':{'season':season,'teams':sorted({r['data'][k] for r in all_matches for k in ('homeTeamId','awayTeamId')})},
                  'reference_code': {},
                  'new_code': {d['document_id']: d['data'] for d in setup['documents']}}
        guards['reference_code'] = code_reference(evidence['ai_samples']['code'],guards['code_scope'])
        report = generate_delivery(evidence, selected, aggregate_items, slots, setup, match_only=True)
        if scope.get('latest'):
            retain_changed_documents(report, target)
        self.report_progress(100)
        return report, guards

    def capture_aggregates(self, scope, slot_path):
        """Capture team KPI/rating context without reading player or coach masters."""
        self.report_progress(0)
        season = season_label(scope['seasonId'])
        fixtures = self.rows(self.did, 'matches', [('seasonId', '==', scope['seasonId']),
                             ('leagueId', '==', 'EPL')], source=True, bound=2000)
        selected = [r for r in fixtures if (not scope.get('matchId') or r['id'] == scope['matchId'])
                    and (scope.get('roundFrom') is None or r['data']['round'] >= scope['roundFrom'])
                    and (scope.get('roundTo') is None or r['data']['round'] <= scope['roundTo'])]
        if not selected:
            fail('No DID matches in this scope')
        codes = self.rows(self.target, 'code', bound=500)
        target = {}
        for col in ('dMST', *AGGREGATES):
            field, value = ('SEASON', season[2:4] + '/' + season[7:9]) if col == 'sAVG' else ('PlSeason', season)
            target[col] = self.rows(self.target, col, [(field, '==', value)], bound=20000)
        # Earlier edits must propagate through already published cumulative rounds.
        existing_end = max((r['data'].get('gm_round' if col == 'pMST' else 'Round', 0) or 0
                            for col in AGGREGATES if col != 'sAVG' for r in target[col]), default=0)
        cutoff = max(existing_end, max(r['data']['round'] for r in selected))
        context = [r for r in fixtures if r['data']['round'] <= cutoff]
        target['dMST'] = [r for r in target['dMST'] if (r['data'].get('gm_round') or 0) <= cutoff]
        slots = {'seasonSlotMappings': {}}
        try:
            if slot_path:
                slots = json.loads(slot_path.read_text(encoding='utf-8'))
            if not isinstance(slots, dict) or not isinstance(slots.get('seasonSlotMappings', {}), dict):
                raise ValueError('Invalid season slot configuration')
            setup = plan_new_season(season, fixtures, codes, target, slots)
        except (BackendError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            reason = exc.message if isinstance(exc, BackendError) else f'Season slot configuration unavailable ({type(exc).__name__})'
            setup = {'documents': [], 'code_rows': [], 'slots': slots, 'new_season': False, 'errors': [reason]}
        teams = self.get_many('teams/' + team for team in sorted({r['data'][key]
                             for r in context for key in ('homeTeamId', 'awayTeamId')}))
        self.report_progress(20)
        refs = [self.did.document(f'matches/{r["id"]}/recordings/{side}') for r in context for side in ('H', 'A')]
        heads = {}
        for start in range(0, len(refs), 100):
            for snap in self.did.get_all(refs[start:start + 100], timeout=60, retry=None):
                self.documents[snap.reference.path] = self.version(snap)
                heads[snap.reference.path] = encode(snap.to_dict()) or {}
            self.report_progress(20 + 55 * min(start + 100, len(refs)) / len(refs))
        items = [{'match_id': r['id'], 'match': r['data'], 'side': side,
                  'recording': heads[f'matches/{r["id"]}/recordings/{side}'],
                  'team_name': (teams.get('teams/' + r['data']['homeTeamId' if side == 'H' else 'awayTeamId'], {}).get('nameKr'))}
                 for r in context for side in ('H', 'A')]
        evidence = {'did_matches': context, 'source_recordings': [], 'player_profiles': {},
                    'target_existing': target, 'ai_samples': {'code': codes + setup['code_rows']}}
        self.capture_aggregate_review_sources(evidence, items)
        self.report_progress(95)
        report = generate_masters(evidence, context, items, setup['slots'], setup, 'aggregates')
        report = scoped_aggregate_delivery(report, selected, target, latest=scope.get('latest', False))
        guards = {'documents': self.documents, 'queries': self.queries, 'code_scope': None,
                  'reference_code': code_reference(codes),
                  'new_code': {d['document_id']: d['data'] for d in setup['documents']}}
        if slot_path and not setup.get('errors'):
            guards['slot_file'] = {'path': str(slot_path.resolve()), 'sha256': sha256(slot_path.read_bytes()).hexdigest()}
        self.report_progress(100)
        return report, guards

    def capture_masters(self, scope, slot_path):
        """Read masters independently of match RAW, playerStats and rating jobs."""
        from google.cloud.firestore_v1.field_path import FieldPath
        self.report_progress(0)
        season = season_label(scope['seasonId'])
        fixtures = self.rows(self.did, 'matches', [('seasonId','==',scope['seasonId']),('leagueId','==','EPL')], source=True, bound=2000)
        season_doc = self.get('seasons/' + scope['seasonId']) or {}
        codes = self.rows(self.target,'code',bound=500)
        context = list(fixtures)
        start = season_doc.get('from') or season[:4] + '.07.01'
        end = season_doc.get('to') or season[5:] + '.06.30'
        team_ids = {r['data'][k] for r in fixtures for k in ('homeTeamId','awayTeamId')}
        if not context:
            team_ids = {r['data'].get('TCODE') or r['data'].get('Code') for r in codes if r['data'].get('PlSeason')==season}
            if not team_ids:
                team_ids = {r['id'] for r in self.rows(self.did,'teams',[('currentLeagueId','==','EPL')],source=True,bound=100)}
        team_ids = sorted(team_ids-{None})
        teams = {p.split('/')[-1]:v for p,v in self.get_many('teams/'+tid for tid in team_ids).items()}
        selected_contracts = self.team_contracts(team_ids,re.sub(r'\D','',start),re.sub(r'\D','',end))
        player_ids = set(selected_contracts) | self.historical_league_players('EPL')
        self.report_progress(10)
        profiles = {}
        links = {}
        for first in range(0,len(team_ids),30):
            rows = self.rows(self.did,'players',[('currentTeamId','in',team_ids[first:first+30])],
                             source=True,bound=5000)
            player_ids.update(r['id'] for r in rows)
            profiles.update({r['id']:r['data'] for r in rows})
        for tid in team_ids:
            for row in self.rows(self.did,f'teams/{tid}/squad',source=True,bound=2000):
                player_ids.add(row['id']);links.setdefault(row['id'],[]).append(tid)
        profiles.update({p.split('/')[-1]:v for p,v in self.get_many('players/'+pid for pid in player_ids-set(profiles)).items()})
        self.report_progress(20)
        contracts = {}
        for number, pid in enumerate(sorted(profiles), 1):
            contracts[pid] = [dict(r['data'], _id=r['id']) for r in self.rows(
                self.did, f'players/{pid}/contracts', source=True, bound=500)]
            self.report_progress(20 + 45 * number / len(profiles))
        historical_teams = {c['teamId'] for values in contracts.values() for c in values
                            if c.get('leagueId') == 'EPL' and c.get('teamId')}
        teams.update({path.split('/')[-1]: value for path, value in
                      self.get_many('teams/' + tid for tid in sorted(historical_teams - set(teams))).items()})
        # Registration spans the season, even before the first/after the last
        # recorded fixture. A transfer must not be frozen at the last game date.
        context.extend({'id':f'registration-{tid}-{n}','data':{'seasonId':scope['seasonId'],'leagueId':'EPL',
                        'date':date,'homeTeamId':tid,'awayTeamId':tid}}
                       for tid in sorted(team_ids) for n,date in enumerate((start,end)))
        if not context: fail('No teams or registrations for this season')
        target = {}
        target_collections = ('Manager', 'Player', 'playerDB') if delivery_mode(scope) == 'masters' else ('dMST','Manager',*AGGREGATES,'Player','playerDB')
        for col in target_collections:
            field,value = (FieldPath('P-6').to_api_repr(),int(season[2:4]+season[7:9])) if col=='playerDB' else (
                ('SEASON',season[2:4]+'/'+season[7:9]) if col=='sAVG' else ('PlSeason',season))
            target[col] = self.rows(self.target,col,[(field,'==',value)],bound=20000)
        # Auto-ID collision checks read only relevant identities across seasons.
        auto_ids = sorted({player_identity(pid,profile) for pid,profile in profiles.items() if not str(pid).isdigit()})
        for col,field in (('Player','p_id'),('playerDB','P-1')):
            existing = {r['id']:r for r in target[col]}
            for first in range(0,len(auto_ids),30):
                existing.update({r['id']:r for r in self.rows(self.target,col,
                    [(FieldPath(field).to_api_repr(),'in',auto_ids[first:first+30])],bound=5000)})
            target[col] = list(existing.values())
        slots = {'seasonSlotMappings':{}}
        try:
            if slot_path: slots = json.loads(slot_path.read_text(encoding='utf-8'))
            if not isinstance(slots,dict) or not isinstance(slots.get('seasonSlotMappings',{}),dict):
                raise ValueError('Invalid season slot configuration')
            setup = plan_new_season(season,context,codes,target,slots)
        except (BackendError,OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
            reason = exc.message if isinstance(exc,BackendError) else f'Season slot configuration unavailable ({type(exc).__name__})'
            setup = {'documents':[],'code_rows':[],'slots':slots,'new_season':False,'errors':[reason]}
        reference_context = {'target_existing': target, 'ai_samples': {'code': codes + setup['code_rows']}}
        missing_team_ids = []
        for team_id in sorted(historical_teams):
            try:
                registration_team_index(reference_context, season, team_id)
            except ValueError:
                missing_team_ids.append(team_id)
        historical_indexes = self.historical_team_indexes(missing_team_ids)
        self.report_progress(75)
        evidence = {'did_matches':context,'source_recordings':[],'player_profiles':profiles,'player_contracts':contracts,
                    'teams':teams,'player_team_links':links,'target_existing':target,'ai_samples':{'code':codes+setup['code_rows']},
                    'historical_team_indexes': historical_indexes,
                    'leagues':{r['id']:r['data'] for r in self.rows(self.did,'leagues',source=True,bound=100)},
                    'coach_rounds':[] if fixtures else self.rows(self.did,'coachRounds',[('plSeason','==',season)],source=True,bound=2000),
                    'coach_contracts':[],'coaches':{}}
        for first in range(0,len(team_ids),30):
            evidence['coach_contracts'].extend(self.rows(self.did,'coachContracts',
                [('teamId','in',team_ids[first:first+30])],source=True,bound=5000,
                season_range=[re.sub(r'\D','',start),re.sub(r'\D','',end)]))
        coach_ids = {str(r['data']['coachId']) for r in evidence['coach_contracts']+evidence['coach_rounds'] if r['data'].get('coachId') is not None}
        evidence['coaches'] = {p.split('/')[-1]:v for p,v in self.get_many('coaches/'+pid for pid in coach_ids).items()}
        items = []
        if delivery_mode(scope) != 'masters':
            items = []
            refs = [self.did.document(f'matches/{r["id"]}/recordings/{side}') for r in fixtures for side in ('H','A')]
            heads = {}
            for start in range(0,len(refs),100):
                for snap in self.did.get_all(refs[start:start+100],timeout=60,retry=None):
                    self.documents[snap.reference.path]=self.version(snap)
                    heads[snap.reference.path]=encode(snap.to_dict()) or {}
            for row in fixtures:
                for side in ('H','A'):
                    rec=heads[f'matches/{row["id"]}/recordings/{side}']
                    items.append({'match_id':row['id'],'match':row['data'],'side':side,'recording':rec,
                                  'team_name':teams.get(rec.get('teamId'),{}).get('nameKr') or teams.get(rec.get('teamId'),{}).get('name')})
            self.capture_aggregate_review_sources(evidence, items)
        self.report_progress(95)
        report = generate_masters(evidence, fixtures, items, setup['slots'], setup, delivery_mode(scope), latest=scope.get('latest', False))
        self.report_progress(100)
        guards = {'documents':self.documents,'queries':self.queries,'code_scope':None,
                  'reference_code':{},
                  'new_code':{d['document_id']:d['data'] for d in setup['documents']}}
        guards['reference_code'] = code_reference(codes,guards['code_scope'])
        guards['registration_references'] = {
            'season': season, 'values': registration_reference_values(target.get('Player', []), season),
            'player_db_values': player_db_reference_values(target.get('playerDB', []), season)}
        guards['historical_team_indexes'] = {'team_ids': missing_team_ids, 'values': historical_indexes}
        if slot_path and not setup.get('errors'):
            guards['slot_file']={'path':str(slot_path.resolve()),'sha256':sha256(slot_path.read_bytes()).hexdigest()}
        return report,guards

    def capture_aggregate_review_sources(self, evidence, items):
        """Read RAW only for imported records affected by approved exclusions.

        New input uses its built KPI. An empty guarded H3/H4 query also detects
        an excluded-half insertion after preview. No source values are written.
        """
        for item_number, item in enumerate(items, 1):
            self.report_progress(80 + 14 * item_number / len(items))
            if not item['recording'].get('legacyGiId'):
                continue
            mid, side = item['match_id'], item['side']
            path = f'matches/{mid}/recordings/{side}/records'
            extras = self.rows(self.did, path, [('half', 'in', ['H3','H4'])], source=True, bound=1000)
            if extras or (mid, side) in APPROVED_RAW_EXCLUSIONS or (mid, side) in APPROVED_PATH_DELTAS:
                records = self.rows(self.did, path, source=True, bound=20000)
                evidence['source_recordings'].append({**item,
                    'children': {'records': records, 'paths': [], 'playerStats': [], 'cards': []}})


def code_reference(rows, scope=None):
    return {r['id']:r['data'] for r in rows if scope is None or (
        r['data'].get('PlSeason') in (None,'',scope['season'])
        and (r['data'].get('TCODE') or r['data'].get('Code')) in scope['teams'])}


def registration_reference_values(rows, season):
    return {r['id']: {field: r['data'].get(field) for field in
                     ('PlSeason', 'p_id', 'l_code', 't_code', 'pt_begin', 'IDX_CODE', 'p_id_old')}
            for r in rows if r['data'].get('PlSeason') == season}


def player_db_reference_values(rows, season):
    return {r['id']: {field: r['data'].get(field) for field in ('P-6', 'P-7', 'P-8')}
            for r in rows if r['data'].get('P-6') == int(season[2:4]+season[7:9])}


def plan_new_season(season, matches, code_rows, target, slots):
    """Match rows alone do not imply that season code/aggregate slots exist."""
    slots = deepcopy(slots)
    result = {'code_rows': [], 'documents': [], 'slots': slots, 'new_season': False}
    exact = [r for r in code_rows if r['data'].get('PlSeason') == season]
    if season not in slots.get('seasonSlotMappings', {}) and len(exact) == 20:
        saved = {r['data'].get('DID_CAVG_SLOT'): r['data'].get('Team Code') for r in exact}
        if set(saved) == set(range(1, 21)) and len(set(saved.values())) == 20:
            slots.setdefault('seasonSlotMappings', {})[season] = {'cavgSlotToCodeTeamCodeMapping': {
                str(n): {'codeTeamCode': number, 'cavgSlot': n} for n, number in saved.items()}}
    established = any(target.get(col) for col in ('mMST', 'pMST', 'cAVG', 'rAVG', 'rank'))
    if exact or established:
        return result
    teams = {r['data'][field] for r in matches for field in ('homeTeamId', 'awayTeamId')}
    if len(teams) != 20:
        return result
    references = []
    for team in sorted(teams):
        code = resolve_team_code(code_rows, season, team)
        positions = {r['data'].get('POSITION') for r in code_rows
                     if not r['data'].get('PlSeason') and (r['data'].get('TCODE') or r['data'].get('Code')) == team}
        if len(positions) != 1 or None in positions:
            fail(f'New season requires an unambiguous global POSITION: {team}')
        data = {'PlSeason': season, 'TCODE': team, 'Code': team, 'IDX_NAME': code['IDX_CODE'],
                'No': code['No'], 'Team Code': code['team_code_number'], 'POSITION': next(iter(positions))}
        ident = stable_id('code', (season, team))
        result['code_rows'].append({'id': ident, 'data': data})
        result['documents'].append({'collection': 'code', 'document_id': ident, 'data': dict(data, uid=ident), 'action': 'create-candidate'})
        references.append(code['team_code_number'])
    if len(set(references)) != 20 or len({r['data']['POSITION'] for r in result['code_rows']}) != 20:
        fail('New season team numbers/positions must be unique')
    slots.setdefault('seasonSlotMappings', {}).setdefault(season, {'cavgSlotToCodeTeamCodeMapping': {
        str(n): {'codeTeamCode': number, 'cavgSlot': n} for n, number in enumerate(sorted(references), 1)}})
    mapping = slots['seasonSlotMappings'][season].get('cavgSlotToCodeTeamCodeMapping',{})
    if set(mapping)!={str(n) for n in range(1,21)} or {v.get('codeTeamCode') for v in mapping.values()}!=set(references):
        fail('Season slot mapping must cover the same 20 team numbers')
    assigned = {value['codeTeamCode']:int(key) for key,value in mapping.items()}
    for row, doc in zip(result['code_rows'], result['documents'], strict=True):
        row['data']['DID_CAVG_SLOT'] = assigned[row['data']['Team Code']]
        doc['data']['DID_CAVG_SLOT'] = assigned[row['data']['Team Code']]
    result['new_season'] = True
    return result


def season_player(data, col, evidence, all_matches):
    pid=data['p_id'] if col=='Player' else data['P-1']
    player=next((p for p in set(evidence.get('player_contracts',{}))|set(evidence.get('player_profiles',{}))
                 if player_identity(p,evidence.get('player_profiles',{}).get(p))==pid),None)
    try:
        link=registration_link(evidence,player,season_label(all_matches[0]['data']['seasonId']),all_matches)
    except ValueError as exc:
        fail(str(exc))
    if link is None:
        fail(f'No registered team for player {pid}')
    fields={'t_code':link['teamId'],'IDX_CODE':resolve_team_code(evidence['ai_samples']['code'],season_label(all_matches[0]['data']['seasonId']),link['teamId'])['IDX_CODE']}
    for source,dest in (('from','pt_begin'),('to','pt_end')):
        if link.get(source): fields[dest]=dotted_date(link[source])
    if link.get('pos'): fields['pt_pos']=link['pos']
    if str(link.get('no') or '').isdigit(): fields['pt_num']=int(link['no'])
    aliases={'t_code':'P-7','IDX_CODE':'P-8','pt_begin':'P-14','pt_end':'P-15','pt_pos':'P-10','pt_num':'P-9'}
    return {**data,**{aliases[k] if col=='playerDB' else k:v for k,v in fields.items()}}


def excluded_existing_own_goals(evidence, selected_ids):
    """Identify only imported OWN goal documents backed by the same RAW event.

    OWN is intentionally absent from playerGoal output. Existing historical
    OWN documents are an approved comparison exclusion, never a delete plan.
    """
    keys = set()
    for item in evidence['source_recordings']:
        if item['match_id'] not in selected_ids or not item['recording'].get('legacyGiId'):
            continue
        season = season_label(item['match']['seasonId'])
        idx = resolve_team_code(evidence['ai_samples']['code'], season, item['recording']['teamId'])['IDX_CODE']
        for row in item['children']['records']:
            value = row['data']
            if (value.get('playerId') == 'OWN' and value.get('half') in ('H1', 'H2')
                    and (value.get('legacyKpiFlags') or {}).get('goal')):
                keys.add((item['match_id'], idx, value['half'], value['halfSeconds'], 'OWN'))
    return {row['id'] for row in evidence['target_existing'].get('playerGoal', [])
            if natural_key('playerGoal', row['data']) in keys}


def reviewed_aggregate_items(evidence, items):
    """Project approved RAW corrections into aggregates without editing source KPI."""
    sources = {(r['match_id'], r['side']): r for r in evidence.get('source_recordings', [])}
    result = []
    for item in items:
        source = sources.get((item['match_id'], item['side']))
        if source is None:
            result.append(item)
            continue
        records = source_records(source)
        if not records:
            raise ValueError('Reviewed aggregate RAW is empty')
        calculated = calculate_kpis(records).team_kpi
        unfiltered = source_records(source, include_reviewed_exclusions=True)
        full = source_records(source, include_reviewed_exclusions=True, include_extra_halves=True)
        before = calculate_kpis(unfiltered).team_kpi if len(unfiltered) != len(records) else calculated
        with_extras = calculate_kpis(full).team_kpi if len(full) != len(unfiltered) else before
        stored = item['recording'].get('kpi') or {}
        imported = bool(item['recording'].get('legacyGiId')) and all(r.legacy_flags is not None for r in records)
        for field, value in stored.items():
            if field not in calculated or abs(value - calculated[field]) < 1e-6:
                continue
            approved = imported and (
                (len(unfiltered) != len(records) and abs(value-before[field]) < 1e-6)
                or (len(full) != len(unfiltered) and abs(value-with_extras[field]) < 1e-6)
                or approved_path_difference(item['match_id'], item['side'], field, value, calculated[field])
                or (field == 'OG' and value == 0 and calculated['OG'] > 0
                    and all(abs(stored[k]-calculated[k]) < 1e-6 for k in ('GOAL','SHOT','SSR'))))
            if not approved:
                raise ValueError(f'Unreviewed aggregate KPI difference: {item["match_id"]}/{item["side"]}/{field}')
        result.append({**item, 'recording': {**item['recording'], 'kpi': calculated}})
    return result


def generate_delivery(evidence, selected, aggregate_items, slots, setup, *, match_only=False):
    errors, checks, documents = [], [], [] if match_only else list(setup['documents'])
    for fixture in selected:
        match=fixture['data']
        for team in (match['homeTeamId'],match['awayTeamId']):
            selected_coaches=[] if match_only else [r['data'] for r in evidence.get('coach_rounds',[]) if r['data'].get('teamId')==team
                              and r['data'].get('round')==match['round'] and r['data'].get('plSeason')==season_label(match['seasonId'])]
            if len(selected_coaches)==1:
                coach=selected_coaches[0]
                contracts=[r['data'] for r in evidence.get('coach_contracts',[]) if str(r['data'].get('coachId'))==str(coach.get('coachId'))
                           and r['data'].get('teamId')==team and JpdDidData._contract_covers_match_date(r['data'],match['date'])]
                if not any(c.get('coachName')==coach.get('coachName') and c.get('coachNameEn')==coach.get('coachNameEn')
                           and c.get('status')==coach.get('status') for c in contracts):
                    errors.append(f'{fixture["id"]}/{team}: coachRounds does not match the match-date coachContracts identity/names/status')
            for item in evidence['source_recordings']:
                if item['match_id']==fixture['id'] and item['recording'].get('teamId')==team and item['recording'].get('teamRating'):
                    from backend.system.system_schema import TeamRatingSnapshot
                    try: TeamRatingSnapshot.model_validate(item['recording']['teamRating'])
                    except ValueError: errors.append(f'{fixture["id"]}/{team}: required team rating/grade snapshot fields missing')
        scoped = {**evidence, 'target_existing': match_target_scope(evidence['target_existing'], fixture['data'])}
        report = build_preview(scoped, fixture['id'], covers=JpdDidData._contract_covers_match_date, match_only=match_only)
        errors.extend(f'{fixture["id"]}: {e}' for e in report['errors'])
        checks.extend({'matchId': fixture['id'], **c} for c in report['checks'])
        for doc in report['documents']:
            if doc['collection'] == 'playerDB':
                try:
                    doc['data'] = season_player(doc['data'], doc['collection'], evidence, evidence['did_matches'])
                except BackendError as exc:
                    errors.append(exc.message)
            documents.append(doc)
    if match_only:
        report = finish_delivery(documents, errors, checks, evidence['target_existing'], 'matches', len(selected))
        selected_ids = {r['id'] for r in selected}
        excluded_own = excluded_existing_own_goals(evidence, selected_ids) if not report['errors'] else set()
        report['excluded_existing_documents'] = [
            {'collection': 'playerGoal', 'document_id': ident, 'reason': 'Approved imported OWN goal exclusion; no deletion'}
            for ident in sorted(excluded_own)]
        identities = {(d['collection'],d['document_id']) for d in report['documents']}
        for col in MATCH_TARGETS:
            stale = [r['id'] for r in evidence['target_existing'].get(col,[])
                     if (col,r['id']) not in identities and r['data'].get('P-DB1' if col=='playerMST' else 'gm_id') in selected_ids
                     and not (col=='playerGoal' and r['id'] in excluded_own)]
            if stale: report['errors'].append(f'{col}: {len(stale)} unmatched target documents require reviewed reconciliation; no deletes performed')
        report['validation_passed'] = bool(selected) and not report['errors'] and all(c['passed'] for c in checks)
        report['summary']['errors'] = len(report['errors'])
        return report
    aggregate_items = reviewed_aggregate_items(evidence, aggregate_items)
    aggregate = build_aggregates(aggregate_items, evidence['ai_samples']['code'], slots)
    bind_existing_documents(aggregate, evidence['target_existing'])
    validation = validate_aggregate_links(aggregate, aggregate_items, evidence['ai_samples']['code'], slots)
    errors.extend(aggregate['errors'])
    if not aggregate['complete']:
        errors.append('Season aggregates incomplete: ' + json.dumps(aggregate['blocked'], ensure_ascii=False))
    if not validation['passed']:
        errors.append('Season aggregate source/rating validation failed')
    for doc in aggregate['documents']:
        doc['data'].setdefault('uid', doc['document_id'])
    documents.extend(aggregate['documents'])
    unique = {}
    for doc in documents:
        identity = (doc['collection'], doc['document_id'])
        if identity in unique and unique[identity]['data'] != doc['data']:
            errors.append(f'Conflicting destination identity: {identity}')
        unique[identity] = doc
    # A merge cannot reconcile a removed source event by itself. Detect obsolete
    # target rows and require a reviewed cleanup instead of leaving silent extras.
    selected_ids = {r['id'] for r in selected}
    excluded_own = excluded_existing_own_goals(evidence, selected_ids) if not errors else set()
    for col in MATCH_TARGETS:
        stale = [r['id'] for r in evidence['target_existing'].get(col, [])
                 if (col, r['id']) not in unique
                 and r['data'].get('P-DB1' if col == 'playerMST' else 'gm_id') in selected_ids
                 and not (col=='playerGoal' and r['id'] in excluded_own)]
        if stale:
            errors.append(f'{col}: {len(stale)} unmatched target documents require reviewed reconciliation; no deletes performed')
    existing = {col: {r['id']: r['data'] for r in rows} for col, rows in evidence['target_existing'].items()}
    for doc in unique.values():
        old = existing.get(doc['collection'], {}).get(doc['document_id'])
        doc['data'] = delivery_payload(doc['collection'], doc['data'], old)
        doc['baseline'] = replacement_baseline(doc['data'], old)
        doc['existed'] = old is not None
        doc.pop('changed_fields', None)
    passed = bool(selected) and not errors and all(c['passed'] for c in checks)
    return {'validation_passed': passed, 'write_enabled': False, 'documents': list(unique.values()),
            'excluded_existing_documents': [
                {'collection': 'playerGoal', 'document_id': ident, 'reason': 'Approved imported OWN goal exclusion; no deletion'}
                for ident in sorted(excluded_own)],
            'checks': checks, 'errors': errors, 'aggregate_validation': validation,
            'new_season': setup['new_season'], 'slot_definitions': slots,
            'scope_note': 'Selected match details plus whole-season aggregates and registered player identities independent of appearances',
            'summary': {'matches': len(selected), 'documents': len(unique), 'errors': len(errors),
                        'failed_checks': sum(not c['passed'] for c in checks)}}


# ==========================================================================
# Async delivery and job status
# ==========================================================================


def delivery_payload(collection, generated, old=None):
    """Preserve destination-only fields, replace each generated value atomically.

    This is deliberately a shallow overlay: a newly generated nested map owns
    that entire map, so removed calculated entries cannot survive a deep merge.
    The writer commits this reviewed full document under a complete baseline.
    """
    data = deepcopy(generated)
    if collection == 'vibePlayer':
        fields = ('P-APX', 'P-TPX', 'P-FPX', 'P-JMX')
    elif collection == 'dMST':
        fields = ('APX', 'TPX', 'FPX', 'FPX(AiFPX)', 'JMX', 'JMX2', 'gi_score2', 'Power3X(AVG)')
    elif collection in ('multiSheet_JMX', 'multiSheet_ALL'):
        fields = tuple(k for k in data if k.startswith('JMX-H'))
    elif collection == 'pMST':
        fields = ('gi_score',)
    else:
        fields = ()
    for field in fields:
        if data.get(field) is not None:
            data[field] = rating_two_decimals(data[field])
    precision = ({'ASR': 3, 'SSR': 3, 'SHOTScore': 2, 'GOALScore': 2, 'SSRScore': 2,
                  'DAP(AiAttack)': 1, 'DTP(AiTactic)': 1, 'SHOT(AiShot)': 1,
                  'GOAL(AiGoal)': 1, 'SSR(AiSSR)': 1} if collection == 'dMST' else
                 {'P-SSR': 3} if collection == 'vibePlayer' else
                 {'ASR':3,'SSR':3,'SSR2':3} if collection == 'pMST' else
                 {'ASR':3,'SSR':3} if collection == 'sAVG' else
                 {**{side+'-'+m:6 for side in ('H','A') for m in ('ASR','SSR')},
                  **{'6'+side+'-SSR':3 for side in ('H','A')}} if collection == 'mMST' else {})
    for field, digits in precision.items():
        if data.get(field) is not None:
            data[field] = rounded(data[field], digits)
            previous = (old or {}).get(field)
            if (type(previous) in (int,float) and abs(previous-data[field]) < 1e-12
                    and rounded(previous,digits) == data[field]):
                # Retain an equivalent historical IEEE representation instead
                # of rewriting 0.33299999999999996 as 0.333 on every upload.
                data[field] = previous
    for field in ('p_name', 'p_name_en', 'p_name_full') if collection == 'Player' else (
            ('P-2', 'P-3', 'P-4') if collection == 'playerDB' else ()):
        if field in data:
            data[field] = clean_player_name(data[field])
    # Keep each collection's established club label. The user explicitly
    # corrected Everton/Leicester; other labels need no spelling migration.
    team_names = {'Manager': (('T_Code','T_Name'),), 'dMST': (('t_code','t_Nm'),),
                  'pMST': (('t_code','t_Nm'),),
                  'mMST': (('H-Code','H-Name'),('A-Code','A-Name'))}
    for team_field, name_field in team_names.get(collection, ()):
        team = data.get(team_field)
        if team in ('E-EV', 'E-LE'):
            data[name_field] = {'E-EV':'에버턴', 'E-LE':'레스터'}[team]
        elif (old or {}).get(team_field) == team and (old or {}).get(name_field):
            data[name_field] = old[name_field]
    preserved = {k: deepcopy(v) for k, v in (old or {}).items()
                 if not retired_calculation_field(collection, k)
                 and not generated_calculation_field(collection, k)}
    return {**preserved, **data}


def retired_calculation_field(collection, field):
    return ((collection == 'pMST' and field.startswith(('I-', 'PH-', 'PA-', 'PI-')))
            or (collection == 'rank' and (field.startswith('WGR') or field in ('Season', 'S_Avg', 'R_Rate'))))


def generated_calculation_field(collection, field):
    """Dynamic calculation families belong to the generator, including removals."""
    if collection.startswith('multiSheet_'):
        return bool(re.fullmatch(r'(?:BAP|DAP|DTP|SHOT|GOAL|SSR|JMX)-H[1-4]-.+', field))
    if collection == 'rank':
        return bool(re.fullmatch(r'(?:WRK|RRK|RAV)\d+', field))
    if collection == 'rAVG':
        return bool(re.fullmatch(r'M[HA]\d+-IDX', field))
    return False


def replacement_baseline(data, old):
    return {k: {'exists': old is not None and k in old, 'value': old.get(k) if old else None}
            for k in set(data) | set(old or {})}


def retain_changed_documents(report, existing):
    by_id = {col: {r['id']: r['data'] for r in rows} for col, rows in existing.items()}
    report['documents'] = [d for d in report['documents']
                           if by_id.get(d['collection'], {}).get(d['document_id']) != d['data']]
    report['summary']['documents'] = len(report['documents'])


def scoped_aggregate_delivery(report, selected, target, *, latest=False):
    """Limit fixture rows; retain affected later round totals and season averages."""
    season = season_label(selected[0]['data']['seasonId'])
    first_round = min(r['data']['round'] for r in selected)
    fixture_keys = {(season, r['data']['round'], r['data']['homeTeamId'], r['data']['awayTeamId']) for r in selected}
    team_keys = {(season, r['data']['round'], r['data'][side])
                 for r in selected for side in ('homeTeamId', 'awayTeamId')}
    def selected_document(doc):
        col, data = doc['collection'], doc['data']
        if col == 'pMST':
            return aggregate_key(col, data) in team_keys
        if col == 'mMST':
            return aggregate_key(col, data) in fixture_keys
        return col == 'sAVG' or data.get('Round', 0) >= first_round
    result = finish_delivery([d for d in report['documents'] if selected_document(d)],
                             report['errors'], report['checks'], target, 'aggregates', len(selected), latest=latest)
    return {**report, **result}


def finish_delivery(documents, errors, checks, existing, mode, matches=0, *, latest=False):
    """Bind the reviewed overlay and complete destination concurrency baseline."""
    errors = list(errors)
    by_id = {col:{r['id']:r['data'] for r in rows} for col,rows in existing.items()}
    unique = {}
    allowed = (MATCH_COLLECTIONS if mode == 'matches' else IDENTITY_COLLECTIONS if mode == 'masters'
               else frozenset(AGGREGATES) if mode == 'aggregates' else MASTER_COLLECTIONS)
    for original in documents:
        doc = deepcopy(original)
        if doc['collection'] not in allowed:
            errors.append(f'Collection outside {mode} scope: {doc["collection"]}')
            continue
        identity = (doc['collection'],doc['document_id'])
        if identity in unique and unique[identity]['data']!=doc['data']:
            errors.append(f'Conflicting destination identity: {identity}')
        old = by_id.get(doc['collection'],{}).get(doc['document_id'])
        doc['data'] = delivery_payload(doc['collection'], doc['data'], old)
        doc['baseline'] = replacement_baseline(doc['data'], old)
        doc['existed'] = old is not None
        doc.pop('changed_fields',None)
        validate_document(doc)
        unique[identity] = doc
    changed = [d for (col,ident),d in unique.items() if by_id.get(col,{}).get(ident) != d['data']]
    result = changed if latest or mode=='latest' else list(unique.values())
    return {'mode':mode,'validation_passed':not errors and all(c['passed'] for c in checks),'write_enabled':False,
            'documents':result,'checks':checks,'errors':errors,
            'scope_note':('Selected match documents only' if mode == 'matches' else
                          'Player, playerDB, Manager, code only' if mode == 'masters' else
                          'pMST, mMST, cAVG, rAVG, rank, sAVG only' if mode == 'aggregates' else
                          'Legacy combined master/aggregate scope'),
            'summary':{'matches':matches,'documents':len(result),'candidateDocuments':len(unique),
                       'changedDocuments':len(changed),'unchangedDocuments':len(unique)-len(changed),
                       'errors':len(errors),'failed_checks':sum(not c['passed'] for c in checks)}}


def generate_masters(evidence, fixtures, aggregate_items, slots, setup, mode, *, latest=False):
    evidence = with_registration_references(evidence)
    errors, documents = [], ([] if mode == 'aggregates' else list(setup['documents']))
    context = evidence['did_matches']
    season = season_label(context[0]['data']['seasonId'])
    target = evidence['target_existing']
    indexes = {}
    destination_ids = {col:{r['id']:r for r in rows} for col,rows in target.items()}
    for col,rows in target.items():
        indexes[col] = defaultdict(list)
        for row in rows: indexes[col][natural_key(col,row['data'])].append(row)
    def emit(col,data):
        key = natural_key(col,data)
        found = indexes.get(col,{}).get(key,[])
        if len(found)>1:
            errors.append(f'{col}: ambiguous existing identity {key}')
            return
        ident = found[0]['id'] if found else stable_id(col,key)
        # Do not replace an unrelated document whose random ID happens to collide.
        collision = destination_ids.get(col,{}).get(ident)
        if collision and natural_key(col,collision['data'])!=key:
            errors.append(f'{col}: destination ID collision {ident}')
            return
        if found and found[0]['data'].get('uid'): data['uid']=found[0]['data']['uid']
        elif col=='Manager': data['uid']=ident
        documents.append({'collection':col,'document_id':ident,'data':data,'action':'replace-existing' if found else 'create-candidate'})
    if mode != 'aggregates':
        identities = {}
        for pid,profile in evidence['player_profiles'].items():
            try:
                mapped = registered_player_projection(evidence,pid,season,context)
                registrations = registered_player_projections(evidence, pid, season, context)
                if mapped is None and not registrations: continue
                identity = player_identity(pid, profile)
                if identity in identities and identities[identity]!=pid: raise ValueError(f'Player identity collision: {pid}/{identities[identity]}')
                identities[identity]=pid
                for registration in registrations:
                    emit('Player', registration)
                if mapped is None:
                    continue
                # Explicitly cleared source fields must clear the old owned value.
                for dest,src in PLAYER_FIELDS.items():
                    if src in profile and profile[src] is None: mapped[dest]=None
                link = registration_link(evidence,pid,season,context)
                if 'to' in link and link['to'] is None: mapped['pt_end']='9999.99.99'
                for src,dest in (('no','pt_num'),('pos','pt_pos')):
                    if src in link and link[src] is None: mapped[dest]=None
                emit('playerDB',player_db_projection(evidence,mapped,season))
            except (KeyError,TypeError,ValueError) as exc: errors.append(str(exc))
        coach_rows = []
        if fixtures:
            for fixture in fixtures:
                match=fixture['data']
                for team in (match['homeTeamId'],match['awayTeamId']):
                    contracts = [c['data'] for c in evidence.get('coach_contracts',[]) if c['data'].get('teamId')==team
                                 and JpdDidData._contract_covers_match_date(c['data'],match['date'])]
                    latest_contract_start = max((re.sub(r'\D','',c.get('from') or '') for c in contracts),default=None)
                    contracts = [c for c in contracts if re.sub(r'\D','',c.get('from') or '')==latest_contract_start]
                    if len(contracts)!=1:
                        errors.append(f'{fixture["id"]}/{team}: expected one match-date coach contract')
                        continue
                    coach_rows.append({**contracts[0],'plSeason':season,'round':match['round']})
        else:
            for row in evidence.get('coach_rounds',[]):
                coach=row['data']
                candidates=[r['data'] for r in evidence.get('coach_contracts',[]) if str(r['data'].get('coachId'))==str(coach.get('coachId'))
                            and r['data'].get('teamId')==coach.get('teamId') and r['data'].get('from')==coach.get('from')]
                coach_rows.append({**coach,**candidates[0]} if len(candidates)==1 else coach)
        represented = {(r.get('teamId'),str(r.get('coachId'))) for r in coach_rows}
        teams = {r['data'][k] for r in context for k in ('homeTeamId','awayTeamId')}
        season_end = max(re.sub(r'\D','',r['data']['date']) for r in context)
        for row in evidence.get('coach_contracts',[]):
            coach = row['data']
            if (coach.get('teamId') in teams and not coach.get('to')
                    and re.sub(r'\D','',coach.get('from') or '') <= season_end
                    and (coach.get('teamId'),str(coach.get('coachId'))) not in represented):
                errors.append(f'{coach.get("teamId")}/{coach.get("coachId")}: active coach needs a fixture or coachRounds round mapping')
        for coach in coach_rows:
            try:
                profile=evidence.get('coaches',{}).get(str(coach.get('coachId')),{})
                coach={**coach,**{k:v for k,v in {'coachName':profile.get('nameKr') or profile.get('name'),
                        'coachNameEn':profile.get('nameEn'),'legacyCoachId':profile.get('legacyCoachId')}.items() if v is not None}}
                team=coach['teamId'];code=resolve_team_code(evidence['ai_samples']['code'],season,team)
                profile=evidence['teams'].get(team,{})
                emit('Manager',manager_projection(coach,code,team_delivery_name(evidence['ai_samples']['code'],season,team,profile)))
            except (KeyError,TypeError,ValueError) as exc: errors.append(str(exc))
    validation, aggregate_errors, aggregate_docs = None, [], []
    if mode != 'masters':
        try:
            aggregate_items = reviewed_aggregate_items(evidence, aggregate_items)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
            aggregate_items = []
        final = [i for i in aggregate_items if i['recording'].get('status')=='final']
        known = {(r['data'].get('gm_round'),r['data'].get('t_code')) for r in target.get('dMST',[])}
        supplied = {(i['match']['round'],i['recording'].get('teamId')) for i in final}
        validation = None
        aggregate_errors = list(setup.get('errors',[]))
        aggregate_docs = []
        if known-supplied:
            aggregate_errors.append('Season aggregate coverage incomplete: DID lacks existing destination team/match rows')
        elif final and not aggregate_errors:
            try:
                aggregate=build_aggregates(aggregate_items,evidence['ai_samples']['code'],slots)
                bind_existing_documents(aggregate,target)
                validation=validate_aggregate_links(aggregate,aggregate_items,evidence['ai_samples']['code'],slots)
                aggregate_errors.extend(aggregate['errors'])
                if not aggregate['complete'] or not validation['passed']:
                    aggregate_errors.append('Season aggregate source/rating validation failed')
                generated = {(doc['collection'],aggregate_key(doc['collection'],doc['data'])) for doc in aggregate['documents']}
                if any((col,aggregate_key(col,row['data'])) not in generated for col in AGGREGATES for row in target.get(col,[])):
                    aggregate_errors.append('Season aggregate coverage incomplete: existing destination aggregate rows are missing from DID')
                if not aggregate_errors:
                    aggregate_docs=aggregate['documents']
                    for doc in aggregate_docs: doc['data'].setdefault('uid',doc['document_id'])
                    documents.extend(aggregate_docs)
            except (BackendError,KeyError,TypeError,ValueError) as exc:
                aggregate_errors.append(exc.message if isinstance(exc,BackendError) else f'Aggregate validation failed ({type(exc).__name__})')
    if mode == 'aggregates':
        errors.extend(aggregate_errors)
        if not aggregate_docs and not aggregate_errors:
            errors.append('No final DID recordings available for aggregates')
    report=finish_delivery(documents,errors,[],target,mode,latest=latest)
    report.update(aggregate_validation=validation,new_season=setup['new_season'],slot_definitions=slots,
                  aggregate_status=None if mode == 'masters' else ('blocked' if aggregate_errors else ('ready' if aggregate_docs else 'empty')),
                  warnings=aggregate_errors,blocked_groups=['aggregates'] if aggregate_errors else [])
    return report


VALIDATION_POLICY = 'unified-all-matches-masters-aggregates-v23'


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return sha256(dump(value).encode()).hexdigest()


def merge_guards(previous, current):
    merged = dict(previous)
    documents = dict(previous['documents'])
    for path, version in current['documents'].items():
        if path in documents and documents[path] != version:
            fail('DID source changed between preview chunks', 'aifootballx_source_changed')
        documents[path] = version
    queries = {fingerprint([g.get('path'),g.get('group'),g.get('filters',[])]): g for g in previous['queries']}
    for guard in current['queries']:
        key = fingerprint([guard.get('path'),guard.get('group'),guard.get('filters',[])])
        # Persisted JSON turns filter tuples into lists; compare their canonical
        # representation while still checking every version and query option.
        if key in queries and dump(queries[key]) != dump(guard):
            fail('DID source collection changed between preview chunks', 'aifootballx_source_changed')
        queries[key] = guard
    for key in ('reference_code', 'new_code', 'slot_file', 'code_scope'):
        if previous.get(key) != current.get(key):
            fail('Season references changed between preview chunks', 'aifootballx_source_changed')
    merged.update(documents=documents, queries=list(queries.values()))
    if previous.get('delivery_groups') or current.get('delivery_groups'):
        groups = previous.get('delivery_groups', []) + current.get('delivery_groups', [])
        additions = {}
        for group in groups:
            for ident, data in group.get('new_code', {}).items():
                if ident in additions and additions[ident] != data:
                    fail('Season code candidates changed between delivery groups', 'aifootballx_source_changed')
                additions[ident] = data
        merged['delivery_groups'] = [{**group, 'new_code': additions} for group in groups]
    return merged


ALLOWED_COLLECTIONS = {'code', 'Manager', 'Player', 'playerDB', 'playerMST', 'vibePlayer', 'dMST', 'TACTIC',
    'playerGoal', 'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank', 'bmas_DTA', 'bmas_DTB', 'bmas_DTM', 'bmas_DTS',
    'multiSheet_ALL', 'multiSheet_BAP', 'multiSheet_DAP', 'multiSheet_DTP', 'multiSheet_GOAL',
    'multiSheet_JMX', 'multiSheet_SHOT', 'multiSheet_GOAL-시간'}
ALLOWED_COLLECTIONS -= EXCLUDED_COLLECTIONS
MASTER_FIELDS = {
    'Manager': {'PlSeason', 'Round', 'T_Code', 'IDX_CODE', 'T_Name', 'm_id', 'Manager_EN', 'Manager_KR',
                'Status_EN', 'Status_KR', 'gm_date', 'e_date', 'uid'},
    'Player': {'p_id', 'PlSeason', 'l_code', 't_code', 'IDX_CODE', 'pt_begin', 'pt_end', 'pt_pos', 'pt_num',
               'p_name', 'p_name_en', 'p_name_full', 'p_birth', 'p_height', 'p_nation', 'p_foot', 'p_id_old', 'DID_PLAYER_ID', 'uid'},
    'playerDB': {*PLAYER_DB, 'P-17', 'DID_PLAYER_ID', 'uid'},
    'playerMST': {*(f'P-DB{n}' for n in range(1, 40)), *(f'P-KPI{n}' for n in range(42, 50)), 'uid'},
    'code': {'PlSeason', 'TCODE', 'Code', 'IDX_NAME', 'No', 'Team Code', 'POSITION', 'DID_CAVG_SLOT', 'uid'},
}


def validate_document(doc):
    col, ident, data = doc['collection'], doc['document_id'], doc['data']
    if col not in ALLOWED_COLLECTIONS or not ident or '/' in ident or not data:
        fail('Invalid delivery collection/document')
    preserved = {field for field, expected in doc.get('baseline', {}).items()
                 if expected['exists'] and field in data and data[field] == expected['value']}
    if col in MASTER_FIELDS and set(data) - MASTER_FIELDS[col] - preserved:
        fail(f'Unowned fields in {col}')
    for field in data:
        if ('.' in field or field.startswith('`')) and field not in preserved:
            fail('Payload fields must be literal top-level names')
        if col == 'pMST' and field.startswith(('I-', 'PH-', 'PA-', 'PI-')):
            fail('Unused prediction field in delivery')
        if col == 'rank' and (field.startswith('WGR') or field in ('Season', 'S_Avg', 'R_Rate')):
            fail('Unused rank field in delivery')
    dump(data)
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            fail('Non-finite delivery value')
        if isinstance(value, dict):
            if '__nonfinite_float__' in value:
                fail('Invalid numeric source value')
            for v in value.values(): finite(v)
        if isinstance(value, list):
            for v in value: finite(v)
    finite(data)


class JobStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'delivery.sqlite3'
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL,
              preview_id TEXT, request_key TEXT UNIQUE, scope TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
              created REAL NOT NULL, updated REAL NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS documents (
              preview_id TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL, identity TEXT,
              PRIMARY KEY (preview_id, ordinal));
            CREATE TABLE IF NOT EXISTS results (
              job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, state TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
              PRIMARY KEY(job_id, ordinal));
            ''')
            if 'public' not in {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN public INTEGER NOT NULL DEFAULT 1')
            if 'identity' not in {r['name'] for r in db.execute('PRAGMA table_info(documents)')}:
                db.execute('ALTER TABLE documents ADD COLUMN identity TEXT')
                for row in db.execute('SELECT preview_id,ordinal,payload FROM documents').fetchall():
                    doc = json.loads(row['payload'])
                    db.execute('UPDATE documents SET identity=? WHERE preview_id=? AND ordinal=?',
                               (fingerprint([doc['collection'], doc['document_id']]), row['preview_id'], row['ordinal']))
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS document_identity ON documents(preview_id,identity)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def job(self, ident, owner):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (ident, owner)).fetchone()
        if row is None:
            raise BackendError('Delivery job not found', status_code=404, code='aifootballx_job_not_found')
        value = dict(row)
        for key in ('scope', 'meta'):
            value[key] = json.loads(value[key])
        return value

    def create(self, kind, scope, owner, preview_id=None, key=None, *, public=True, deduplicate_active=False):
        ident, now = uuid4().hex, time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if deduplicate_active:
                active = db.execute("SELECT id,updated,meta FROM jobs WHERE owner=? AND kind=? AND scope=? AND state IN ('queued','running') ORDER BY created DESC LIMIT 1",
                                    (owner, kind, dump(scope))).fetchone()
                if active:
                    heartbeat = active['updated']
                    for child in ('previewId', 'sendId'):
                        child_id = json.loads(active['meta']).get(child)
                        child_row = db.execute('SELECT updated FROM jobs WHERE id=?',(child_id,)).fetchone() if child_id else None
                        if child_row: heartbeat=max(heartbeat,child_row['updated'])
                    if time.time()-heartbeat<300:
                        return active['id'], False
                    db.execute("UPDATE jobs SET state='failed',error='aifootballx_interrupted' WHERE id=?",(active['id'],))
            if key:
                old = db.execute('SELECT id,preview_id FROM jobs WHERE request_key=?', (key,)).fetchone()
                if old:
                    if old['preview_id'] != preview_id:
                        fail('Idempotency key already used with another preview')
                    return old['id'], False
            db.execute('INSERT INTO jobs (id,kind,state,owner,preview_id,request_key,scope,created,updated,public) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (ident, kind, 'queued', owner, preview_id, key, dump(scope), now, now, int(public)))
            if preview_id:
                db.execute("INSERT INTO results (job_id,ordinal,state) SELECT ?,ordinal,'pending' FROM documents WHERE preview_id=?", (ident, preview_id))
        return ident, True

    def latest(self, owner, *, kind=None, scope=None, exclude=None, public=True):
        query, args = 'SELECT id FROM jobs WHERE owner=?', [owner]
        for field, value in [('kind', kind), ('scope', dump(scope) if scope is not None else None)]:
            if value is not None:
                query += f' AND {field}=?'; args.append(value)
        if exclude:
            query += ' AND id<>?'; args.append(exclude)
        if public:
            query += ' AND public=1'
            if kind is None:
                query += " AND kind IN ('delivery','preview')"
        with self.connect() as db:
            row = db.execute(query + ' ORDER BY created DESC LIMIT 1', args).fetchone()
        return self.job(row['id'], owner) if row else None

    def fail_job(self, ident, exc):
        with self.connect() as db:
            meta = json.loads(db.execute('SELECT meta FROM jobs WHERE id=?', (ident,)).fetchone()[0])
        meta['failureReason'] = exc.message if isinstance(exc, BackendError) else f'처리 중 오류가 발생했습니다 ({type(exc).__name__}).'
        self.set_job(ident, 'failed', meta=meta, error=error_code(exc))

    def claim(self, ident):
        with self.connect() as db:
            return db.execute("UPDATE jobs SET state='running',updated=? WHERE id=? AND state='queued'", (time.time(), ident)).rowcount == 1

    def set_job(self, ident, state, *, meta=None, error=None):
        with self.connect() as db:
            if meta is None:
                db.execute('UPDATE jobs SET state=?,updated=?,error=? WHERE id=?', (state, time.time(), error, ident))
            else:
                db.execute('BEGIN IMMEDIATE')
                previous = json.loads(db.execute('SELECT meta FROM jobs WHERE id=?', (ident,)).fetchone()[0])
                if 'progress' in previous:
                    progress = meta.get('progress', {})
                    meta = {**meta, 'progress': {**previous['progress'], **progress,
                        'percent': max(previous['progress'].get('percent', 0), progress.get('percent', 0))}}
                db.execute('UPDATE jobs SET state=?,updated=?,error=?,meta=? WHERE id=?', (state, time.time(), error, dump(meta), ident))

    def record_progress(self, ident, percent, **details):
        """Persist completed work without discarding a manifest or moving backwards."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            meta = json.loads(db.execute('SELECT meta FROM jobs WHERE id=?', (ident,)).fetchone()[0])
            previous = meta.get('progress', {})
            percent = max(previous.get('percent', 0), min(99, int(percent)))
            progress = {**previous, **details, 'percent': percent}
            if progress != previous:
                meta['progress'] = progress
                db.execute('UPDATE jobs SET meta=?,updated=? WHERE id=?', (dump(meta), time.time(), ident))

    def save_preview(self, ident, report, guards, *, complete=True):
        docs = report.pop('documents')
        for doc in docs:
            validate_document(doc)
        # SQLite commits the immutable manifest and validation result together.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ordinal = db.execute('SELECT coalesce(max(ordinal)+1,0) FROM documents WHERE preview_id=?', (ident,)).fetchone()[0]
            for doc in docs:
                identity = fingerprint([doc['collection'], doc['document_id']])
                payload = dump(doc)
                old = db.execute('SELECT payload FROM documents WHERE preview_id=? AND identity=?', (ident, identity)).fetchone()
                if old and old['payload'] != payload:
                    fail('Shared candidate changed between source chunks; create a new preview')
                if not old:
                    db.execute('INSERT INTO documents (preview_id,ordinal,payload,identity) VALUES (?,?,?,?)', (ident, ordinal, payload, identity))
                    ordinal += 1
            previous = json.loads(db.execute('SELECT meta FROM jobs WHERE id=?', (ident,)).fetchone()[0])
            if previous.get('report'):
                report['validation_passed'] &= previous['report']['validation_passed']
                report['errors'] = sorted(set(previous['report']['errors'] + report['errors']))
                report['checks'] = previous['report']['checks'] + report['checks']
                report['summary']['matches'] += previous['report']['summary']['matches']
                for key in ('candidateDocuments','changedDocuments','unchangedDocuments'):
                    if key in report['summary']:
                        report['summary'][key] += previous['report']['summary'].get(key,0)
                report['summary']['errors'] = len(report['errors'])
                report['summary']['failed_checks'] = sum(not c['passed'] for c in report['checks'])
                guards = merge_guards(previous['guards'], guards)
            report['summary']['documents'] = ordinal
            meta = {**previous, 'report': report, 'guards': guards, 'validationPolicy': VALIDATION_POLICY}
            db.execute('UPDATE jobs SET state=?,meta=?,updated=? WHERE id=?',
                       (('validated' if report['validation_passed'] else 'invalid') if complete else 'running', dump(meta), time.time(), ident))

    def finalize_preview(self, ident, owner, source):
        job = self.job(ident, owner)
        source.assert_fresh(job['meta']['guards'])
        digest = sha256()
        with self.connect() as db:
            for row in db.execute('SELECT payload FROM documents WHERE preview_id=? ORDER BY ordinal', (ident,)):
                digest.update(row['payload'].encode()); digest.update(b'\n')
        job['meta']['digest'] = digest.hexdigest()
        self.set_job(ident, 'validated' if job['meta']['report']['validation_passed'] else 'invalid', meta=job['meta'])

    def page(self, job, offset, limit, state=None):
        with self.connect() as db:
            if job['kind'] == 'preview':
                rows = db.execute('SELECT ordinal,payload FROM documents WHERE preview_id=? ORDER BY ordinal LIMIT ? OFFSET ?',
                                  (job['id'], limit, offset)).fetchall()
                return [{'ordinal': r['ordinal'], **json.loads(r['payload'])} for r in rows]
            where = ' AND r.state=?' if state else ''
            args = [job['id']] + ([state] if state else []) + [limit, offset]
            rows = db.execute('SELECT r.*,d.payload FROM results r JOIN documents d ON d.preview_id=? AND d.ordinal=r.ordinal '
                              'WHERE r.job_id=?' + where + ' ORDER BY r.ordinal LIMIT ? OFFSET ?', [job['preview_id'], *args]).fetchall()
            return [{'ordinal': r['ordinal'], 'state': r['state'], 'attempts': r['attempts'], 'error': r['error'],
                     **json.loads(r['payload'])} for r in rows]

    def counts(self, ident):
        with self.connect() as db:
            return {r['state']: r['n'] for r in db.execute('SELECT state,count(*) n FROM results WHERE job_id=? GROUP BY state', (ident,))}

    def result(self, ident, ordinal, state, error=None):
        with self.connect() as db:
            db.execute('UPDATE results SET state=?,attempts=attempts+1,error=? WHERE job_id=? AND ordinal=?', (state, error, ident, ordinal))
            db.execute('UPDATE jobs SET updated=? WHERE id=?', (time.time(), ident))

    def results_many(self, ident, values):
        with self.connect() as db:
            db.executemany('UPDATE results SET state=?,attempts=attempts+1,error=NULL WHERE job_id=? AND ordinal=?',
                           ((state, ident, ordinal) for ordinal, state in values))
            db.execute('UPDATE jobs SET updated=? WHERE id=?', (time.time(), ident))

    def retry(self, ident, owner):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (ident, owner)).fetchone()
            if row is None:
                raise BackendError('Job not found', status_code=404, code='aifootballx_job_not_found')
            if row['kind'] != 'send':
                fail('Only send jobs can be retried; create a new preview for invalid data')
            if row['state'] in ('running', 'queued') and time.time() - row['updated'] < 300:
                fail('Delivery job is already active')
            if not db.execute("SELECT 1 FROM results WHERE job_id=? AND state IN ('failed','pending') LIMIT 1", (ident,)).fetchone():
                return False
            db.execute("UPDATE results SET state='pending',error=NULL WHERE job_id=? AND state='failed'", (ident,))
            db.execute("UPDATE jobs SET state='queued',updated=?,error=NULL WHERE id=?", (time.time(), ident))
        return True


def decide_merge(doc, current):
    """Commit the reviewed overlay only against the complete captured baseline."""
    current = encode(current) if current is not None else None
    data = doc['data']
    if current == data:
        return False
    if doc['existed'] != (current is not None):
        fail('Destination existence changed; create a new preview', 'aifootballx_destination_changed')
    baseline = {field: expected['value'] for field, expected in doc['baseline'].items() if expected['exists']}
    if (current or {}) != baseline:
        fail('Destination document changed; create a new preview', 'aifootballx_destination_changed')
    return True


def firestore_payload(doc, current):
    """Keep unchanged destination values in their original Firestore types.

    Preview JSON encodes timestamps as strings. Reuse the transaction's raw
    value when unchanged, so preserving metadata never converts its type.
    """
    return {field: deepcopy(current[field])
            if current is not None and field in current and encode(current[field]) == value else deepcopy(value)
            for field, value in doc['data'].items()}


class FirestoreWriter:
    def __init__(self, client):
        if client.project != 'aifootballx':
            fail('Unexpected destination project')
        self.client = client

    def send(self, doc, preview_id):
        from firebase_admin import firestore
        validate_document(doc)
        digest = fingerprint({'previewId': preview_id, 'collection': doc['collection'], 'id': doc['document_id'], 'data': doc['data']})
        ref = self.client.collection(doc['collection']).document(doc['document_id'])
        receipt = self.client.collection('_didDeliveryReceipts').document(digest)

        @firestore.transactional
        def apply(tx):
            seen = receipt.get(transaction=tx)
            if seen.exists:
                return 'skipped'
            snap = ref.get(transaction=tx)
            current = snap.to_dict() if snap.exists else None
            write = decide_merge(doc, current)
            if write:
                tx.set(ref, firestore_payload(doc, current), merge=False)
            tx.set(receipt, {'previewId': preview_id, 'collection': doc['collection'], 'documentId': doc['document_id'],
                             'payloadHash': digest, 'completedAt': firestore.SERVER_TIMESTAMP})
            return 'success' if write else 'skipped'
        return apply(self.client.transaction())

    def send_many(self, docs, preview_id):
        """At most 200 payloads + 200 receipts, below Firestore's write limit."""
        from firebase_admin import firestore
        if len(docs) > 200:
            fail('Delivery transaction too large')
        refs = []
        for doc in docs:
            validate_document(doc)
            digest = fingerprint({'previewId': preview_id, 'collection': doc['collection'], 'id': doc['document_id'], 'data': doc['data']})
            refs.append((doc, self.client.collection(doc['collection']).document(doc['document_id']),
                         self.client.collection('_didDeliveryReceipts').document(digest)))

        @firestore.transactional
        def apply(tx):
            snapshots = {s.reference.path: s for s in self.client.get_all(
                [ref for _, dest, receipt in refs for ref in (dest, receipt)], transaction=tx, timeout=60, retry=None)}
            plan = []
            # Validate the entire batch before scheduling any write.
            for doc, dest, receipt in refs:
                if snapshots[receipt.path].exists:
                    plan.append((doc, dest, receipt, 'skipped', False))
                    continue
                snap = snapshots[dest.path]
                write = decide_merge(doc, snap.to_dict() if snap.exists else None)
                plan.append((doc, dest, receipt, 'success' if write else 'skipped', True))
            for doc, dest, receipt, state, record in plan:
                if state == 'success':
                    snap = snapshots[dest.path]
                    tx.set(dest, firestore_payload(doc, snap.to_dict() if snap.exists else None), merge=False)
                if record:
                    tx.set(receipt, {'previewId': preview_id, 'collection': doc['collection'],
                                    'documentId': doc['document_id'], 'completedAt': firestore.SERVER_TIMESTAMP})
            return [state for _, _, _, state, _ in plan]
        return apply(self.client.transaction())


_client_lock = Lock()


def target_client():
    import firebase_admin
    from firebase_admin import credentials, firestore
    settings = get_settings()
    path = settings.aifootballx_credentials_path
    if not path and settings.app_env == 'local':
        path = 'C:/Users/user/footballx/server/keys/firebase-admin.json'
    if not path or not Path(path).is_file():
        raise BackendError('Configure AIfootballX credentials on DID backend', status_code=503, code='aifootballx_not_configured')
    with _client_lock:
        try:
            app = firebase_admin.get_app('did-aifootballx-delivery')
        except ValueError:
            app = firebase_admin.initialize_app(credentials.Certificate(path), name='did-aifootballx-delivery')
    client = firestore.client(app)
    if client.project != 'aifootballx':
        fail('AIfootballX credentials point at another project')
    return client


class DeliveryService:
    def __init__(self, store=None, source_factory=None, writer_factory=None):
        self.store = store or JobStore(PROJECT_ROOT / 'backend/data/aifootballx/delivery')
        self.source_factory = source_factory or (lambda: SourceCapture(get_firestore_client(), target_client()))
        self.writer_factory = writer_factory or (lambda: FirestoreWriter(target_client()))
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='aifootballx')
        self.stopping = Event()

    def ensure_running(self):
        if self.stopping.is_set():
            raise BackendError('Backend stopping; retry unfinished documents', status_code=409, code='aifootballx_interrupted')

    def close(self):
        self.stopping.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def preview(self, scope, owner):
        delivery_mode(scope)
        ident, created = self.store.create('preview', scope, owner, deduplicate_active=True)
        if created:
            self.pool.submit(self.run_preview, ident, owner)
        return ident

    def run_preview(self, ident, owner):
        if not self.store.claim(ident): return
        try:
            job = self.store.job(ident, owner)
            settings = get_settings()
            slot_path = Path(settings.aifootballx_slot_mapping_path) if settings.aifootballx_slot_mapping_path else None
            if slot_path is None and settings.app_env == 'local':
                candidate = Path('C:/Users/user/footballx/app/utils/team_slot_mapping.json')
                slot_path = candidate if candidate.is_file() else None
            source = self.source_factory()
            scopes = source.split_scopes(job['scope']) if hasattr(source, 'split_scopes') else [job['scope']]
            for number,scope in enumerate(scopes,1):
                self.ensure_running()
                source = self.source_factory()
                if hasattr(source, 'set_progress_callback'):
                    source.set_progress_callback(lambda value, chunk=number:
                        self.store.record_progress(ident, 80 * (chunk - 1 + value / 100) / len(scopes)))
                report, guards = source.capture(scope, slot_path)
                if job['scope'].get('unified'):
                    # Verify the shared source once, plus each group's destination references.
                    guards = {'documents': guards['documents'], 'queries': guards['queries'],
                              'delivery_groups': [{k: v for k, v in guards.items() if k not in ('documents', 'queries')}]}
                    if scope['mode'] != 'matches':
                        report['summary']['matches'] = 0
                self.ensure_running()
                self.store.save_preview(ident, report, guards, complete=False)
                self.store.record_progress(ident, 80 * number / len(scopes),
                                           roundsTotal=len(scopes), roundsProcessed=number)
            if hasattr(source, 'set_progress_callback'):
                source.set_progress_callback(lambda value: self.store.record_progress(ident, 80 + 19 * value / 100))
            self.store.finalize_preview(ident, owner, source)
        except Exception as exc:
            self.store.fail_job(ident, exc)

    @staticmethod
    def require_enabled():
        if not get_settings().aifootballx_write_enabled:
            raise BackendError('Firestore delivery is disabled (AIFOOTBALLX_WRITE_ENABLED=false)', status_code=409, code='aifootballx_write_disabled')

    def send(self, preview_id, owner, idempotency_key):
        self.require_enabled()
        preview = self.store.job(preview_id, owner)
        if preview['kind'] != 'preview' or preview['state'] != 'validated' or not preview['meta']['report']['validation_passed']:
            fail('Only validated DID previews may be sent')
        key = fingerprint({'owner': owner, 'key': idempotency_key})
        ident, created = self.store.create('send', preview['scope'], owner, preview_id, key)
        if created:
            self.pool.submit(self.run_send, ident, owner)
        return ident

    def retry(self, ident, owner):
        self.require_enabled()
        if self.store.retry(ident, owner):
            self.pool.submit(self.run_send, ident, owner)
        return ident

    def run_send(self, ident, owner):
        if not self.store.claim(ident): return
        try:
            self.require_enabled()
            job = self.store.job(ident, owner)
            preview = self.store.job(job['preview_id'], owner)
            if preview['state'] != 'validated': fail('Preview is not validated')
            if preview['meta'].get('validationPolicy') != VALIDATION_POLICY:
                fail('검증 규칙이 변경됐습니다. 같은 입력으로 새 미리보기가 필요합니다.', 'aifootballx_preview_outdated')
            if not self.store.page(job,0,1):
                self.store.set_job(ident,'complete')
                return
            source = self.source_factory()
            if hasattr(source, 'set_progress_callback'):
                source.set_progress_callback(lambda value: self.store.record_progress(ident, value / 10))
            source.assert_fresh(preview['meta']['guards'])
            self.store.record_progress(ident, 10)
            last_fresh = time.monotonic()
            writer = self.writer_factory()
            # Read a fixed ordinal window. Success rows are never retried.
            offset = 0
            while True:
                self.ensure_running()
                self.require_enabled()
                rows = self.store.page(job, offset, 100)
                if not rows: break
                pending = [r for r in rows if r['state'] == 'pending']
                if pending and time.monotonic() - last_fresh >= 300:
                    source.assert_fresh(preview['meta']['guards'])
                    last_fresh = time.monotonic()
                if pending and hasattr(writer, 'send_many'):
                    try:
                        states = writer.send_many(pending, job['preview_id'])
                    except Exception:
                        # A conflicted document must not strand the other valid
                        # documents. Receipts also cover a lost batch response.
                        pass
                    else:
                        self.store.results_many(ident, [(row['ordinal'], state) for row, state in zip(pending, states, strict=True)])
                        offset += len(rows)
                        continue
                for row in pending:
                    try:
                        state = writer.send(row, job['preview_id'])
                    except Exception as exc:
                        self.store.result(ident, row['ordinal'], 'failed', error_code(exc))
                    else:
                        self.store.result(ident, row['ordinal'], state)
                offset += len(rows)
            counts = self.store.counts(ident)
            self.store.set_job(ident, 'partial' if counts.get('failed') else 'complete')
        except Exception as exc:
            self.store.fail_job(ident, exc)

    def send_scope(self, scope, owner):
        delivery_mode(scope)
        self.require_enabled()
        ident, created = self.store.create('delivery', scope, owner, deduplicate_active=True)
        if created:
            self.pool.submit(self.run_scope_send, ident, owner)
        return ident

    def run_scope_send(self, ident, owner):
        if not self.store.claim(ident): return
        try:
            self.require_enabled()
            job = self.store.job(ident, owner)
            previous = self.store.latest(owner, kind='delivery', scope=job['scope'], exclude=ident)
            if previous and previous['meta'].get('sendId'):
                sent = self.store.job(previous['meta']['sendId'], owner)
                if sent['state'] in ('failed', 'partial') and self.store.job(sent['preview_id'],owner)['meta'].get('validationPolicy')==VALIDATION_POLICY:
                    preview = self.store.job(sent['preview_id'], owner)
                    if self.store.retry(sent['id'], owner):
                        self.store.set_job(ident, 'running', meta={'phase':'send','previewId':preview['id'],'sendId':sent['id']})
                        # run_send performs the mandatory freshness check once.
                        self.run_send(sent['id'], owner)
                        completed = self.store.job(sent['id'], owner)
                        if completed['error'] != 'aifootballx_source_changed':
                            blocked = preview['meta']['report'].get('blocked_groups',[])
                            state = 'partial' if blocked and completed['state']=='complete' else completed['state']
                            self.store.set_job(ident, state, meta={'phase':'send','previewId':preview['id'],'sendId':sent['id']},
                                error=completed['error'] or ('aifootballx_aggregates_blocked' if blocked else None))
                            return
            preview_id, _ = self.store.create('preview', job['scope'], owner, public=False)
            self.store.set_job(ident, 'running', meta={'phase':'preview','previewId':preview_id})
            self.run_preview(preview_id, owner)
            preview = self.store.job(preview_id, owner)
            if preview['state'] != 'validated':
                self.store.set_job(ident, 'invalid' if preview['state']=='invalid' else 'failed',
                                   meta={'phase':'preview','previewId':preview_id}, error=preview['error'] or 'aifootballx_validation_failed')
                return
            send_id, _ = self.store.create('send', job['scope'], owner, preview_id=preview_id, public=False)
            self.store.set_job(ident, 'running', meta={'phase':'send','previewId':preview_id,'sendId':send_id})
            self.run_send(send_id, owner)
            completed = self.store.job(send_id, owner)
            blocked = preview['meta']['report'].get('blocked_groups',[])
            state = 'partial' if blocked and completed['state']=='complete' else completed['state']
            self.store.set_job(ident, state, meta={'phase':'send','previewId':preview_id,'sendId':send_id},
                               error=completed['error'] or ('aifootballx_aggregates_blocked' if blocked else None))
        except Exception as exc:
            self.store.fail_job(ident, exc)

    def public_status(self, owner):
        # IDs remain internal. The same selector inputs identify a submission;
        # GET shows the caller's latest operation like the existing pipelines.
        latest = self.store.latest(owner)
        enabled = get_settings().aifootballx_write_enabled
        if latest is None:
            return {'status':'idle','percent':0,'writeEnabled':enabled,'scope':None,'reason':None,'failures':[]}
        preview_id = latest['id'] if latest['kind']=='preview' else latest['meta'].get('previewId')
        send_id = latest['meta'].get('sendId') if latest['kind']=='delivery' else None
        preview = self.store.job(preview_id, owner) if preview_id else None
        sent = self.store.job(send_id, owner) if send_id else None
        report = preview['meta'].get('report', {}) if preview else {}
        reasons = list(report.get('errors', []))
        reasons.extend(report.get('warnings',[]))
        failed_checks = [c for c in report.get('checks', []) if not c['passed']]
        reasons.extend(f'{c.get("matchId", "")}: {c["name"]}' for c in failed_checks)
        for job in (latest, preview, sent):
            if job and job['meta'].get('failureReason'):
                reasons.append(job['meta']['failureReason'])
        failures = []
        if sent:
            for row in self.store.page(sent, 0, 100, 'failed'):
                failures.append({'collection':row['collection'],'documentId':row['document_id'],
                                 'code':row['error'],'reason':failure_reason(row['error']),'attempts':row['attempts']})
        if not reasons and failures:
            reasons.extend(f['reason'] for f in failures)
        state = {'invalid':'fail','failed':'error','partial':'fail','validated':'ready'}.get(latest['state'],latest['state'])
        values = []
        if preview:
            values = [{'collection':d['collection'],'documentId':d['document_id'],'data':d['data']}
                      for d in self.store.page(preview,0,20)]
        counts=self.store.counts(send_id) if send_id else {}
        total=sum(counts.values())
        preview_percent = (100 if preview and preview['state'] == 'validated' else
                           preview['meta'].get('progress', {}).get('percent', 0) if preview else 0)
        percent = preview_percent
        if latest['kind'] == 'delivery':
            percent = int(preview_percent / 2)
            if sent:
                send_percent = sent['meta'].get('progress', {}).get('percent', 0)
                processed = total - counts.get('pending', 0)
                if total and (send_percent >= 10 or processed):
                    send_percent = max(send_percent, 10 + 90 * processed / total)
                percent = min(99, 50 + int(send_percent / 2))
        percent = min(99, percent)
        if latest['kind'] == 'delivery':
            percent = max(percent, latest['meta'].get('progress', {}).get('percent', 0))
            if latest['state'] in ('queued', 'running'):
                # A retry may need a new preview; retain this operation's high-water mark.
                self.store.record_progress(latest['id'], percent)
        if latest['state'] in ('complete', 'validated'):
            percent = 100
        return {'status':state,'operation':'send' if latest['kind']=='delivery' else 'preview',
                'mode':delivery_mode(latest['scope']),
                'phase':latest['meta'].get('phase', 'preview'),'percent':percent,'writeEnabled':enabled,'scope':latest['scope'],
                'validationPassed':report.get('validation_passed'),'summary':report.get('summary'),
                'aggregateStatus':report.get('aggregate_status'),'blockedGroups':report.get('blocked_groups',[]),
                'warnings':report.get('warnings',[]),
                'counts':counts,
                'reason':'; '.join(dict.fromkeys(reasons)) if reasons else (failure_reason(latest['error']) if latest['error'] else None),
                'failures':failures,'failedChecks':failed_checks[:100],'previewValues':values,
                'waivedChecks':[c for c in report.get('checks',[]) if c.get('waived')],
                'previewValuesNote':'최대 20개 생성값을 표시합니다. 전체 값과 상세 이력은 서버의 작업 저장소에 보관됩니다.'}

    def status(self, ident, owner, offset=0, limit=50, state=None):
        job = self.store.job(ident, owner)
        report = job['meta'].get('report', {})
        return {'id': job['id'], 'kind': job['kind'], 'state': job['state'], 'scope': job['scope'],
                'previewId': job['preview_id'], 'error': job['error'], 'counts': self.store.counts(ident),
                'validationPassed': report.get('validation_passed'), 'summary': report.get('summary'),
                'newSeason': report.get('new_season'), 'errors': report.get('errors', [])[:100],
                'failedChecks': [c for c in report.get('checks', []) if not c['passed']][:100],
                'offset': offset, 'limit': limit, 'documents': self.store.page(job, offset, limit, state)}


def error_code(exc):
    # Do not expose SDK errors containing credentials, tokens, filesystem paths.
    return exc.code if isinstance(exc, BackendError) else 'aifootballx_' + type(exc).__name__


def failure_reason(code):
    return {'aifootballx_source_changed':'미리보기 후 DID 원천 또는 코드·슬롯 설정이 바뀌었습니다. 같은 입력으로 다시 검증해야 합니다.',
            'aifootballx_destination_changed':'대상 문서의 생성 필드가 다른 작업에서 변경됐습니다.',
            'aifootballx_validation_failed':'필수 데이터 검증에 실패했습니다. 아래 검증 사유를 확인하세요.',
            'aifootballx_write_disabled':'현재 실제 Firestore 전송이 비활성화돼 있습니다.',
            'aifootballx_aggregates_blocked':'선수·감독 등 경기 외 데이터 전송은 완료됐으며 시즌 집계는 검증 실패로 보류됐습니다.',
            'aifootballx_PermissionDenied':'대상 Firestore 서비스 계정 권한이 부족합니다.'}.get(code, f'처리 오류 ({code})')


_service = None
_service_lock = Lock()


def delivery_service():
    global _service
    with _service_lock:
        if _service is None:
            _service = DeliveryService()
    return _service


def close_delivery_service():
    global _service
    with _service_lock:
        if _service is not None:
            _service.close()
            _service = None
