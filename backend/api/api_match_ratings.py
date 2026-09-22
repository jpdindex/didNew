from typing import Any

from fastapi import APIRouter, Form
from pydantic import BaseModel

from backend.build.build_match_rating import BuildMatchRating
from backend.system.system_schema import Side
from backend.system.system_firestore import BackendError, JpdDidData, RequiredUser


class MatchRatingBuildResponse(BaseModel):
    status: str
    matched: int
    rated: int
    unchanged: int
    failed: int
    errors: list[str]


class MatchRatingReadResponse(BaseModel):
    status: str
    gmId: str
    side: Side
    teamId: str
    opponentTeamId: str
    kpiVersion: int
    ratingBasedOn: int | None
    kpi: dict[str, int | float] | None
    teamRating: dict[str, Any] | None
    playerRatings: list[dict[str, Any]]


router = APIRouter(tags=["match-ratings"])


@router.post("/match-ratings/build", response_model=MatchRatingBuildResponse, summary="Build team and player rating")
def build_match_rating(
    league: str | None = Form(default="EPL"),
    gm_id: str | None = Form(default=None),
    round: int | None = Form(default=None, ge=1),
    season: str | None = Form(default=None),
    side: Side | None = Form(default=None),
    force: bool = Form(default=False, description="Recalculate and overwrite an existing rating for the current KPI version"),
    _: RequiredUser = None,
) -> MatchRatingBuildResponse:
    if not any((league, gm_id, round is not None, season)):
        raise BackendError(
            "Enter at least one of league, season, round, or gm_id",
            status_code=422,
            code="rating_scope_missing",
        )
    data = JpdDidData()
    matches = data.list_matches(league_id=league, gm_id=gm_id, round_number=round, season_id=season)
    builder = BuildMatchRating(data)
    rated = 0
    unchanged = 0
    errors: list[str] = []
    for current_gm_id, _ in matches:
        for current_side in (side,) if side else ("H", "A"):
            try:
                result = builder.build(current_gm_id, current_side, force=force)
                if result.unchanged:
                    unchanged += 1
                else:
                    rated += 1
            except BackendError as exc:
                if len(errors) < 20:
                    errors.append(f"{current_gm_id}/{current_side}: {exc.code}")
    attempted = len(matches) * (1 if side else 2)
    return MatchRatingBuildResponse(
        status="ok",
        matched=len(matches),
        rated=rated,
        unchanged=unchanged,
        failed=attempted - rated - unchanged,
        errors=errors,
    )


@router.post("/match-ratings/read", response_model=MatchRatingReadResponse, summary="Read team and player rating")
def read_match_rating(
    gm_id: str = Form(...),
    side: Side = Form(...),
    _: RequiredUser = None,
) -> MatchRatingReadResponse:
    data = JpdDidData()
    recording = data.get_recording(gm_id, side)
    player_ratings = [stat for _, stat in sorted(data.list_player_stats(gm_id, side).items())]
    return MatchRatingReadResponse(
        status="ok",
        gmId=gm_id,
        side=side,
        teamId=recording.teamId,
        opponentTeamId=recording.opponentTeamId,
        kpiVersion=recording.kpiVersion,
        ratingBasedOn=recording.ratingBasedOn,
        kpi=recording.kpi.model_dump(mode="json") if recording.kpi else None,
        teamRating=recording.teamRating.model_dump(mode="json") if recording.teamRating else None,
        playerRatings=player_ratings,
    )
