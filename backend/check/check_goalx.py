import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.api import api_goalx, api_player_requests
from backend.system.system_firestore import BackendError, CurrentUser
from backend.system.system_goalx import (
    EstimatedShotInput, automatic_goal_x, coordinate_geometry, estimate_shot,
    EvaluationInput, GoalAnalysis, Grades, POINTS,
    RepresentativeApproval, representative_proposal, finalize_representative, automatic_representative_approval,
)


def shot_input(**changes):
    return {"posX": 80, "posY": 50, "shootPosX": 273.5, "shootPosY": 300,
            "goalX": 105, "timeToGoal": .5, **changes}


def test_arc_lengths_endpoints_and_average_speed():
    shots = [estimate_shot(EstimatedShotInput(**shot_input()), grade) for grade in ("high", "medium", "low")]
    assert shots[0]["distance"] > shots[1]["distance"] > shots[2]["distance"]
    assert shots[2]["distance"] == pytest.approx(21)
    assert shots[2]["averageSpeed"] == pytest.approx(151.2)
    for shot in shots:
        assert shot["trajectory"][0] == {"x": 84, "y": 34, "z": 0}
        assert shot["trajectory"][-1] == {"x": 105, "y": 34, "z": 0}
        assert all(point["y"] == 34 for point in shot["trajectory"])


def test_goal_height_and_missing_time():
    shot = estimate_shot(EstimatedShotInput(**shot_input(shootPosX=83, shootPosY=83, timeToGoal=None)), "medium")
    assert shot["goalPoint"]["z"] == pytest.approx(2.44)
    assert shot["goalPoint"]["y"] == pytest.approx(30.34)
    assert shot["averageSpeed"] is None


def test_raw_coordinates_reads_the_selected_record_without_shotx():
    service = GoalAnalysis.__new__(GoalAnalysis)
    coordinates = {"posX": .10298661174047373, "posY": 52.208201892744476,
                   "shootPosX": 211, "shootPosY": 275}
    service._read_goal = Mock(return_value={"res": "GOAL", **coordinates})
    service._recording_direction = Mock(return_value={})
    assert service.raw_coordinates("match", "A", "goal") == {**coordinates, "goalX": None, "shotType": None, "playerHeightCm": None, "jumpHeightM": 0, "startHeightM": 0, **coordinate_geometry(coordinates)}
    service._read_goal.assert_called_once_with("match", "A", "goal")


@pytest.mark.parametrize("changes", [{"timeToGoal": 0}, {"timeToGoal": -1}, {"timeToGoal": float("nan")},
                                     {"posX": 101}, {"shootPosY": 0}, {"goalX": 50}])
def test_invalid_estimation_rejected(changes):
    with pytest.raises(ValidationError):
        EstimatedShotInput(**shot_input(**changes))


@pytest.mark.parametrize("result_y,expected", [(83, 2.44), (191.5, 1.22), (300, 0)])
def test_calculated_height_reverses_goal_result_y(result_y, expected):
    assert coordinate_geometry(shot_input(shootPosY=result_y))["height"] == pytest.approx(expected)


def test_horizontal_launch_angle_is_symmetric_and_not_elevation():
    from math import atan2, degrees
    expected = degrees(atan2(3.66, 21))
    for goal_x, pos_x in ((105, 80), (0, 20)):
        assert coordinate_geometry(shot_input(goalX=goal_x, posX=pos_x))["launchAngle"] == pytest.approx(0)
        for result_x in (83, 464):
            geometry = coordinate_geometry(shot_input(goalX=goal_x, posX=pos_x, shootPosX=result_x, shootPosY=83))
            assert geometry["launchAngle"] == pytest.approx(expected)
            assert geometry["height"] == pytest.approx(2.44)


def test_invalid_or_degenerate_coordinates_do_not_fabricate_angle():
    assert coordinate_geometry(shot_input(posX=100, goalX=105))["launchAngle"] is None
    assert coordinate_geometry({"shootPosY": 83})["height"] == pytest.approx(2.44)
    assert coordinate_geometry({"shootPosY": float("nan")})["height"] is None


def test_delivery_adds_geometry_from_existing_approved_coordinates_without_reapproval():
    from backend.system import system_goalx
    record = {**shot_input(shootPosY=83), "date": "2026-11-21", "clock": "12:34",
              "playerName": "Example Player", "playerNameEn": "Example Player",
              "home": "Home", "away": "Away", "homeNameEn": "Home FC", "awayNameEn": "Away FC", "scoringSide": "H"}
    analysis = {"gmId": "testgoal-a", "side": "H", "recordId": "goal", "manualGoal": record,
                "evaluations": {"analyst": {"grades": {"artistry": "high", "defense": "medium", "trajectory": "low"},
                                                "estimatedShot": {"input": shot_input(shootPosY=83)}}}}
    proposal = representative_proposal(analysis, record, {}, POINTS)
    payload = RepresentativeApproval(sourceVersion=proposal["sourceVersion"], trajectory="low", coordinates=shot_input(shootPosY=83))
    analysis["representative"] = finalize_representative(analysis, record, {}, POINTS, payload)
    analysis["representative"]["points"] = POINTS.copy()
    # Simulate an approval saved before the derived geometry fields existed.
    for key in ("height", "launchAngle", "calculationVersion", "angleReference"):
        analysis["representative"]["estimatedShot"].pop(key, None)
    analysis["rating"] = system_goalx.goal_rating(analysis)
    approval_before = deepcopy(analysis["representative"])
    docs = {"GoalAalysis/_settings": {}, "GoalAalysis/TESTGOAL/seasons/test/goals/manual-one": analysis}
    class Ref:
        def __init__(self, path): self.path = path
        def get(self, **kwargs):
            return SimpleNamespace(exists=self.path in docs, to_dict=lambda: deepcopy(docs.get(self.path)))
    source = SimpleNamespace(did=SimpleNamespace(document=Ref), target=object(), documents={}, queries=[], version=lambda snap: "v1")
    source.rows = lambda client, path, *args, **kwargs: [{"id": "manual-one", "data": analysis}] if path == "GoalAalysis/TESTGOAL/seasons/test/goals" else []
    report, _ = system_goalx.capture_goalx_delivery(source, {"seasonId": "test", "leagueId": "TESTGOAL", "mode": "goalx", "force": True})
    assert report["validation_passed"], report["errors"]
    data = report["documents"][0]["data"]
    assert data["schemaVersion"] == 4
    assert data["metrics"]["height"] == pytest.approx(2.44)
    assert data["metrics"]["launchAngle"] == pytest.approx(0)
    assert analysis["representative"] == approval_before


def test_save_persists_per_analyst_estimation_and_preserves_three_grade_categories():
    service = GoalAnalysis.__new__(GoalAnalysis)
    batch = Mock()
    service.db = SimpleNamespace(batch=lambda: batch)
    service.verify_analyst = Mock(return_value="분석관")
    service._read_goal = Mock(return_value={})
    service._ref = Mock(return_value="goal-ref")
    service.move_legacy_goals = Mock()
    service._write_scope = Mock()
    service._read_analysis_state = Mock(return_value={"saved": True})
    service._persist_rating = Mock()
    service._recording_direction = Mock(return_value={})
    payload = EvaluationInput(artistry="high", defense="medium", trajectory="high", curveDirection="left", estimatedShot=shot_input())
    assert service.save("match", "H", "record", "analyst", payload) == {"saved": True}
    row = batch.set.call_args.args[1]["evaluations"]["analyst"]
    assert set(row["grades"]) == set(Grades.model_fields)
    assert row["estimatedShot"] == estimate_shot(payload.estimatedShot, "high")
    batch.commit.assert_called_once()
    service._persist_rating.assert_called_once_with("goal-ref", "analyst")
    state = service._analysis_state({"evaluations": {"analyst": row}}, "analyst", POINTS, {})
    assert state["myEstimatedShot"] == row["estimatedShot"]
    assert state["score"] == 94
    assert row["curveDirection"] == "left"
    assert state["myCurveDirection"] == "left"


@pytest.mark.parametrize("half,recording,pos_x,expected", [
    ("H1", {"fieldSide": "left"}, 5, 105),
    ("H2", {"fieldSide": "left"}, 95, 0),
    ("H1", {"fieldSide": "right"}, 95, 0),
    ("H2", {"fieldSide": "right"}, 5, 105),
    ("H3", {"fieldSide": "left", "fieldSideEx": "right"}, 95, 0),
    ("H4", {"fieldSideEx": "right"}, 5, 105),
    ("H3", {"fieldSide": "left"}, 5, 0),
    ("H1", {}, 5, 0), ("H2", {}, 95, 105),
])
def test_attack_goal_prefers_raw_direction_then_nearest_goal(half, recording, pos_x, expected):
    assert automatic_goal_x({"half": half}, recording, pos_x) == expected


def test_saved_goal_is_automatic_even_if_client_sends_manual_goal():
    service = GoalAnalysis.__new__(GoalAnalysis)
    batch = Mock()
    service.db = SimpleNamespace(batch=lambda: batch)
    service.verify_analyst = Mock(return_value="분석관")
    service._read_goal = Mock(return_value={"half": "H1"})
    service._recording_direction = Mock(return_value={"fieldSide": "right"})
    service._ref = Mock(return_value="goal-ref")
    service.move_legacy_goals = Mock()
    service._write_scope = Mock()
    service._read_analysis_state = Mock(return_value={})
    service._persist_rating = Mock()
    payload = EvaluationInput(artistry="high", defense="medium", trajectory="medium", curveDirection="left", estimatedShot=shot_input(goalX=105))
    service.save("match", "A", "goal", "analyst", payload)
    saved = batch.set.call_args.args[1]["evaluations"]["analyst"]["estimatedShot"]
    assert saved["input"]["goalX"] == 0
    assert saved["goalPoint"]["x"] == 0


@pytest.mark.parametrize("direction", [None, "left", "right"])
@pytest.mark.parametrize("goal_x", [0, 105])
def test_browser_and_server_generate_identical_curves(direction, goal_x):
    root = Path(__file__).resolve().parents[2]
    script = """
const fs = require('node:fs');
const vm = require('node:vm');
const { transformSync } = require('./node_modules/.pnpm/node_modules/esbuild');
const source = fs.readFileSync('app/utils/goalx.ts', 'utf8');
const module = { exports: {} };
vm.runInNewContext(transformSync(source, { loader: 'ts', format: 'cjs' }).code, { module, exports: module.exports });
const input = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(['high', 'medium', 'low'].map(grade => module.exports.estimateGoalShot(input, grade))));
"""
    browser = json.loads(subprocess.check_output(["node", "-e", script, json.dumps(shot_input(shootPosY=100, posY=20, goalX=goal_x, curveDirection=direction))], cwd=root))
    for grade, actual in zip(("high", "medium", "low"), browser):
        expected = estimate_shot(EstimatedShotInput(**actual["input"]), grade)
        assert actual["distance"] == pytest.approx(expected["distance"], abs=1e-10)
        assert actual["averageSpeed"] == pytest.approx(expected["averageSpeed"], abs=1e-10)
        assert actual["height"] == pytest.approx(expected["height"], abs=1e-10)
        assert actual["launchAngle"] == pytest.approx(expected["launchAngle"], abs=1e-10)
        for a, b in zip(actual["trajectory"], expected["trajectory"]):
            assert a == pytest.approx(b, abs=1e-10)


def sources():
    evaluations = {}
    for i, (grade, time) in enumerate((("high", .5), ("medium", .6), ("medium", 5))):
        evaluations[str(i)] = {"grades": {"artistry": "high", "defense": "low", "trajectory": grade},
                               "estimatedShot": {"input": {"timeToGoal": time}}}
    return {"evaluations": evaluations, "shotx": {"id": "shot-1"}}, {
        "half": "H1", "posX": 80, "posY": 50, "shootPosX": 273.5, "shootPosY": 300,
    }, {"fieldSide": "left"}, POINTS


def approval(source, **changes):
    proposal = representative_proposal(*source)
    return RepresentativeApproval(**{"sourceVersion": proposal["sourceVersion"], "trajectory": "medium", "curveDirection": "left",
        "coordinates": {**proposal["coordinates"], "goalX": 0, "timeToGoal": 100}, **changes})


def test_proposal_combines_means_mode_and_median_without_averaging_outlier_time():
    proposal = representative_proposal(*sources())
    assert proposal["timeToGoal"] == .6
    assert proposal["timeSampleCount"] == 3
    assert proposal["trajectoryCandidates"] == ["medium"]
    assert proposal["categoryScores"] == {"artistry": 20, "defense": 24, "trajectory": pytest.approx(56 / 3)}


def test_finalization_recalculates_speed_from_median_and_automatic_goal():
    source = sources()
    result = finalize_representative(*source, approval(source))
    assert result["estimatedShot"]["input"]["goalX"] == 105
    assert result["estimatedShot"]["input"]["timeToGoal"] == .6
    assert result["estimatedShot"]["averageSpeed"] == pytest.approx(result["estimatedShot"]["distance"] / .6 * 3.6)


def test_tied_trajectory_requires_one_of_the_modes():
    source = sources()
    del source[0]["evaluations"]["2"]
    assert representative_proposal(*source)["trajectoryCandidates"] == ["high", "medium"]
    with pytest.raises(BackendError) as error:
        finalize_representative(*source, approval(source, trajectory="low"))
    assert error.value.status_code == 422


@pytest.mark.parametrize("change", ["evaluation", "raw", "direction", "rules", "shotx"])
def test_changed_sources_cannot_be_approved_from_an_old_dialog(change):
    source = sources()
    payload = approval(source)
    if change == "evaluation": source[0]["evaluations"]["0"]["grades"]["artistry"] = "low"
    if change == "raw": source[1]["posX"] = 70
    if change == "direction": source[2]["fieldSide"] = "right"
    if change == "rules": source = (*source[:3], {"high": 5, "medium": 2, "low": 1})
    if change == "shotx": source[0]["shotx"]["id"] = "changed"
    with pytest.raises(BackendError) as error:
        finalize_representative(*source, payload)
    assert error.value.status_code == 409


def test_approval_status_becomes_needs_review_without_erasing_the_approved_result():
    source = sources()
    source[0]["representative"] = {**finalize_representative(*source, approval(source)), "points": POINTS, "approvedBy": "manager"}
    service = GoalAnalysis.__new__(GoalAnalysis)
    assert service._analysis_state(source[0], "0", POINTS, {})["representative"]["status"] == "approved"
    original = deepcopy(source[0]["representative"])
    source[0]["evaluations"]["0"]["grades"]["artistry"] = "low"
    assert service._analysis_state(source[0], "0", POINTS, {})["representative"]["status"] == "needs_review"
    assert source[0]["representative"] == original


def test_approval_routes_require_manager_and_do_not_change_analyst_evaluations(monkeypatch):
    app = FastAPI()
    app.include_router(api_goalx.router)
    @app.exception_handler(BackendError)
    async def reject(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"code": error.code}, status_code=error.status_code)
    service = Mock()
    service.representative_preview.return_value = representative_proposal(*sources())
    service.approve_representative.return_value = {}
    monkeypatch.setattr(api_goalx, "GoalAnalysis", lambda: service)
    app.dependency_overrides[api_player_requests.verified_user] = lambda: CurrentUser(uid="analyst", claims={"email": "analyst@example.com"})
    path = "/goalx/goals/match/H/record/representative"
    client = TestClient(app)
    assert client.get(path).status_code == 403
    assert client.put(path, json=approval(sources()).model_dump()).status_code == 403
    service.approve_representative.assert_not_called()
    app.dependency_overrides[api_player_requests.verified_user] = lambda: CurrentUser(uid="manager", claims={"email": "jpdindex@gmail.com"})
    assert client.get(path).status_code == 200
    assert client.put(path, json=approval(sources()).model_dump()).status_code == 200
    service.approve_representative.assert_called_once()


def test_export_contains_all_analysts_and_representative():
    service = GoalAnalysis.__new__(GoalAnalysis)
    goal = {"gmId": "match", "side": "A", "recordId": "goal", "evaluationCount": 2,
            "representative": {"status": "approved"}}
    service.list = Mock(return_value={"goals": [goal]})
    ref = SimpleNamespace(path="current")
    service._ref = Mock(return_value=ref)
    service._historical_refs = Mock(return_value=[SimpleNamespace(path="old")])
    stored = {"evaluations": {"a": {"estimatedShot": {"distance": 20}}, "b": {"estimatedShot": {"distance": 22}}},
              "representative": {"score": 8}}
    doc = SimpleNamespace(reference=ref, exists=True, to_dict=lambda: stored)
    service.db = SimpleNamespace(get_all=lambda *args, **kwargs: [doc])
    exported = service.export_completed("EPL", "20252026", "38", "a")
    assert [entry["id"] for entry in exported[0]["evaluations"]] == ["a", "b"]
    assert exported[0]["representative"] == {"score": 8}


def test_approval_transaction_writes_only_representative_snapshot(monkeypatch):
    from backend.system import system_goalx
    monkeypatch.setattr(system_goalx.firestore, "transactional", lambda function: function)
    service = GoalAnalysis.__new__(GoalAnalysis)
    transaction = Mock()
    service.db = SimpleNamespace(transaction=lambda: transaction)
    league = SimpleNamespace(id="EPL")
    season = SimpleNamespace(id="20252026", parent=SimpleNamespace(parent=league))
    ref = SimpleNamespace(parent=SimpleNamespace(parent=season))
    service._ref = Mock(return_value=ref)
    service.move_legacy_goals = Mock()
    source = sources()
    service._representative_source = Mock(return_value=source)
    service._read_analysis_state = Mock(return_value={"representative": {"status": "approved"}})
    user = CurrentUser(uid="manager", claims={"email": "jpdindex@gmail.com"})
    state = service.approve_representative("match", "A", "goal", user, approval(source))
    assert state["representative"]["status"] == "approved"
    values = transaction.update.call_args.args[1]
    assert set(values) == {"representative"}
    assert values["representative"]["approvedBy"] == "manager"
    assert values["representative"]["leagueId"] == "EPL"
    assert values["representative"]["timeToGoal"] == .6


@pytest.mark.parametrize("grade", ["high", "medium"])
def test_curved_evaluation_and_approval_require_direction_without_estimation(grade):
    with pytest.raises(ValidationError):
        EvaluationInput(artistry="high", defense="medium", trajectory=grade)
    with pytest.raises(ValidationError):
        RepresentativeApproval(sourceVersion="a" * 64, trajectory=grade)
    assert EvaluationInput(artistry="high", defense="medium", trajectory=grade, curveDirection="right").curveDirection == "right"


def test_straight_evaluation_removes_stale_direction_from_saved_input():
    payload = EvaluationInput(artistry="high", defense="medium", trajectory="low", curveDirection="right",
                              estimatedShot=shot_input(curveDirection="left"))
    assert payload.curveDirection is None
    assert payload.estimatedShot.curveDirection is None


@pytest.mark.parametrize("goal_x,pos_x", [(105, 80), (0, 20)])
@pytest.mark.parametrize("grade", ["high", "medium"])
def test_left_and_right_curves_are_mirrored_in_shooters_frame(goal_x, pos_x, grade):
    left = estimate_shot(EstimatedShotInput(**shot_input(goalX=goal_x, posX=pos_x, curveDirection="left")), grade)
    right = estimate_shot(EstimatedShotInput(**shot_input(goalX=goal_x, posX=pos_x, curveDirection="right")), grade)
    assert left["trajectory"][0] == right["trajectory"][0]
    assert left["trajectory"][-1] == right["trajectory"][-1]
    forward = 1 if goal_x == 105 else -1
    assert (left["trajectory"][50]["y"] - 34) * forward < 0
    assert (right["trajectory"][50]["y"] - 34) * forward > 0
    assert left["distance"] == pytest.approx(right["distance"])
    assert left["modelVersion"] == 3


def test_representative_direction_is_voted_within_selected_trajectory_only():
    source = sources()
    source[0]["evaluations"]["0"]["curveDirection"] = "left"  # high
    source[0]["evaluations"]["1"]["curveDirection"] = "right"  # medium
    source[0]["evaluations"]["2"]["curveDirection"] = "right"  # medium
    proposal = representative_proposal(*source)
    assert proposal["curveDirectionCandidates"]["medium"] == ["right"]
    assert proposal["curveDirectionCandidates"]["high"] == ["left"]
    with pytest.raises(BackendError):
        finalize_representative(*source, approval(source, curveDirection="left"))
    result = finalize_representative(*source, approval(source, curveDirection="right"))
    assert result["curveDirection"] == "right"
    assert result["estimatedShot"]["input"]["curveDirection"] == "right"


def _bulk_approval_source():
    saved={'posX':75,'posY':48,'shootPosX':300,'shootPosY':250,'goalX':105,'timeToGoal':.8}
    analysis={'evaluations':{'one':{'grades':{'artistry':'high','defense':'high','trajectory':'low'},'estimatedShot':{'input':saved}}}}
    raw={'half':'H1','act':'S','posX':80,'posY':50,'shootPosX':273.5,'shootPosY':300}
    return analysis,raw,{'fieldSide':'left'},POINTS


def test_saved_analysis_coordinates_replace_raw_in_approval_and_version():
    values=_bulk_approval_source();proposal=representative_proposal(*values)
    assert proposal['coordinateSource']=='evaluations' and proposal['coordinateSampleCount']==1
    assert proposal['coordinates']=={'posX':75,'posY':48,'shootPosX':300,'shootPosY':250}
    payload=automatic_representative_approval(proposal)
    result=finalize_representative(*values,payload)
    assert result['estimatedShot']['input']['posX']==75
    assert values[1]['posX']==80
    values[0]['evaluations']['one']['estimatedShot']['input']['posX']=70
    assert representative_proposal(*values)['coordinates']['posX']==70
    with pytest.raises(BackendError,match='변경'):
        finalize_representative(*values,payload)


def test_approval_coordinates_use_complete_valid_analyst_samples_and_medians():
    values=_bulk_approval_source();entries=values[0]['evaluations']
    entries['two']=deepcopy(entries['one']);entries['two']['estimatedShot']['input'].update(posX=65,posY=44,shootPosX=320,shootPosY=200)
    entries['bad']=deepcopy(entries['one']);entries['bad']['estimatedShot']['input']['posX']=-1
    entries['partial']=deepcopy(entries['one']);del entries['partial']['estimatedShot']['input']['shootPosX']
    proposal=representative_proposal(*values)
    assert proposal['coordinateSampleCount']==2
    assert proposal['coordinates']=={'posX':70,'posY':46,'shootPosX':310,'shootPosY':225}
    for entry in entries.values():entry['estimatedShot']['input']={'timeToGoal':.8}
    proposal=representative_proposal(*values)
    assert proposal['coordinateSource']=='raw' and proposal['coordinateSampleCount']==0
    assert proposal['coordinates']['posX']==80


@pytest.mark.parametrize('change', [
    {'score':None}, {'timeToGoal':None}, {'trajectoryCandidates':['high','low']},
    {'trajectoryCandidates':['medium'],'curveDirectionCandidates':{'medium':['left','right']}},
    {'coordinates':{'posX':-1}}, {'shotType':'H','playerHeightCm':None},
])
def test_bulk_skips_missing_invalid_or_ambiguous_proposals(change):
    proposal=representative_proposal(*_bulk_approval_source());proposal.update(change)
    with pytest.raises(BackendError):automatic_representative_approval(proposal)


def test_bulk_isolated_results_deduplicates_and_continues_after_invalid_and_errors():
    service=GoalAnalysis.__new__(GoalAnalysis)
    good=representative_proposal(*_bulk_approval_source());bad={**good,'coordinates':{'posX':-1}}
    service.representative_preview=Mock(side_effect=[good,bad,good,good])
    service.approve_representative=Mock(side_effect=[{'representative':{'status':'approved'}},BackendError('검토 중 변경',409,'changed'),RuntimeError('secret')])
    keys=[{'gmId':'match','side':'H','recordId':str(i)} for i in range(4)]
    result=service.approve_representatives([keys[0],keys[0],*keys[1:]],CurrentUser(uid='manager',claims={'email':'jpdindex@gmail.com'}))
    assert (result['total'],result['approved'],result['skipped'],result['failed'])==(4,1,2,1)
    assert service.approve_representative.call_count==3
    assert 'secret' not in str(result)
    assert result['results'][2]['reason']=='검토 중 변경'


def test_bulk_api_requires_manager_and_valid_bounded_goal_list(monkeypatch):
    app=FastAPI();app.include_router(api_goalx.router)
    app.add_exception_handler(BackendError,lambda request,exc:JSONResponse({'error':exc.message},status_code=exc.status_code))
    service=Mock();service.approve_representatives.return_value={'results':[]}
    monkeypatch.setattr(api_goalx,'GoalAnalysis',lambda:service)
    app.dependency_overrides[api_player_requests.verified_user]=lambda:CurrentUser(uid='analyst',claims={'email':'analyst@example.com'})
    client=TestClient(app);path='/goalx/representative/bulk';goal={'gmId':'match','side':'H','recordId':'goal'}
    assert client.post(path,json={'goals':[goal]}).status_code==403
    service.approve_representatives.assert_not_called()
    app.dependency_overrides[api_player_requests.verified_user]=lambda:CurrentUser(uid='manager',claims={'email':'jpdindex@gmail.com'})
    assert client.post(path,json={'goals':[]}).status_code==422
    assert client.post(path,json={'goals':[goal]*101}).status_code==422
    assert client.post(path,json={'goals':[{**goal,'gmId':'bad/path'}]}).status_code==422
    assert client.post(path,json={'goals':[goal]}).status_code==200
    service.approve_representatives.assert_called_once()
