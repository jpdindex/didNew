import logging
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Form
from pydantic import BaseModel

from backend.build.build_match_rating import BuildMatchRating
from backend.system.system_schema import Side
from backend.system.system_firestore import BackendError, JpdDidData, RequiredUser
from backend.system.system_pipeline_jobs import PipelineJob, pipeline_jobs


class MatchRatingStatusResponse(BaseModel):
    status: str
    league: str
    season: str | None
    roundFrom: int | None
    roundTo: int | None
    gmId: str | None
    side: Side | None
    force: bool
    matchesTotal: int
    matchesProcessed: int
    targetsTotal: int
    targetsProcessed: int
    percent: int
    startedAt: datetime | None
    completedAt: datetime | None
    rated: int
    unchanged: int
    failed: int
    errors: list[str]
    error: str | None = None


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
logger = logging.getLogger(__name__)


def _status_response(job: PipelineJob | None) -> MatchRatingStatusResponse:
    if job is None:
        return MatchRatingStatusResponse(
            status="idle", league="EPL", season=None, roundFrom=None, roundTo=None,
            gmId=None, side=None, force=False, matchesTotal=0, matchesProcessed=0,
            targetsTotal=0, targetsProcessed=0, percent=0, startedAt=None,
            completedAt=None, rated=0, unchanged=0, failed=0, errors=[],
        )
    snapshot = job.snapshot()
    scope = job.scope
    counts = snapshot["counts"]
    return MatchRatingStatusResponse(
        status=str(snapshot["status"]), league=str(scope["league"]), season=scope["season"],
        roundFrom=scope["roundFrom"], roundTo=scope["roundTo"], gmId=scope["gmId"],
        side=scope["side"], force=bool(scope["force"]),
        matchesTotal=int(snapshot["matchesTotal"]), matchesProcessed=int(snapshot["matchesProcessed"]),
        targetsTotal=int(snapshot["targetsTotal"]), targetsProcessed=int(snapshot["targetsProcessed"]),
        percent=int(snapshot["percent"]), startedAt=snapshot["startedAt"],
        completedAt=snapshot["completedAt"], rated=int(counts.get("rated", 0)),
        unchanged=int(counts.get("unchanged", 0)), failed=int(counts.get("failed", 0)),
        errors=list(snapshot["errors"]), error=snapshot["error"],
    )


def _run_rating_job(job: PipelineJob) -> None:
    scope = job.scope
    data = JpdDidData()
    matches = data.list_matches(
        league_id=str(scope["league"]), season_id=scope["season"],
        round_from=scope["roundFrom"], round_to=scope["roundTo"], gm_id=scope["gmId"],
    )
    sides = (scope["side"],) if scope["side"] else ("H", "A")
    match_sides = [(gm_id, side) for gm_id, _ in matches for side in sides]
    job.begin(matches_total=len(matches), targets_total=len(match_sides))
    builder = BuildMatchRating(data)
    counts = {"rated": 0, "unchanged": 0, "failed": 0}
    errors: list[str] = []
    completed_matches: set[str] = set()
    remaining_by_match = Counter(gm_id for gm_id, _ in match_sides)
    for index, (current_gm_id, current_side) in enumerate(match_sides, start=1):
        try:
            result = builder.build(current_gm_id, current_side, force=bool(scope["force"]))
        except BackendError as exc:
            counts["failed"] += 1
            if len(errors) < 20:
                errors.append(f"{current_gm_id}/{current_side}: {exc.code}")
        else:
            counts["unchanged" if result.unchanged else "rated"] += 1
        remaining_by_match[current_gm_id] -= 1
        if remaining_by_match[current_gm_id] == 0:
            completed_matches.add(current_gm_id)
        job.progress(matches_processed=len(completed_matches), targets_processed=index, counts=counts, errors=errors)
    job.complete(matches_processed=len(completed_matches), targets_processed=len(match_sides), counts=counts, errors=errors)
    logger.info("Rating pipeline complete: matches=%s targets=%s", len(matches), len(match_sides))


@router.get("/match-ratings/status", response_model=MatchRatingStatusResponse, summary="Read rating pipeline status")
def read_match_rating_status(_: RequiredUser = None) -> MatchRatingStatusResponse:
    return _status_response(pipeline_jobs.get("match-ratings"))


@router.post("/match-ratings/build", response_model=MatchRatingStatusResponse, status_code=202, summary="Start team and player rating pipeline")
def build_match_rating(
    league: str | None = Form(default="EPL"),
    gm_id: str | None = Form(default=None),
    round_from: int | None = Form(default=None, ge=1),
    round_to: int | None = Form(default=None, ge=1),
    season: str | None = Form(default=None),
    side: Side | None = Form(default=None),
    force: bool = Form(default=False, description="Recalculate and overwrite an existing rating for the current KPI version"),
    _: RequiredUser = None,
 ) -> MatchRatingStatusResponse:
    if not any((league, gm_id, round_from is not None, round_to is not None, season)):
        raise BackendError(
            "Enter at least one of league, season, round range, or gm_id",
            status_code=422,
            code="rating_scope_missing",
        )
    if round_from is not None and round_to is not None and round_from > round_to:
        raise BackendError("round_from must not exceed round_to", status_code=422, code="round_range_invalid")
    scope = {
        "league": league or "EPL", "season": season or None, "roundFrom": round_from,
        "roundTo": round_to, "gmId": gm_id or None, "side": side, "force": force,
    }
    job = pipeline_jobs.start("match-ratings", scope, _run_rating_job)
    if job is None:
        raise BackendError("Rating pipeline is already running", status_code=409, code="rating_pipeline_running")
    return _status_response(job)


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
