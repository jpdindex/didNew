import logging
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Form
from pydantic import BaseModel

from backend.build.build_match_kpis import BuildMatchKpis
from backend.system.system_firestore import BackendError, JpdDidData, RequiredUser
from backend.system.system_pipeline_jobs import PipelineJob, pipeline_jobs


class MatchKpiStatusResponse(BaseModel):
    status: str
    league: str
    season: str | None
    roundFrom: int | None
    roundTo: int | None
    teamId: str | None
    gmId: str | None
    force: bool
    matchesTotal: int
    matchesProcessed: int
    targetsTotal: int
    targetsProcessed: int
    percent: int
    startedAt: datetime | None
    completedAt: datetime | None
    created: int
    recalculated: int
    validated: int
    validationFailed: int
    unchanged: int
    empty: int
    failed: int
    errors: list[str]
    error: str | None = None


logger = logging.getLogger(__name__)


router = APIRouter(tags=["match-kpis"])


def _scope_from_job(job: PipelineJob) -> dict[str, object]:
    return job.scope


def _status_response(job: PipelineJob | None) -> MatchKpiStatusResponse:
    if job is None:
        return MatchKpiStatusResponse(
            status="idle", league="EPL", season=None, roundFrom=None, roundTo=None,
            teamId=None, gmId=None, force=False, matchesTotal=0, matchesProcessed=0,
            targetsTotal=0, targetsProcessed=0, percent=0, created=0, recalculated=0,
            startedAt=None, completedAt=None, validated=0, validationFailed=0,
            unchanged=0, empty=0, failed=0, errors=[],
        )
    snapshot = job.snapshot()
    scope = _scope_from_job(job)
    counts = snapshot["counts"]
    return MatchKpiStatusResponse(
        status=str(snapshot["status"]), league=str(scope["league"]), season=scope["season"],
        roundFrom=scope["roundFrom"], roundTo=scope["roundTo"], teamId=scope["teamId"],
        gmId=scope["gmId"], force=bool(scope["force"]),
        matchesTotal=int(snapshot["matchesTotal"]), matchesProcessed=int(snapshot["matchesProcessed"]),
        targetsTotal=int(snapshot["targetsTotal"]), targetsProcessed=int(snapshot["targetsProcessed"]),
        percent=int(snapshot["percent"]), startedAt=snapshot["startedAt"],
        completedAt=snapshot["completedAt"], created=int(counts.get("created", 0)),
        recalculated=int(counts.get("recalculated", 0)), validated=int(counts.get("validated", 0)),
        validationFailed=int(counts.get("validationFailed", 0)), unchanged=int(counts.get("unchanged", 0)),
        empty=int(counts.get("empty", 0)), failed=int(counts.get("failed", 0)),
        errors=list(snapshot["errors"]), error=snapshot["error"],
    )


def _run_kpi_job(job: PipelineJob) -> None:
    scope = _scope_from_job(job)
    data = JpdDidData()
    matches = data.list_matches(
        league_id=str(scope["league"]), season_id=scope["season"],
        round_from=scope["roundFrom"], round_to=scope["roundTo"],
        team_id=scope["teamId"], gm_id=scope["gmId"],
    )
    match_sides = [
        (gm_id, side)
        for gm_id, match in matches
        for side in (("H",) if scope["teamId"] == match.homeTeamId else ("A",) if scope["teamId"] == match.awayTeamId else ("H", "A"))
    ]
    job.begin(matches_total=len(matches), targets_total=len(match_sides))
    builder = BuildMatchKpis(data)
    counts = {"created": 0, "recalculated": 0, "validated": 0, "validationFailed": 0, "unchanged": 0, "empty": 0, "failed": 0}
    errors: list[str] = []
    completed_matches: set[str] = set()
    remaining_by_match = Counter(gm_id for gm_id, _ in match_sides)
    for index, (current_gm_id, side) in enumerate(match_sides, start=1):
        try:
            result = builder.rebuild_for_logic_change(current_gm_id, side) if scope["force"] else builder.build_for_raw_change(current_gm_id, side)
        except BackendError as exc:
            counts["failed"] += 1
            if len(errors) < 20:
                errors.append(f"{current_gm_id}/{side}: {exc.code}")
        else:
            if result.empty:
                counts["empty"] += 1
            elif result.validation_failed:
                counts["validationFailed"] += 1
            elif result.created:
                counts["created"] += 1
            elif result.recalculated:
                counts["recalculated"] += 1
            elif result.validated:
                counts["validated"] += 1
            else:
                counts["unchanged"] += 1
        remaining_by_match[current_gm_id] -= 1
        if remaining_by_match[current_gm_id] == 0:
            completed_matches.add(current_gm_id)
        job.progress(matches_processed=len(completed_matches), targets_processed=index, counts=counts, errors=errors)
    job.complete(matches_processed=len(completed_matches), targets_processed=len(match_sides), counts=counts, errors=errors)
    logger.info("KPI pipeline complete: matches=%s targets=%s", len(matches), len(match_sides))


@router.get("/match-kpis/status", response_model=MatchKpiStatusResponse, summary="Read KPI pipeline status")
def read_match_kpi_status(_: RequiredUser = None) -> MatchKpiStatusResponse:
    return _status_response(pipeline_jobs.get("match-kpis"))


@router.post("/match-kpis/build", response_model=MatchKpiStatusResponse, status_code=202, summary="Start match KPI pipeline")
def build_match_kpis(
    league: str = Form(default="EPL"),
    season: str | None = Form(default=None),
    round_from: int | None = Form(default=None, ge=1),
    round_to: int | None = Form(default=None, ge=1),
    team_id: str | None = Form(default=None),
    gm_id: str | None = Form(default=None),
    force: bool = Form(default=False, description="Recalculate current raw after a KPI rule change"),
    _: RequiredUser = None,
) -> MatchKpiStatusResponse:
    if round_from is not None and round_to is not None and round_from > round_to:
        raise BackendError("round_from must not exceed round_to", status_code=422, code="round_range_invalid")
    scope = {
        "league": league, "season": season or None, "roundFrom": round_from,
        "roundTo": round_to, "teamId": team_id or None, "gmId": gm_id or None, "force": force,
    }
    job = pipeline_jobs.start("match-kpis", scope, _run_kpi_job)
    if job is None:
        raise BackendError("KPI pipeline is already running", status_code=409, code="kpi_pipeline_running")
    return _status_response(job)
