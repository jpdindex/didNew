"""AIfootballX regression suite. Run: python -m backend.check.check_aifootballx."""

import unittest
import pytest
import json
from backend.build.build_aifootballx import (
    bind_existing_documents,
    compare,
    read_sql,
    validate_sql_facts,
    validate_rating,
    encode,
    match_target_scope,
    validate_aggregate_links,
    build_details,
    TEAM_COLUMNS,
    field_rule,
    type_compatible,
    rating_input,
    PLAYER_COLUMNS,
)
from backend.system.system_aifootballx import (
    numeric_player_id,
    resolve_team_code,
    season_label,
    tactic_projection,
    tactic_record_ids,
    build_preview,
    bucket,
    dotted_date,
    grade,
    history,
    intervals,
    manager_projection,
    pitch_coordinate,
    player_game_type,
    select_contract,
    source_records,
    rating_display,
    truncate_rating,
    PLAYER_MST_KPI,
    LEGACY_PREDICTION_BASELINES,
    build_aggregates,
    rounded,
    JobStore,
    DeliveryService,
    decide_merge,
    validate_document,
    FirestoreWriter,
    plan_new_season,
    season_player,
)
from copy import deepcopy
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.system import system_aifootballx as delivery
from backend.system.system_firestore import BackendError, CurrentUser, JpdDidData, resolve_contract_duplicates
from backend.api import api_aifootballx
from backend.system.system_kpi_rules import (
    calculate_kpis,
    KpiRecord,
    calculate_team_kpi_5min,
    reviewed_records,
    approved_path_difference,
    rating_two_decimals,
    APPROVED_RAW_EXCLUSIONS,
    approved_apx_difference,
)
from datetime import datetime, timezone
from backend.build.build_match_kpis import _team_differences, BuildMatchKpis
from backend.system.system_schema import RecordingKpi
from backend.build.build_match_rating import BuildMatchRating

# ==========================================================================
# aggregates
# ==========================================================================


def aggregate_fixture():
    items, codes, slots = [], [], {}
    for number in range(1, 21):
        team = f'T{number:02}'
        codes.append({'data': {'TCODE': team, 'PlSeason': '2024-2025', 'IDX_NAME': f'IDX{number}',
                               'No': number * 100, 'Team Code': number, 'POSITION': 21 - number}})
        slots[str(number)] = {'codeTeamCode': number}
    for n in range(1, 11):
        match = {'seasonId': '20242025', 'leagueId': 'EPL', 'round': 1, 'date': '2024.08.12',
                 'homeTeamId': f'T{n * 2 - 1:02}', 'awayTeamId': f'T{n * 2:02}', 'score': {'home': 1, 'away': 0}}
        for side, number in [('H', n * 2 - 1), ('A', n * 2)]:
            goals = 1 if side == 'H' else 0
            kpi = {'TAP': 50, 'DAP': 40, 'TTP': 5, 'DTP': 2, 'BAP': 4, 'DTB': 2, 'DTM': 1,
                   'DTA': 2, 'DTS': 3, 'SHOT': 3, 'ASR': .75, 'SSR': goals / 3, 'GOAL': goals, 'OG': 0}
            items.append({'match_id': f'20242024fixture{n:04}', 'match': deepcopy(match), 'side': side,
                          'recording': {'status': 'final', 'teamId': f'T{number:02}', 'kpi': kpi,
                                        'kpiVersion': 1, 'ratingBasedOn': 1, 'teamRating': {'jmx': 50 + number}}})
    return items, codes, {'seasonSlotMappings': {'2024-2025': {'cavgSlotToCodeTeamCodeMapping': slots}}}


def docs(result, collection):
    return [d['data'] for d in result['documents'] if d['collection'] == collection]


class AggregateTests(unittest.TestCase):
    def test_sql_reference_does_not_depend_on_old_firestore_values(self):
        items, codes, slots = aggregate_fixture()
        result = build_aggregates(items, codes, slots)
        report = validate_sql_facts(result, items, codes, slots)
        self.assertTrue(report['passed'], report['failures'])
        self.assertEqual(set(report['collections']), {'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank'})
        self.assertEqual(report['ratings']['status'], 'skipped')
        # Missing current ratings cannot be counted as SQL value validation.
        for item in items:
            item['recording']['teamRating'] = None
        without = validate_sql_facts(build_aggregates(items, codes, slots), items, codes, slots)
        self.assertTrue(without['passed'])
        self.assertEqual(without['checked'], report['checked'])

    def test_sql_reference_detects_corruption_and_missing_document(self):
        items, codes, slots = aggregate_fixture()
        result = build_aggregates(items, codes, slots)
        docs(result, 'pMST')[0]['DAP'] += 1
        report = validate_sql_facts(result, items, codes, slots)
        self.assertFalse(report['passed'])
        self.assertTrue(any(f['field'] == 'DAP' for f in report['failures']))
        result['documents'] = [d for d in result['documents'] if d['collection'] != 'sAVG']
        report = validate_sql_facts(result, items, codes, slots)
        self.assertFalse(report['passed'])
        self.assertTrue(report['collections']['sAVG']['failed'])

    def test_current_mode_excludes_unconfirmed_historical_fields(self):
        result = build_aggregates(*aggregate_fixture())
        rank = docs(result, 'rank')[0]
        self.assertEqual(result['calculation_mode'], 'current')
        for field in ('WGR20', 'Season', 'S_Avg', 'R_Rate'):
            self.assertNotIn(field, rank)
        with self.assertRaisesRegex(ValueError, 'legacy-audit'):
            build_aggregates(*aggregate_fixture(), prediction_index_baselines=LEGACY_PREDICTION_BASELINES)

    def test_current_points_rank_uses_goal_difference_then_goals_then_idx(self):
        items, codes, slots = aggregate_fixture()
        # Both teams win with GD=2; T03 scores more and must rank above T01.
        for item in items[:2]:
            item['match']['score'] = {'home': 2, 'away': 0}
        for item in items[2:4]:
            item['match']['score'] = {'home': 3, 'away': 1}
        rank = docs(build_aggregates(items, codes, slots), 'rank')[0]
        self.assertEqual(rank['WRK18'], 1)  # T03
        self.assertEqual(rank['WRK20'], 2)  # T01
        self.assertEqual(len({rank[f'WRK{n}'] for n in range(1, 21)}), 20)

    def test_current_jmx_tie_uses_previous_played_round_growth(self):
        items, codes, slots = aggregate_fixture()
        second = deepcopy(items)
        for item in second:
            item['match_id'] += '-r2'
            item['match']['round'] = 2
            item['recording']['teamRating']['jmx'] = 70
        # T02 grows from 40 to 70, T01 from 60 to 70.
        items[0]['recording']['teamRating']['jmx'] = 60
        items[1]['recording']['teamRating']['jmx'] = 40
        ranks = docs(build_aggregates(items + second, codes, slots), 'rank')
        self.assertEqual(ranks[1]['RRK19'], 1)
        self.assertGreater(ranks[1]['RRK20'], ranks[1]['RRK19'])
        self.assertEqual(len({ranks[1][f'RRK{n}'] for n in range(1, 21)}), 20)

    def test_missing_previous_rating_blocks_current_jmx_rank(self):
        items, codes, slots = aggregate_fixture()
        second = deepcopy(items)
        items[0]['recording']['teamRating'] = None
        for item in second:
            item['match_id'] += '-r2'
            item['match']['round'] = 2
        rank = docs(build_aggregates(items + second, codes, slots), 'rank')[1]
        self.assertNotIn('RRK20', rank)

    def test_current_round_gap_does_not_skip_an_absent_round(self):
        items, codes, slots = aggregate_fixture()
        third = deepcopy(items)
        for item in third:
            item['match_id'] += '-r3'
            item['match']['round'] = 3
        rank = docs(build_aggregates(items + third, codes, slots), 'rank')[1]
        self.assertNotIn('R_Gap', rank)

    def test_all_six_collections(self):
        result = build_aggregates(*aggregate_fixture(), prediction_index_baselines=LEGACY_PREDICTION_BASELINES, calculation_mode='legacy-audit')
        self.assertEqual(result['summary']['collections'], {'pMST': 20, 'mMST': 10, 'cAVG': 1, 'rAVG': 1, 'rank': 1, 'sAVG': 1})
        self.assertFalse(result['write_enabled'])
        self.assertFalse(result['complete'])  # Forecast inputs/AVG definition remain absent.
        self.assertEqual(result['errors'], [])

    def test_rank_position_is_not_cavg_slot_or_team_code(self):
        result = build_aggregates(*aggregate_fixture())
        self.assertEqual(docs(result, 'rank')[0]['RAV20'], 51)
        self.assertEqual(docs(result, 'cAVG')[0]['C1-AVG'], 51)
        self.assertEqual(docs(result, 'rank')[0]['WTT20'], 3)

    def test_competition_ties_and_grade(self):
        result = build_aggregates(*aggregate_fixture(), calculation_mode='legacy-audit')
        rank = docs(result, 'rank')[0]
        self.assertEqual(rank['WRK20'], 1)
        self.assertEqual(rank['WRK19'], 11)
        self.assertEqual(rank['WGR19'], 3)

    def test_cumulative_points_and_legacy_gap_rate_denominator(self):
        items, codes, slots = aggregate_fixture()
        second = deepcopy(items)
        for item in second:
            item['match_id'] += '-r2'
            item['match']['round'] = 2
            item['recording']['teamRating']['jmx'] += 10
        result = build_aggregates(items + second, codes, slots, calculation_mode='legacy-audit')
        ranks = docs(result, 'rank')
        self.assertEqual(ranks[1]['WTT20'], 6)
        self.assertEqual(ranks[1]['R_Gap'], 10)
        self.assertEqual(ranks[1]['R_Rate'], .142)  # 10/70.5, not 10/60.5.

    def test_no_legacy_score_fallback(self):
        items, codes, slots = aggregate_fixture()
        for item in items:
            item['recording']['teamRating'] = None
            item['source_evidence'] = {'legacy_gi_score': 99.9}
        result = build_aggregates(items, codes, slots)
        self.assertNotIn('H-FX', docs(result, 'mMST')[0])
        self.assertNotIn('gi_score', docs(result, 'pMST')[0])
        self.assertNotIn('C1-AVG', docs(result, 'cAVG')[0])
        self.assertNotIn('AI Rating', docs(result, 'sAVG')[0])

    def test_prediction_indices_are_opt_in_and_decimal_safe(self):
        items, codes, slots = aggregate_fixture()
        items[0]['recording']['kpi']['DAP'] = 201
        without = build_aggregates(items, codes, slots)
        self.assertNotIn('I-DAP', docs(without, 'pMST')[0])
        with_policy = build_aggregates(items, codes, slots, prediction_index_baselines=LEGACY_PREDICTION_BASELINES, calculation_mode='legacy-audit')
        self.assertEqual(docs(with_policy, 'pMST')[0]['I-DAP'], 50.3)
        self.assertEqual(rounded(Decimal('50.25')), 50.3)

    def test_same_season_avg_and_no_unused_forecasts(self):
        result = build_aggregates(*aggregate_fixture())
        self.assertNotIn('PH-DAP', docs(result, 'pMST')[0])
        self.assertEqual(docs(result, 'sAVG')[0]['AVG'], docs(result, 'sAVG')[0]['AI Rating'])
        self.assertEqual(docs(result, 'sAVG')[0]['AVG'], 60.5)
        self.assertNotIn('prediction', result['summary']['collections'])
        self.assertTrue(result['complete'], result['blocked'])

    def test_explicit_forecast_inputs(self):
        items, codes, slots = aggregate_fixture()
        forecast = {(items[0]['match_id'], 'H'): {'PH-DAP': 120}}
        result = build_aggregates(items, codes, slots, prediction_inputs=forecast, calculation_mode='legacy-audit')
        self.assertEqual(docs(result, 'pMST')[0]['PH-DAP'], 120)

    def test_unused_predictions_are_not_reported_as_missing(self):
        result = build_aggregates(*aggregate_fixture())
        self.assertFalse(any(b['collection'] == 'pMST' for b in result['blocked']))
        self.assertIn('prediction', result['excluded']['unused'])
        self.assertIn('rank.WGR*', result['excluded']['unused'])
        with self.assertRaisesRegex(ValueError, 'disabled'):
            build_aggregates(*aggregate_fixture(), prediction_inputs={})

    def test_invalid_own_goal_total_does_not_generate_negative_ssr(self):
        items, codes, slots = aggregate_fixture()
        items[1]['recording']['kpi'].update(GOAL=0, OG=1)
        result = build_aggregates(items, codes, slots, prediction_index_baselines=LEGACY_PREDICTION_BASELINES, calculation_mode='legacy-audit')
        row = next(d for d in docs(result, 'pMST') if d['t_code'] == 'T02')
        self.assertNotIn('I-SSR', row)

    def test_incomplete_round_does_not_claim_league_rank(self):
        items, codes, slots = aggregate_fixture()
        result = build_aggregates(items[:2], codes, slots)
        self.assertNotIn('WRK20', docs(result, 'rank')[0])
        self.assertNotIn('M-AVG', docs(result, 'rAVG')[0])
        self.assertTrue(result['blocked'])

    def test_missing_previous_round_does_not_claim_season_cumulative_values(self):
        items, codes, slots = aggregate_fixture()
        for item in items:
            item['match']['round'] = 2
        result = build_aggregates(items, codes, slots)
        self.assertNotIn('WTT20', docs(result, 'rank')[0])
        self.assertNotIn('WRK20', docs(result, 'rank')[0])
        self.assertNotIn('S_AVG', docs(result, 'rAVG')[0])

    def test_rescheduling_does_not_change_order_ids_or_mutate_input(self):
        items, codes, slots = aggregate_fixture()
        prior = deepcopy(items)
        before = build_aggregates(items, codes, slots)
        self.assertEqual(prior, items)
        for item in items:
            item['match']['date'] = '2025.05.31'
        after = build_aggregates(items, codes, slots)
        self.assertEqual([d['document_id'] for d in before['documents']], [d['document_id'] for d in after['documents']])

    def test_stale_rating_is_rejected(self):
        items, codes, slots = aggregate_fixture()
        items[0]['recording']['ratingBasedOn'] = 0
        result = build_aggregates(items, codes, slots)
        self.assertTrue(any('Stale rating' in e for e in result['errors']))

    def test_invalid_round_and_nonfinite_kpi_are_reported(self):
        items, codes, slots = aggregate_fixture()
        items[0]['match']['round'] = 0
        items[1]['recording']['kpi']['DAP'] = float('nan')
        result = build_aggregates(items, codes, slots)
        self.assertTrue(any('Round' in e for e in result['errors']))
        self.assertTrue(any('KPI' in e for e in result['errors']))

    def test_duplicate_side_and_missing_slots_are_reported(self):
        items, codes, slots = aggregate_fixture()
        result = build_aggregates(items + [items[0]], codes, {})
        self.assertTrue(result['errors'])
        self.assertTrue(any('slot' in str(b) for b in result['blocked']))

    def test_missing_fields_are_not_counted_as_equal(self):
        generated = {'documents': [{'collection': 'sAVG', 'data': {'SEASON': '24/25', 'TAP': 50}}],
                     'blocked': ['AVG missing'], 'errors': []}
        target = {'sAVG': [{'id': 'old', 'data': {'SEASON': '24/25', 'TAP': 50, 'AVG': 80}}]}
        result = compare(generated, target)
        self.assertEqual(result['collections']['sAVG']['omitted_field_counts'], {'AVG': 1})
        self.assertFalse(result['complete'])

    def test_binding_preserves_existing_random_id_and_uid(self):
        result = build_aggregates(*aggregate_fixture())
        target = {'sAVG': [{'id': 'legacy-random-id', 'data': {'SEASON': '24/25', 'uid': 'original-uid'}}]}
        bind_existing_documents(result, target)
        doc = next(d for d in result['documents'] if d['collection'] == 'sAVG')
        self.assertEqual(doc['document_id'], 'legacy-random-id')
        self.assertEqual(doc['data']['uid'], 'original-uid')
        self.assertEqual(doc['action'], 'replace-existing')

    def test_sql_is_parsed_as_data_and_rejects_wrong_season(self):
        sql = ("INSERT INTO `ff_game` (`gm_id`) VALUES ('wrong-season');\n"
               "INSERT INTO `ff_game_info` (`gm_id`, `gi_id`) VALUES ('wrong-season', 'id');\n")
        with patch.object(Path, 'open', return_value=StringIO(sql)):
            with self.assertRaisesRegex(ValueError, 'season'):
                read_sql(Path('input.sql'), '2024-2025')


# ==========================================================================
# delivery
# ==========================================================================


def document(ident='one'):
    return {'collection': 'Manager', 'document_id': ident, 'data': {'m_id': 12, 'uid': ident},
            'baseline': {'m_id': {'exists': False, 'value': None}, 'uid': {'exists': False, 'value': None}}, 'existed': False}


class Immediate:
    def submit(self, fn, *args):
        fn(*args)


class Source:
    stale = False
    passed = True

    def capture(self, scope, slot_path):
        return {'documents': [document('one'), document('two')], 'validation_passed': self.passed,
                'summary': {'documents': 2, 'matches': 1, 'failed_checks': 0}, 'errors': [], 'checks': []}, {'documents': {}, 'queries': []}

    def assert_fresh(self, guards):
        if self.stale:
            raise BackendError('changed', 409, 'aifootballx_source_changed')


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'get_settings', lambda: SimpleNamespace(aifootballx_write_enabled=True,
        aifootballx_slot_mapping_path=None, app_env='test'))
    source = Source()
    calls = []
    class Writer:
        fail_two = True
        def send(self, doc, preview):
            calls.append(doc['document_id'])
            if doc['document_id'] == 'two' and self.fail_two:
                raise RuntimeError('simulated network error containing a secret')
            return 'success'
    writer = Writer()
    svc = DeliveryService(JobStore(tmp_path), lambda: source, lambda: writer)
    svc.pool.shutdown()
    svc.pool = Immediate()
    return svc, source, writer, calls


def test_persistent_idempotency_and_retry_only_failed_documents(service):
    svc, _, writer, calls = service
    preview = svc.preview({'seasonId': '20242025'}, 'operator')
    sent = svc.send(preview, 'operator', 'unique-key')
    assert svc.status(sent, 'operator')['state'] == 'partial'
    assert svc.status(sent, 'operator')['counts'] == {'failed': 1, 'success': 1}
    assert svc.send(preview, 'operator', 'unique-key') == sent
    assert calls == ['one', 'two']
    writer.fail_two = False
    svc.retry(sent, 'operator')
    assert calls == ['one', 'two', 'two']
    assert svc.status(sent, 'operator')['state'] == 'complete'
    assert svc.store.job(sent, 'operator')['state'] == JobStore(svc.store.root).job(sent, 'operator')['state']
    assert svc.status(sent, 'operator', state='success')['documents'][1]['attempts'] == 2
    assert 'secret' not in str(svc.status(sent, 'operator'))


def test_disabled_send_and_retry_never_construct_writer(service, monkeypatch):
    svc, _, _, calls = service
    preview = svc.preview({'seasonId': '20242025'}, 'operator')
    monkeypatch.setattr(delivery, 'get_settings', lambda: SimpleNamespace(aifootballx_write_enabled=False))
    with pytest.raises(BackendError, match='disabled'):
        svc.send(preview, 'operator', 'unique-key')
    with pytest.raises(BackendError, match='disabled'):
        svc.retry('missing', 'operator')
    assert calls == []


def test_invalid_preview_and_changed_source_cannot_send(service):
    svc, source, _, calls = service
    source.passed = False
    invalid = svc.preview({'seasonId': '20242025'}, 'operator')
    with pytest.raises(BackendError, match='validated'):
        svc.send(invalid, 'operator', 'invalid-key')
    source.passed = True
    valid = svc.preview({'seasonId': '20242025'}, 'operator')
    source.stale = True
    sent = svc.send(valid, 'operator', 'valid-key')
    assert svc.status(sent, 'operator')['state'] == 'failed'
    assert svc.status(sent, 'operator')['error'] == 'aifootballx_source_changed'
    assert calls == []


def test_keys_and_jobs_are_owner_scoped(service):
    svc, _, _, _ = service
    first = svc.preview({'seasonId': '20242025'}, 'operator')
    second = svc.preview({'seasonId': '20252026'}, 'operator')
    svc.send(first, 'operator', 'unique-key')
    with pytest.raises(BackendError, match='another preview'):
        svc.send(second, 'operator', 'unique-key')
    with pytest.raises(BackendError) as error:
        svc.status(first, 'other')
    assert error.value.status_code == 404


def test_full_replacement_rejects_concurrent_changes_including_legacy_fields():
    doc = document()
    doc['existed'] = True
    doc['baseline'] = {'m_id': {'exists': True, 'value': 10}, 'uid': {'exists': True, 'value': 'one'}}
    current = {'m_id': 10, 'uid': 'one', 'Coach': 'manual text', 'Captain': 15}
    doc['baseline'] = delivery.replacement_baseline(doc['data'], current)
    assert decide_merge(doc, current)
    assert current['Coach'] == 'manual text'
    assert not decide_merge(doc, doc['data'])
    with pytest.raises(BackendError, match='document changed'):
        decide_merge(doc, {**current, 'm_id': 13})
    bad = deepcopy(doc)
    bad['data']['Coach'] = 'overwrite'
    with pytest.raises(BackendError, match='Unowned'):
        validate_document(bad)


def test_atomic_receipt_prevents_duplicate_merge_after_lost_ack(monkeypatch):
    from firebase_admin import firestore
    monkeypatch.setattr(firestore, 'transactional', lambda fn: fn)
    data, writes = {}, []
    class Ref:
        def __init__(self, path): self.path = path
        def document(self, ident): return Ref(self.path + '/' + ident)
        def get(self, transaction):
            return SimpleNamespace(exists=self.path in data, to_dict=lambda: data.get(self.path))
    class Transaction:
        def set(self, ref, value, merge=False):
            writes.append(ref.path)
            data[ref.path] = {**data.get(ref.path, {}), **value} if merge else value
    client = SimpleNamespace(project='aifootballx', collection=lambda name: Ref(name), transaction=Transaction)
    writer = FirestoreWriter(client)
    assert writer.send(document(), 'preview1') == 'success'
    assert writer.send(document(), 'preview1') == 'skipped'
    assert writes.count('Manager/one') == 1
    assert len(writes) == 2  # payload + receipt, both inside the transaction
    changed = document()
    changed['data']['m_id'] = 13
    changed['existed'] = True
    changed['baseline'] = {'m_id': {'exists': True, 'value': 12}, 'uid': {'exists': True, 'value': 'one'}}
    assert writer.send(changed, 'preview2') == 'success'
    restored = document()
    restored['existed'] = True
    restored['baseline'] = {'m_id': {'exists': True, 'value': 13}, 'uid': {'exists': True, 'value': 'one'}}
    # The same payload in a later source snapshot may be a legitimate correction.
    assert writer.send(restored, 'preview3') == 'success'
    assert data['Manager/one']['m_id'] == 12


def test_new_season_setup_is_deterministic_and_never_changes_established_season():
    items, codes, _ = aggregate_fixture()
    matches = {i['match_id']: {'id': i['match_id'], 'data': i['match']} for i in items}
    for row in codes: row['data']['PlSeason'] = None
    plan = plan_new_season('2024-2025', list(matches.values()), codes, {}, {'seasonSlotMappings': {}})
    assert plan['new_season']
    assert len(plan['documents']) == 20
    assert plan == plan_new_season('2024-2025', list(reversed(matches.values())), codes, {}, {'seasonSlotMappings': {}})
    old = plan_new_season('2024-2025', list(matches.values()), codes, {'rank': [{'id': 'old'}]}, {'seasonSlotMappings': {}})
    assert not old['new_season']
    # The new season's slot choices survive a backend restart/new preview.
    saved = plan_new_season('2024-2025', list(matches.values()), codes + plan['code_rows'],
                            {'rank': [{'id': 'created'}]}, {'seasonSlotMappings': {}})
    assert saved['slots'] == plan['slots']


def test_season_player_master_uses_latest_season_contract_for_every_scope():
    items, codes, _ = aggregate_fixture()
    all_matches = [{'id': 'first', 'data': {**items[0]['match'], 'date': '2024.08.12'}},
                   {'id': 'last', 'data': {**items[0]['match'], 'date': '2025.05.20'}}]
    evidence = {'ai_samples': {'code': codes}, 'player_contracts': {'0001': [
        {'teamId':'T01','leagueId':'EPL','from':'2024-07-01','to':'2024-12-31','no':'1','pos':'MF'},
        {'teamId':'T02','leagueId':'EPL','from':'2025-01-01','to':None,'no':'2','pos':'FW'}]}}
    result = season_player({'p_id':1,'t_code':'T01','p_name':'name'}, 'Player', evidence, all_matches)
    assert result['t_code'] == 'T02'
    assert result['pt_num'] == 2
    evidence['player_contracts']['0001'].append({'teamId':'T01','leagueId':'EPL','from':'2025-01-01','to':None,'no':'3','pos':'FW'})
    with pytest.raises(BackendError, match='Ambiguous'):
        season_player({'p_id':1},'Player',evidence,all_matches)


def test_batch_writer_keeps_receipts_atomic_and_preflights_conflicts(monkeypatch):
    from firebase_admin import firestore
    monkeypatch.setattr(firestore, 'transactional', lambda fn: fn)
    data, writes = {}, []
    class Ref:
        def __init__(self, path): self.path = path
        def document(self, ident): return Ref(self.path + '/' + ident)
    class Transaction:
        def set(self, ref, value, merge=False):
            writes.append(ref.path)
            data[ref.path] = {**data.get(ref.path,{}),**value} if merge else value
    def get_all(refs, **kwargs):
        return [SimpleNamespace(reference=ref,exists=ref.path in data,to_dict=lambda p=ref.path:data.get(p)) for ref in refs]
    client = SimpleNamespace(project='aifootballx',collection=lambda n:Ref(n),transaction=Transaction,get_all=get_all)
    writer = FirestoreWriter(client)
    assert writer.send_many([document('one'),document('two')],'preview') == ['success','success']
    assert writer.send_many([document('one'),document('two')],'preview') == ['skipped','skipped']
    assert len(writes) == 4
    data['Manager/conflict']={'m_id':999}
    with pytest.raises(BackendError):
        writer.send_many([document('fresh'),document('conflict')],'next')
    assert 'Manager/fresh' not in data


def test_query_membership_guard_catches_new_source_children():
    from backend.system.system_aifootballx import SourceCapture
    source = SourceCapture(SimpleNamespace(project='jpd-did'), SimpleNamespace(project='aifootballx'))
    snapshot = SimpleNamespace(id='new', exists=True, update_time=SimpleNamespace(isoformat=lambda:'v2'))
    source.query = lambda *args: SimpleNamespace(limit=lambda _:SimpleNamespace(stream=lambda **kw:[snapshot]))
    with pytest.raises(BackendError, match='source collection changed'):
        source.assert_fresh({'documents':{},'queries':[{'path':'records','filters':[],'bound':20,'versions':{}}]})


def test_whole_season_preview_streams_chunks_and_deduplicates_shared_documents(service):
    svc, source, _, _ = service
    source.split_scopes = lambda scope: [scope, scope]
    ident = svc.preview({'seasonId':'20242025'},'operator')
    result = svc.status(ident,'operator')
    assert result['state'] == 'validated'
    assert result['summary']['matches'] == 2
    assert result['summary']['documents'] == 2
    assert len(result['documents']) == 2


def test_preview_chunk_merge_rejects_source_version_change():
    from backend.system.system_aifootballx import merge_guards
    first={'documents':{'players/one':'v1'},'queries':[]}
    assert merge_guards(first,first) == first
    with pytest.raises(BackendError, match='source changed'):
        merge_guards(first,{'documents':{'players/one':'v2'},'queries':[]})


def test_preview_chunks_accept_unchanged_queries_after_json_storage(service):
    svc, source, _, _ = service
    source.split_scopes = lambda scope: [scope, scope]
    original_capture = source.capture
    def capture(scope, slot_path):
        report, guards = original_capture(scope, slot_path)
        guards['queries'] = [{'path': 'matches', 'filters': [('seasonId', '==', '20232024')],
                              'bound': 2000, 'versions': {'match-one': 'v1'}}]
        return report, guards
    source.capture = capture
    ident = svc.preview({'seasonId': '20232024'}, 'operator')
    assert svc.status(ident, 'operator')['state'] == 'validated'


@pytest.mark.parametrize('change', ['update', 'add', 'remove'])
def test_preview_chunk_merge_still_rejects_query_changes_after_json_storage(change):
    query = {'path': 'matches', 'filters': [('seasonId', '==', '20232024')],
             'bound': 2000, 'versions': {'match-one': 'v1'}}
    previous = json.loads(json.dumps({'documents': {}, 'queries': [query]}))
    if change == 'update':
        query['versions']['match-one'] = 'v2'
    elif change == 'add':
        query['versions']['match-two'] = 'v1'
    else:
        query['versions'].clear()
    with pytest.raises(BackendError, match='source collection changed'):
        delivery.merge_guards(previous, {'documents': {}, 'queries': [query]})


def test_send_by_inputs_runs_async_validation_and_retries_only_failures(service):
    svc, source, writer, calls=service
    scope={'seasonId':'20242025','roundFrom':1,'roundTo':1,'matchId':None}
    svc.send_scope(scope,'operator')
    status=svc.public_status('operator')
    assert status['status']=='fail' and status['reason']
    assert status['failures'][0]['documentId']=='two'
    assert 'jobId' not in status and 'previewId' not in status
    writer.fail_two=False
    svc.send_scope(scope,'operator')
    assert svc.public_status('operator')['status']=='complete'
    assert calls==['one','two','two']


def test_invalid_scope_send_reports_validation_reasons_without_writes(service):
    svc,source,_,calls=service
    source.passed=False
    svc.send_scope({'seasonId':'20242025'},'operator')
    status=svc.public_status('operator')
    assert status['status']=='fail'
    assert status['reason']
    assert calls==[]


def test_old_validation_policy_cannot_publish_cached_preview(service):
    svc,_,_,calls=service
    ident=svc.preview({'seasonId':'20242025'},'operator')
    job=svc.store.job(ident,'operator'); job['meta']['validationPolicy']='old-partial-policy'
    svc.store.set_job(ident,'validated',meta=job['meta'])
    sent=svc.send(ident,'operator','old-preview-key')
    assert svc.status(sent,'operator')['error']=='aifootballx_preview_outdated'
    assert not calls


def test_swagger_routes_validate_scope_and_require_operator(service, monkeypatch):
    svc, _, writer, _ = service
    writer.fail_two = False
    monkeypatch.setattr(api_aifootballx, 'delivery_service', lambda: svc)
    app = FastAPI()
    app.include_router(api_aifootballx.router, prefix='/api/v1')
    @app.exception_handler(BackendError)
    async def error(_, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error': exc.code}, status_code=exc.status_code)
    app.dependency_overrides[api_aifootballx.delivery_operator] = lambda: CurrentUser(uid='operator', claims={})
    client = TestClient(app)
    assert client.post('/api/v1/aifootballx/send/matches', data={'season':'2024-2025','round_from':5,'round_to':1}).status_code == 422
    assert client.post('/api/v1/aifootballx/send/matches', data={'season':'2024-2025','gm_id':'20252026x'}).status_code == 422
    result = client.post('/api/v1/aifootballx/send/matches', data={'season':'2024-2025','round_from':1,'round_to':1})
    assert result.status_code == 202
    assert 'jobId' not in result.json()
    assert result.json()['scope']['seasonId'] == '20242025'
    response=client.get('/api/v1/aifootballx/send/status').json()
    assert response['validationPassed'] and response['status']=='complete'
    paths=app.openapi()['paths']
    assert list(paths)==['/api/v1/aifootballx/send/status', '/api/v1/aifootballx/send/matches',
                         '/api/v1/aifootballx/send/masters', '/api/v1/aifootballx/send/aggregates', '/api/v1/aifootballx/send/all']
    assert not paths['/api/v1/aifootballx/send/status']['get'].get('parameters')
    for path in ('matches','masters','aggregates','all'):
        endpoint=paths['/api/v1/aifootballx/send/'+path]['post']
        assert 'application/x-www-form-urlencoded' in endpoint['requestBody']['content']
        assert not endpoint.get('parameters')
    with pytest.raises(BackendError) as rejected:
        api_aifootballx.delivery_operator(CurrentUser(uid='u', claims={'auth': 'local_frontend'}))
    assert rejected.value.status_code == 403


# ==========================================================================
# full_validation
# ==========================================================================


def rating_fixture():
    body={'gm_id':'m','side':'H','teamId':'T1','opponentTeamId':'T2','kpiVersion':1,
          'teamKpi':{'DAP':154,'DTP':7,'SHOT':10,'GOAL':1,'OG':0},'playerKpis':[]}
    result={k:body[k] for k in ('gm_id','side','teamId','opponentTeamId','kpiVersion')}
    result.update(ratingBasedOn=1,playerRatings=[],teamRating={'jmx':70.2867,'jmxSeries':[70.2867]*20,
                   'apx':38.5,'apxGrade':'F','tpx':58.3333,'tpxGrade':'D','fpx':50.0,'fpxGrade':'D'})
    return body,result


def test_current_rating_checks_identity_version_baselines_and_terminal_value():
    body,response=rating_fixture()
    assert all(c['passed'] for c in validate_rating(body,response))
    response['gm_id']='other'
    assert any(c['name']=='response-link-gm_id' and not c['passed'] for c in validate_rating(body,response))


def test_current_rating_rejects_visible_50_point_f_grade():
    body,response=rating_fixture()
    response['teamRating']['fpxGrade']='F'
    failed=[c['name'] for c in validate_rating(body,response) if not c['passed']]
    assert failed==['team-fpx-grade']


def test_current_rating_fpx_includes_own_goals_without_changing_shots():
    body,response=rating_fixture()
    body['teamKpi']['OG']=1
    failed=[c for c in validate_rating(body,response) if not c['passed']]
    assert not failed
    response['teamRating']['fpx']=33.3333
    assert any(c['name']=='team-fpx-NEW-baseline' for c in validate_rating(body,response) if not c['passed'])


def test_current_rating_cannot_omit_player_identities():
    body,response=rating_fixture()
    body['playerKpis']=[{'playerId':'0000000001'}]
    assert any(c['name']=='all-player-identities' and not c['passed'] for c in validate_rating(body,response))


# ==========================================================================
# preview
# ==========================================================================


def preview_fixture():
    match_id = '20232024fixture0001'
    match = {'seasonId': '20232024', 'leagueId': 'EPL', 'round': 1, 'date': '2023.08.12',
             'homeTeamId': 'E-H', 'awayTeamId': 'E-A', 'score': {'home': 1, 'away': 1}}
    evidence = {'source_recordings': [], 'did_matches': [{'id': match_id, 'data': match}],
                'ai_samples': {'code': []}, 'target_existing': {}, 'projects': {},
                'player_profiles': {}, 'player_contracts': {}}
    for side, team, no in [('H', 'E-H', 100), ('A', 'E-A', 200)]:
        rows = [{'id': 'pass', 'data': {'half': 'H1', 'halfSeconds': 299, 'seq': 1,
                                      'act': 'P', 'res': 'O', 'area': 8, 'posX': 100, 'posY': 0}},
                {'id': 'shot', 'data': {'half': 'H1', 'halfSeconds': 300, 'seq': 2,
                                      'act': 'S', 'res': 'GOAL', 'area': 2, 'posX': 0, 'posY': 100}}]
        item = {'match_id': match_id, 'match': deepcopy(match), 'side': side,
                'recording': {'teamId': team, 'status': 'final', 'kpiVersion': 1, 'ratingBasedOn': 1,
                              'halves': {'H1': {'seconds': 2900}}, 'lineup': {}},
                'children': {'records': rows, 'playerStats': []}}
        kpi = calculate_kpis(source_records(item)).team_kpi
        fpx = (min(kpi['SHOT'] / 15 * 100, 99.9) + min(kpi['GOAL'] / 3 * 100, 99.9)) / 2
        item['recording'].update(kpi=kpi, teamRating={'apx': kpi['DAP'] / 4, 'tpx': kpi['DTP'] / 12 * 100,
                                                     'fpx': fpx, 'jmx': 50, 'jmxSeries': [50] * 20})
        evidence['source_recordings'].append(item)
        evidence['ai_samples']['code'].append({'data': {'TCODE': team, 'PlSeason': None,
                                                       'IDX_NAME': side + 'X', 'Team Code': no // 100, 'No': no}})
    return evidence, match_id


def build(evidence, match_id):
    return build_preview(evidence, match_id, covers=JpdDidData._contract_covers_match_date)


class PreviewTests(unittest.TestCase):
    def test_half_open_intervals_and_stoppage(self):
        self.assertEqual([bucket('H1', t) for t in (0, 299, 300, 2699, 2700, 3600)], [0, 0, 1, 8, 9, 9])
        self.assertEqual(bucket('H2', 300), 11)
        with self.assertRaises(ValueError):
            bucket('H3', 0)

    def test_pitch_conversion_reverses_import(self):
        self.assertEqual(pitch_coordinate(100, 971), 971)
        self.assertEqual(pitch_coordinate(50, 634), 317)
        self.assertEqual(pitch_coordinate(928 / 971 * 100, 971), 928)
        self.assertEqual(pitch_coordinate(-4 / 971 * 100, 971), -4)
        with self.assertRaises(ValueError):
            pitch_coordinate(float('nan'), 971)

    def test_grade_thresholds(self):
        self.assertEqual([grade(n) for n in (0, 40, 49.9, 50, 60, 70, 80, 90, 99.9)],
                         ['F', 'F', 'F', 'D', 'C', 'B', 'A', 'A+', 'A+'])

    def test_destination_dates(self):
        self.assertEqual(dotted_date('2023-08-12'), '2023.08.12')
        self.assertEqual(dotted_date('0000.00.00'), '0000.00.00')
        with self.assertRaises(ValueError):
            dotted_date('unknown')

    def test_dtp_count_once_at_path_end_and_cumulative_ssr(self):
        evidence, _ = preview_fixture()
        records = source_records(evidence['source_recordings'][0])
        values = intervals(records, calculate_kpis(records))
        self.assertEqual(sum(values['DTP']), 1)
        self.assertEqual(values['DTP'][0:2], [0, 1])
        self.assertEqual(values['SSR'][0:3], [0, 1, 1])
        self.assertEqual(values['SSR'][10], 1)

        item = evidence['source_recordings'][0]
        item['children']['records'].append({'id': 'second-half-shot', 'data': {
            'half': 'H2', 'halfSeconds': 300, 'seq': 3, 'act': 'S', 'res': 'O', 'area': 2}})
        records = source_records(item)
        values = intervals(records, calculate_kpis(records))
        self.assertEqual(values['SSR'][10], 1)
        self.assertEqual(values['SSR'][11:], [.5] * 9)

    def test_team_goal_intervals_include_own_goals(self):
        evidence, _ = preview_fixture()
        item = evidence['source_recordings'][0]
        for row in item['children']['records']:
            row['data'].update(legacyPathId='one', legacyPathType='DTP', legacyPathTtp=True,
                               legacyKpiFlags={'goal': row['id'] == 'shot', 'shot': row['id'] == 'shot'})
        item['children']['records'][1]['data']['playerId'] = 'OWN'
        records = source_records(item)
        values = intervals(records, calculate_kpis(records))
        self.assertEqual(sum(values['GOAL']), 1)

    def test_complete_preview_and_exclusions(self):
        evidence, match_id = preview_fixture()
        report = build(evidence, match_id)
        self.assertTrue(report['validation_passed'], report['errors'])
        self.assertFalse(report['write_enabled'])
        self.assertEqual(report['summary']['collections']['TACTIC'], 4)
        dmst = [d['data'] for d in report['documents'] if d['collection'] == 'dMST']
        self.assertEqual([d['Match'] for d in dmst], [2, 1])
        self.assertTrue(all('BAP(AiBuildup)' not in d and not any(k.startswith('X-') for k in d) for d in dmst))

    def test_repeat_and_date_change_preserve_candidate_ids(self):
        evidence, match_id = preview_fixture()
        before = build(evidence, match_id)
        for item in evidence['source_recordings']:
            item['match']['date'] = '2023.12.31'
        after = build(evidence, match_id)
        self.assertEqual([(d['collection'], d['document_id']) for d in before['documents']],
                         [(d['collection'], d['document_id']) for d in after['documents']])

    def test_existing_uid_and_match_preserved(self):
        evidence, match_id = preview_fixture()
        before = build(evidence, match_id)
        old = next(d for d in before['documents'] if d['collection'] == 'dMST')
        old['data'].update(uid='original-uid', Match=20)
        evidence['target_existing']['dMST'] = [{'id': 'original-document', 'data': old['data']}]
        result = next(d for d in build(evidence, match_id)['documents']
                      if d['collection'] == 'dMST' and d['data']['t_code'] == old['data']['t_code'])
        self.assertEqual(result['document_id'], 'original-document')
        self.assertEqual(result['data']['uid'], 'original-uid')
        self.assertEqual(result['data']['Match'], 20)

    def test_ambiguous_existing_document_blocks(self):
        evidence, match_id = preview_fixture()
        old = next(d for d in build(evidence, match_id)['documents'] if d['collection'] == 'dMST')
        evidence['target_existing']['dMST'] = [{'id': str(n), 'data': old['data']} for n in range(2)]
        report = build(evidence, match_id)
        self.assertFalse(report['validation_passed'])
        self.assertTrue(any('ambiguous' in error for error in report['errors']))

    def test_stale_ratings_and_nonfinal_matches_block(self):
        for field, value in [('ratingBasedOn', 0), ('status', 'inputting')]:
            evidence, match_id = preview_fixture()
            evidence['source_recordings'][0]['recording'][field] = value
            self.assertFalse(build(evidence, match_id)['validation_passed'])

    def test_deleted_or_duplicate_sides_block(self):
        evidence, match_id = preview_fixture()
        evidence['source_recordings'] = [evidence['source_recordings'][0]] * 2
        self.assertFalse(build(evidence, match_id)['validation_passed'])

    def test_kpi_difference_blocks_without_rewriting_source(self):
        evidence, match_id = preview_fixture()
        evidence['source_recordings'][0]['recording']['kpi']['DTP'] += 1
        prior = deepcopy(evidence)
        result = build(evidence, match_id)
        self.assertFalse(result['validation_passed'])
        self.assertEqual(evidence, prior)

    def test_contract_matches_date_and_league(self):
        evidence, _ = preview_fixture()
        match = evidence['source_recordings'][0]['match']
        contracts = [{'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2023.01.01', 'to': '2023.08.13', 'no': '10'},
                     {'teamId': 'E-H', 'leagueId': 'CUP', 'from': '2023.08.01', 'to': None, 'no': '20'},
                     {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2023.08.14', 'to': None, 'no': '30'}]
        chosen = select_contract(contracts, match, 'E-H', JpdDidData._contract_covers_match_date)
        self.assertEqual(chosen['no'], '10')

    def test_history_preserves_existing_and_excludes_national(self):
        contracts = [{'teamId': 'E-OLD', 'no': '9', 'from': '2020', 'competitionType': 'league'},
                     {'teamId': 'N-KR', 'no': '7', 'from': '2021', 'competitionType': 'national'},
                     {'teamId': 'E-NEW', 'no': '10', 'from': '2022', 'competitionType': 'league'}]
        self.assertEqual(history('E-OLD9,E-OLD9', contracts, {}, 'league'), 'E-OLD9,E-NEW10')

    def test_player_projection_uses_manual_id_contract_and_omits_unused_fields(self):
        evidence, match_id = preview_fixture()
        item = evidence['source_recordings'][0]
        for row in item['children']['records']:
            row['data']['playerId'] = '0000000007'
        computed = calculate_kpis(source_records(item))
        item['recording']['kpi'] = computed.team_kpi
        item['children']['playerStats'] = [{'id': '0000000007', 'data': {
            **computed.player_kpis['0000000007'], 'ratingBasedOn': 1,
            'apx': 70, 'tpx': 60, 'fpx': 50, 'jmx': 10, 'apxGrade': 'B', 'tpxGrade': 'C', 'fpxGrade': 'D'}}]
        evidence['player_profiles']['0000000007'] = {'name': 'Player', 'birth': '2000-01-02'}
        item['recording']['lineup']['0000000007'] = {'type': 'START', 'slot': 'start', 'pos': 'MF'}
        evidence['player_contracts']['0000000007'] = [
            {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2023-01-01', 'to': None, 'no': '7', 'pos': 'MF'}]
        report = build(evidence, match_id)
        self.assertTrue(report['validation_passed'], report['errors'])
        player = next(d['data'] for d in report['documents'] if d['collection'] == 'Player')
        self.assertEqual(player['p_id'], 7)
        self.assertEqual(player['p_birth'], '2000.01.02')
        self.assertEqual(player['pt_begin'], '2023.01.01')
        # Shirt number 7 is not a legacy registration identifier.
        self.assertEqual(player['p_id_old'], '')
        self.assertFalse(any(d['collection'] == 'playerMST' for d in report['documents']))
        vibe = next(d['data'] for d in report['documents'] if d['collection'] == 'vibePlayer')
        self.assertEqual(vibe['P-BAP'], computed.player_kpis['0000000007']['DTB'])
        self.assertEqual(vibe['gp_type'], 'KY')

    def test_player_game_type_uses_actual_substitute_appearance(self):
        self.assertEqual(player_game_type({'type': 'START', 'slot': 'gk'}, 'GK'), 'GK')
        self.assertEqual(player_game_type({'type': 'START'}, 'MF'), 'KY')
        self.assertEqual(player_game_type({'type': 'BENCH', 'inHalf': 'H2', 'inSeconds': 0}, 'MF'), 'ST')
        self.assertIsNone(player_game_type({'type': 'BENCH', 'inHalf': None, 'inSeconds': None}, 'MF'))

    def test_manager_projection_uses_coach_round_identity_and_names(self):
        row = {'plSeason': '2023-2024', 'round': 1, 'teamId': 'E-AR', 'coachId': '32',
               'coachName': '미켈 아르테타', 'coachNameEn': 'Mikel Arteta', 'status': 'MANAGER',
               'from': '2023.08.01', 'to': '2024.05.31'}
        result = manager_projection(row, {'IDX_CODE': 'ARSX'}, '아스널')
        self.assertEqual(result['m_id'], 32)
        self.assertEqual(result['Manager_KR'], '미켈 아르테타')
        self.assertEqual(result['Manager_EN'], 'Mikel Arteta')
        self.assertEqual(result['Status_KR'], '정식')
        self.assertEqual(result['gm_date'], '2023.08.01')


# ==========================================================================
# season
# ==========================================================================


class SeasonAcceptanceTests(unittest.TestCase):
    def test_source_facts_and_saved_ratings_pass_together(self):
        items, codes, slots = aggregate_fixture()
        result = validate_aggregate_links(build_aggregates(items, codes, slots), items, codes, slots)
        self.assertTrue(result['passed'], result)
        self.assertGreater(result['rating_checks_count'], 60)

    def test_changed_saved_rating_link_fails(self):
        items, codes, slots = aggregate_fixture()
        for collection, field in [('mMST', 'H-FX'), ('pMST', 'gi_score'), ('cAVG', 'C1-AVG'),
                                  ('rAVG', 'MH1-IDX'), ('sAVG', 'AI Rating'), ('rank', 'RAV20')]:
            with self.subTest(collection=collection):
                generated = build_aggregates(items, codes, slots)
                next(d for d in generated['documents'] if d['collection'] == collection)['data'][field] += 1
                result = validate_aggregate_links(generated, items, codes, slots)
                self.assertFalse(result['passed'])
                self.assertTrue(result['source_facts']['passed'])
                self.assertEqual(result['rating_checks_failed'], 1)

    def test_missing_season_positions_reports_blocker_without_crashing(self):
        items, codes, slots = aggregate_fixture()
        for row in codes:
            row['data']['PlSeason'] = None
        generated = build_aggregates(items, codes, slots)
        result = validate_aggregate_links(generated, items, codes, slots)
        self.assertFalse(result['passed'])
        self.assertFalse(result['source_facts']['passed'])
        self.assertEqual({d['collection'] for d in generated['documents']}, {'mMST', 'pMST', 'cAVG', 'rAVG', 'sAVG', 'rank'})
        self.assertFalse(generated['complete'])
        self.assertTrue(generated['blocked'])

    def test_invalid_numeric_source_stays_visible_in_strict_json(self):
        encoded = encode({'metric': float('nan')})
        self.assertEqual(encoded['metric'], {'__nonfinite_float__': 'nan'})
        json.dumps(encoded, allow_nan=False)

    def test_other_season_legacy_nan_and_round_positions_do_not_pollute_scope(self):
        existing = {'Player': [{'id': 'other', 'data': {'PlSeason': '2024-2025'}}],
                    'playerDB': [{'id': 'invalid', 'data': {'P-6': {'__nonfinite_float__': 'nan'}}}],
                    'dMST': [{'id': 'round2', 'data': {'gm_round': 2, 'Match': 1}}]}
        scoped = match_target_scope(existing, {'seasonId': '20232024', 'round': 1})
        self.assertEqual(scoped, {'Player': [], 'playerDB': [], 'dMST': []})


# ==========================================================================
# sql_details
# ==========================================================================

def test_tactic_selection_keeps_complete_shot_and_goal_sequences_only():
    evidence, _ = preview_fixture()
    item = evidence['source_recordings'][0]
    cases = [
        ('possession', 'ordinary', 'UTP', 'P', {}),
        ('flag-only', 'stale-dtp', 'DTP', 'P', {}),
        ('isolated-shot', 'stp', 'STP', 'S', {'shot': True}),
        ('orphan-pass', 'missing-master', None, 'P', {}),
        ('orphan-shot', 'missing-master', None, 'S', {'shot': True}),
        ('own', 'own-path', 'UPP', 'S', {'goal': True}),
    ]
    item['children']['records'] = [
        {'id': ident, 'data': {'half': 'H1', 'halfSeconds': 10+n, 'seq': n,
         'act': act, 'res': 'G' if ident == 'own' else 'O', 'area': 2,
         'playerId': 'OWN' if ident == 'own' else None,
         'legacyPathId': path, 'legacyPathType': kind, 'legacyKpiFlags': flags}}
        for n, (ident, path, kind, act, flags) in enumerate(cases)]
    records = source_records(item)
    assert tactic_record_ids(records, calculate_kpis(records)) == {
        'isolated-shot', 'orphan-pass', 'orphan-shot', 'own'}
    own = item['children']['records'][-1]
    assert tactic_projection(item, own, '69157471581173.21631899')['p_id'] == 'OWN'
    assert tactic_projection(item, own, '69157471581173.21631899')['gt_id'] == '69157471581173.21631899'


def test_tactic_binding_preserves_distinct_simultaneous_positions():
    evidence, mid = preview_fixture()
    old = next(d for d in build(evidence, mid)['documents']
               if d['collection'] == 'TACTIC' and d['data']['gr_act_code'] == 'P'
               and d['data']['t_code'] == 'HX')
    evidence['target_existing']['TACTIC'] = [{'id': 'legacy-pass', 'data': old['data']}]
    item = next(i for i in evidence['source_recordings'] if i['side'] == 'H')
    duplicate = deepcopy(item['children']['records'][0])
    duplicate['id'] = 'pass-other'
    duplicate['data']['posX'] = 99
    item['children']['records'].append(duplicate)
    kpi = calculate_kpis(source_records(item)).team_kpi
    item['recording']['kpi'] = kpi
    item['recording']['teamRating'].update(apx=kpi['DAP']/4, tpx=kpi['DTP']/12*100,
        fpx=(min(kpi['SHOT']/15*100,99.9)+min(kpi['GOAL']/3*100,99.9))/2)
    report = build(evidence, mid)
    assert report['validation_passed'], report['errors']
    passes = [d for d in report['documents'] if d['collection'] == 'TACTIC'
              and d['data']['t_code'] == 'HX' and d['data']['gr_act_code'] == 'P']
    assert len(passes) == 2
    assert len({d['document_id'] for d in passes}) == 2
    assert any(d['document_id'] == 'legacy-pass' for d in passes)


def test_sql_tactic_excludes_non_shooting_paths_but_preserves_own_identity():
    game, infos, children, codes = sql_fixture()
    goal = children['H']['ff_game_record'][0]
    goal['p_id'] = 'OWN'
    children['H']['ff_game_record'].append({**goal, 'gr_id': 'ordinary', 'gt_id': 'ordinary',
        'p_id': '0000000001', 'gr_is_sht': 0, 'gr_is_gol': 0})
    children['H']['ff_game_path'].append({'gt_id': 'ordinary', 'gt_utp': 1})
    report = build_details(game, infos, children, codes)
    assert report['available_source_validation_passed'], report['errors']
    tactic = [d['data'] for d in report['documents'] if d['collection'] == 'TACTIC']
    assert len(tactic) == 1
    assert tactic[0]['p_id'] == 'OWN'


def sql_fixture():
    _, codes, _ = aggregate_fixture()
    game = {'gm_id':'20242025fixture','gm_round':1,'gm_date':'2024.08.12','gm_h_t_code':'T01','gm_a_t_code':'T02'}
    infos, children = [], {}
    for side in ('H','A'):
        info = {'gi_id':side,'gi_write_code':side,'gi_h1_seconds':2900,**{field:0 for field in TEAM_COLUMNS.values()}}
        if side=='H':
            for key, value in {'TAP':1,'DAP':1,'SHOT':1,'GOAL':1,'DTP':1,'BAP':1}.items(): info[TEAM_COLUMNS[key]]=value
        infos.append(info)
        children[side]={'ff_game_record':[],'ff_game_path':[],'ff_game_bap':[],'ff_game_player':[]}
    record={'gr_id':'goal','gr_half':'H1','gr_half_seconds':300,'gt_id':'path','p_id':'0000000001','gr_is_tap':1,
            'gr_is_sht':1,'gr_is_gol':1,'gr_area_code':2,'gr_pos_x':971,'gr_pos_y':634,'gr_res_code':'G','gr_act_code':'S'}
    children['H']['ff_game_record']=[record]
    children['H']['ff_game_path']=[{'gt_id':'path','gt_ctp':1}]
    children['H']['ff_game_bap']=[{'gr_half':'1','gr_half_seconds':299}]
    return game, infos, children, codes


def test_sql_detail_intervals_and_raw_coordinates_without_fabricating_ratings():
    report=build_details(*sql_fixture())
    assert report['available_source_validation_passed'], report
    shot=next(d['data'] for d in report['documents'] if d['collection']=='multiSheet_SHOT')
    assert shot['SHOT-H1-5-10']==1
    assert not any(k.startswith('BAP-') for k in shot)
    tactic=next(d['data'] for d in report['documents'] if d['collection']=='TACTIC')
    assert tactic['gr_pos_x']==971
    assert tactic['p_id']=='1'
    assert not report['ready_for_write']
    assert not any(d['collection'] in ('vibePlayer','Manager','Player','multiSheet_JMX') for d in report['documents'])


def test_sql_detail_rebuilds_totals_instead_of_trusting_sql_summary():
    game, infos, children, codes=sql_fixture()
    infos[0]['gi_ctp']=2
    report=build_details(game,infos,children,codes)
    assert report['available_source_validation_passed']
    dmst=next(d['data'] for d in report['documents'] if d['collection']=='dMST' and d['data']['H-A']=='H')
    assert dmst['DTP']==1
    children['H']['ff_game_record'][0]['gr_half']='4'
    excluded = build_details(game,infos,children,codes)
    assert not excluded['errors']


def test_sql_own_goal_contributes_to_team_intervals_but_not_player_goal():
    game, infos, children, codes=sql_fixture()
    children['H']['ff_game_record'][0]['p_id']='OWN'
    report=build_details(game,infos,children,codes)
    assert report['available_source_validation_passed']
    assert not any(d['collection']=='playerGoal' for d in report['documents'])
    wide=next(d['data'] for d in report['documents'] if d['collection']=='multiSheet_ALL' and d['data']['team_type']=='HOME')
    assert sum(v for k,v in wide.items() if k.startswith('GOAL-H'))==1
    assert next(d['data'] for d in report['documents'] if d['collection']=='dMST' and d['data']['H-A']=='H')['OG']==1
    timeline=next(d['data'] for d in report['documents'] if d['collection']=='multiSheet_GOAL-시간' and d['data']['team_type']=='HOME')
    assert timeline['GOAL1']=='H1_OG_300'
    assert all(timeline[f'GOAL{n}']=='\\N' for n in range(2,11))


def test_sql_ssr_carries_first_half_and_timeline_orders_own_goals_with_normal_goals():
    game,infos,children,codes=sql_fixture()
    goal=children['H']['ff_game_record'][0]
    children['H']['ff_game_record'].extend([
        {**goal,'gr_id':'late-goal','gr_half':'H2','gr_half_seconds':600,'gr_is_sht':1},
        {**goal,'gr_id':'own-goal','gr_half':'H2','gr_half_seconds':300,'p_id':'OWN','gr_is_sht':0},
    ])
    report=build_details(game,infos,children,codes)
    assert report['available_source_validation_passed'],report['errors']
    wide=next(d['data'] for d in report['documents'] if d['collection']=='multiSheet_ALL' and d['data']['team_type']=='HOME')
    assert wide['SSR-H2-0-5']==1
    assert wide['SSR-H2-5-10']==1
    assert wide['SSR-H2-10-15']==1
    assert sum(v for k,v in wide.items() if k.startswith('GOAL-H'))==3
    timeline=next(d['data'] for d in report['documents'] if d['collection']=='multiSheet_GOAL-시간' and d['data']['team_type']=='HOME')
    assert [timeline[f'GOAL{n}'] for n in range(1,5)]==['H1_300','H2_OG_300','H2_600','\\N']
    assert len([d for d in report['documents'] if d['collection']=='playerGoal'])==2


@pytest.mark.parametrize('seconds,total_seconds', [(0,0),(1,1),(4,3064),(1,2755)])
def test_incomplete_half_never_becomes_a_real_event_or_player_kpi(seconds,total_seconds):
    game,infos,children,codes=sql_fixture()
    valid=children['H']['ff_game_record'][0]
    valid['gr_is_tmp']=1
    invalid={**valid,'gr_id':'incomplete','gr_half':'','gr_half_seconds':seconds,
             'gr_seconds':total_seconds,'gt_id':'abandoned','p_id':'0000000001'}
    children['H']['ff_game_record'].append(invalid)
    children['H']['ff_game_path'].append({'gt_id':'abandoned','gt_ctp':1})
    children['H']['ff_game_player']=[{'p_id':'0000000001','gp_tap':999}]
    original=deepcopy(children)
    report=build_details(game,infos,children,codes)
    assert report['available_source_validation_passed']
    assert children==original
    assert len([d for d in report['documents'] if d['collection']=='TACTIC'])==1
    dmst=next(d['data'] for d in report['documents'] if d['collection']=='dMST' and d['data']['H-A']=='H')
    assert dmst['DAP']==1 and dmst['DTP']==1 and dmst['gi_tmp']==1
    player=next(d['data'] for d in report['documents'] if d['collection']=='playerMST')
    assert player['P-DB12']==1 and player['P-DB17']==1
    request=rating_input(game,infos[0],[{'p_id':'0000000001',**{v:player['P-DB'+str(k)] for k,v in PLAYER_COLUMNS.items()}}],report)
    assert request['teamKpi']['DAP']==1 and request['teamKpi']['DTP']==1
    assert request['teamKpi5min'][-1]['DAP']==1
    assert request['playerKpis'][0]['DAP']==1


def test_orphan_dtp_paths_do_not_create_interval_or_total_counts():
    game,infos,children,codes=sql_fixture()
    children['H']['ff_game_path'].extend([{'gt_id':'orphan1','gt_ctp':1,'gt_ttp':1},
                                        {'gt_id':'orphan2','gt_ctp':1,'gt_ttp':1}])
    infos[0]['gi_ctp']=3
    report=build_details(game,infos,children,codes)
    assert report['available_source_validation_passed']
    dtp=next(d['data'] for d in report['documents'] if d['collection']=='multiSheet_DTP' and d['data']['team_type']=='HOME')
    assert sum(v for k,v in dtp.items() if k.startswith('DTP-H'))==1
    assert not report['errors']


def test_preview_excludes_incomplete_events_from_details_and_player_flags():
    evidence,mid=preview_fixture()
    original=build(evidence,mid)
    item=evidence['source_recordings'][0]
    invalid=deepcopy(item['children']['records'][0])
    invalid['id']='incomplete'
    invalid['data'].update(half='',halfSeconds=4)
    item['children']['records'].append(invalid)
    result=build(evidence,mid)
    assert result['validation_passed'], result['errors']
    assert result['documents']==original['documents']
    assert len(item['children']['records'])>len(source_records(item))



@pytest.mark.parametrize('player_id', ['0000000999','newDidRegisteredPlayer'])
def test_registered_player_without_any_match_stats_is_preserved(player_id):
    evidence,mid=preview_fixture()
    evidence['player_profiles'][player_id]={'name':'Registered','currentTeamId':'E-H'}
    original=deepcopy(evidence)
    first=build(evidence,mid); second=build(evidence,mid)
    assert first['validation_passed'],first['errors']
    player=next(d for d in first['documents'] if d['collection']=='Player')
    assert player['data']['t_code']=='E-H'
    assert player['data']['p_name']=='Registered'
    assert player['data']['pt_begin']=='0000.00.00' and player['data']['pt_end']=='9999.99.99'
    assert not any(d['collection'] in ('vibePlayer','playerMST') for d in first['documents'])
    assert first['documents']==second['documents'] and evidence==original
    for doc in first['documents']:
        validate_document(doc)


def test_unused_bench_retains_identity_without_match_date_contract_or_rating():
    evidence,mid=preview_fixture(); pid='0000000999'
    evidence['player_profiles'][pid]={'name':'Bench'}
    evidence['player_contracts'][pid]=[{'teamId':'E-H','leagueId':'EPL','from':'2020-01-01','to':'2020-06-30'}]
    item=evidence['source_recordings'][0]
    item['recording']['lineup'][pid]={'type':'BENCH','slot':'bench'}
    item['children']['playerStats'].append({'id':pid,'data':{k:0 for k in PLAYER_MST_KPI.values()}})
    result=build(evidence,mid)
    assert result['validation_passed'],result['errors']
    assert next(d['data'] for d in result['documents'] if d['collection']=='Player')['pt_end']=='2020.06.30'
    assert not any(d['collection']=='playerMST' for d in result['documents'])
    assert not any(d['collection']=='vibePlayer' for d in result['documents'])


def test_new_player_identity_merge_preserves_destination_id_and_manual_fields():
    evidence,mid=preview_fixture();pid='newDidPlayer'
    evidence['player_profiles'][pid]={'name':'New','currentTeamId':'E-H'}
    first=build(evidence,mid)
    player=next(d for d in first['documents'] if d['collection']=='Player')
    evidence['target_existing']['Player']=[{'id':'manual-existing','data':{**player['data'],'uid':'keep-uid','manualNote':'keep'}}]
    second=build(evidence,mid)
    generated=next(d for d in second['documents'] if d['collection']=='Player')
    assert generated['document_id']=='manual-existing' and generated['data']['uid']=='keep-uid'
    assert generated['data']['manualNote'] == 'keep'
    evidence['target_existing']['Player'][0]['data'].pop('DID_PLAYER_ID')
    assert not build(evidence,mid)['validation_passed']


def test_actual_appearance_still_requires_match_date_contract_even_with_zero_kpi():
    evidence,mid=preview_fixture();pid='0000000999'
    evidence['player_profiles'][pid]={'name':'Starter','currentTeamId':'E-H'}
    item=evidence['source_recordings'][0]
    item['recording']['lineup'][pid]={'type':'START','slot':'start','pos':'MF'}
    item['children']['playerStats'].append({'id':pid,'data':{k:0 for k in PLAYER_MST_KPI.values()}})
    result=build(evidence,mid)
    assert any(d['collection']=='Player' for d in result['documents'])
    assert not result['validation_passed']
    assert any('no match-date contract' in e for e in result['errors'])


def test_explicit_end_date_wins_over_null_for_the_same_did_contract_identity():
    evidence,mid=preview_fixture();pid='0000000999'
    evidence['player_profiles'][pid]={'name':'Registered','currentTeamId':'E-H'}
    evidence['player_contracts'][pid]=[
        {'teamId':'E-H','leagueId':'EPL','from':'2020-01-01','to':None,'no':'11','pos':'FW'},
        {'teamId':'E-H','leagueId':'EPL','from':'2020-01-01','to':'2026-09-02','no':'11','pos':'FW'}]
    result=build(evidence,mid)
    assert result['validation_passed'], result['errors']
    player = next(d['data'] for d in result['documents'] if d['collection']=='Player' and d['data']['p_id']==999)
    assert player['pt_end']=='2026.09.02'



def test_new_did_player_links_master_tactic_and_appearance_to_same_identity():
    evidence,mid=preview_fixture();pid='newDidAppearance'
    item=evidence['source_recordings'][0]
    for row in item['children']['records']: row['data']['playerId']=pid
    computed=calculate_kpis(source_records(item))
    item['recording']['kpi']=computed.team_kpi
    item['recording']['lineup'][pid]={'type':'START','slot':'start','pos':'MF'}
    item['children']['playerStats']=[{'id':pid,'data':{**computed.player_kpis[pid],'ratingBasedOn':1,
        'apx':70,'tpx':60,'fpx':50,'jmx':10,'apxGrade':'B','tpxGrade':'C','fpxGrade':'D'}}]
    evidence['player_profiles'][pid]={'name':'New','currentTeamId':'E-H'}
    evidence['player_contracts'][pid]=[{'teamId':'E-H','leagueId':'EPL','from':'2023-01-01','to':None,'no':'7','pos':'MF'}]
    result=build(evidence,mid)
    assert result['validation_passed'],result['errors']
    identity=next(d['data']['p_id'] for d in result['documents'] if d['collection']=='Player')
    assert identity<2**53
    assert next(d['data']['p_id'] for d in result['documents'] if d['collection']=='vibePlayer')==identity
    assert not any(d['collection']=='playerMST' for d in result['documents'])
    for doc in result['documents']:
        if doc['collection']=='TACTIC' and doc['data']['p_id']!='\\N':
            assert doc['data']['p_id']==str(identity)



@pytest.mark.parametrize('value,display,expected_grade',[
    (59.76,'59.7','D'),(49.99,'49.9','F'),(49.9999,'49.9','F'),
    (50.0,'50.0','D'),(59.9999,'59.9','D'),(60.0,'60.0','C')])
def test_rating_presentation_truncates_without_regrading_or_changing_raw(value,display,expected_grade):
    raw={'fpx':value,'fpxGrade':expected_grade,'jmxSeries':[value]*20}
    original=deepcopy(raw)
    result=rating_display(raw)
    assert result['fpx']==display and result['fpxGrade']==expected_grade
    assert result['jmxSeries']==[display]*20 and raw==original
    assert grade(value)==expected_grade


def test_aggregate_rating_precision_preserves_source_and_averages_before_truncation():
    items,codes,slots=aggregate_fixture()
    for index,item in enumerate(items):
        item['recording']['teamRating']['jmx']=59.79 if index<10 else 59.89
    original=deepcopy(items)
    result=build_aggregates(items,codes,slots)
    assert result['complete'],result
    assert validate_aggregate_links(result,items,codes,slots)['passed']
    assert docs(result,'sAVG')[0]['AI Rating']==59.8
    assert {d['gi_score'] for d in docs(result,'pMST')}=={59.79,59.89}
    assert docs(result,'rAVG')[0]['MH1-IDX']==59.7
    assert items==original


def test_rating_read_exposes_truncated_strings_beside_original_numbers(monkeypatch):
    from backend.api import api_match_ratings
    raw={'apx':49.9999,'apxGrade':'F','tpx':59.76,'tpxGrade':'D',
         'fpx':50.0,'fpxGrade':'D','jmx':59.76,'jmxSeries':[59.76]*20}
    recording=SimpleNamespace(teamId='H',opponentTeamId='A',kpiVersion=1,ratingBasedOn=1,kpi=None,
                              teamRating=SimpleNamespace(model_dump=lambda **kw:deepcopy(raw)))
    data=SimpleNamespace(get_recording=lambda *a:recording,
                         list_player_stats=lambda *a:{'p':{'playerId':'p',**deepcopy(raw)}})
    monkeypatch.setattr(api_match_ratings,'JpdDidData',lambda:data)
    response=api_match_ratings.read_match_rating('match','H')
    assert response.teamRating['tpx']==59.76
    assert response.teamRatingDisplay['tpx']=='59.7'
    assert response.teamRatingDisplay['apx']=='49.9'
    assert response.playerRatings[0]['jmx']==59.76
    assert response.playerRatingsDisplay[0]['jmx']=='59.7'


def test_preview_fpx_and_goals_include_own_but_ssr_excludes_it():
    evidence,mid=preview_fixture();item=evidence['source_recordings'][0]
    item['children']['records'][-1]['data']['playerId']='OWN'
    computed=calculate_kpis(source_records(item));kpi=computed.team_kpi
    item['recording']['kpi']=kpi
    item['recording']['teamRating'].update(apx=kpi['DAP']/4,tpx=kpi['DTP']/12*100,
        fpx=(min(kpi['SHOT']/15*100,99.9)+min(kpi['GOAL']/3*100,99.9))/2)
    result=build(evidence,mid)
    assert result['validation_passed'],result['errors']
    dmst=next(d['data'] for d in result['documents'] if d['collection']=='dMST' and d['data']['H-A']=='H')
    assert dmst['GOAL']==1 and dmst['OG']==1
    assert dmst['SSR']==0 and dmst['SSRScore']==0
    assert dmst['FPX']==delivery.rating_two_decimals(item['recording']['teamRating']['fpx'])
    assert dmst['GOALScore']==33.33
    wide=next(d['data'] for d in result['documents'] if d['collection']=='multiSheet_ALL' and d['data']['team_type']=='HOME')
    assert sum(v for k,v in wide.items() if k.startswith('GOAL-H'))==1
    sheet=next(d['data'] for d in result['documents'] if d['collection']=='multiSheet_GOAL' and d['data']['team_type']=='HOME')
    assert all(v==0 for k,v in sheet.items() if k.startswith('GOAL-H'))
    assert all(v==0 for k,v in wide.items() if k.startswith('SSR-H'))
    timeline=next(d['data'] for d in result['documents'] if d['collection']=='multiSheet_GOAL-시간' and d['data']['team_type']=='HOME')
    assert timeline['GOAL1']=='H1_OG_300'
    assert all(timeline[f'GOAL{n}']=='\\N' for n in range(2,11))
    assert not any(d['collection']=='playerGoal' and d['data']['team_type']=='HOME' for d in result['documents'])


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
            self.assertEqual(field_rule('dMST','DTP(AiTactic)')[0], 'derived')
            self.assertEqual(field_rule('dMST','DAP(AiAttack)')[0], 'derived')
            self.assertEqual(field_rule('dMST','JMX2')[1], 'recordings.teamRating.jmx')
            self.assertEqual(field_rule('dMST','DAP(AiAttack)')[1], 'recordings.kpi.DAP / 400 * 100')
            self.assertEqual(field_rule('dMST','DTP(AiTactic)')[1], 'recordings.kpi.DTP / 12 * 100')

        def test_buildup_rating_excluded_without_excluding_counts(self):
            self.assertEqual(field_rule('dMST', 'BAP(AiBuildup)')[0], 'excluded')
            self.assertEqual(field_rule('dMST', 'BAP')[0], 'candidate')
            self.assertEqual(field_rule('multiSheet_BAP', 'BAP-H1-0-5')[0], 'derived')

        def test_numeric_type_compatibility(self):
            self.assertTrue(type_compatible(1.5,['int','float']))
            self.assertFalse(type_compatible(True,['int']))
            self.assertFalse(type_compatible('1',['int']))

        def test_manager_date_is_not_match_date(self):
            self.assertNotEqual(field_rule('Manager','gm_date')[1], 'matches.date')

        def test_player_db_short_season(self):
            self.assertIn('정수', field_rule('playerDB','P-6')[1])

        def test_existing_collection_ownership(self):
            self.assertEqual(field_rule('prediction','actualWdlHome')[0], 'excluded')
            self.assertEqual(field_rule('user','uid')[0], 'out_of_scope')
            self.assertEqual(field_rule('code','Team Code')[0], 'preserve')

        def test_confirmed_manual_unused_and_season_average_rules(self):
            for field in ('Coach', 'Captain'):
                self.assertEqual(field_rule('Manager', field)[0], 'manual')
            for field in ('P-KPI40', 'P-KPI41'):
                self.assertEqual(field_rule('playerMST', field)[0], 'manual')
            for field in ('I-DAP', 'PH-DAP', 'PA-SHOT', 'PI-GOAL'):
                self.assertEqual(field_rule('pMST', field)[0], 'excluded')
            for field in ('WGR1', 'Season', 'S_Avg', 'R_Rate'):
                self.assertEqual(field_rule('rank', field)[0], 'excluded')
            self.assertEqual(field_rule('sAVG', 'AVG')[1], '동일 시즌 sAVG.AI Rating')

        def test_x_fields_are_excluded(self):
            self.assertEqual(field_rule('dMST','X-DAP')[0], 'excluded')
            self.assertEqual(field_rule('dMST','X-SSR')[0], 'excluded')
            self.assertEqual(field_rule('dMST','DAP(AiAttack)')[0], 'derived')

        def test_unused_player_weighted_scores(self):
            for number in range(50, 69):
                self.assertEqual(field_rule('playerMST', f'P-KPI{number}')[0], 'excluded')
            self.assertEqual(field_rule('playerMST', 'P-KPI49')[0], 'candidate')

        def test_history_competition_scope(self):
            status,source,rule = field_rule('Player','p_id_old')
            self.assertEqual(status, 'join')
            self.assertIn('contracts', source)
            self.assertIn('league/cup/national/test', rule)
            self.assertEqual(field_rule('playerDB','P-17')[0], 'join')

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MappingTests))
    return 0 if result.wasSuccessful() else 1


def test_mapping_rules():
    assert run_self_tests() == 0


def master_fixture():
    evidence, _ = preview_fixture()
    evidence.update(teams={'E-H':{'name':'Home'},'E-A':{'name':'Away'}},coach_rounds=[],coach_contracts=[])
    evidence['player_profiles']={'0000000001':{'name':'Bench','currentTeamId':'E-H','currentNo':7}}
    evidence['did_matches'].append({'id':'season-end','data':{**evidence['did_matches'][0]['data'],'date':'2024.06.30'}})
    return evidence, {'documents':[],'new_season':False,'slots':{}}


def test_master_all_includes_registered_nonparticipants_without_raw_or_ratings():
    evidence,setup=master_fixture()
    evidence['source_recordings']=[]
    evidence['player_profiles']['unrelated-foreign-profile']={}
    report=delivery.generate_masters(evidence,[],[],{},setup,'all')
    assert report['validation_passed'], report['errors']
    assert {d['collection'] for d in report['documents']}=={'Player','playerDB'}
    assert docs(report,'Player')[0]['t_code']=='E-H'
    assert not docs(report,'vibePlayer')


def test_latest_preserves_destination_only_fields_and_bound_ids():
    evidence,setup=master_fixture()
    original=delivery.generate_masters(evidence,[],[],{},setup,'all')
    for doc in original['documents']:
        evidence['target_existing'].setdefault(doc['collection'],[]).append(
            {'id':'existing-'+doc['collection'],'data':{**doc['data'],'uid':'manual-uid','manualNote':'keep'}})
    unchanged=delivery.generate_masters(evidence,[],[],{},setup,'latest')
    assert unchanged['validation_passed'], unchanged['errors']
    assert len(unchanged['documents'])==0
    evidence['player_profiles']['0000000001']['name']='Renamed'
    changed=delivery.generate_masters(evidence,[],[],{},setup,'latest')
    assert changed['validation_passed'], changed['errors']
    assert len(changed['documents'])==2
    for doc in changed['documents']:
        assert doc['document_id']=='existing-'+doc['collection']
        assert doc['data']['uid']=='manual-uid'
        assert doc['data']['manualNote']=='keep'
        merged=delivery.decide_merge(doc,evidence['target_existing'][doc['collection']][0]['data'])
        assert merged


def test_latest_transfer_uses_season_end_and_clears_optional_contract_fields():
    evidence,setup=master_fixture()
    evidence['player_contracts']={'0000000001':[
        {'teamId':'E-H','from':'2023.07.01','to':'2024.01.01','no':7},
        {'teamId':'E-A','from':'2024.01.01','to':None,'no':None,'pos':None}]}
    report=delivery.generate_masters(evidence,[],[],{},setup,'latest')
    assert report['validation_passed'], report['errors']
    player=docs(report,'Player')[0]
    assert player['t_code']=='E-A' and player['pt_end']=='9999.99.99'
    assert player['pt_num'] is None and player['pt_pos'] is None
    assert docs(report,'playerDB')[0]['P-7']=='E-A'


def test_master_manager_switch_uses_scheduled_fixture_without_raw():
    evidence,setup=master_fixture()
    evidence['player_profiles']={}
    first=evidence['did_matches'][0]
    second={'id':'next-game','data':{**first['data'],'date':'2024.02.01','round':2}}
    def contract(team,coach,start,end=None):
        return {'data':{'teamId':team,'coachId':coach,'coachName':coach,'coachNameEn':coach,
                        'from':start,'to':end,'status':'MANAGER'}}
    evidence['coach_contracts']=[contract('E-H','old','2023.07.01','2024.01.01'),
        contract('E-H','new','2024.01.02'),contract('E-A','away','2023.07.01')]
    report=delivery.generate_masters(evidence,[first,second],[],{},setup,'all')
    assert report['validation_passed'], report['errors']
    home=sorted((d for d in docs(report,'Manager') if d['T_Code']=='E-H'),key=lambda d:d['Round'])
    assert len(home)==2 and home[0]['e_date']=='2024.01.01'
    assert home[1]['Manager_EN']=='new' and home[1]['e_date']=='9999.99.99'
    assert home[0]['m_id']!=home[1]['m_id']


def test_active_coach_without_round_context_is_reported_instead_of_silently_omitted():
    evidence,setup=master_fixture()
    evidence['coach_contracts']=[{'data':{'teamId':'E-H','coachId':'new-coach','from':'2024.01.01','to':None}}]
    report=delivery.generate_masters(evidence,[],[],{},setup,'latest')
    assert not report['validation_passed']
    assert any('round mapping' in error for error in report['errors'])


def test_master_partial_source_cannot_overwrite_existing_season_aggregates():
    evidence,setup=master_fixture()
    evidence['target_existing']['dMST']=[{'id':'missing','data':{'gm_round':38,'t_code':'E-A'}}]
    report=delivery.generate_masters(evidence,[],[],{},setup,'all')
    assert report['validation_passed'],report['errors']
    assert report['aggregate_status']=='blocked' and report['blocked_groups']==['aggregates']
    assert any('coverage incomplete' in e for e in report['warnings'])
    assert {d['collection'] for d in report['documents']}=={'Player','playerDB'}


def test_match_scope_excludes_masters_managers_and_aggregates(monkeypatch):
    evidence,mid=preview_fixture()
    # Complete the rating snapshot required by production validation.
    for item in evidence['source_recordings']:
        item['recording']['teamRating'].update(apxGrade='F',tpxGrade='F',fpxGrade='F')
    evidence['coach_rounds']=[{'data':{'teamId':'E-H','round':1,'plSeason':'2023-2024','coachId':'missing'}}]
    def forbidden(*args,**kwargs): raise AssertionError('match mode must not generate season aggregates')
    monkeypatch.setattr(delivery,'build_aggregates',forbidden)
    report=delivery.generate_delivery(evidence,evidence['did_matches'],[],{},
        {'documents':[],'new_season':False},match_only=True)
    assert report['validation_passed'], (report['errors'],[c for c in report['checks'] if not c['passed']])
    assert report['documents']
    assert all(d['collection'] in delivery.MATCH_COLLECTIONS for d in report['documents'])
    assert not {d['collection'] for d in report['documents']}&delivery.MASTER_COLLECTIONS


@pytest.mark.parametrize('mode',['all','latest'])
def test_nonmatch_scope_is_season_only_and_never_split_by_round(mode):
    scope={'seasonId':'20232024','mode':mode}
    source=object.__new__(delivery.SourceCapture)
    assert source.split_scopes(scope)==[scope]
    for field in ('roundFrom','roundTo','matchId'):
        with pytest.raises(BackendError,match='season only'):
            delivery.delivery_mode({**scope,field:1})


def test_all_posts_queue_work_before_source_or_writer_is_created(service,monkeypatch):
    svc,_,_,calls=service
    submitted=[]
    svc.pool=SimpleNamespace(submit=lambda *args:submitted.append(args))
    svc.source_factory=lambda:pytest.fail('request handler performed source reads')
    svc.writer_factory=lambda:pytest.fail('request handler constructed a Firestore writer')
    monkeypatch.setattr(api_aifootballx,'delivery_service',lambda:svc)
    app=FastAPI()
    app.include_router(api_aifootballx.router,prefix='/api/v1')
    app.dependency_overrides[api_aifootballx.delivery_operator]=lambda:CurrentUser(uid='operator',claims={})
    client=TestClient(app)
    for path in ('send/matches','send/masters','send/aggregates','send/all'):
        response=client.post('/api/v1/aifootballx/'+path,data={'season':'2023-2024','latest':'true'})
        assert response.status_code==202
        assert response.json()['status']=='queued'
        assert response.json()['scope']['latest'] is True
    assert len(submitted)==4 and not calls
    for removed in ('preview', 'send/latest'):
        assert client.post('/api/v1/aifootballx/'+removed,data={'season':'2023-2024'}).status_code == 404
    schema=app.openapi()
    for mode in ('matches','masters','aggregates','all'):
        body=schema['paths']['/api/v1/aifootballx/send/'+mode]['post']['requestBody']['content']['application/x-www-form-urlencoded']['schema']
        form=schema['components']['schemas'][body['$ref'].split('/')[-1]]
        assert form['required']==['season']
        assert form['properties']['latest']['type']=='boolean'
        assert form['properties']['latest']['default'] is False
        if mode=='masters': assert set(form['properties'])=={'season','latest'}


def test_latest_no_changes_completes_without_writer(service):
    svc,source,_,calls=service
    def capture(*args):
        return delivery.finish_delivery([],[],[],{},'latest'),{'documents':{},'queries':[]}
    source.capture=capture
    svc.writer_factory=lambda:pytest.fail('unchanged latest must not construct a writer')
    ident=svc.send_scope({'seasonId':'20232024','mode':'latest'},'operator')
    assert svc.store.job(ident,'operator')['state']=='complete'
    assert svc.public_status('operator')['mode']=='latest'
    assert not calls


def test_contract_group_guard_detects_changes_by_full_path():
    source=object.__new__(delivery.SourceCapture)
    def snap(path,version):
        return SimpleNamespace(id='shared-id',reference=SimpleNamespace(path=path),exists=True,
                               update_time=SimpleNamespace(isoformat=lambda:version))
    snapshots=[snap('players/a/contracts/shared-id','one'),snap('players/b/contracts/shared-id','two')]
    query=SimpleNamespace(limit=lambda n:SimpleNamespace(stream=lambda **kwargs:snapshots))
    source.did=SimpleNamespace(collection_group=lambda name:query)
    guards={'documents':{},'queries':[{'group':'contracts','bound':10,
            'versions':{s.reference.path:source.version(s) for s in snapshots}}]}
    source.assert_fresh(guards)
    snapshots.pop()
    with pytest.raises(BackendError) as changed: source.assert_fresh(guards)
    assert changed.value.code=='aifootballx_source_changed'


def test_send_enabled_by_default():
    from backend.system.system_firestore import Settings
    assert Settings.model_fields['aifootballx_write_enabled'].default is True


@pytest.mark.parametrize('bad_slot',[False,True])
@pytest.mark.parametrize('with_fixtures',[False,True])
@pytest.mark.parametrize('mode', ['latest', 'masters'])
def test_master_capture_reads_registration_without_match_children(monkeypatch,tmp_path,bad_slot,with_fixtures,mode):
    evidence,_=master_fixture()
    source=object.__new__(delivery.SourceCapture)
    source.documents={};source.queries=[]
    empty=SimpleNamespace(limit=lambda n:SimpleNamespace(stream=lambda **kwargs:[]))
    source.did=SimpleNamespace(collection_group=lambda name:SimpleNamespace(where=lambda **kwargs:empty),
        document=lambda path:SimpleNamespace(path=path),
        get_all=lambda refs,**kwargs:[SimpleNamespace(reference=ref,exists=False,to_dict=lambda:None) for ref in refs])
    source.target=object()
    paths=[]
    def rows(client,path,filters=(),**kwargs):
        paths.append(path)
        assert not any(part in path for part in ('/records','/paths','/playerStats','/cards'))
        if path in ('players','coachContracts'):
            assert filters and filters[0][0] in ('currentTeamId','teamId')
        if client is source.target and path in ('Player','playerDB'):
            assert filters and filters[0][0] in ('PlSeason','`P-6`','P-6')
        if client is source.did:
            if path=='matches' and with_fixtures: return evidence['did_matches']
            if path=='players': return [{'id':k,'data':v} for k,v in evidence['player_profiles'].items()]
            if path=='teams': return [{'id':k,'data':{**v,'currentLeagueId':'EPL'}} for k,v in evidence['teams'].items()]
            if path=='coachContracts' and with_fixtures:
                return [{'id':tid,'data':{'teamId':tid,'coachId':tid,'coachName':tid,'coachNameEn':tid,
                    'from':'2023.07.01','to':None,'status':'MANAGER'}} for tid in evidence['teams']]
        if client is source.target and path=='code':
            return [{'id':str(n),**r} for n,r in enumerate(evidence['ai_samples']['code'])]
        return []
    source.rows=rows
    source.get=lambda path:{}
    source.get_many=lambda paths:{p:evidence['teams'][p.split('/')[-1]] for p in paths if p.startswith('teams/')}
    source.assert_fresh=lambda guards:None
    capture_progress = []
    source.set_progress_callback(capture_progress.append)
    slot_path=tmp_path/'bad-slots.json' if bad_slot else None
    if slot_path: slot_path.write_text('{',encoding='utf-8')
    if mode == 'masters':
        source.did.get_all = lambda *args, **kwargs: pytest.fail('Masters must not read recording heads')
    report,guards=source.capture({'seasonId':'20232024','mode':mode},slot_path)
    assert report['validation_passed'],report['errors']
    assert len(report['documents'])==(4 if with_fixtures else 2)
    assert capture_progress == sorted(capture_progress)
    assert capture_progress[0] == 0 and capture_progress[-1] == 100
    assert any(20 < value <= 65 for value in capture_progress)
    assert guards['queries'][0]['group']=='contracts'
    assert 'coachContracts' in paths and 'coaches' not in paths
    assert ('coachRounds' in paths) is (not with_fixtures)
    if mode == 'masters':
        assert report['aggregate_status'] is None and not report['warnings']
    elif bad_slot:
        assert report['aggregate_status']=='blocked' and report['warnings']
        assert 'slot_file' not in guards


def test_master_all_generates_all_six_aggregates_without_match_detail():
    items,codes,slots=aggregate_fixture()
    fixtures=[{'id':i['match_id'],'data':i['match']} for i in items if i['side']=='H']
    contracts=[{'data':{'teamId':r['data']['TCODE'],'coachId':str(n),'coachName':'감독'+str(n),
                'coachNameEn':'Coach '+str(n),'from':'2024.07.01','to':None,'status':'MANAGER'}}
               for n,r in enumerate(codes,1)]
    evidence={'did_matches':fixtures,'ai_samples':{'code':codes},'target_existing':{},'player_profiles':{},
              'teams':{},'coach_contracts':contracts}
    report=delivery.generate_masters(evidence,fixtures,items,slots,
        {'documents':[],'new_season':False},'all')
    assert report['validation_passed'],report['errors']
    collections={d['collection'] for d in report['documents']}
    assert set(delivery.AGGREGATES)<=collections
    assert not collections&delivery.MATCH_COLLECTIONS
    assert report['aggregate_validation']['passed']


def test_match_new_season_can_send_without_nonmatch_setup():
    evidence,_=preview_fixture()
    for item in evidence['source_recordings']:
        item['recording']['teamRating'].update(apxGrade='F',tpxGrade='F',fpxGrade='F')
    report=delivery.generate_delivery(evidence,evidence['did_matches'],[],{},
        {'documents':[{'collection':'code','document_id':'season-code','data':{'PlSeason':'2023-2024'}}],
         'new_season':True},match_only=True)
    assert report['validation_passed'],report['errors']
    assert report['documents']
    assert all(doc['collection'] in delivery.MATCH_COLLECTIONS for doc in report['documents'])


def test_match_capture_does_not_require_season_code_or_slot_setup(monkeypatch,tmp_path):
    evidence,_=preview_fixture()
    existing_targets = {}
    source=object.__new__(delivery.SourceCapture)
    source.did=object();source.target=SimpleNamespace(collection=lambda col:SimpleNamespace(
        limit=lambda n:SimpleNamespace(stream=lambda **kwargs:[])))
    source.documents={};source.queries=[]
    recordings={f'matches/{i["match_id"]}/recordings/{i["side"]}':i for i in evidence['source_recordings']}
    class Did:
        def document(self,path): return SimpleNamespace(path=path)
        def get_all(self,refs,**kwargs):
            return [SimpleNamespace(reference=ref,exists=True,
                    update_time=SimpleNamespace(isoformat=lambda:'version'),
                    to_dict=lambda p=ref.path:recordings[p]['recording']) for ref in refs]
    source.did=Did()
    def rows(client,path,filters=(),**kwargs):
        if client is source.did:
            if path=='matches': return evidence['did_matches']
            parent,_,child=path.rpartition('/')
            if parent in recordings: return recordings[parent]['children'].get(child,[])
        if client is source.target and path=='code':
            return [{'id':str(n),**row} for n,row in enumerate(evidence['ai_samples']['code'])]
        if client is source.target:
            return existing_targets.get(path, [])
        return []
    source.rows=rows;source.get=lambda path:{};source.assert_fresh=lambda guards:None
    monkeypatch.setattr(delivery,'plan_new_season',lambda *a,**k:pytest.fail('Match delivery attempted season setup'))
    for item in evidence['source_recordings']:
        item['recording']['teamRating'].update(apxGrade='F',tpxGrade='F',fpxGrade='F')
    report,guards=source.capture({'seasonId':'20232024','mode':'matches','roundFrom':1,'roundTo':1},
                                tmp_path/'missing-aggregate-slots.json')
    assert report['validation_passed'],report['errors']
    assert 'slot_file' not in guards and guards['new_code']=={}
    for doc in report['documents']:
        existing_targets.setdefault(doc['collection'], []).append({'id': doc['document_id'], 'data': doc['data']})
    scope = {'seasonId':'20232024','mode':'matches','roundFrom':1,'roundTo':1,'latest':True}
    unchanged, _ = source.capture(scope, None)
    assert unchanged['validation_passed'], unchanged['errors']
    assert not unchanged['documents'] and unchanged['summary']['unchangedDocuments'] > 0
    extra = deepcopy(existing_targets['TACTIC'][0])
    extra['id'] = 'obsolete-event'
    extra['data']['gr_half_seconds'] += 1
    existing_targets['TACTIC'].append(extra)
    rejected, _ = source.capture(scope, None)
    assert not rejected['validation_passed']
    assert any('unmatched target documents' in e for e in rejected['errors'])


def test_match_first_then_all_can_create_missing_season_code_and_slots():
    items,codes,_=aggregate_fixture()
    fixtures=[{'id':i['match_id'],'data':i['match']} for i in items if i['side']=='H']
    for row in codes: row['data']['PlSeason']=None
    original=plan_new_season('2024-2025',fixtures,codes,{}, {'seasonSlotMappings':{}})
    after_matches=plan_new_season('2024-2025',fixtures,codes,
        {'dMST':[{'id':'match-already-sent'}]}, {'seasonSlotMappings':{}})
    assert after_matches==original
    assert len(after_matches['documents'])==20
    assert '2024-2025' in after_matches['slots']['seasonSlotMappings']
    contracts=[{'data':{'teamId':r['data']['TCODE'],'coachId':str(n),'coachName':'Coach '+str(n),
        'coachNameEn':'Coach '+str(n),'from':'2024.07.01','to':None,'status':'MANAGER'}} for n,r in enumerate(codes,1)]
    evidence={'did_matches':fixtures,'ai_samples':{'code':codes+after_matches['code_rows']},
        'target_existing':{'dMST':[{'id':'already-sent','data':{'gm_round':1,'t_code':'T01'}}]},
        'player_profiles':{},'teams':{},'coach_contracts':contracts}
    report=delivery.generate_masters(evidence,fixtures,items,after_matches['slots'],after_matches,'all')
    assert report['validation_passed'],report['errors']
    assert report['aggregate_status']=='ready' and not report['warnings']
    assert len(docs(report,'code'))==20
    assert set(delivery.AGGREGATES)<={d['collection'] for d in report['documents']}


def test_new_season_setup_preserves_existing_explicit_slot_order():
    items,codes,_=aggregate_fixture()
    fixtures=[{'id':i['match_id'],'data':i['match']} for i in items if i['side']=='H']
    for row in codes: row['data']['PlSeason']=None
    slots={'seasonSlotMappings':{'2024-2025':{'cavgSlotToCodeTeamCodeMapping':{
        str(n):{'codeTeamCode':21-n} for n in range(1,21)}}}}
    setup=plan_new_season('2024-2025',fixtures,codes,{'dMST':[{'id':'match'}]},slots)
    assert setup['slots']==slots and len(setup['documents'])==20
    assert all(d['data']['DID_CAVG_SLOT']==21-d['data']['Team Code'] for d in setup['documents'])


@pytest.mark.parametrize('mode',['all','latest'])
def test_invalid_season_rating_blocks_only_aggregates_not_player_master(mode):
    evidence,setup=master_fixture()
    items,codes,slots=aggregate_fixture()
    evidence['ai_samples']['code']+=codes
    items[0]['recording']['ratingBasedOn']=999
    report=delivery.generate_masters(evidence,[],items,slots,setup,mode)
    assert report['validation_passed'],report['errors']
    assert report['aggregate_status']=='blocked' and report['warnings']
    assert {d['collection'] for d in report['documents']}=={'Player','playerDB'}
    assert not {d['collection'] for d in report['documents']}&set(delivery.AGGREGATES)


def test_aggregate_builder_failure_is_reported_without_losing_master_candidates(monkeypatch):
    evidence,setup=master_fixture()
    items,_,_=aggregate_fixture()
    def broken(*args,**kwargs): raise ValueError('bad aggregate source')
    monkeypatch.setattr(delivery,'build_aggregates',broken)
    report=delivery.generate_masters(evidence,[],items,{},setup,'latest')
    assert report['validation_passed'] and report['aggregate_status']=='blocked'
    assert len(report['documents'])==2 and report['warnings']


def test_master_transmission_completes_even_when_aggregate_group_is_blocked(service):
    svc,source,_,calls=service
    def capture(scope,*args):
        evidence,setup=master_fixture()
        evidence['target_existing']['dMST']=[{'id':'missing','data':{'gm_round':38,'t_code':'E-A'}}]
        report=delivery.generate_masters(evidence,[],[],{},setup,scope['mode'])
        return report,{'documents':{},'queries':[]}
    source.capture=capture
    svc.send_scope({'seasonId':'20232024','mode':'latest'},'operator')
    status=svc.public_status('operator')
    assert len(calls)==2 and status['counts']=={'success':2}
    assert status['validationPassed'] is True
    assert status['status']=='fail' and status['blockedGroups']==['aggregates']
    assert status['aggregateStatus']=='blocked' and 'coverage incomplete' in status['reason']
    assert status['warnings'] and not status['failures']


def test_invalid_master_still_prevents_delivery_despite_independent_aggregates(service):
    svc,source,_,calls=service
    def capture(scope,*args):
        evidence,setup=master_fixture()
        evidence['player_profiles']['0000000001']['name']=None
        return delivery.generate_masters(evidence,[],[],{},setup,scope['mode']),{'documents':{},'queries':[]}
    source.capture=capture
    svc.send_scope({'seasonId':'20232024','mode':'latest'},'operator')
    assert svc.public_status('operator')['validationPassed'] is False
    assert not calls


def test_season_scoped_contract_guard_ignores_unrelated_history_and_detects_new_registration():
    source=object.__new__(delivery.SourceCapture)
    def snap(pid,start,end,version):
        return SimpleNamespace(id=pid,reference=SimpleNamespace(path=f'players/{pid}/contracts/c'),exists=True,
            update_time=SimpleNamespace(isoformat=lambda:version),
            to_dict=lambda:{'teamId':'T01','from':start,'to':end})
    snapshots=[snap('relevant','2024.07.01',None,'v1'),snap('old','2000.07.01','2001.06.30','v1')]
    filters=[]
    class Query:
        def where(self,**kwargs): filters.append(kwargs['filter']);return self
        def select(self,fields): assert fields==['from','to'];return self
        def limit(self,n): return self
        def stream(self,**kwargs): return snapshots
    source.did=SimpleNamespace(collection_group=lambda group:Query())
    guard={'group':'contracts','filters':[('teamId','in',['T01'])],'bound':50,
           'season_range':['20240701','20250630'],'membership_only':True}
    guard['versions']=source.guard_versions(snapshots,guard)
    assert set(guard['versions'])=={'players/relevant/contracts/c'}
    snapshots[1]=snap('old','2000.07.01','2001.06.30','v2')
    source.assert_fresh({'documents':{},'queries':[guard]})
    assert filters[0].field_path=='teamId' and filters[0].value==['T01']
    snapshots.append(snap('new','2024.08.01',None,'v1'))
    with pytest.raises(BackendError) as changed: source.assert_fresh({'documents':{},'queries':[guard]})
    assert changed.value.code=='aifootballx_source_changed'


def test_code_guard_ignores_other_season_but_rejects_relevant_mapping_changes():
    source=object.__new__(delivery.SourceCapture)
    code=[{'id':'global','data':{'TCODE':'T01','IDX_NAME':'ONE'}},
          {'id':'other-season','data':{'TCODE':'T01','PlSeason':'2030-2031','IDX_NAME':'FUTURE'}}]
    scope={'season':'2024-2025','teams':['T01']}
    guard={'documents':{},'queries':[],'code_scope':scope,'reference_code':delivery.code_reference(code,scope)}
    source.target=object();source.rows=lambda *args,**kwargs:code
    code[1]['data']['IDX_NAME']='FUTURE-CHANGED'
    source.assert_fresh(guard)
    code[0]['data']={**code[0]['data'],'IDX_NAME':'CHANGED'}
    with pytest.raises(BackendError) as changed: source.assert_fresh(guard)
    assert changed.value.code=='aifootballx_source_changed'


def test_unchanged_latest_checks_source_once_and_never_creates_writer(service):
    svc,source,_,calls=service
    validations=[]
    source.assert_fresh=lambda guards:validations.append(guards)
    source.capture=lambda *args:(delivery.finish_delivery([],[],[],{},'latest'),{'documents':{},'queries':[]})
    svc.writer_factory=lambda:pytest.fail('No changed documents')
    svc.send_scope({'seasonId':'20232024','mode':'latest'},'operator')
    assert len(validations)==1 and not calls


def test_retry_checks_source_once_and_reuses_failed_document_only(service):
    svc,source,writer,calls=service
    validations=[]
    source.assert_fresh=lambda guards:validations.append(guards)
    scope={'seasonId':'20232024','mode':'matches'}
    svc.send_scope(scope,'operator')
    assert len(validations)==2 and calls==['one','two']
    writer.fail_two=False
    validations.clear()
    svc.send_scope(scope,'operator')
    assert validations==[{'documents':{},'queries':[]}]
    assert calls==['one','two','two'] and svc.public_status('operator')['status']=='complete'


def chelsea_fpx_waiver_fixture():
    evidence,_=preview_fixture()
    mid='20232024eplj2rgn13na28rd2b8q0009'
    match=evidence['did_matches'][0]
    match['id']=mid
    match['data'].update(homeTeamId='E-CH',awayTeamId='E-LI')
    for item in evidence['source_recordings']:
        item['match_id']=mid;item['match']=deepcopy(match['data'])
        item['recording']['teamId']='E-CH' if item['side']=='H' else 'E-LI'
        if item['side']=='H':
            item['children']['records'] += [{'id':f'miss-{n}','data':{'half':'H1','halfSeconds':400+n*10,
                'seq':3+n,'act':'S','res':'R','area':2,'posX':0,'posY':100}} for n in range(9)]
            kpi=calculate_kpis(source_records(item)).team_kpi
            item['recording']['kpi']=kpi
            item['recording']['teamRating'].update(apx=kpi['DAP']/4,tpx=kpi['DTP']/12*100,fpx=50.0,fpxGrade='F')
    for row,team in zip(evidence['ai_samples']['code'],('E-CH','E-LI'),strict=True): row['data']['TCODE']=team
    return evidence,mid


def test_approved_chelsea_fpx_snapshot_can_be_delivered_with_auditable_exception():
    evidence,mid=chelsea_fpx_waiver_fixture()
    report=build_preview(evidence,mid,covers=JpdDidData._contract_covers_match_date,match_only=True)
    assert report['validation_passed'],(report['errors'],[c for c in report['checks'] if not c['passed']])
    waived=[c for c in report['checks'] if c.get('waived')]
    assert len(waived)==1 and waived[0]['name']=='H:source-FPX-grade'
    assert waived[0]['actual_passed'] is False and waived[0]['stored']=='F' and waived[0]['expected']=='D'
    assert evidence['source_recordings'][0]['recording']['teamRating']['fpxGrade']=='F'
    strict=build_preview(evidence,mid,covers=JpdDidData._contract_covers_match_date)
    assert not strict['validation_passed']


@pytest.mark.parametrize('change',['match','team','opponent','score','grade'])
def test_chelsea_fpx_exception_does_not_disable_other_rating_validation(change):
    evidence,mid=chelsea_fpx_waiver_fixture()
    home=evidence['source_recordings'][0]
    if change=='match':
        mid='20232024eplj2rgn13na28rd2b8q0010'
        evidence['did_matches'][0]['id']=mid
        for item in evidence['source_recordings']: item['match_id']=mid
    if change=='team': home['recording']['teamId']='E-LI'
    if change=='opponent': home['match']['awayTeamId']='E-H'
    if change=='score': home['recording']['teamRating']['fpx']=50.1
    if change=='grade': home['recording']['teamRating']['fpxGrade']='C'
    report=build_preview(evidence,mid,covers=JpdDidData._contract_covers_match_date,match_only=True)
    assert not report['validation_passed']
    assert not any(c.get('waived') for c in report['checks'])


# Delivery policy regressions (offline; no service clients).




@pytest.mark.parametrize('match_side,signature', list(APPROVED_RAW_EXCLUSIONS.items()))
def test_reviewed_raw_exclusion_is_exact_and_never_deletes_source(match_side, signature):
    ident, player, half, seconds, act, res = signature
    event = KpiRecord(ident, half, seconds, 0, act, res, 6, player_id=player, legacy_flags={'tap': True})
    other = KpiRecord('unreviewed', half, seconds, 1, act, res, 6, player_id=player, legacy_flags={'tap': True})
    records = [event, other]
    assert reviewed_records(*match_side, records) == [other]
    assert records == [event, other]
    assert reviewed_records('another-match', match_side[1], records) == records
    from dataclasses import replace
    with pytest.raises(ValueError, match='exclusion changed'):
        reviewed_records(*match_side, [replace(event, seconds=seconds + 1)])


def test_reviewed_orphan_path_delta_does_not_waive_other_mismatches():
    match = '20242025eplj2rgn13na28rd2b8q0070'
    assert approved_path_difference(match, 'H', 'DTP', 21, 16)
    assert approved_path_difference(match, 'H', 'TTP', 69, 58)
    assert not approved_path_difference(match, 'H', 'DTP', 22, 16)
    assert not approved_path_difference(match, 'A', 'DTP', 21, 16)
    assert not approved_path_difference(match, 'H', 'SHOT', 21, 16)


def test_ssr_index_is_uncapped_and_keeps_operating_precision_without_changing_fpx():
    evidence, mid = preview_fixture()
    result = build(evidence, mid)
    home = next(d['data'] for d in result['documents'] if d['collection']=='dMST' and d['data']['H-A']=='H')
    raw = calculate_kpis(delivery.source_records(evidence['source_recordings'][0])).team_kpi
    assert home['SSRScore'] == delivery.rounded(raw['SSR'] / .18 * 100, 2)
    assert home['SSRScore'] > 99.9
    assert home['FPX'] == rating_two_decimals(evidence['source_recordings'][0]['recording']['teamRating']['fpx'])
    assert home['SSR(AiSSR)'] == delivery.rounded(raw['SSR'] / .18 * 100, 1)


def test_decimal_mean_prevents_one_hundredth_loss_before_truncation():
    assert delivery.precise_mean([99.9, 95.0, 35.05]) == 76.65
    assert rating_two_decimals(delivery.precise_mean([99.9,95.0,35.05])) == 76.65


def test_existing_registration_alias_is_preserved_verbatim_by_contract_identity():
    evidence, setup = master_fixture()
    contract = {'teamId':'E-H','leagueId':'EPL','from':'2023.01.01','no':7}
    evidence['player_contracts'] = {'0000000001':[contract]}
    old = {'PlSeason':'2023-2024','p_id':1,'l_code':'EPL','t_code':'E-H',
           'pt_begin':'2023.01.01','p_id_old':'E-EV07,E-CA34.E-HU60'}
    evidence['target_existing']['Player']=[{'id':'old','data':old}]
    assert delivery.registration_alias(evidence,'0000000001','2023-2024',contract) == old['p_id_old']
    assert delivery.registration_alias(evidence,'0000000001','2023-2024',dict(contract,from_='unused',teamId='E-A')) == ''


def test_player_names_remove_whitespace_and_literal_line_endings_only():
    for value in ('Demarai Gray\r\n',' Demarai Gray ','Demarai Gray\\r\\n'):
        assert delivery.clean_player_name(value) == 'Demarai Gray'
    assert delivery.clean_player_name('Name\\nInside') == 'Name\\nInside'


def test_club_labels_keep_operating_spelling_except_explicit_user_corrections():
    old={'t_code':'E-NF','t_Nm':'노팅엄 포레스트'}
    assert delivery.delivery_payload('dMST',dict(old,t_Nm='노팅엄 포리스트'),old)['t_Nm']==old['t_Nm']
    for team,name in [('E-EV','에버턴'),('E-LE','레스터')]:
        old={'t_code':team,'t_Nm':'old-label'}
        assert delivery.delivery_payload('dMST',dict(old),old)['t_Nm']==name
    assert delivery.delivery_payload('dMST',{'t_code':'OTHER','t_Nm':'new-label'},old)['t_Nm']=='new-label'


def test_rate_precision_preserves_only_equivalent_ieee_values_not_real_changes():
    original={'SSR':.33299999999999996}
    assert delivery.delivery_payload('pMST',{'SSR':1/3},original)['SSR']==original['SSR']
    assert delivery.delivery_payload('pMST',{'SSR':.334},original)['SSR']==.334
    assert delivery.delivery_payload('mMST',{'H-ASR':1/3})['H-ASR']==.333333
    assert delivery.delivery_payload('mMST',{'6H-SSR':1/3})['6H-SSR']==.333


def test_existing_relegated_club_registration_uses_latest_did_contract_and_real_kit():
    evidence, setup = master_fixture()
    evidence['player_profiles']['0000000001']['currentTeamId']='E-OUT'
    evidence['player_contracts']={'0000000001':[
        {'teamId':'E-H','leagueId':'EPL','from':'2020.07.23','to':'2020.07.29','no':0},
        {'teamId':'E-OUT','leagueId':'EPL','from':'2020.09.01','to':'2024.08.31','no':10},
        {'teamId':'E-OUT','leagueId':'EPL','from':'2024.05.31','to':'2024.08.31','no':0}]}
    evidence['target_existing']['playerDB']=[{'id':'old','data':{'P-1':1,'P-6':2324,'P-7':'E-OUT'}}]
    link=delivery.registration_link(evidence,'0000000001','2023-2024',evidence['did_matches'])
    assert link['teamId']=='E-OUT' and link['from']=='2024.05.31' and link['no']==10


def test_reviewed_aggregate_raw_corrections_are_projected_and_unrelated_drift_is_rejected():
    evidence, mid = preview_fixture()
    mid='20242025eplj2rgn13na28rd2b8q0070'
    source=evidence['source_recordings'][0]
    source['match_id']=mid
    source['recording']['legacyGiId']='legacy'
    for row in source['children']['records']:
        row['data'].update(legacyKpiFlags={'tap': True, 'dap': True},
                           legacyPathId=row['id'], legacyPathType='UPP', legacyPathTtp=False)
    # Fixture records carry legacy flags, so the approved stored path delta is auditable.
    computed=calculate_kpis(delivery.source_records(source)).team_kpi
    source['recording']['kpi']={**computed,'DTP':computed['DTP']+5,'TTP':computed['TTP']+11}
    items=[{'match_id':mid,'side':'H','match':source['match'],'recording':source['recording']}]
    original=deepcopy(items)
    corrected=delivery.reviewed_aggregate_items(evidence,items)
    assert corrected[0]['recording']['kpi']['DTP']==computed['DTP']
    assert corrected[0]['recording']['kpi']['TTP']==computed['TTP']
    assert items==original
    source['recording']['kpi']['SHOT']+=1
    with pytest.raises(ValueError,match='Unreviewed aggregate KPI'):
        delivery.reviewed_aggregate_items(evidence,items)


def test_master_review_capture_reads_only_affected_imported_raw_with_guards():
    capture=object.__new__(delivery.SourceCapture)
    capture.did=object()
    calls=[]
    def rows(client,path,filters=(),**kwargs):
        calls.append((path,filters,kwargs))
        return []
    capture.rows=rows
    evidence={'source_recordings':[]}
    mid='20242025eplj2rgn13na28rd2b8q0070'
    capture.capture_aggregate_review_sources(evidence,[
        {'match_id':'new-match','side':'H','recording':{},'match':{}},
        {'match_id':mid,'side':'H','recording':{'legacyGiId':'legacy'},'match':{}}])
    assert len(calls)==2 and all(c[2]['source'] for c in calls)
    assert all(mid in c[0] for c in calls)
    assert calls[0][1]==[('half','in',['H3','H4'])]
    assert evidence['source_recordings'][0]['match_id']==mid


def test_0087_apx_waiver_requires_exact_fixture_raw_delta_and_saved_rating():
    args = dict(match_id='20242025eplj2rgn13na28rd2b8q0087', side='H',
                team_id='E-EV', opponent_id='E-FU', metric='APX', imported=True,
                excluded_raw=True, stored_dap=271, original_dap=271,
                calculated_dap=270, stored_rating=67.75, baseline=400)
    assert approved_apx_difference(**args)
    for key, value in dict(match_id='another-match', side='A', team_id='E-AR',
                           opponent_id='E-CH', metric='TPX', imported=False,
                           excluded_raw=False, stored_dap=272, original_dap=272,
                           calculated_dap=269, stored_rating=68.0, baseline=450).items():
        assert not approved_apx_difference(**{**args, key: value}), key


def test_contract_resolution_keeps_histories_and_prefers_nonzero_kit_and_explicit_end():
    base = {'teamId': 'E-H', 'from': '2020-01-01', 'no': '0', 'to': None}
    records = [base, dict(base, no='42', to='2026-09-02'), {**base, 'from': '2021-01-01'}]
    before = deepcopy(records)
    result = resolve_contract_duplicates(records)
    assert len(result) == 2 and result[0]['no'] == '42' and result[0]['to'] == '2026-09-02'
    assert result[1]['no'] == '0'
    assert records == before


def test_francis_end_uses_creation_order_not_batch_repair_order():
    old = {'teamId': 'E-LT', 'from': '2023-05-31', 'to': '2023-09.02',
           'createdAt': '2026-09-09T07:04:05Z', 'updatedAt': '2026-10-02T07:10:19.114589Z'}
    new = dict(old, to='2023-08-31', createdAt='2026-10-01T07:02:51Z', updatedAt='2026-10-02T07:10:19.114543Z')
    assert resolve_contract_duplicates([old, new])[0]['to'] == '2023-08-31'
    for left, right in [(dict(old, createdAt=None), new), (old, dict(new, createdAt=old['createdAt']))]:
        with pytest.raises(ValueError, match='Conflicting DID contract identity'):
            resolve_contract_duplicates([left, right])
    with pytest.raises(ValueError, match='field no'):
        resolve_contract_duplicates([dict(old, no='11'), dict(old, no='42')])


def test_overlay_preserves_operating_fields_but_replaces_generated_nested_maps():
    old = {'Coach': 'coach', 'Captain': 'captain', 'PDF': None, 'W-TOT': 8,
           'W-TOT2': 11, 'calculation': {'removed': 1, 'kept': 2}, 'GOAL': 0}
    new = {'GOAL': 4, 'calculation': {'kept': 3}}
    result = delivery.delivery_payload('dMST', new, old)
    assert result == {**old, **new}
    assert result['calculation'] == {'kept': 3}
    assert old['calculation']['removed'] == 1 and new['GOAL'] == 4


def test_overlay_removes_absent_generated_fields_but_keeps_operating_notes():
    result = delivery.delivery_payload('multiSheet_ALL', {'DTP-H1-0-5': 1},
        {'DTP-H1-0-5': 2, 'DTP-H3-0-5': 9, 'Coach': 'manual'})
    assert result == {'DTP-H1-0-5': 1, 'Coach': 'manual'}
    result = delivery.delivery_payload('rank', {'WRK1': 1}, {'WRK21': 21, 'note': 'keep', 'WGR1': 2})
    assert result == {'WRK1': 1, 'note': 'keep'}


def test_kpi_builder_never_synthesizes_raw_from_obsolete_path_metadata(monkeypatch):
    event = KpiRecord('one', 'H1', 20, 0, 'S', 'X', 2, legacy_path_id='current',
        legacy_path_type='DTP', legacy_path_ttp=True, legacy_flags={'shot': True})
    record = SimpleNamespace(half=event.half, halfSeconds=event.seconds, seq=event.seq,
        act=event.act, res=event.res, area=event.area, playerId=None, shootPosX=None,
        shootPosY=None, shootDspRange=None, isShot=None, createdBy='legacy-import',
        createdAt=datetime(2026, 1, 1, tzinfo=timezone.utc), legacyPathId='current',
        legacyPathType='DTP', legacyPathTtp=True, legacyKpiFlags={'shot': True}, bapReason=None)
    source = SimpleNamespace(records=[('one', record)], fingerprint='raw-version',
        recording=SimpleNamespace(kpi=RecordingKpi(**calculate_kpis([event]).team_kpi),
                                 kpiVersion=1, kpiSourceFingerprint=None, lineup={}),
        paths=[('obsolete', SimpleNamespace(gtId='obsolete', ptype='DTP', ttp=True))])
    saved = []
    data = SimpleNamespace(read_match_kpi_source=lambda *args: source,
        list_player_stats=lambda *args: {}, save_recording_kpi=lambda *args, **kwargs: saved.append(kwargs))
    monkeypatch.setattr(BuildMatchKpis, '_save_data_artifact', staticmethod(lambda *args: None))
    result = BuildMatchKpis(data).rebuild_for_logic_change('match', 'H')
    assert not result.validation_failed and len(saved) == 1
    assert saved[0]['kpi'].DTP == 1 and saved[0]['kpi'].TTP == 1


def test_preserved_master_fields_are_allowed_only_when_unchanged_in_baseline():
    old = {'m_id': 1, 'Coach': 'manual', 'Captain': 'manual-captain'}
    result = delivery.finish_delivery([{'collection': 'Manager', 'document_id': 'one', 'data': {'m_id': 2}}],
        [], [], {'Manager': [{'id': 'one', 'data': old}]}, 'all')
    doc = result['documents'][0]
    assert doc['data'] == dict(old, m_id=2)
    delivery.validate_document(doc)
    doc['data']['Coach'] = 'unreviewed-change'
    with pytest.raises(BackendError, match='Unowned fields'):
        delivery.validate_document(doc)


def test_preserved_metadata_keeps_firestore_timestamp_and_nested_types():
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    current = {'createdAt': stamp, 'manual': {'timestamp': stamp}, 'calculation': {'removed': 1}}
    reviewed = delivery.delivery_payload('dMST', {'calculation': {'new': 2}}, delivery.encode(current))
    payload = delivery.firestore_payload({'data': reviewed}, current)
    assert payload['createdAt'] == stamp and isinstance(payload['createdAt'], datetime)
    assert payload['manual']['timestamp'] == stamp
    assert payload['calculation'] == {'new': 2}


@pytest.mark.parametrize('value,expected', [(10.685, 10.68), (9.435, 9.43), (10.185, 10.18),
                                           (99.9999, 99.99), (0, 0), (10.68, 10.68)])
def test_delivery_ratings_truncate_two_decimals_without_mutating_source(value, expected):
    assert rating_two_decimals(value) == expected
    original = {'P-JMX': value, 'P-APX': value, 'P-DTP': 7, 'P-SSR': .123456}
    result = delivery.delivery_payload('vibePlayer', original)
    assert result['P-JMX'] == result['P-APX'] == expected
    assert result['P-DTP'] == 7 and result['P-SSR'] == .123
    assert original['P-JMX'] == value


@pytest.mark.parametrize('reverse', [False, True])
def test_scientific_path_id_binds_only_the_same_event_in_both_directions(reverse):
    raw, scientific = '67076605852888.11901468', '6.70766E+13'
    event = dict(gm_id='match', t_code='TEAM', gr_half='H1', gr_half_seconds=5,
                 gt_id=raw, p_id='7', gr_act_code='P', gr_res_code='O', gr_pos_x=20, gr_pos_y=30)
    old = dict(event, gt_id=scientific)
    if reverse:
        event, old = old, event
    rows = [{'id': 'existing', 'data': old}]
    assert delivery.path_ids_equivalent(raw, scientific)
    assert delivery.path_ids_equivalent(scientific, raw)
    assert delivery.matching_rows('TACTIC', event, rows) == rows
    assert not delivery.matching_rows('TACTIC', dict(event, gr_half_seconds=6), rows)
    assert not delivery.path_ids_equivalent('67079999999999', scientific)
    assert not delivery.path_ids_equivalent('hex-path', scientific)


def test_scientific_path_mapping_keeps_ambiguity_visible():
    event = dict(gm_id='match', t_code='TEAM', gr_half='H1', gr_half_seconds=5,
                 gt_id='6.70766E+13', p_id='7', gr_act_code='P', gr_res_code='O')
    rows = [{'id': str(n), 'data': dict(event, gt_id=value)} for n, value in enumerate([
        '67076605852888.11901468', '67076605852889.11901468'])]
    assert len(delivery.matching_rows('TACTIC', event, rows)) == 2


def test_player_history_uses_did_team_start_identity_and_preserves_sentinels():
    evidence, setup = master_fixture()
    evidence['player_contracts'] = {'0000000001': [
        {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '0000.00.00', 'to': '2024.01.01', 'no': 7},
        {'teamId': 'E-A', 'leagueId': 'EPL', 'from': '2024.01.02', 'to': '2024.02.01', 'no': 8},
        {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2024.02.02', 'to': None, 'no': 9},
    ]}
    original = deepcopy(evidence)
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    players = docs(report, 'Player')
    assert len(players) == 3
    assert {(p['t_code'], p['pt_begin'], p['pt_end']) for p in players} == {
        ('E-H', '0000.00.00', '2024.01.01'), ('E-A', '2024.01.02', '2024.02.01'),
        ('E-H', '2024.02.02', '9999.99.99')}
    assert len({delivery.natural_key('Player', p) for p in players}) == 3
    assert len(docs(report, 'playerDB')) == 1
    assert docs(report, 'playerDB')[0]['P-7'] == 'E-H'
    assert evidence == original
    for doc in report['documents']:
        evidence['target_existing'].setdefault(doc['collection'], []).append(
            {'id': doc['document_id'], 'data': deepcopy(doc['data'])})
    before = {delivery.natural_key('Player', d['data']): d['document_id']
              for d in report['documents'] if d['collection'] == 'Player'}
    evidence['target_existing']['Player'][0]['data']['p_id_old'] = 'FOREIGN-999'
    evidence['player_contracts']['0000000001'][-1]['to'] = '2024.06.30'
    closed = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert closed['validation_passed'], closed['errors']
    assert any(p['p_id_old'] == 'FOREIGN-999' for p in docs(closed, 'Player'))
    assert {delivery.natural_key('Player', d['data']): d['document_id']
            for d in closed['documents'] if d['collection'] == 'Player'} == before


def test_player_dtp_is_maker_plus_assister_not_paths_buildup_or_shots():
    assert delivery.player_tactical_participation(dict(DTM=2, DTA=3, DTB=9, DTS=7, DTP=11)) == 5
    assert delivery.player_tactical_participation(dict(DTM=0, DTA=0, DTB=2, DTS=1, DTP=3)) == 0


@pytest.mark.parametrize('half', ['H3', 'H4'])
def test_extra_half_events_do_not_block_delivery_or_change_source(half):
    evidence, mid = preview_fixture()
    item = evidence['source_recordings'][0]
    extra = deepcopy(item['children']['records'][0])
    extra['id'] = 'test-extra-half'
    extra['data'].update(half=half, halfSeconds=0)
    item['children']['records'].append(extra)
    before = deepcopy(evidence)
    result = build(evidence, mid)
    assert result['validation_passed'], result['errors']
    assert any('excluded from delivery eligibility' in warning for warning in result['warnings'])
    assert all(d['data'].get('gr_half') not in ('H3', 'H4') for d in result['documents'])
    assert evidence == before


def test_extra_half_rating_baseline_waiver_requires_the_full_raw_explanation():
    evidence, mid = preview_fixture()
    item = evidence['source_recordings'][0]
    for row in item['children']['records']:
        row['data'].update(legacyPathId='main', legacyPathType='DTP', legacyPathTtp=True,
            legacyKpiFlags={'tap': True, 'dap': True,
                            'shot': row['id']=='shot', 'goal': row['id']=='shot'})
    extra = deepcopy(item['children']['records'][0])
    extra['id'] = 'extra-dap'
    extra['data'].update(half='H4', halfSeconds=0, legacyPathId='extra',
                         legacyPathType='UPP', legacyPathTtp=False)
    item['children']['records'].append(extra)
    full = calculate_kpis(delivery.source_records(item, include_extra_halves=True)).team_kpi
    item['recording']['kpi'] = full
    item['recording']['teamRating']['apx'] = full['DAP'] / 4
    result = build(evidence, mid)
    assert result['validation_passed'], [c for c in result['checks'] if not c['passed']]
    assert any(c['name']=='H:APX-baseline' and c.get('excluded_extra_halves') for c in result['checks'])
    item['recording']['teamRating']['apx'] += 2
    assert not build(evidence, mid)['validation_passed']


def test_reviewed_path_correction_does_not_certify_a_rating_from_old_kpi():
    evidence, old_mid = preview_fixture()
    mid = '20242025eplj2rgn13na28rd2b8q0070'
    evidence['did_matches'][0]['id'] = mid
    for item in evidence['source_recordings']:
        item['match_id'] = mid
    item = evidence['source_recordings'][0]
    item['recording']['kpi']['DTP'] += 5
    item['recording']['kpi']['TTP'] += 11
    result = build(evidence, mid)
    checks = {c['name']: c for c in result['checks']}
    assert checks['H:source-KPI-DTP']['passed']
    assert checks['H:source-KPI-TTP']['passed']
    assert not checks['H:reviewed-KPI-rebuild']['passed']


@pytest.mark.parametrize('legacy', [False, True])
def test_ssr_excludes_own_in_team_cumulative_and_rating_inputs_without_changing_goals(legacy):
    def record(ident, half, seconds, result, player, goal):
        return KpiRecord(ident, half, seconds, 0, 'S', result, 4, player_id=player,
            legacy_path_id=ident if legacy else None, legacy_path_type='STP' if legacy else None,
            legacy_path_ttp=False if legacy else None,
            legacy_flags={'shot': player!='OWN', 'goal': goal} if legacy else None)
    records = [record('normal', 'H1', 10, 'GOAL', 'scorer', True),
               record('own', 'H2', 300, 'GOAL', 'OWN', True),
               record('miss', 'H2', 600, 'X', 'shooter', False)]
    result = calculate_kpis(records)
    assert result.team_kpi['GOAL']==2 and result.team_kpi['OG']==1 and result.team_kpi['SHOT']==2
    assert result.team_kpi['SSR']==.5
    assert 'OWN' not in result.player_kpis
    values = delivery.intervals(records, result)
    assert sum(values['GOAL'])==2 and sum(values['SHOT'])==2
    assert values['SSR'][10:13]==[1, 1, .5]
    rating = calculate_team_kpi_5min(records, {'H1': 2700, 'H2': 3000})
    assert rating[11]['GOAL']==2 and rating[11]['SSR']==1
    assert rating[-1]['GOAL']==2 and rating[-1]['SSR']==.5
    zero = calculate_kpis([records[1]])
    assert zero.team_kpi['GOAL']==1 and zero.team_kpi['SHOT']==0 and zero.team_kpi['SSR']==0


def test_imported_og_placeholder_is_audited_without_requiring_new_input_kpi_post():
    evidence, mid = preview_fixture()
    item = evidence['source_recordings'][0]
    for row in item['children']['records']:
        row['data'].update(legacyPathId='main', legacyPathType='DTP', legacyPathTtp=True,
            legacyKpiFlags={'tap': True, 'dap': True,
                            'shot': row['id']=='shot', 'goal': row['id']=='shot'})
    own = deepcopy(item['children']['records'][-1])
    own['id']='own'
    own['data'].update(half='H2', halfSeconds=100, playerId='OWN', legacyPathId='own',
        legacyPathType='STP', legacyPathTtp=False, legacyKpiFlags={'goal': True})
    item['children']['records'].append(own)
    raw = calculate_kpis(delivery.source_records(item)).team_kpi
    item['recording'].update(legacyGiId='imported', kpi={**raw, 'OG': 0})
    item['recording']['teamRating'].update(apx=raw['DAP']/4, tpx=raw['DTP']/12*100,
        fpx=(min(raw['SHOT']/15*100, 99.9)+min(raw['GOAL']/3*100, 99.9))/2)
    result = build(evidence, mid)
    assert result['validation_passed'], [c for c in result['checks'] if not c['passed']]
    goal_sheet = next(d['data'] for d in result['documents']
                      if d['collection']=='multiSheet_GOAL' and d['data']['team_type']=='HOME')
    assert sum(v for k,v in goal_sheet.items() if k.startswith('GOAL-H')) == raw['GOAL'] - raw['OG']
    assert goal_sheet['GOAL-H2-0-5'] == 0
    check = next(c for c in result['checks'] if c['name']=='H:source-KPI-OG')
    assert check['legacy_og_placeholder'] and not check['actual_passed']
    assert item['recording']['kpi']['OG']==0
    evidence['target_existing']['playerGoal']=[{'id':'old-own', 'data':{
        'gm_id':mid,'IDX_CODE':'HX','gr_half':'H2','gr_half_seconds':100,'p_id':'OWN'}}]
    for current in evidence['source_recordings']:
        rating=current['recording']['teamRating']
        rating.update({metric+'Grade':delivery.grade(rating[metric]) for metric in ('apx','tpx','fpx')})
    full=delivery.generate_delivery(evidence,evidence['did_matches'],[],{}, {'documents':[]},match_only=True)
    assert full['validation_passed'], full['errors']
    assert full['excluded_existing_documents'][0]['document_id']=='old-own'
    assert not any(d['collection']=='playerGoal' and d['data'].get('p_id')=='OWN' for d in full['documents'])
    evidence['target_existing']['playerGoal'][0]['data']['gr_half_seconds']=101
    mismatch=delivery.generate_delivery(evidence,evidence['did_matches'],[],{}, {'documents':[]},match_only=True)
    assert not mismatch['validation_passed']
    item['recording'].pop('legacyGiId')
    assert not build(evidence, mid)['validation_passed']


def test_imported_reviewed_path_delta_does_not_require_a_kpi_post_but_still_checks_ratings():
    evidence, _ = preview_fixture()
    mid='20242025eplj2rgn13na28rd2b8q0070'
    evidence['did_matches'][0]['id']=mid
    for item in evidence['source_recordings']: item['match_id']=mid
    item=evidence['source_recordings'][0]
    for row in item['children']['records']:
        row['data'].update(legacyPathId='main', legacyPathType='DTP', legacyPathTtp=True,
            legacyKpiFlags={'tap': True, 'dap': True,
                            'shot': row['id']=='shot', 'goal': row['id']=='shot'})
    raw=calculate_kpis(delivery.source_records(item)).team_kpi
    item['recording'].update(legacyGiId='imported', kpi={**raw, 'DTP':raw['DTP']+5, 'TTP':raw['TTP']+11})
    item['recording']['teamRating'].update(apx=raw['DAP']/4, tpx=raw['DTP']/12*100)
    result=build(evidence, mid)
    assert result['validation_passed'], [c for c in result['checks'] if not c['passed']]
    assert next(c for c in result['checks'] if c['name']=='H:reviewed-import-KPI-difference')['passed']
    item['recording']['teamRating']['apx']+=2
    assert not build(evidence, mid)['validation_passed']


def test_dtp_uses_final_shot_result_even_when_later_touch_is_in_same_path():
    def record(ident, seconds, act, result, flags):
        return KpiRecord(ident, 'H1', seconds, 0, act, result, 2,
                         legacy_path_id='path', legacy_path_type='DTP', legacy_path_ttp=True,
                         legacy_flags=flags)
    records = [record('pass', 290, 'P', 'O', {'dap': True}),
               record('shot', 299, 'S', 'O', {'shot': True}),
               record('result', 300, '', 'X', {}),
               record('unrelated-later-touch', 601, 'P', 'X', {})]
    bins = delivery.intervals(records, calculate_kpis(records))
    assert bins['DTP'][1] == 1 and bins['DTP'][2] == 0
    with pytest.raises(ValueError, match='result is missing'):
        delivery.intervals(records[:2], calculate_kpis(records[:2]))


@pytest.mark.parametrize('collection', sorted(delivery.EXCLUDED_COLLECTIONS))
def test_excluded_collections_cannot_reach_the_writer(collection):
    with pytest.raises(BackendError, match='Invalid delivery collection'):
        delivery.validate_document({'collection': collection, 'document_id': 'id', 'data': {'value': 1}})
    assert collection not in delivery.MATCH_COLLECTIONS
    assert collection not in delivery.MASTER_COLLECTIONS


def test_full_replacement_removes_old_fields_and_guards_the_entire_baseline(monkeypatch):
    from firebase_admin import firestore
    monkeypatch.setattr(firestore, 'transactional', lambda fn: fn)
    old = {'m_id': 10, 'uid': 'one', 'obsolete': 99, 'updatedAt': datetime(2026, 1, 1, tzinfo=timezone.utc)}
    current = {'Manager/one': old}
    writes = []
    class Ref:
        def __init__(self, path): self.path = path
        def document(self, ident): return Ref(self.path + '/' + ident)
        def get(self, transaction):
            return SimpleNamespace(exists=self.path in current, to_dict=lambda: current.get(self.path))
    class Transaction:
        def set(self, ref, value, merge=False):
            writes.append((ref.path, merge))
            current[ref.path] = deepcopy(value)
    client = SimpleNamespace(project='aifootballx', collection=lambda name: Ref(name), transaction=Transaction)
    doc = document()
    doc.update(existed=True, baseline=delivery.replacement_baseline(doc['data'], delivery.encode(old)))
    assert delivery.FirestoreWriter(client).send(doc, 'preview') == 'success'
    assert current['Manager/one'] == doc['data']
    assert ('Manager/one', False) in writes
    with pytest.raises(BackendError, match='document changed'):
        delivery.decide_merge(doc, dict(old, obsolete=100))


def test_rating_request_preserves_legacy_flags_and_own_goal_team_inputs(monkeypatch):
    flags = {'shot': False, 'goal': True}
    record = SimpleNamespace(half='H1', halfSeconds=20, seq=0, act='S', res='GOAL', area=2,
        playerId='OWN', shootPosX=0, shootPosY=0, shootDspRange=None, isShot=None,
        createdBy='legacy-import', createdAt=datetime(2026, 1, 1, tzinfo=timezone.utc),
        legacyPathId='path', legacyPathType='STP', legacyPathTtp=False, legacyKpiFlags=flags, bapReason=None)
    kpi = {'GOAL': 2, 'OG': 1, 'SHOT': 1, 'SSR': 1}
    scorer = SimpleNamespace(**{**vars(record), 'playerId': '0000000001', 'halfSeconds': 19,
        'legacyPathId': 'scorer-path', 'legacyKpiFlags': {'shot': True, 'goal': True}})
    source = SimpleNamespace(records=[('record', record), ('scorer', scorer)],
        recording=SimpleNamespace(kpi=SimpleNamespace(model_dump=lambda **kwargs: kpi), kpiVersion=1,
            teamRating=None, ratingBasedOn=None, teamId='T1', opponentTeamId='T2', halves={}),
        match=SimpleNamespace(leagueId='EPL', seasonId='20262027', matchType='league', round=1))
    captured = {}
    def intervals(records, ends):
        captured['records'] = records
        return [calculate_kpis(records).team_kpi] * 20
    def request(body):
        captured['body'] = body
        raise RuntimeError('offline request captured')
    monkeypatch.setattr('backend.build.build_match_rating.calculate_team_kpi_5min', intervals)
    monkeypatch.setattr(BuildMatchRating, '_request_rating', staticmethod(request))
    data = SimpleNamespace(read_match_kpi_source=lambda *args: source, list_player_stats=lambda *args: {})
    with pytest.raises(RuntimeError, match='offline request captured'):
        BuildMatchRating(data).build('match', 'H', force=True)
    assert captured['records'][0].legacy_flags == flags
    assert captured['body']['teamKpi'] == kpi
    # Approved import OG=0 needs no KPI POST or source rewrite. Its request
    # must still carry RAW OG and the same OWN-exclusive success ratio.
    source.recording.legacyGiId='legacy'
    kpi['OG']=0
    with pytest.raises(RuntimeError, match='offline request captured'):
        BuildMatchRating(data).build('match', 'H', force=True)
    assert kpi['OG']==0 and captured['body']['teamKpi']['OG']==1
    assert captured['body']['teamKpi']['SSR']==1


def test_rating_cache_requires_exact_current_input_fingerprint(monkeypatch):
    event=SimpleNamespace(half='H1',halfSeconds=20,seq=0,act='S',res='GOAL',area=2,
        playerId='p',shootPosX=0,shootPosY=0,shootDspRange=None,isShot=None,
        createdBy='legacy-import',createdAt=datetime(2026,1,1,tzinfo=timezone.utc),
        legacyPathId='path',legacyPathType='STP',legacyPathTtp=False,
        legacyKpiFlags={'shot':True,'goal':True,'dap':True},bapReason=None)
    raw=KpiRecord('event','H1',20,0,'S','GOAL',2,'p',legacy_path_id='path',
                  legacy_path_type='STP',legacy_flags=event.legacyKpiFlags)
    kpi=calculate_kpis([raw]).team_kpi
    source=SimpleNamespace(records=[('event',event)],fingerprint='source-v1',
        recording=SimpleNamespace(kpi=SimpleNamespace(model_dump=lambda **kw:kpi),kpiVersion=1,
            ratingBasedOn=1,teamRating={'jmx':50},ratingInputFingerprint=None,
            teamId='T1',opponentTeamId='T2',halves={}),
        match=SimpleNamespace(leagueId='EPL',seasonId='20262027',matchType='league',round=1))
    requests=[]
    def request(body):
        requests.append(deepcopy(body))
        return {'teamRating':{'jmx':50,'jmxSeries':[50]*20,'apx':.25,'apxGrade':'F',
                'tpx':0,'tpxGrade':'F','fpx':20,'fpxGrade':'F'},'playerRatings':[]}
    def save(*args,**kwargs):
        source.recording.ratingInputFingerprint=kwargs['input_fingerprint']
        source.recording.teamRating=kwargs['team_rating']
        return 0
    monkeypatch.setattr(BuildMatchRating,'_request_rating',staticmethod(request))
    data=SimpleNamespace(read_match_kpi_source=lambda *a:source,
                         list_player_stats=lambda *a:{},save_recording_rating=save)
    builder=BuildMatchRating(data)
    assert not builder.build('match','H').unchanged
    assert len(requests)==1 and source.recording.ratingInputFingerprint
    assert builder.build('match','H').unchanged and len(requests)==1
    source.recording.ratingInputFingerprint='obsolete-policy'
    assert not builder.build('match','H').unchanged and len(requests)==2


def test_rating_rejects_inconsistent_final_interval_before_request(monkeypatch):
    event=SimpleNamespace(half='H1',halfSeconds=20,seq=0,act='S',res='GOAL',area=2,
        playerId='p',shootPosX=0,shootPosY=0,shootDspRange=None,isShot=None,
        createdBy='legacy-import',createdAt=datetime(2026,1,1,tzinfo=timezone.utc),
        legacyPathId='path',legacyPathType='STP',legacyPathTtp=False,
        legacyKpiFlags={'shot':True,'goal':True,'dap':True},bapReason=None)
    raw=KpiRecord('event','H1',20,0,'S','GOAL',2,'p',legacy_path_id='path',
                  legacy_path_type='STP',legacy_flags=event.legacyKpiFlags)
    kpi=calculate_kpis([raw]).team_kpi
    source=SimpleNamespace(records=[('event',event)],recording=SimpleNamespace(
        kpi=SimpleNamespace(model_dump=lambda **kw:kpi),kpiVersion=1,teamId='T1',opponentTeamId='T2',halves={}),
        match=SimpleNamespace(leagueId='EPL',seasonId='20262027',matchType='league',round=1))
    monkeypatch.setattr('backend.build.build_match_rating.calculate_team_kpi_5min',
                        lambda *a:[{**kpi,'DAP':kpi['DAP']+5}]*20)
    monkeypatch.setattr(BuildMatchRating,'_request_rating',staticmethod(lambda b:pytest.fail('Request must be blocked')))
    with pytest.raises(BackendError) as rejected:
        BuildMatchRating(SimpleNamespace(read_match_kpi_source=lambda *a:source,list_player_stats=lambda *a:{})).build('match','H')
    assert rejected.value.code=='rating_interval_kpi_mismatch'


def test_legacy_dtp_is_not_counted_until_its_shot_result_and_uses_half_open_buckets():
    def record(ident, seconds, act, result, flags):
        return KpiRecord(ident, 'H1', seconds, 0, act, result, 2,
                         legacy_path_id='path', legacy_path_type='DTP', legacy_path_ttp=True,
                         legacy_flags=flags)
    records = [record('pass', 10, 'P', 'O', {'dap': True}),
               record('shot', 299, 'S', 'O', {'shot': True}),
               record('result', 300, '', 'X', {})]
    snapshots = calculate_team_kpi_5min(records)
    bins = delivery.intervals(records, calculate_kpis(records))
    assert snapshots[0]['DTP'] == bins['DTP'][0] == 0
    assert snapshots[1]['DTP'] == bins['DTP'][1] == 1
    assert snapshots[-1]['DTP'] == 1
    with pytest.raises(ValueError, match='result is missing'):
        calculate_team_kpi_5min(records[:2])


def test_modern_rating_input_uses_same_finalized_dap_flags_as_delivery_intervals():
    records=[KpiRecord('first','H1',299,0,'P','O',2,'p'),
             KpiRecord('second','H1',301,1,'P','X',2,'p')]
    computed=calculate_kpis(records)
    bins=delivery.intervals(records,computed)
    snapshots=calculate_team_kpi_5min(records)
    assert snapshots[0]['DAP']==bins['DAP'][0]==1
    assert snapshots[1]['DAP']==sum(bins['DAP'][:2])==2
    assert calculate_kpis(records[:1]).team_kpi['DAP']==0
    for index, snapshot in enumerate(snapshots):
        for field in ('DAP','DTP','SHOT','GOAL'):
            assert snapshot[field]==sum(bins[field][:index+1])
        assert snapshot['SSR']==bins['SSR'][index]


def test_last_rating_bucket_keeps_stoppage_records_even_with_a_short_stored_half_duration():
    records = [KpiRecord('late-shot', 'H2', 3301, 0, 'S', 'GOAL', 2, 'p')]
    assert calculate_team_kpi_5min(records, {'H2': 2900})[-1]['GOAL'] == 1


def test_h4_player_eligibility_waives_only_differences_explained_by_the_excluded_records():
    evidence, mid = preview_fixture()
    item = evidence['source_recordings'][0]
    pid = '0000000007'
    for row in item['children']['records']:
        row['data']['playerId'] = pid
    extra = deepcopy(item['children']['records'][0])
    extra['id'] = 'extra-h4'
    extra['data'].update(half='H4', halfSeconds=0)
    item['children']['records'].append(extra)
    full = calculate_kpis(delivery.source_records(item, include_extra_halves=True))
    item['recording']['kpi'] = full.team_kpi
    item['recording']['lineup'][pid] = {'type': 'START', 'pos': 'MF'}
    item['children']['playerStats'] = [{'id': pid, 'data': {
        **full.player_kpis[pid], 'ratingBasedOn': 1, 'apx': 70, 'tpx': 60, 'fpx': 50, 'jmx': 5}}]
    evidence['player_profiles'][pid] = {'name': 'Player'}
    evidence['player_contracts'][pid] = [{'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2023-01-01'}]
    before = deepcopy(evidence)
    result = build(evidence, mid)
    assert result['validation_passed'], (result['errors'], [c for c in result['checks'] if not c['passed']])
    assert any(c.get('excluded_extra_halves') for c in result['checks'])
    assert evidence == before
    item['children']['playerStats'][0]['data']['TAP'] += 1
    rejected = build(evidence, mid)
    assert not rejected['validation_passed']
    assert any(c['name'].endswith('player-7-TAP') and not c['passed'] for c in rejected['checks'])


def test_historical_contract_uses_a_verified_cross_season_index_without_relaxing_match_codes():
    evidence, setup = master_fixture()
    evidence['player_profiles'] = {'0000000001': {'name': 'Retired'}}
    evidence['player_contracts'] = {'0000000001': [
        {'teamId': 'E-OLD', 'leagueId': 'EPL', 'from': '2000-01-01', 'to': '2001-01-01'}]}
    evidence['ai_samples']['code'].append({'data': {'TCODE': 'E-OLD', 'PlSeason': '2020-2021', 'IDX_NAME': 'OLDX'}})
    before = deepcopy(evidence)
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert docs(report, 'Player')[0]['IDX_CODE'] == 'OLDX'
    assert not docs(report, 'playerDB')
    assert evidence == before
    with pytest.raises(ValueError, match='season-specific'):
        delivery.resolve_team_code(evidence['ai_samples']['code'], '2023-2024', 'E-OLD')
    evidence['ai_samples']['code'].append({'data': {'TCODE': 'E-OLD', 'PlSeason': '2021-2022', 'IDX_NAME': 'OTHER'}})
    assert not delivery.generate_masters(evidence, [], [], {}, setup, 'all')['validation_passed']


def test_existing_contract_alias_is_bound_by_team_and_start_instead_of_shirt_number():
    evidence, setup = master_fixture()
    pid = '0000000001'
    evidence['player_contracts'] = {pid: [{'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2023-01-01', 'no': 38}]}
    evidence['target_existing']['Player'] = [{'id': 'old', 'data': {
        'PlSeason': '2023-2024', 'p_id': 1, 'l_code': 'EPL', 't_code': 'E-H',
        'pt_begin': '2023.01.01', 'IDX_CODE': 'HX', 'p_id_old': 'E-H111'}}]
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert docs(report, 'Player')[0]['p_id_old'] == 'E-H111'
    assert docs(report, 'Player')[0]['pt_num'] == 38
    assert docs(report, 'playerDB')[0]['P-17'] == 'E-H111'
    evidence['player_contracts'][pid][0]['from'] = '2023-01-02'
    assert docs(delivery.generate_masters(evidence, [], [], {}, setup, 'all'), 'Player')[0]['p_id_old'] == ''


@pytest.mark.parametrize('bundle', ['F-PS04,E-H111,USA121', 'E-H', 'E-OLD11,ENG117'])
def test_verified_registration_alias_preserves_the_existing_multi_club_national_bundle(bundle):
    evidence, setup = master_fixture()
    evidence['player_contracts'] = {'0000000001': [
        {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2023-01-01', 'no': 38}]}
    evidence['target_existing']['Player'] = [{'id': 'old', 'data': {
        'PlSeason': '2023-2024', 'p_id': 1, 'l_code': 'EPL', 't_code': 'E-H',
        'pt_begin': '2023.01.01', 'IDX_CODE': 'HX', 'p_id_old': bundle}}]
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert docs(report, 'Player')[0]['p_id_old'] == bundle
    assert docs(report, 'playerDB')[0]['P-17'] == bundle


def test_player_db_abbreviation_is_not_the_player_index_and_open_dates_are_explicit():
    evidence, setup = master_fixture()
    evidence['ai_samples']['code'][0]['data']['구단(약자)'] = 'HOM'
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert docs(report, 'Player')[0]['IDX_CODE'] == 'HX'
    assert docs(report, 'playerDB')[0]['P-8'] == 'HOM'
    assert docs(report, 'playerDB')[0]['P-14'] == '0000.00.00'
    assert docs(report, 'playerDB')[0]['P-15'] == '9999.99.99'


def test_registration_namespaces_can_differ_from_chart_team_codes():
    evidence, setup = master_fixture()
    evidence['target_existing']['Player'] = [{'id': 'old', 'data': {
        'PlSeason': '2023-2024', 'p_id': 1, 'l_code': 'EPL', 't_code': 'E-H',
        'pt_begin': '0000.00.00', 'IDX_CODE': 'LEGACYX'}}]
    evidence['target_existing']['playerDB'] = [{'id': 'old-db', 'data': {
        'P-6': 2324, 'P-1': 1, 'P-7': 'E-H', 'P-8': 'LEG'}}]
    evidence['ai_samples']['code'][0]['data']['구단(약자)'] = 'HOM'
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert docs(report, 'Player')[0]['IDX_CODE'] == 'LEGACYX'
    assert docs(report, 'playerDB')[0]['P-8'] == 'LEG'
    assert delivery.resolve_team_code(evidence['ai_samples']['code'], '2023-2024', 'E-H')['IDX_CODE'] == 'HX'


def test_a_future_transfer_does_not_create_a_season_registration():
    evidence, setup = master_fixture()
    evidence['player_contracts'] = {'0000000001': [
        {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2026-01-01'}]}
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert not docs(report, 'playerDB')


def test_retired_history_is_preserved_without_creating_an_active_registration():
    evidence, setup = master_fixture()
    evidence['player_profiles']['0000000001'].pop('currentTeamId')
    evidence['player_contracts'] = {'0000000001': [
        {'teamId': 'E-H', 'leagueId': 'EPL', 'from': '2000-01-01', 'to': '2001-01-01'}]}
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert len(docs(report, 'Player')) == 1
    assert not docs(report, 'playerDB')


def test_explicit_rating_build_rejects_incorrect_og_metadata_before_any_request(monkeypatch):
    record = SimpleNamespace(half='H1', halfSeconds=20, seq=0, act='S', res='GOAL', area=2,
        playerId='OWN', shootPosX=0, shootPosY=0, shootDspRange=None, isShot=None,
        createdBy='legacy-import', createdAt=datetime(2026, 1, 1, tzinfo=timezone.utc),
        legacyPathId='own', legacyPathType='STP', legacyPathTtp=False,
        legacyKpiFlags={'goal': True}, bapReason=None)
    source = SimpleNamespace(records=[('own', record)], recording=SimpleNamespace(
        kpi=SimpleNamespace(model_dump=lambda **kwargs: {'GOAL': 1, 'OG': 0, 'SHOT': 0, 'SSR': 0}),
        kpiVersion=1, ratingBasedOn=1, teamRating={'jmx': 80}))
    data = SimpleNamespace(read_match_kpi_source=lambda *args: source)
    monkeypatch.setattr(BuildMatchRating, '_request_rating', staticmethod(lambda body: pytest.fail('Unexpected rating request')))
    with pytest.raises(BackendError) as rejected:
        BuildMatchRating(data).build('match', 'H')
    assert rejected.value.code == 'rating_kpi_definition_mismatch'


def test_legacy_own_goal_baseline_is_recognized_but_arbitrary_ssr_drift_is_rejected():
    raw = RecordingKpi(**{k: 0 for k in RecordingKpi.model_fields}).model_copy(
        update={'GOAL': 2, 'OG': 1, 'SHOT': 10, 'SSR': .1})
    imported = raw.model_copy(update={'OG': 0, 'SSR': .1})
    assert _team_differences(imported, raw) == {}
    assert 'SSR' in _team_differences(imported.model_copy(update={'SSR': .12}), raw)
    assert 'GOAL' in _team_differences(imported.model_copy(update={'GOAL': 3}), raw)
    assert 'SSR' in _team_differences(imported, raw.model_copy(update={'SSR': .15}))


def test_registration_reference_guard_rejects_changed_aliases_before_send():
    source = object.__new__(delivery.SourceCapture)
    source.target = object()
    row = {'id': 'old', 'data': {'PlSeason': '2024-2025', 'p_id': 1, 'p_id_old': 'E-H10'}}
    baseline = delivery.registration_reference_values([row], '2024-2025')
    source.rows = lambda *args, **kwargs: [dict(row, data=dict(row['data'], p_id_old='E-H11'))]
    with pytest.raises(BackendError, match='Registration identity references changed'):
        source.assert_fresh({'documents': {}, 'queries': [],
                             'registration_references': {'season': '2024-2025', 'values': baseline}})


def test_historical_membership_has_a_bounded_version_guarded_fallback_without_a_group_index():
    from google.api_core.exceptions import FailedPrecondition
    def missing(**kwargs):
        raise FailedPrecondition('query requires an index')
    snaps = [SimpleNamespace(reference=SimpleNamespace(path='players/' + pid + '/contracts/one'),
                             update_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                             to_dict=lambda league=league: {'leagueId': league}, exists=True)
             for pid, league in [('one', 'EPL'), ('two', 'K1')]]
    limits = []
    fallback = SimpleNamespace(limit=lambda n: (limits.append(n) or SimpleNamespace(stream=lambda **kwargs: snaps)))
    group = SimpleNamespace(where=lambda **kwargs: SimpleNamespace(limit=lambda n: SimpleNamespace(stream=missing)),
                            select=lambda fields: fallback)
    source = object.__new__(delivery.SourceCapture)
    source.did = SimpleNamespace(collection_group=lambda name: group)
    source.queries = []
    assert source.historical_league_players('EPL') == {'one'}
    assert limits == [50001]
    guard = source.queries[0]
    assert guard['filters'] == [] and not guard['membership_only']
    assert len(guard['versions']) == 2 and all(guard['versions'].values())


def test_source_version_is_identical_for_protobuf_and_datetime_sdk_results():
    from google.protobuf.timestamp_pb2 import Timestamp
    from google.api_core.datetime_helpers import DatetimeWithNanoseconds
    stamp = Timestamp(seconds=1770000000, nanos=123456789)
    dt = DatetimeWithNanoseconds.from_timestamp_pb(stamp)
    a = SimpleNamespace(exists=True, update_time=stamp)
    b = SimpleNamespace(exists=True, update_time=dt)
    assert delivery.SourceCapture.version(a) == delivery.SourceCapture.version(b)
    stamp.nanos += 1
    assert delivery.SourceCapture.version(a) != delivery.SourceCapture.version(b)


@pytest.mark.parametrize('team,index', [
    ('E-HU', 'HUDX'), ('E-CA', 'CARX'), ('S-BA', 'S-BA'),
    ('S-GI', 'S-GI'), ('S-VI', 'S-VI'), ('S-BE', 'S-BE'), ('S-OV', 'S-OV'),
])
def test_historical_player_indexes_are_reference_only_and_do_not_expand_write_scope(team, index):
    evidence, setup = master_fixture()
    evidence['player_profiles'] = {'0000000001': {'name': 'Retired'}}
    evidence['player_contracts'] = {'0000000001': [
        {'teamId': team, 'leagueId': 'EPL', 'from': '2000-01-01', 'to': '2001-01-01'}]}
    evidence['historical_team_indexes'] = {team: [index]}
    before = deepcopy(evidence)
    report = delivery.generate_masters(evidence, [], [], {}, setup, 'all')
    assert report['validation_passed'], report['errors']
    assert docs(report, 'Player')[0]['IDX_CODE'] == index
    assert docs(report, 'Player')[0]['PlSeason'] == '2023-2024'
    assert not docs(report, 'playerDB')
    assert evidence == before
    with pytest.raises(ValueError, match='season-specific'):
        delivery.resolve_team_code(evidence['ai_samples']['code'], '2023-2024', team)
    evidence['historical_team_indexes'][team].append('CONFLICT')
    assert not delivery.generate_masters(evidence, [], [], {}, setup, 'all')['validation_passed']


def test_historical_team_index_capture_uses_bounded_team_queries_only():
    source = object.__new__(delivery.SourceCapture)
    source.target = object()
    calls = []
    def rows(client, path, filters, **kwargs):
        calls.append((path, filters, kwargs))
        assert client is source.target and path == 'Player'
        return [{'id': str(n), 'data': {'t_code': team, 'IDX_CODE': team+'X',
                                      'PlSeason': '2026-2027'}}
                for n, team in enumerate(filters[0][2])]
    source.rows = rows
    teams = [f'T{n}' for n in range(31)]
    result = source.historical_team_indexes(teams)
    assert result == {team: [team+'X'] for team in teams}
    assert len(calls) == 2
    assert all(call[1][0][:2] == ('t_code', 'in') and call[2]['bound'] == 20000 for call in calls)
    source.rows = lambda *args, **kwargs: pytest.fail('Empty team list must not query Player')
    assert source.historical_team_indexes([]) == {}


def test_historical_team_index_guard_rejects_mapping_changes_before_send():
    source = object.__new__(delivery.SourceCapture)
    source.historical_team_indexes = lambda teams: {'E-HU': ['HUDX']}
    guards = {'documents': {}, 'queries': [], 'historical_team_indexes': {
        'team_ids': ['E-HU'], 'values': {'E-HU': ['HUDX']}}}
    source.assert_fresh(guards)
    source.historical_team_indexes = lambda teams: {'E-HU': ['CHANGED']}
    with pytest.raises(BackendError, match='Historical team index references changed'):
        source.assert_fresh(guards)


@pytest.mark.parametrize('mode,chunks', [('matches', 1), ('matches', 3), ('masters', 1), ('aggregates', 1), ('all', 1), ('latest', 1)])
def test_public_progress_covers_capture_validation_and_send_without_reset(service, mode, chunks):
    svc, source, writer, _ = service
    writer.fail_two = False
    timeline = []
    source.set_progress_callback = lambda callback: setattr(source, 'progress_callback', callback)
    original_capture = source.capture
    original_send = writer.send
    def capture(scope, slot):
        for value in (0, 20, 50, 100):
            source.progress_callback(value)
            timeline.append(svc.public_status('operator')['percent'])
        return original_capture(scope, slot)
    def fresh(guards):
        for value in (0, 50, 100):
            source.progress_callback(value)
            timeline.append(svc.public_status('operator')['percent'])
    def send(doc, preview):
        timeline.append(svc.public_status('operator')['percent'])
        return original_send(doc, preview)
    source.capture = capture
    source.assert_fresh = fresh
    source.split_scopes = lambda scope: [scope] * chunks
    writer.send = send
    svc.send_scope({'seasonId': '20232024', 'mode': mode}, 'operator')
    status = svc.public_status('operator')
    timeline.append(status['percent'])
    assert status['status'] == 'complete', status
    assert timeline == sorted(timeline), timeline
    assert timeline[0] == 0 and timeline[-1] == 100
    assert any(0 < value < 40 for value in timeline)
    assert any(40 <= value < 50 for value in timeline)
    assert any(50 <= value < 99 for value in timeline)
    assert all(value < 100 for value in timeline[:-1])


def test_progress_preserves_manifest_and_only_success_reaches_100(service):
    svc, source, _, _ = service
    preview = svc.preview({'seasonId': '20232024'}, 'operator')
    assert svc.public_status('operator')['percent'] == 100
    report_before = deepcopy(svc.store.job(preview, 'operator')['meta']['report'])
    svc.store.record_progress(preview, 90)
    svc.store.record_progress(preview, 10)
    meta = svc.store.job(preview, 'operator')['meta']
    assert meta['progress']['percent'] == 90
    assert meta['report'] == report_before
    svc.send_scope({'seasonId': '20232024'}, 'operator')
    partial = svc.public_status('operator')
    assert partial['status'] == 'fail' and partial['percent'] == 99
    source.passed = False
    svc.send_scope({'seasonId': '20242025'}, 'operator')
    invalid = svc.public_status('operator')
    assert invalid['status'] == 'fail' and invalid['percent'] < 100


def test_progress_does_not_regress_when_send_must_revalidate(service):
    svc, _, _, _ = service
    scope = {'seasonId': '20232024', 'mode': 'all'}
    preview = svc.preview(scope, 'operator')
    operation, _ = svc.store.create('delivery', scope, 'operator')
    send, _ = svc.store.create('send', scope, 'operator', preview_id=preview, public=False)
    svc.store.set_job(operation, 'running', meta={'phase': 'send', 'previewId': preview, 'sendId': send})
    svc.store.record_progress(send, 10)
    assert svc.public_status('operator')['percent'] == 55
    meta = svc.store.job(preview, 'operator')['meta']
    meta['progress'] = {'percent': 20}
    svc.store.set_job(preview, 'running', meta=meta)
    svc.store.set_job(operation, 'running', meta={'phase': 'preview', 'previewId': preview})
    assert svc.public_status('operator')['percent'] == 55
    svc.store.set_job(operation, 'failed')
    assert svc.public_status('operator')['percent'] == 55


def test_masters_never_generate_or_require_aggregate_ratings(monkeypatch):
    evidence, setup = master_fixture()
    items, _, _ = aggregate_fixture()
    items[0]['recording']['teamRating'] = None
    monkeypatch.setattr(delivery, 'build_aggregates', lambda *args, **kwargs: pytest.fail('Masters called aggregate builder'))
    report = delivery.generate_masters(evidence, [], items, {}, setup, 'masters')
    assert report['validation_passed'], report['errors']
    assert {d['collection'] for d in report['documents']} == {'Player', 'playerDB'}
    assert report['aggregate_status'] is None and not report['blocked_groups']


def test_aggregates_ignore_unrelated_player_and_coach_errors_and_support_latest():
    items, codes, slots = aggregate_fixture()
    fixtures = [{'id': i['match_id'], 'data': i['match']} for i in items if i['side'] == 'H']
    evidence = {'did_matches': fixtures, 'ai_samples': {'code': codes}, 'target_existing': {},
                'player_profiles': {'bad-player': {'name': None}}, 'teams': {}, 'coach_contracts': []}
    setup = {'documents': [{'collection': 'code', 'document_id': 'must-not-send', 'data': {}}], 'new_season': False}
    report = delivery.generate_masters(evidence, fixtures, items, slots, setup, 'aggregates')
    assert report['validation_passed'], report['errors']
    assert {d['collection'] for d in report['documents']} == set(delivery.AGGREGATES)
    for d in report['documents']:
        evidence['target_existing'].setdefault(d['collection'], []).append({'id': d['document_id'], 'data': d['data']})
    unchanged = delivery.generate_masters(evidence, fixtures, items, slots, setup, 'aggregates', latest=True)
    assert unchanged['validation_passed'] and not unchanged['documents']


@pytest.mark.parametrize('mode,collection', [('matches', 'TACTIC'), ('masters', 'Player'), ('aggregates', 'rAVG')])
def test_latest_option_is_independent_of_the_three_delivery_groups(mode, collection):
    data = {'PlSeason': '2023-2024', 'p_id': 1, 't_code': 'E-H', 'IDX_CODE': 'HX', 'Round': 1}
    doc = {'collection': collection, 'document_id': 'existing', 'data': data}
    existing = {collection: [{'id': 'existing', 'data': data}]}
    full = delivery.finish_delivery([doc], [], [], existing, mode)
    changed = delivery.finish_delivery([doc], [], [], existing, mode, latest=True)
    assert full['validation_passed'] and len(full['documents']) == 1
    assert changed['validation_passed'] and not changed['documents']
    delivery.retain_changed_documents(full, existing)
    assert not full['documents']


@pytest.mark.parametrize('published_later', [False, True])
def test_aggregate_capture_scopes_rounds_and_carries_existing_later_totals(tmp_path, published_later):
    items, codes, slots = aggregate_fixture()
    second = deepcopy(items)
    for item in second:
        item['match_id'] += '-r2'
        item['match'].update(round=2, date='2024.08.19')
        item['recording']['teamRating']['jmx'] += 10
    all_items = items + second
    fixtures = [{'id': i['match_id'], 'data': i['match']} for i in all_items if i['side'] == 'H']
    prior = delivery.build_aggregates(all_items, codes, slots)
    assert prior['complete'], prior['errors']
    existing = {}
    if published_later:
        for d in prior['documents']:
            existing.setdefault(d['collection'], []).append({'id': d['document_id'], 'data': d['data']})
    source = object.__new__(delivery.SourceCapture)
    source.documents = {}; source.queries = []
    heads = {f'matches/{i["match_id"]}/recordings/{i["side"]}': i['recording'] for i in all_items}
    def get_all(refs, **kwargs):
        return [SimpleNamespace(reference=ref, exists=True,
                                update_time=SimpleNamespace(isoformat=lambda: 'v1'),
                                to_dict=lambda path=ref.path: heads[path]) for ref in refs]
    source.did = SimpleNamespace(document=lambda path: SimpleNamespace(path=path), get_all=get_all)
    source.target = object()
    def rows(client, path, filters=(), **kwargs):
        assert path not in ('Player', 'playerDB', 'Manager', 'players', 'coachContracts', 'coaches')
        if client is source.did:
            assert path == 'matches'
            return fixtures
        if path == 'code':
            return [{'id': str(n), **r} for n, r in enumerate(codes)]
        return existing.get(path, [])
    source.rows = rows
    source.get_many = lambda paths: {p: {'nameKr': p.split('/')[-1]} for p in paths}
    slot_path = tmp_path / 'slots.json'
    slot_path.write_text(json.dumps(slots), encoding='utf-8')
    scope = {'seasonId': '20242025', 'mode': 'aggregates', 'roundFrom': 1, 'roundTo': 1}
    assert source.split_scopes(scope) == [scope]
    report, guards = source.capture(scope, slot_path)
    assert report['validation_passed'], report['errors']
    assert {d['collection'] for d in report['documents']} == set(delivery.AGGREGATES)
    assert {d['Round'] for d in docs(report, 'rAVG')} == ({1, 2} if published_later else {1})
    assert {d['gm_round'] for d in docs(report, 'pMST')} == {1}
    assert {d['Round'] for d in docs(report, 'mMST')} == {1}
    assert len(source.documents) == (40 if published_later else 20)
    assert 'registration_references' not in guards


def test_unified_all_expands_to_all_three_scopes_with_shared_latest_and_filters():
    source = object.__new__(delivery.SourceCapture)
    source.did = object()
    source.rows = lambda *args, **kwargs: [{'id': str(n), 'data': {'round': n}} for n in (1, 2, 3)]
    scope = {'seasonId': '20232024', 'mode': 'all', 'unified': True, 'latest': True,
             'roundFrom': 2, 'roundTo': 3, 'matchId': None}
    assert delivery.delivery_mode(scope) == 'all'
    scopes = source.split_scopes(scope)
    assert [s['mode'] for s in scopes] == ['masters', 'matches', 'matches', 'aggregates']
    assert scopes[0]['roundFrom'] is None and scopes[0]['roundTo'] is None
    assert [s['roundFrom'] for s in scopes[1:3]] == [2, 3]
    assert scopes[-1]['roundFrom'] == 2 and scopes[-1]['roundTo'] == 3
    assert all(s['latest'] for s in scopes)


@pytest.mark.parametrize('invalid_group', [None, 'masters', 'matches', 'aggregates', 'source-change'])
def test_unified_all_validates_every_group_before_any_writes(service, invalid_group):
    svc, source, writer, calls = service
    writer.fail_two = False
    scope = {'seasonId': '20232024', 'mode': 'all', 'unified': True, 'latest': False}
    source.split_scopes = lambda _: [{**scope, 'mode': mode} for mode in ('masters', 'matches', 'aggregates')]
    captured = []
    def capture(group, slot):
        assert not calls
        mode = group['mode']
        captured.append(mode)
        passed = invalid_group != mode
        report = {'documents': [document(mode)], 'validation_passed': passed,
                  'summary': {'documents': 1, 'matches': 1, 'errors': int(not passed), 'failed_checks': 0},
                  'errors': [] if passed else [mode+' invalid'], 'checks': []}
        guard = {'documents': {'shared': 'v2' if invalid_group == 'source-change' and mode == 'aggregates' else 'v1'},
                 'queries': [], 'reference_code': {}, 'code_scope': {'teams': [mode]}, 'new_code': {}}
        return report, guard
    source.capture = capture
    svc.send_scope(scope, 'operator')
    status = svc.public_status('operator')
    assert status['mode'] == 'all'
    assert captured == ['masters', 'matches', 'aggregates']
    if invalid_group:
        assert status['status'] in ('fail', 'error') and not calls
    else:
        assert status['status'] == 'complete' and status['percent'] == 100
        assert status['counts'] == {'success': 3}
        assert status['summary']['matches'] == 1
        assert calls == ['masters', 'matches', 'aggregates']


def test_unified_guards_allow_only_planned_code_additions_across_groups():
    code = {'Code': 'E-H', 'PlSeason': '2023-2024', 'IDX_NAME': 'HX'}
    first = {'documents': {}, 'queries': [], 'delivery_groups': [
        {'reference_code': {}, 'code_scope': None, 'new_code': {'planned': code}}]}
    second = {'documents': {}, 'queries': [], 'delivery_groups': [
        {'reference_code': {}, 'code_scope': {'season': '2023-2024', 'teams': ['E-H']}, 'new_code': {}}]}
    combined = delivery.merge_guards(first, second)
    assert all(g['new_code'] == {'planned': code} for g in combined['delivery_groups'])
    source = object.__new__(delivery.SourceCapture)
    source.target = object()
    source.rows = lambda *args, **kwargs: [{'id': 'planned', 'data': code}]
    source.assert_fresh(combined)
    source.rows = lambda *args, **kwargs: [{'id': 'unexpected', 'data': code}]
    with pytest.raises(BackendError, match='Destination team code configuration changed'):
        source.assert_fresh(combined)
    conflicting = deepcopy(second)
    conflicting['delivery_groups'][0]['new_code'] = {'planned': {**code, 'IDX_NAME': 'OTHER'}}
    with pytest.raises(BackendError, match='Season code candidates changed'):
        delivery.merge_guards(first, conflicting)


@pytest.mark.parametrize('mode', ['masters', 'all'])
def test_coach_contract_selection_does_not_override_latest_option(mode):
    evidence, setup = master_fixture()
    fixtures = evidence['did_matches'][:1]
    evidence['coach_contracts'] = [{'data': {
        'teamId': team, 'coachId': str(number), 'coachName': 'Coach '+team,
        'coachNameEn': 'Coach '+team, 'from': '2023-07-01', 'to': None, 'status': 'MANAGER'}}
        for number, team in enumerate(('E-H', 'E-A'), 1)]
    first = delivery.generate_masters(evidence, fixtures, [], {}, setup, mode, latest=False)
    assert first['validation_passed'], first['errors']
    assert len(docs(first, 'Manager')) == 2
    for doc in first['documents']:
        evidence['target_existing'].setdefault(doc['collection'], []).append({
            'id': doc['document_id'], 'data': doc['data']})
    full = delivery.generate_masters(evidence, fixtures, [], {}, setup, mode, latest=False)
    changed = delivery.generate_masters(evidence, fixtures, [], {}, setup, mode, latest=True)
    assert full['validation_passed'] and changed['validation_passed']
    assert len(full['documents']) == len(first['documents'])
    assert full['summary']['unchangedDocuments'] == len(first['documents'])
    assert not changed['documents']
    evidence['player_profiles']['0000000001']['name'] = 'Changed'
    updated = delivery.generate_masters(evidence, fixtures, [], {}, setup, mode, latest=True)
    assert updated['validation_passed'], updated['errors']
    assert {d['collection'] for d in updated['documents']} == {'Player', 'playerDB'}


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__]))
