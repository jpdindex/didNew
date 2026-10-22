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
from backend.system.system_firestore import BackendError, CurrentUser, JpdDidData
from backend.api import api_aifootballx
from backend.system.system_kpi_rules import calculate_kpis


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
        self.assertEqual(doc['action'], 'merge-existing')

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


def test_manual_fields_preserved_and_concurrent_owned_field_changes_rejected():
    doc = document()
    doc['existed'] = True
    doc['baseline'] = {'m_id': {'exists': True, 'value': 10}, 'uid': {'exists': True, 'value': 'one'}}
    current = {'m_id': 10, 'uid': 'one', 'Coach': 'manual text', 'Captain': 15}
    assert decide_merge(doc, current)
    assert current['Coach'] == 'manual text'
    assert not decide_merge(doc, {**current, 'm_id': 12})
    with pytest.raises(BackendError, match='owned fields changed'):
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
    svc, _, _, _ = service
    monkeypatch.setattr(api_aifootballx, 'delivery_service', lambda: svc)
    app = FastAPI()
    app.include_router(api_aifootballx.router, prefix='/api/v1')
    @app.exception_handler(BackendError)
    async def error(_, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error': exc.code}, status_code=exc.status_code)
    app.dependency_overrides[api_aifootballx.delivery_operator] = lambda: CurrentUser(uid='operator', claims={})
    client = TestClient(app)
    assert client.post('/api/v1/aifootballx/preview', data={'season':'2024-2025','round_from':5,'round_to':1}).status_code == 422
    assert client.post('/api/v1/aifootballx/preview', data={'season':'2024-2025','gm_id':'20252026x'}).status_code == 422
    result = client.post('/api/v1/aifootballx/preview', data={'season':'2024-2025','round_from':1,'round_to':1})
    assert result.status_code == 202
    assert 'jobId' not in result.json()
    assert result.json()['scope']['seasonId'] == '20242025'
    response=client.get('/api/v1/aifootballx/send/status').json()
    assert response['validationPassed'] and response['status']=='ready'
    paths=app.openapi()['paths']
    assert list(paths)==['/api/v1/aifootballx/send/status','/api/v1/aifootballx/preview',
                         '/api/v1/aifootballx/send/matches','/api/v1/aifootballx/send/all','/api/v1/aifootballx/send/latest']
    assert not paths['/api/v1/aifootballx/send/status']['get'].get('parameters')
    for path in ('matches','all','latest'):
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

    def test_own_goals_excluded(self):
        evidence, _ = preview_fixture()
        item = evidence['source_recordings'][0]
        for row in item['children']['records']:
            row['data'].update(legacyPathId='one', legacyPathType='DTP', legacyPathTtp=True,
                               legacyKpiFlags={'goal': row['id'] == 'shot', 'shot': row['id'] == 'shot'})
        item['children']['records'][1]['data']['playerId'] = 'OWN'
        records = source_records(item)
        values = intervals(records, calculate_kpis(records))
        self.assertEqual(sum(values['GOAL']), 0)

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
        self.assertEqual(player['p_id_old'], 'E-H7')
        mst = next(d['data'] for d in report['documents'] if d['collection'] == 'playerMST')
        self.assertTrue(all(f'P-KPI{n}' not in mst for n in range(50, 69)))
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
    assert build_details(game,infos,children,codes)['errors']


def test_sql_own_goal_does_not_create_player_goal_or_analysis_goal():
    game, infos, children, codes=sql_fixture()
    children['H']['ff_game_record'][0]['p_id']='OWN'
    report=build_details(game,infos,children,codes)
    assert report['available_source_validation_passed']
    assert not any(d['collection']=='playerGoal' for d in report['documents'])
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
    assert sum(v for k,v in wide.items() if k.startswith('GOAL-H'))==2
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
    assert 'pt_begin' not in player['data'] and 'pt_end' not in player['data']
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
    assert any(d['collection']=='playerMST' for d in result['documents'])
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
    assert 'manualNote' not in generated['data']
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


def test_registered_identity_does_not_require_agreement_on_optional_contract_end():
    evidence,mid=preview_fixture();pid='0000000999'
    evidence['player_profiles'][pid]={'name':'Registered','currentTeamId':'E-H'}
    evidence['player_contracts'][pid]=[
        {'teamId':'E-H','leagueId':'EPL','from':'2020-01-01','to':None,'no':'11','pos':'FW'},
        {'teamId':'E-H','leagueId':'EPL','from':'2020-01-01','to':'2026-09-02','no':'11','pos':'FW'}]
    result=build(evidence,mid)
    assert result['validation_passed'], result['errors']
    player=next(d['data'] for d in result['documents'] if d['collection']=='Player')
    assert player['t_code']=='E-H' and player['pt_num']==11
    assert 'pt_end' not in player



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
    assert next(d['data']['P-DB7'] for d in result['documents'] if d['collection']=='playerMST')==identity
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


def test_preview_fpx_includes_own_goal_but_analysis_goal_intervals_do_not():
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
    assert dmst['GOALScore']==pytest.approx(100/3)
    wide=next(d['data'] for d in result['documents'] if d['collection']=='multiSheet_ALL' and d['data']['team_type']=='HOME')
    assert sum(v for k,v in wide.items() if k.startswith('GOAL-H'))==0
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


def test_latest_compares_owned_values_preserves_ids_and_ignores_manual_changes():
    evidence,setup=master_fixture()
    original=delivery.generate_masters(evidence,[],[],{},setup,'all')
    for doc in original['documents']:
        evidence['target_existing'].setdefault(doc['collection'],[]).append(
            {'id':'existing-'+doc['collection'],'data':{**doc['data'],'uid':'manual-uid','manualNote':'keep'}})
    unchanged=delivery.generate_masters(evidence,[],[],{},setup,'latest')
    assert unchanged['validation_passed'], unchanged['errors']
    assert not unchanged['documents']
    evidence['player_profiles']['0000000001']['name']='Renamed'
    changed=delivery.generate_masters(evidence,[],[],{},setup,'latest')
    assert changed['validation_passed'], changed['errors']
    assert len(changed['documents'])==2
    for doc in changed['documents']:
        assert doc['document_id']=='existing-'+doc['collection']
        assert doc['data']['uid']=='manual-uid'
        assert 'manualNote' not in doc['data']
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
    for path in ('preview','send/matches','send/all','send/latest'):
        response=client.post('/api/v1/aifootballx/'+path,data={'season':'2023-2024'})
        assert response.status_code==202
        assert response.json()['status']=='queued'
    assert len(submitted)==4 and not calls
    schema=app.openapi()
    for mode in ('all','latest'):
        body=schema['paths']['/api/v1/aifootballx/send/'+mode]['post']['requestBody']['content']['application/x-www-form-urlencoded']['schema']
        form=schema['components']['schemas'][body['$ref'].split('/')[-1]]
        assert set(form['properties'])=={'season'}


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
def test_master_capture_reads_registration_without_match_children(monkeypatch,tmp_path,bad_slot,with_fixtures):
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
    slot_path=tmp_path/'bad-slots.json' if bad_slot else None
    if slot_path: slot_path.write_text('{',encoding='utf-8')
    report,guards=source.capture({'seasonId':'20232024','mode':'latest'},slot_path)
    assert report['validation_passed'],report['errors']
    assert len(report['documents'])==(4 if with_fixtures else 2)
    assert guards['queries'][0]['group']=='contracts'
    assert 'coachContracts' in paths and 'coaches' not in paths
    assert ('coachRounds' in paths) is (not with_fixtures)
    if bad_slot:
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
        return []
    source.rows=rows;source.get=lambda path:{};source.assert_fresh=lambda guards:None
    monkeypatch.setattr(delivery,'plan_new_season',lambda *a,**k:pytest.fail('Match delivery attempted season setup'))
    for item in evidence['source_recordings']:
        item['recording']['teamRating'].update(apxGrade='F',tpxGrade='F',fpxGrade='F')
    report,guards=source.capture({'seasonId':'20232024','mode':'matches','roundFrom':1,'roundTo':1},
                                tmp_path/'missing-aggregate-slots.json')
    assert report['validation_passed'],report['errors']
    assert 'slot_file' not in guards and guards['new_code']=={}


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


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__]))
