from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.build.build_match_kpis import BuildMatchKpis
from backend.system.system_base import Side
from backend.system.system_control import BackendError, JpdDidData, RequiredUser


class MatchKpiRunResponse(BaseModel):
    status: str
    target: str
    matched: int
    created: int
    recalculated: int
    unchanged: int
    empty: int
    failed: int
    errors: list[str]


router = APIRouter(tags=["match-kpis"])


def run_scope(
    *,
    target: str,
    logic_changed: bool,
    round_number: int | None = None,
    gm_id: str | None = None,
    side: Side | None = None,
) -> MatchKpiRunResponse:
    data = JpdDidData()
    matches = data.list_matches(round_number=round_number, gm_id=gm_id)
    builder = BuildMatchKpis(data)
    counts = {"created": 0, "recalculated": 0, "unchanged": 0, "empty": 0, "failed": 0}
    errors: list[str] = []

    for current_gm_id, _ in matches:
        for current_side in [side] if side else ["H", "A"]:
            try:
                result = (
                    builder.rebuild_for_logic_change(current_gm_id, current_side)
                    if logic_changed
                    else builder.build_for_raw_change(current_gm_id, current_side)
                )
            except BackendError as exc:
                counts["failed"] += 1
                if len(errors) < 20:
                    errors.append(f"{current_gm_id}/{current_side}: {exc.code}")
                continue
            if result.empty:
                counts["empty"] += 1
            elif result.created:
                counts["created"] += 1
            elif result.recalculated:
                counts["recalculated"] += 1
            else:
                counts["unchanged"] += 1

    return MatchKpiRunResponse(status="ok", target=target, matched=len(matches), errors=errors, **counts)


@router.post("/match-kpis/raw-change/all", response_model=MatchKpiRunResponse, summary="Raw Change: all matches")
def build_all_after_raw_change(_: RequiredUser) -> MatchKpiRunResponse:
    return run_scope(target="all", logic_changed=False)


@router.post("/match-kpis/raw-change/round", response_model=MatchKpiRunResponse, summary="Raw Change: round rebuild")
def build_round_after_raw_change(
    round_number: int = Query(ge=1),
    gm_id: str | None = None,
    side: Side | None = None,
    _: RequiredUser = None,
) -> MatchKpiRunResponse:
    return run_scope(
        target="round", logic_changed=False, round_number=round_number,
        gm_id=gm_id, side=side,
    )


@router.post("/match-kpis/logic-change/all", response_model=MatchKpiRunResponse, summary="Logic Change: all matches")
def rebuild_all_after_logic_change(_: RequiredUser) -> MatchKpiRunResponse:
    return run_scope(target="all", logic_changed=True)


@router.post("/match-kpis/logic-change/round", response_model=MatchKpiRunResponse, summary="Logic Change: round rebuild")
def rebuild_round_after_logic_change(
    round_number: int = Query(ge=1),
    gm_id: str | None = None,
    side: Side | None = None,
    _: RequiredUser = None,
) -> MatchKpiRunResponse:
    return run_scope(
        target="round", logic_changed=True, round_number=round_number,
        gm_id=gm_id, side=side,
    )
