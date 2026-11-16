from functools import lru_cache
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, ConfigDict, StringConstraints

from backend.api.api_player_requests import User
from backend.system.system_goalx import GoalAnalysis, Grades, TestGoalInput, shotx_access
from backend.system.system_schema import Side

router = APIRouter(prefix="/goalx", tags=["GoalX"], include_in_schema=False)
DocumentId = Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r"^[^/]+$")]
GoalId = Annotated[str, Path(min_length=1, max_length=500, pattern=r"^[^/]+$")]


class ShotLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shotId: DocumentId | None

@lru_cache(maxsize=1)
def goal_analysis():
    return GoalAnalysis()


@router.get("/catalog")
def catalog(_: User):
    return goal_analysis().catalog(int(monotonic() // 60))


@router.get("/rounds")
def rounds(_: User, league_id: str, season_id: str):
    return {"rounds": goal_analysis().rounds(league_id, season_id, int(monotonic() // 30))}


@router.get("/fixture-seasons")
def fixture_seasons(_: User, league_id: str):
    return {"ids": goal_analysis().fixture_seasons(league_id, int(monotonic() // 300))}


@router.get("/goals")
def goals(
    user: User, league_id: str, season_id: str,
    round_id: Annotated[str, Query(pattern=r"^(?:[0-9]{1,4}|R32|R16|QF|SF|F|unassigned)$")],
):
    return GoalAnalysis().list(league_id, season_id, round_id, user.uid)


@router.get("/ranking")
def ranking(
    user: User, league_id: str, season_id: str,
    round_id: Annotated[str, Query(pattern=r"^(?:all|[0-9]{1,4}|R32|R16|QF|SF|F|unassigned)$")] = "all",
):
    return GoalAnalysis().ranking(league_id, season_id, round_id, user.uid)


@router.get("/test-goals/shotx-candidates")
def test_shot_candidates(_: User, search: Annotated[str, Query(max_length=150)] = ""):
    with shotx_access():
        return {"candidates": GoalAnalysis().shot_candidates(search)}


@router.post("/test-goals", status_code=201)
def create_test_goal(payload: TestGoalInput, user: User):
    with shotx_access():
        return {"id": GoalAnalysis().write_test_goal(payload, user.uid)}


@router.put("/test-goals/{goal_id}")
def update_test_goal(goal_id: GoalId, payload: TestGoalInput, user: User):
    with shotx_access():
        return {"id": GoalAnalysis().write_test_goal(payload, user.uid, goal_id)}


@router.get("/goals/{gm_id}/{side}/{record_id}/shotx")
def shot_detail(gm_id: GoalId, side: Side, record_id: GoalId, _: User):
    with shotx_access():
        return {"shot": GoalAnalysis().detail(gm_id, side, record_id)}


@router.get("/goals/{gm_id}/{side}/{record_id}/candidates")
def shot_candidates(
    gm_id: GoalId, side: Side, record_id: GoalId, _: User,
    search: Annotated[str, Query(max_length=150)] = "",
):
    with shotx_access():
        return {"candidates": GoalAnalysis().candidates(gm_id, side, record_id, search)}


@router.put("/goals/{gm_id}/{side}/{record_id}/shotx")
def link_shot(gm_id: GoalId, side: Side, record_id: GoalId, payload: ShotLink, user: User):
    with shotx_access():
        return {"analysis": GoalAnalysis().link(gm_id, side, record_id, user.uid, payload.shotId)}


@router.put("/goals/{gm_id}/{side}/{record_id}/evaluation")
def save_evaluation(gm_id: GoalId, side: Side, record_id: GoalId, payload: Grades, user: User):
    return {"analysis": GoalAnalysis().save(gm_id, side, record_id, user.uid, payload)}
