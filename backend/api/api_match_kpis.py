from fastapi import APIRouter, Form
from pydantic import BaseModel

from backend.build.build_match_kpis import BuildMatchKpis
from backend.system.system_firestore import BackendError, JpdDidData, RequiredUser


class MatchKpiBuildResponse(BaseModel):
    status: str
    matched: int
    created: int
    recalculated: int
    validated: int
    validationFailed: int
    unchanged: int
    empty: int
    failed: int
    errors: list[str]


router = APIRouter(tags=["match-kpis"])


@router.post("/match-kpis/build", response_model=MatchKpiBuildResponse, summary="Build match KPI")
def build_match_kpis(
    league: str = Form(...),
    season: str | None = Form(default=None),
    round: int | None = Form(default=None, ge=1),
    team_id: str | None = Form(default=None),
    gm_id: str | None = Form(default=None),
    force: bool = Form(default=False, description="Recalculate current raw after a KPI rule change"),
    _: RequiredUser = None,
) -> MatchKpiBuildResponse:
    data = JpdDidData()
    matches = data.list_matches(
        league_id=league,
        season_id=season,
        round_number=round,
        team_id=team_id,
        gm_id=gm_id,
    )
    builder = BuildMatchKpis(data)
    counts = {
        "created": 0, "recalculated": 0, "validated": 0,
        "validationFailed": 0, "unchanged": 0, "empty": 0, "failed": 0,
    }
    errors: list[str] = []

    for current_gm_id, match in matches:
        sides = (
            ("H",) if team_id == match.homeTeamId
            else ("A",) if team_id == match.awayTeamId
            else ("H", "A")
        )
        for side in sides:
            try:
                result = (
                    builder.rebuild_for_logic_change(current_gm_id, side)
                    if force else builder.build_for_raw_change(current_gm_id, side)
                )
            except BackendError as exc:
                counts["failed"] += 1
                if len(errors) < 20:
                    errors.append(f"{current_gm_id}/{side}: {exc.code}")
                continue
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

    return MatchKpiBuildResponse(status="ok", matched=len(matches), errors=errors, **counts)
