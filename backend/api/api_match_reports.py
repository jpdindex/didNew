from functools import lru_cache
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Path

from backend.api.api_player_requests import User
from backend.system.system_match_reports import MatchReports
from backend.system.system_schema import Side

router = APIRouter(prefix="/match-reports", tags=["Match Reports"], include_in_schema=False)
MatchId = Annotated[str, Path(min_length=1, max_length=500, pattern=r"^[^/]+$")]


@lru_cache(maxsize=1)
def match_reports():
    return MatchReports()


@router.get("/matches")
def matches(_: User, league_id: str, season_id: str):
    return match_reports().matches(league_id, season_id, int(monotonic() // 30))


@router.get("/{gm_id}/{side}")
def report(gm_id: MatchId, side: Side, _: User):
    return match_reports().report(gm_id, side)
