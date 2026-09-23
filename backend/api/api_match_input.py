from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.system.system_firestore import BackendError, JpdDidData, NotFoundError, RequiredUser, utc_now
from backend.system.system_schema import Half, InputMode, Side


class InputLineup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    playerId: str
    slot: str
    order: int = Field(ge=0)
    type: Literal["START", "BENCH"]
    no: str
    name: str
    pos: str
    inHalf: Half | None = None
    inSeconds: int | None = Field(default=None, ge=0)
    outHalf: Half | None = None
    outSeconds: int | None = Field(default=None, ge=0)


class InputRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    half: Half
    halfSeconds: int = Field(ge=0)
    seq: int = Field(ge=0)
    act: Literal["C", "P", "K", "F", "S", "H", "R", ""]
    res: Literal["O", "X", "B", "GB", "GX", "GOAL", "L", "H", "R", "LX", "HX", "RX", ""]
    area: int = Field(ge=1, le=18)
    posX: float | None = None
    posY: float | None = None
    shootPosX: float | None = None
    shootPosY: float | None = None
    shootDspRange: bool | None = None
    isShot: bool | None = None
    playerId: str | None = None


class InputCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    playerId: str
    half: Half
    halfSeconds: int = Field(ge=0)
    card: Literal["Y", "R"]


class HalfInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: int = Field(ge=0)


class MatchSnapshotInput(BaseModel):
    """Identity fields retained with a Draft to prevent match-target mixups."""

    model_config = ConfigDict(extra="forbid")
    leagueId: str
    seasonId: str
    round: int | None = None
    date: str
    kickoffTime: str | None = None
    stadiumId: str | None = None
    matchType: str | None = None
    homeTeamId: str
    awayTeamId: str


class FormationChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    half: Half
    seconds: int = Field(ge=0)
    formationKey: str
    assigned: dict[str, str]


class MatchInputPayload(BaseModel):
    """Complete team input snapshot used for both Draft and RAW promotion."""

    model_config = ConfigDict(extra="forbid")
    gmId: str
    side: Side
    inputMode: InputMode
    fieldSide: Literal["left", "right"]
    formationKey: str
    homeScore: int = Field(ge=0)
    awayScore: int = Field(ge=0)
    status: Literal["ready", "H1", "H1_done", "H2", "H2_done", "final"]
    halves: dict[Half, HalfInput]
    lineup: list[InputLineup]
    records: list[InputRecord]
    cards: list[InputCard] = []
    recorderLevel: Literal["basic", "advanced"] = "advanced"
    # Older local Drafts and validation tools did not include this field.
    # New input clients always send it; promotion validates it when present.
    matchSnapshot: MatchSnapshotInput | None = None
    formationChanges: list[FormationChangeInput] = []

    @field_validator("lineup")
    @classmethod
    def unique_lineup_players(cls, value: list[InputLineup]) -> list[InputLineup]:
        if len({player.playerId for player in value}) != len(value):
            raise ValueError("lineup playerId values must be unique")
        return value


# Compatibility for existing importers and checks.
MatchInputCommit = MatchInputPayload


class DraftWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: MatchInputPayload
    clientState: dict[str, Any]


class DraftResponse(BaseModel):
    status: str
    gmId: str
    side: Side
    payload: MatchInputPayload
    clientState: dict[str, Any]
    updatedAt: str | None = None


class DraftMissingResponse(BaseModel):
    status: Literal["missing"]
    gmId: str
    side: Side


class PromotionResponse(BaseModel):
    status: str
    gmId: str
    side: Side
    recordsSaved: int
    cardsSaved: int
    draftDeleted: bool


class ParticipantJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["primary", "assistant"]
    displayName: str | None = Field(default=None, max_length=100)


router = APIRouter(tags=["match-input"])


def _display_name(document: dict, fallback: str) -> str:
    return str(document.get("nameKr") or document.get("name") or document.get("nameShort") or fallback)


def _draft_response(document: dict[str, Any]) -> DraftResponse:
    updated_at = document.get("updatedAt")
    return DraftResponse(
        status="ok", gmId=document["gmId"], side=document["side"],
        payload=MatchInputPayload.model_validate(document["payload"]),
        clientState=document.get("clientState") or {},
        updatedAt=updated_at.isoformat() if updated_at else None,
    )


def _raw_values(
    payload: MatchInputPayload,
    *,
    status: str,
    halves: set[Half] | None = None,
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    """Convert the durable Draft contract into the existing recording/raw schema."""
    now = utc_now()
    selected_halves = halves or {"H1", "H2"}
    lineup = {
        item.playerId: {
            "slot": item.slot, "order": item.order, "type": item.type, "no": item.no,
            "name": item.name, "pos": item.pos, "inHalf": item.inHalf, "inSeconds": item.inSeconds,
            "outHalf": item.outHalf, "outSeconds": item.outSeconds,
        }
        for item in payload.lineup
    }
    records: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for item in payload.records:
        if item.half not in selected_halves:
            continue
        record_id = item.id or uuid4().hex
        if record_id in seen_ids:
            # Early drafts created by the former screen-local counter can contain
            # rec_1 in both halves. Preserve both events while assigning a raw ID.
            record_id = uuid4().hex
        seen_ids.add(record_id)
        records.append((record_id, {
            **item.model_dump(exclude={"id"}, mode="python"),
            "source": "did", "createdAt": now,
        }))
    cards = [
        (f"{item.half}_{item.halfSeconds}_{item.playerId}_{index}", {
            **item.model_dump(mode="python"), "createdAt": now,
        })
        for index, item in enumerate(payload.cards)
        if item.half in selected_halves
    ]
    all_halves = {
        half: {"startedAt": None, "seconds": payload.halves.get(half, HalfInput(seconds=0)).seconds}
        for half in ("H1", "H2")
    }
    recording = {
        "side": payload.side,
        "status": status,
        "inputMode": payload.inputMode,
        "fieldSide": payload.fieldSide,
        "fieldSideEx": None,
        "formationKey": payload.formationKey,
        "lineup": lineup,
        "halves": all_halves,
        "h1Locked": False,
        "h2Locked": False,
        "maxSeq": max((record[1]["seq"] for record in records), default=0),
        # Any raw replacement invalidates derived KPI/rating values.
        "kpi": None, "kpiComputedAt": None, "kpiVersion": 0, "kpiSourceFingerprint": None,
        "teamRating": None, "ratingBasedOn": None,
        "legacyGiId": None, "legacyRecorderId": None, "syncedAt": None,
        "createdAt": now, "updatedAt": now,
    }
    return recording, records, cards


def _promote(
    data: JpdDidData,
    payload: MatchInputPayload,
    user_id: str,
    *,
    status: Literal["H1_done", "final"],
    halves: set[Half],
    delete_draft: bool,
) -> PromotionResponse:
    match = data.get_match(payload.gmId)
    snapshot = payload.matchSnapshot
    if snapshot and (
        snapshot.homeTeamId != match.homeTeamId
        or snapshot.awayTeamId != match.awayTeamId
        or snapshot.leagueId != match.leagueId
        or snapshot.seasonId != match.seasonId
    ):
        raise BackendError("Draft match snapshot no longer matches the selected match", status_code=409, code="draft_match_mismatch")
    recording, records, cards = _raw_values(payload, status=status, halves=halves)
    recording["teamId"], recording["opponentTeamId"] = (
        (match.homeTeamId, match.awayTeamId) if payload.side == "H" else (match.awayTeamId, match.homeTeamId)
    )
    data.replace_recording_raw(
        payload.gmId, payload.side, recording=recording, records=records, cards=cards,
        score={"home": payload.homeScore, "away": payload.awayScore},
    )
    if delete_draft:
        data.delete_input_draft(payload.gmId, payload.side)
    return PromotionResponse(
        status="ok", gmId=payload.gmId, side=payload.side,
        recordsSaved=len(records), cardsSaved=len(cards), draftDeleted=delete_draft,
    )


@router.get("/match-input/matches", summary="Read scheduled matches")
def list_input_matches(year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12), _: RequiredUser = None) -> dict:
    data = JpdDidData()
    month_matches = data.list_matches_for_month(year, month)
    if not month_matches:
        return {"status": "ok", "matches": []}

    team_ids = {
        team_id
        for _, match in month_matches
        for team_id in (match.homeTeamId, match.awayTeamId)
    }
    stadium_ids = {match.stadiumId for _, match in month_matches if match.stadiumId}
    teams = data.get_documents_by_ids("teams", team_ids)
    stadiums = data.get_documents_by_ids("stadiums", stadium_ids)
    gm_ids = [gm_id for gm_id, _ in month_matches]
    statuses_by_match = data.get_recording_statuses_many(gm_ids)
    collaboration_by_match = data.get_input_draft_participants_many(gm_ids)

    matches = []
    for gm_id, match in month_matches:
        home = teams.get(match.homeTeamId, {})
        away = teams.get(match.awayTeamId, {})
        stadium = stadiums.get(match.stadiumId, {})
        recording_status = statuses_by_match.get(gm_id, {"H": None, "A": None})
        collaboration = collaboration_by_match.get(gm_id, {"H": {}, "A": {}})

        # Imported/finalized fixtures keep their official score on matches.
        # During live input, both team Drafts carry the same scoreboard; use a
        # shared Draft score when present so the schedule does not lag behind.
        live_score = next(
            (summary.get("score") for summary in (collaboration["H"], collaboration["A"])
            if isinstance(summary.get("score"), dict)),
            None,
        )
        score = live_score or {"home": match.score.home, "away": match.score.away}

        def _input_state(side: Side) -> dict[str, Any]:
            raw_status = recording_status[side]
            lifecycle = "final" if raw_status == "final" else collaboration[side].get("status") or "ready"
            return {
                "rawStatus": raw_status,
                "completed": lifecycle == "final",
                "lifecycleStatus": lifecycle,
            }
        matches.append({
            "gmId": gm_id, "date": match.date, "kickoffTime": match.kickoffTime,
            "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
            "stadiumId": match.stadiumId, "stadiumName": _display_name(stadium, match.stadiumId),
            "home": {"teamId": match.homeTeamId, "name": _display_name(home, match.homeTeamId)},
            "away": {"teamId": match.awayTeamId, "name": _display_name(away, match.awayTeamId)},
            "score": score,
            "inputStatus": {
                "H": _input_state("H"),
                "A": _input_state("A"),
            },
            "collaboration": collaboration,
        })
    return {"status": "ok", "matches": sorted(matches, key=lambda item: (item["date"], item["kickoffTime"] or "", item["gmId"]))}


@router.get("/match-input/analyst-dashboard", summary="Read analyst operations dashboard")
def read_analyst_dashboard(
    date_from: str | None = Query(default=None, max_length=20),
    date_to: str | None = Query(default=None, max_length=20),
    level: Literal["basic", "advanced"] | None = Query(default=None),
    lifecycle: Literal["ready", "H1", "H1_done", "H2", "H2_done", "final"] | None = Query(default=None),
    _: RequiredUser = None,
) -> dict:
    return JpdDidData().build_analyst_dashboard(
        date_from=date_from, date_to=date_to, level=level, lifecycle=lifecycle,
    )


@router.get("/match-input/matches/{gm_id}/bootstrap", summary="Read input lobby bootstrap")
def read_match_input_bootstrap(
    gm_id: str,
    side: Side = Query(...),
    _: RequiredUser = None,
) -> dict:
    """Load squads, selected session and all dashboard KPI views atomically for the lobby."""
    return {"status": "ok", **JpdDidData().build_input_bootstrap(gm_id, side)}


@router.get("/match-input/matches/{gm_id}/dashboard-kpis", summary="Read both teams' input dashboard KPI")
def read_match_dashboard_kpis(
    gm_id: str,
    half: Literal["all", "H1", "H2"] = Query(default="all"),
    _: RequiredUser = None,
) -> dict:
    JpdDidData().get_match(gm_id)
    return {"status": "ok", "gmId": gm_id, "half": half, "kpis": JpdDidData().read_match_dashboard_kpis(gm_id, half=half)}


@router.get("/match-input/matches/{gm_id}/squads", summary="Read match squads")
def read_match_squads(gm_id: str, _: RequiredUser = None) -> dict:
    data = JpdDidData()
    match = data.get_match(gm_id)
    result, cached = data.get_or_create_input_squads(gm_id, match)
    return {
        "status": "ok", "gmId": gm_id, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
        "H": result["H"], "A": result["A"],
        "cached": cached,
        "inputStatus": data.get_recording_input_states(gm_id),
        "matchSnapshot": {
            "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
            "date": match.date, "kickoffTime": match.kickoffTime, "stadiumId": match.stadiumId,
            "matchType": match.matchType, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
        },
    }


@router.post("/match-input/matches/{gm_id}/squads/refresh", summary="Refresh match squad snapshot")
def refresh_match_squads(gm_id: str, _: RequiredUser = None) -> dict:
    """Use only after correcting player contract data for a fixture."""
    data = JpdDidData()
    match = data.get_match(gm_id)
    squads = data.refresh_input_squads(gm_id, match)
    return {"status": "ok", "gmId": gm_id, "H": squads["H"], "A": squads["A"], "cached": False}


@router.post("/match-input/legacy-lineups/backfill", summary="Backfill imported match input snapshots")
def backfill_legacy_lineups(
    limit: int = Query(default=100, ge=1, le=500),
    _: RequiredUser = None,
) -> dict:
    """Create lineup-aware input snapshots for a bounded set of imported finals."""
    return {"status": "ok", **JpdDidData().backfill_legacy_input_squads(limit=limit)}


@router.put("/match-input/drafts/{gm_id}/{side}", response_model=DraftResponse, summary="Save input draft")
def save_draft(gm_id: str, side: Side, request: DraftWriteRequest, user: RequiredUser = None) -> DraftResponse:
    if request.payload.gmId != gm_id or request.payload.side != side:
        raise BackendError("Draft path and payload must identify the same match side", status_code=422, code="draft_target_mismatch")
    data = JpdDidData()
    match = data.get_match(gm_id)
    snapshot = request.payload.matchSnapshot
    if snapshot and (snapshot.homeTeamId != match.homeTeamId or snapshot.awayTeamId != match.awayTeamId):
        raise BackendError("Draft match snapshot does not match this fixture", status_code=422, code="draft_match_mismatch")
    document = data.save_input_draft(gm_id, side, payload=request.payload.model_dump(mode="python"), client_state=request.clientState, user_id=user.uid)
    return _draft_response(document)


@router.post("/match-input/drafts/{gm_id}/{side}/participants", summary="Join a shared input draft")
def join_draft_participant(gm_id: str, side: Side, request: ParticipantJoinRequest, user: RequiredUser = None) -> dict:
    data = JpdDidData()
    # The selected fixture must exist even before its first Draft is created.
    data.get_match(gm_id)
    document = data.join_input_draft_participant(
        gm_id, side, user_id=user.uid, role=request.role, display_name=request.displayName,
    )
    return {
        "status": "ok", "gmId": gm_id, "side": side,
        "primaryUid": document.get("primaryUid"), "participants": document.get("participants", {}),
    }


@router.get("/match-input/drafts/{gm_id}/{side}", response_model=DraftResponse | DraftMissingResponse, summary="Read input draft")
def get_draft(gm_id: str, side: Side, _: RequiredUser = None) -> DraftResponse | DraftMissingResponse:
    try:
        document = JpdDidData().get_input_draft(gm_id, side)
        # A primary can be registered before its first lifecycle snapshot. That
        # is a normal empty collaborative Draft, not a malformed API response.
        if not document.get("payload"):
            return DraftMissingResponse(status="missing", gmId=gm_id, side=side)
        return _draft_response(document)
    except NotFoundError:
        # A side without a draft is the normal first-entry condition.
        return DraftMissingResponse(status="missing", gmId=gm_id, side=side)


@router.get("/match-input/matches/{gm_id}/recordings/{side}/input-state", response_model=DraftResponse, summary="Read final raw for input display")
def get_final_raw_input_state(gm_id: str, side: Side, _: RequiredUser = None) -> DraftResponse:
    """Read a final RAW recording as an input-screen snapshot without creating a Draft."""
    return _draft_response(JpdDidData().read_input_state_from_raw(gm_id, side))


@router.delete("/match-input/drafts/{gm_id}/{side}", summary="Delete input draft")
def delete_draft(gm_id: str, side: Side, _: RequiredUser = None) -> dict:
    JpdDidData().delete_input_draft(gm_id, side)
    return {"status": "ok", "gmId": gm_id, "side": side}


@router.post("/match-input/drafts/{gm_id}/{side}/restore-raw", response_model=DraftResponse, summary="Create editable draft from final raw")
def restore_raw_to_draft(gm_id: str, side: Side, user: RequiredUser = None) -> DraftResponse:
    return _draft_response(JpdDidData().restore_input_draft_from_raw(gm_id, side, user_id=user.uid))


@router.post("/match-input/drafts/{gm_id}/{side}/promote-h1", response_model=PromotionResponse, summary="Confirm H1 draft without RAW promotion")
def promote_h1(gm_id: str, side: Side, user: RequiredUser = None) -> PromotionResponse:
    data = JpdDidData()
    draft = data.get_input_draft(gm_id, side)
    if draft.get("primaryUid") and draft["primaryUid"] != user.uid:
        raise BackendError("Only the primary analyst can confirm halftime", status_code=403, code="primary_required")
    payload = MatchInputPayload.model_validate(draft["payload"])
    if payload.recorderLevel != "advanced":
        raise BackendError("Only the primary analyst can confirm halftime", status_code=409, code="primary_required")
    # H1 end is a Draft checkpoint, never a partial RAW write.
    return PromotionResponse(status="ok", gmId=gm_id, side=side, recordsSaved=0, cardsSaved=0, draftDeleted=False)


@router.post("/match-input/drafts/{gm_id}/{side}/finalize", response_model=PromotionResponse, summary="Finalize advanced draft as raw")
def finalize_advanced(gm_id: str, side: Side, user: RequiredUser = None) -> PromotionResponse:
    data = JpdDidData()
    draft = data.get_input_draft(gm_id, side)
    if draft.get("primaryUid") and draft["primaryUid"] != user.uid:
        raise BackendError("Only the primary analyst can finalize RAW", status_code=403, code="primary_required")
    payload = MatchInputPayload.model_validate(draft["payload"])
    if payload.recorderLevel != "advanced":
        raise BackendError("Basic input requires administrator approval", status_code=409, code="basic_approval_required")
    # Keep the final Draft as the editable source for later corrections. RAW is
    # replaced atomically, but the Draft is retained and marked final.
    return _promote(data, payload, user.uid, status="final", halves={"H1", "H2"}, delete_draft=False)


@router.get("/match-input/approvals", summary="List basic drafts awaiting administrator approval")
def list_approvals(_: RequiredUser = None) -> dict:
    drafts = JpdDidData().list_input_drafts(recorder_level="basic", status="final")
    return {"status": "ok", "drafts": [{"gmId": item["gmId"], "side": item["side"], "updatedAt": item.get("updatedAt"), "payload": item["payload"]} for item in drafts]}


@router.post("/match-input/approvals/{gm_id}/{side}/promote", response_model=PromotionResponse, summary="Approve basic draft and promote it to raw")
def approve_basic(gm_id: str, side: Side, user: RequiredUser = None) -> PromotionResponse:
    data = JpdDidData()
    payload = MatchInputPayload.model_validate(data.get_input_draft(gm_id, side)["payload"])
    if payload.recorderLevel != "basic" or payload.status != "final":
        raise BackendError("Only submitted basic drafts can be approved", status_code=409, code="draft_not_awaiting_approval")
    return _promote(data, payload, user.uid, status="final", halves={"H1", "H2"}, delete_draft=True)
