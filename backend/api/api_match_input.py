from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from firebase_admin import auth
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.system.system_firestore import BackendError, CurrentUser, JpdDidData, NotFoundError, RequiredUser, ensure_firebase_app, get_settings, utc_now
from backend.system.system_kpi_rules import KpiRecord, calculate_kpis
from backend.system.system_schema import Half, InputMode, Side


logger = logging.getLogger(__name__)


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
    id: str | None = None
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
    # One endpoint serves durable checkpoints and the two lightweight live-sync
    # mutations.  These endpoints are intentionally internal to the frontend.
    # `state_records` is the live collaboration mutation: the clock/lifecycle
    # state and only the changed record documents commit as one ordered command.
    # It avoids serialising a full state write and a full record write for every
    # analyst action.
    syncScope: Literal["checkpoint", "state", "records", "state_records", "cards"] = "checkpoint"
    deletedRecordIds: list[str] = Field(default_factory=list)
    deletedCardIds: list[str] = Field(default_factory=list)
    clearedRecordPlayerIds: list[str] = Field(default_factory=list)


class InputSetupWriteRequest(BaseModel):
    """Durable lobby configuration. It deliberately cannot mutate live Draft state."""

    model_config = ConfigDict(extra="forbid")
    payload: MatchInputPayload
    clientState: dict[str, Any]
    deletedSubIds: list[str] = Field(default_factory=list)
    syncScope: Literal["setup", "substitutions"] = "setup"


class DraftResponse(BaseModel):
    status: str
    gmId: str
    side: Side
    payload: MatchInputPayload
    clientState: dict[str, Any]
    sharedState: dict[str, Any] = Field(default_factory=dict)
    revision: int = 0
    updatedAt: str | None = None
    inputSetup: dict[str, Any] | None = None
    # Browsers poll only while someone else shares this Draft. A solo primary
    # learns about a newly joined analyst from its own save responses.
    participantCount: int = 0


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
    role: Literal["primary", "assistant", "manager"]
    displayName: str | None = Field(default=None, max_length=100)


class RecorderProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=100)


class InputPreviewRequest(BaseModel):
    """Ephemeral input-screen calculation. Nothing is persisted by this route."""

    model_config = ConfigDict(extra="forbid")
    records: list[InputRecord]
    half: Half | None = None


router = APIRouter(tags=["match-input"])


class DraftSocketHub:
    """Best-effort live fan-out. Firestore remains the durable Draft authority."""

    def __init__(self) -> None:
        self._lock = Lock()
        # A WebSocket may not be written by multiple coroutines at once. Event
        # fan-out used to spawn concurrent sends for records/cards/setup, so a
        # busy pair of analysts could lose an ACK although Firestore had saved
        # the mutation. Keep one send lock per browser connection.
        self._clients: dict[tuple[str, Side], dict[int, tuple[WebSocket, asyncio.AbstractEventLoop, asyncio.Lock]]] = defaultdict(dict)

    async def connect(self, gm_id: str, side: Side, socket: WebSocket) -> None:
        with self._lock:
            self._clients[(gm_id, side)][id(socket)] = (socket, asyncio.get_running_loop(), asyncio.Lock())

    def disconnect(self, gm_id: str, side: Side, socket: WebSocket) -> None:
        with self._lock:
            clients = self._clients.get((gm_id, side))
            if not clients:
                return
            clients.pop(id(socket), None)
            if not clients:
                self._clients.pop((gm_id, side), None)

    async def send(self, gm_id: str, side: Side, socket: WebSocket, message: dict[str, Any]) -> bool:
        with self._lock:
            client = self._clients.get((gm_id, side), {}).get(id(socket))
        if not client:
            return False
        _, _, send_lock = client
        try:
            async with send_lock:
                await socket.send_json(message)
            return True
        except Exception:
            self.disconnect(gm_id, side, socket)
            return False

    def publish(self, gm_id: str, side: Side, message: dict[str, Any], *, exclude: WebSocket | None = None) -> None:
        with self._lock:
            clients = list(self._clients.get((gm_id, side), {}).values())
        for socket, loop, send_lock in clients:
            if exclude is socket:
                continue
            loop.call_soon_threadsafe(asyncio.create_task, self._send(gm_id, side, socket, send_lock, message))

    async def _send(self, gm_id: str, side: Side, socket: WebSocket, send_lock: asyncio.Lock, message: dict[str, Any]) -> None:
        try:
            async with send_lock:
                await socket.send_json(message)
        except Exception:
            self.disconnect(gm_id, side, socket)


draft_sockets = DraftSocketHub()


class DraftMutationLocks:
    """Serialise durable mutations per team Draft, never across matches."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[tuple[str, Side], Lock] = {}

    @contextmanager
    def hold(self, gm_id: str, side: Side):
        key = (gm_id, side)
        with self._guard:
            lock = self._locks.setdefault(key, Lock())
        with lock:
            yield


draft_mutations = DraftMutationLocks()


async def _socket_user(socket: WebSocket) -> CurrentUser:
    hello = json.loads(await asyncio.wait_for(socket.receive_text(), timeout=5))
    if not isinstance(hello, dict) or hello.get("type") != "authenticate":
        raise ValueError("authentication message required")
    settings = get_settings()
    origin = socket.headers.get("origin")
    uid = str(hello.get("uid") or "").strip()
    if settings.app_env == "local" and origin in settings.cors_origins:
        return CurrentUser(uid=uid or "local-did-input", claims={"auth": "local_frontend"})
    token = str(hello.get("token") or "").strip()
    decoded = auth.verify_id_token(token, app=ensure_firebase_app(settings))
    verified_uid = decoded.get("uid")
    if not isinstance(verified_uid, str) or not verified_uid:
        raise ValueError("authentication required")
    return CurrentUser(uid=verified_uid, claims=dict(decoded))


@router.websocket("/match-input/drafts/{gm_id}/{side}/live")
async def live_draft_socket(websocket: WebSocket, gm_id: str, side: Side) -> None:
    await websocket.accept()
    try:
        user = await _socket_user(websocket)
        JpdDidData().get_match(gm_id)
        await draft_sockets.connect(gm_id, side, websocket)
        await websocket.send_json({"type": "ready", "gmId": gm_id, "side": side})
        while True:
            message = json.loads(await websocket.receive_text())
            if isinstance(message, dict) and message.get("type") == "heartbeat":
                await draft_sockets.send(gm_id, side, websocket, {"type": "pong"})
                continue
            if not isinstance(message, dict) or message.get("type") != "command":
                continue
            command_id = str(message.get("commandId") or "")
            try:
                if message.get("command") != "mutation":
                    await draft_sockets.send(gm_id, side, websocket, {"type": "error", "commandId": command_id, "message": "unknown command"})
                    continue
                mutation = message.get("mutation")
                if mutation == "draft":
                    request = DraftWriteRequest.model_validate(message.get("request"))
                    response = await asyncio.to_thread(_save_draft_mutation, gm_id, side, request, user)
                    if request.syncScope == "state":
                        event = {
                            "type": "state", "gmId": gm_id, "side": side,
                            "sharedState": response.sharedState, "revision": response.revision,
                        }
                    elif request.syncScope == "cards":
                        event = {
                            "type": "cards", "gmId": gm_id, "side": side,
                            "cards": response.payload.cards, "revision": response.revision,
                        }
                    else:
                        event = {"type": "draft", "response": response.model_dump(mode="json")}
                    # The writer must receive its ACK directly. Other clients
                    # receive a separate fan-out message. Waiting for a shared
                    # background broadcast made successful card/sub/ACT saves
                    # appear to fail whenever another send was in progress.
                    await draft_sockets.send(gm_id, side, websocket, {"type": "mutation", "event": event, "commandId": command_id})
                    draft_sockets.publish(gm_id, side, {"type": "mutation", "event": event}, exclude=websocket)
                elif mutation == "setup":
                    request = InputSetupWriteRequest.model_validate(message.get("request"))
                    setup = await asyncio.to_thread(_save_setup_mutation, gm_id, side, request, user)
                    event = {"type": "setup", "gmId": gm_id, "side": side, "setup": setup}
                    await draft_sockets.send(gm_id, side, websocket, {"type": "mutation", "event": event, "commandId": command_id})
                    draft_sockets.publish(gm_id, side, {"type": "mutation", "event": event}, exclude=websocket)
                else:
                    await draft_sockets.send(gm_id, side, websocket, {"type": "error", "commandId": command_id, "message": "unknown mutation"})
            except Exception as exc:
                await draft_sockets.send(gm_id, side, websocket, {"type": "error", "commandId": command_id, "message": str(exc)})
    except WebSocketDisconnect as exc:
        logger.info("Draft live socket disconnected: %s/%s code=%s", gm_id, side, exc.code)
    except (asyncio.TimeoutError, ValueError, RuntimeError) as exc:
        # Refresh, route changes and a browser retry can close the transport
        # between accept/authentication/receive. That is a normal disconnect,
        # not an application error and must never trigger a second close.
        logger.warning("Draft live socket stopped: %s/%s (%s)", gm_id, side, exc)
    except Exception:
        # Do not swallow an unexpected transport failure. Its traceback is the
        # only reliable way to distinguish a client close from a server error.
        logger.exception("Draft live socket crashed: %s/%s", gm_id, side)
    finally:
        draft_sockets.disconnect(gm_id, side, websocket)


def _publish_draft_response(response: DraftResponse, command_id: str | None = None) -> None:
    message: dict[str, Any] = {"type": "draft", "response": response.model_dump(mode="json")}
    if command_id:
        message["commandId"] = command_id
    draft_sockets.publish(response.gmId, response.side, message)


def _publish_cards_response(response: DraftResponse) -> None:
    """Publish only card state after a card-only HTTP mutation.

    Card mutations intentionally omit the records subcollection from their
    response to keep the write small. Publishing that response as a complete
    Draft makes subscribers interpret the omitted records list as an empty
    record table. Cards have their own merge authority, so emit only that
    authority's event.
    """
    draft_sockets.publish(response.gmId, response.side, {
        "type": "cards", "gmId": response.gmId, "side": response.side,
        "cards": response.payload.cards, "revision": response.revision,
    })


def _publish_state_response(response: DraftResponse) -> None:
    """Publish state-only mutations without replacing the records stream."""
    draft_sockets.publish(response.gmId, response.side, {
        "type": "state", "gmId": response.gmId, "side": response.side,
        "sharedState": response.sharedState, "revision": response.revision,
    })


def _display_name(document: dict, fallback: str) -> str:
    return str(document.get("nameKr") or document.get("name") or document.get("nameShort") or fallback)


def _draft_response(document: dict[str, Any], input_setup: dict[str, Any] | None = None) -> DraftResponse:
    updated_at = document.get("updatedAt")
    return DraftResponse(
        status="ok", gmId=document["gmId"], side=document["side"],
        payload=MatchInputPayload.model_validate(document["payload"]),
        clientState=document.get("clientState") or {},
        sharedState=document.get("sharedState") or {},
        revision=int(document.get("revision") or 0),
        updatedAt=updated_at.isoformat() if updated_at else None,
        inputSetup=input_setup,
        participantCount=len(participants) if isinstance(participants := document.get("participants"), dict) else 0,
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


def _draft_recorders(
    draft: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Merge Draft participants into the RAW recorders map (main first).

    A correction Draft restored from RAW starts without participants, so the
    roster already in RAW is kept and only newly joined analysts are added.
    Everyone who ever entered stays on the roster, managers included.
    The original main keeps its rank; a later primary is recorded as sub.
    Managers (advanced analysts entering a full or finished session) are kept
    as their own rank because they can write records too.
    """
    recorders = {
        uid: dict(entry) for uid, entry in (existing or {}).items()
        if isinstance(entry, dict) and entry.get("rank") in {"main", "sub", "manager"}
    }
    has_main = any(entry["rank"] == "main" for entry in recorders.values())
    participants = draft.get("participants") if isinstance(draft.get("participants"), dict) else {}
    for uid, values in participants.items():
        if uid in recorders or not isinstance(values, dict) or values.get("role") not in {"primary", "assistant", "manager"}:
            continue
        if values["role"] == "manager":
            rank = "manager"
        else:
            rank = "main" if values["role"] == "primary" and not has_main else "sub"
        has_main = has_main or rank == "main"
        recorders[uid] = {"rank": rank, "joinedAt": values.get("joinedAt") or utc_now(), "name": values.get("name")}
    recorder_ids = sorted(recorders, key=lambda uid: recorders[uid]["rank"] != "main")
    return recorders, recorder_ids


_RANK_ROLES = {"main": "primary", "sub": "assistant", "manager": "manager"}


def _promote(
    data: JpdDidData,
    payload: MatchInputPayload,
    draft: dict[str, Any],
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
    existing_recorders = data.get_recording_heads_many([payload.gmId])[payload.gmId][payload.side].get("recorders")
    recording["recorders"], recording["recorderIds"] = _draft_recorders(draft, existing_recorders)
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


@router.get("/match-input/matches", include_in_schema=False)
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
    heads_by_match = data.get_recording_heads_many(gm_ids)
    collaboration_by_match = data.get_input_draft_participants_many(gm_ids)

    matches = []
    for gm_id, match in month_matches:
        home = teams.get(match.homeTeamId, {})
        away = teams.get(match.awayTeamId, {})
        stadium = stadiums.get(match.stadiumId, {})
        heads = heads_by_match.get(gm_id, {"H": {}, "A": {}})
        recording_status = {side: heads[side].get("status") for side in ("H", "A")}
        collaboration = collaboration_by_match.get(gm_id, {"H": {}, "A": {}})
        # A finalized Draft is deleted after promotion; RAW keeps the final
        # analyst roster so the schedule can still show who worked it. A later
        # correction Draft only adds people (e.g. a manager) to that roster.
        for side in ("H", "A"):
            # 전반전 시작 전 Draft의 참여자는 확정된 명단이 아니다(예전 로비 입장 기록 등).
            if heads[side].get("status") != "final" and (collaboration[side].get("status") or "ready") == "ready":
                collaboration[side] = {**collaboration[side], "participants": []}
            recorders = heads[side].get("recorders") or {}
            if not recorders:
                continue
            roster = [
                {"uid": uid, "role": _RANK_ROLES.get(entry.get("rank"), "assistant"), "name": entry.get("name")}
                for uid, entry in recorders.items() if isinstance(entry, dict)
            ]
            known = {item["uid"] for item in roster}
            roster += [item for item in collaboration[side].get("participants") or [] if item.get("uid") not in known]
            collaboration[side] = {**collaboration[side], "participants": roster}

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


@router.get("/match-input/analyst-dashboard", include_in_schema=False)
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


@router.get("/match-input/matches/{gm_id}/bootstrap", include_in_schema=False)
def read_match_input_bootstrap(
    gm_id: str,
    side: Side = Query(...),
    _: RequiredUser = None,
) -> dict:
    """Load squads, selected session and all dashboard KPI views atomically for the lobby."""
    return {"status": "ok", **JpdDidData().build_input_bootstrap(gm_id, side)}


@router.get("/match-input/matches/{gm_id}/dashboard-kpis", include_in_schema=False)
def read_match_dashboard_kpis(
    gm_id: str,
    half: Literal["all", "H1", "H2"] = Query(default="all"),
    _: RequiredUser = None,
) -> dict:
    data = JpdDidData()
    data.get_match(gm_id)
    return {
        "status": "ok", "gmId": gm_id, "half": half,
        "kpis": data.read_match_dashboard_kpis(gm_id, half=half),
        # The lobby already polls this lightweight response for live KPI. Keep
        # the durable setup in the same response so it never falls back to a
        # transient Draft just to paint formation or player placement.
        "inputSetup": data.get_input_setup(gm_id),
    }


@router.get("/match-input/matches/{gm_id}/squads", include_in_schema=False)
def read_match_squads(gm_id: str, _: RequiredUser = None) -> dict:
    data = JpdDidData()
    match = data.get_match(gm_id)
    result, cached = data.get_or_create_input_squads(gm_id, match)
    return {
        "status": "ok", "gmId": gm_id, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
        "H": result["H"], "A": result["A"],
        "cached": cached,
        "inputStatus": data.get_recording_input_states(gm_id),
        "inputSetup": data.get_input_setup(gm_id),
        "matchSnapshot": {
            "leagueId": match.leagueId, "seasonId": match.seasonId, "round": match.round,
            "date": match.date, "kickoffTime": match.kickoffTime, "stadiumId": match.stadiumId,
            "matchType": match.matchType, "homeTeamId": match.homeTeamId, "awayTeamId": match.awayTeamId,
        },
    }


@router.get("/match-input/drafts/{gm_id}/{side}", response_model=DraftResponse | DraftMissingResponse, include_in_schema=False)
def get_draft(gm_id: str, side: Side, _: RequiredUser = None) -> DraftResponse | DraftMissingResponse:
    try:
        data = JpdDidData()
        document = data.get_input_draft(gm_id, side)
        # A primary can be registered before its first lifecycle snapshot. That
        # is a normal empty collaborative Draft, not a malformed API response.
        if not document.get("payload"):
            return DraftMissingResponse(status="missing", gmId=gm_id, side=side)
        return _draft_response(document, data.get_input_setup(gm_id).get(side))
    except NotFoundError:
        # A side without a draft is the normal first-entry condition.
        return DraftMissingResponse(status="missing", gmId=gm_id, side=side)


@router.get("/match-input/matches/{gm_id}/recordings/{side}/input-state", response_model=DraftResponse, include_in_schema=False)
def get_final_raw_input_state(gm_id: str, side: Side, _: RequiredUser = None) -> DraftResponse:
    """Read a final RAW recording as an input-screen snapshot without creating a Draft."""
    return _draft_response(JpdDidData().read_input_state_from_raw(gm_id, side))


@router.get("/match-input/approvals", include_in_schema=False)
def list_approvals(_: RequiredUser = None) -> dict:
    data = JpdDidData()
    drafts = data.list_input_drafts(recorder_level="basic", status="final")
    gm_ids = {str(item.get("gmId") or "") for item in drafts}
    matches = data.get_documents_by_ids("matches", gm_ids)

    match_by_draft: dict[str, dict] = {}
    team_ids: set[str] = set()
    for item in drafts:
        gm_id = str(item.get("gmId") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        snapshot = payload.get("matchSnapshot") if isinstance(payload.get("matchSnapshot"), dict) else {}
        match = matches.get(gm_id) or snapshot
        match_by_draft[gm_id] = match
        team_ids.update(str(match.get(key) or "") for key in ("homeTeamId", "awayTeamId"))

    teams = data.get_documents_by_ids("teams", team_ids)
    result = []
    for item in drafts:
        gm_id = str(item.get("gmId") or "")
        side = item.get("side")
        match = match_by_draft.get(gm_id, {})
        team_id = str((match.get("awayTeamId") if side == "A" else match.get("homeTeamId")) or "")
        team = teams.get(team_id, {})
        team_name = str(team.get("nameKr") or team.get("name") or team.get("nameShort") or team_id or "팀 정보 없음")
        result.append({
            "gmId": gm_id,
            "side": side,
            "teamId": team_id,
            "teamName": team_name,
            "updatedAt": item.get("updatedAt"),
            "payload": item["payload"],
        })
    return {"status": "ok", "drafts": result}


@router.post("/match-input/preview", include_in_schema=False)
def calculate_input_preview(request: InputPreviewRequest, _: RequiredUser = None) -> dict:
    """Calculate preview KPI and per-record eligibility without Firestore I/O."""
    source = [
        KpiRecord(
            id=item.id or f"record-{index}", half=item.half, seconds=item.halfSeconds, seq=item.seq,
            act=item.act, res=item.res, area=item.area, player_id=item.playerId,
            shoot_pos_x=item.shootPosX, shoot_pos_y=item.shootPosY,
            shoot_dsp_range=item.shootDspRange, is_shot=item.isShot,
        )
        for index, item in enumerate(request.records)
        if request.half is None or item.half == request.half
    ]
    result = calculate_kpis(source)
    return {
        "status": "ok",
        "kpis": result.team_kpi,
        "flags": {
            record_id: {
                "isTap": flag.is_tap, "isDap": flag.is_dap, "isDapSuccess": flag.is_dap_success,
                "isShot": flag.is_shot, "isGoal": flag.is_goal,
            }
            for record_id, flag in result.flags.items()
        },
        "paths": [
            {"id": path.id, "recordIds": list(path.record_ids), "type": path.path_type, "ttp": path.ttp}
            for path in result.paths
        ],
    }


@router.get("/match-input/recorder-profile", include_in_schema=False)
def get_recorder_profile(user: RequiredUser = None) -> dict:
    profile = JpdDidData().get_recorder_profile(user.uid)
    return {"status": "ok", "name": profile.get("name") or user.uid, "level": profile.get("level", "advanced")}


@router.post("/match-input/recorder-profile", include_in_schema=False)
def ensure_recorder_profile(request: RecorderProfileRequest, user: RequiredUser = None) -> dict:
    profile = JpdDidData().ensure_recorder_profile(user.uid, name=request.name)
    return {"status": "ok", "name": profile.get("name") or user.uid, "level": profile.get("level", "advanced")}


@router.post("/match-input/drafts/{gm_id}/{side}/participants", include_in_schema=False)
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
        "role": (document.get("participants", {}).get(user.uid) or {}).get("role"),
        # Lifecycle (clock, half end, final update) belongs to the primary. A
        # manager in a correction Draft has no primary above it, so it owns it.
        "control": not document.get("primaryUid") or document.get("primaryUid") == user.uid,
    }


@router.post("/match-input/drafts/{gm_id}/{side}/restore-raw", response_model=DraftResponse, include_in_schema=False)
def restore_raw_to_draft(gm_id: str, side: Side, user: RequiredUser = None) -> DraftResponse:
    response = _draft_response(JpdDidData().restore_input_draft_from_raw(gm_id, side, user_id=user.uid))
    _publish_draft_response(response)
    return response


@router.post("/match-input/drafts/{gm_id}/{side}/promote-h1", response_model=PromotionResponse, include_in_schema=False)
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


@router.post("/match-input/drafts/{gm_id}/{side}/finalize", response_model=PromotionResponse, include_in_schema=False)
def finalize_advanced(gm_id: str, side: Side, user: RequiredUser = None) -> PromotionResponse:
    data = JpdDidData()
    draft = data.get_input_draft(gm_id, side)
    if draft.get("primaryUid") and draft["primaryUid"] != user.uid:
        raise BackendError("Only the primary analyst can finalize RAW", status_code=403, code="primary_required")
    payload = MatchInputPayload.model_validate(draft["payload"])
    if payload.recorderLevel != "advanced":
        raise BackendError("Basic input requires administrator approval", status_code=409, code="basic_approval_required")
    # RAW is the only durable final source. A later edit explicitly recreates a
    # short-lived Draft from RAW, so stale collaboration records cannot reopen.
    return _promote(data, payload, draft, user.uid, status="final", halves={"H1", "H2"}, delete_draft=True)


@router.post("/match-input/approvals/{gm_id}/{side}/promote", response_model=PromotionResponse, include_in_schema=False)
def approve_basic(gm_id: str, side: Side, user: RequiredUser = None) -> PromotionResponse:
    data = JpdDidData()
    draft = data.get_input_draft(gm_id, side)
    payload = MatchInputPayload.model_validate(draft["payload"])
    if payload.recorderLevel != "basic" or payload.status != "final":
        raise BackendError("Only submitted basic drafts can be approved", status_code=409, code="draft_not_awaiting_approval")
    return _promote(data, payload, draft, user.uid, status="final", halves={"H1", "H2"}, delete_draft=True)


def _save_draft_mutation(gm_id: str, side: Side, request: DraftWriteRequest, user: CurrentUser) -> DraftResponse:
    if request.payload.gmId != gm_id or request.payload.side != side:
        raise BackendError("Draft path and payload must identify the same match side", status_code=422, code="draft_target_mismatch")
    # Every entry point (REST and socket) takes this same lock.  Firestore
    # document revisions therefore cannot race when the two analysts submit at
    # almost the same instant.
    with draft_mutations.hold(gm_id, side):
        data = JpdDidData()
        match = data.get_match(gm_id)
        snapshot = request.payload.matchSnapshot
        if snapshot and (snapshot.homeTeamId != match.homeTeamId or snapshot.awayTeamId != match.awayTeamId):
            raise BackendError("Draft match snapshot does not match this fixture", status_code=422, code="draft_match_mismatch")
        document = data.save_input_draft(
            gm_id, side, payload=request.payload.model_dump(mode="python"), client_state=request.clientState,
            user_id=user.uid, sync_scope=request.syncScope, deleted_record_ids=request.deletedRecordIds,
            deleted_card_ids=request.deletedCardIds, cleared_record_player_ids=request.clearedRecordPlayerIds,
            include_events=request.syncScope not in {"state", "cards"},
        )
        input_setup = None if request.syncScope in {"state", "cards"} else data.get_input_setup(gm_id).get(side)
        return _draft_response(document, input_setup)


def _save_setup_mutation(gm_id: str, side: Side, request: InputSetupWriteRequest, user: CurrentUser) -> dict[str, Any] | None:
    if request.payload.gmId != gm_id or request.payload.side != side:
        raise BackendError("Draft path and payload must identify the same match side", status_code=422, code="draft_target_mismatch")
    with draft_mutations.hold(gm_id, side):
        data = JpdDidData()
        draft = data.get_input_draft(gm_id, side)
        return data.save_input_setup(
            gm_id,
            side,
            payload=request.payload.model_dump(mode="python"),
            client_state=request.clientState,
            deleted_sub_ids=request.deletedSubIds,
            merge_substitutions=request.syncScope == "substitutions",
        )


@router.put("/match-input/drafts/{gm_id}/{side}", response_model=DraftResponse, include_in_schema=False)
def save_draft(gm_id: str, side: Side, request: DraftWriteRequest, user: RequiredUser = None) -> DraftResponse:
    response = _save_draft_mutation(gm_id, side, request, user)
    # A card-only mutation response does not include the Draft's record
    # subcollection. Never broadcast it as a complete Draft or connected
    # analysts will replace their ACT list with that intentionally empty list.
    if request.syncScope == "cards":
        _publish_cards_response(response)
    elif request.syncScope == "state":
        _publish_state_response(response)
    else:
        _publish_draft_response(response)
    return response


@router.put("/match-input/drafts/{gm_id}/{side}/setup", include_in_schema=False)
def save_draft_setup(gm_id: str, side: Side, request: InputSetupWriteRequest, user: RequiredUser = None) -> dict:
    """Write formation/lineup only after a real configuration action.

    This is intentionally hidden from Swagger: it is a browser collaboration
    transport, not an operator action. Records, timer and lifecycle are never
    touched here.
    """
    setup = _save_setup_mutation(gm_id, side, request, user)
    if setup:
        draft_sockets.publish(gm_id, side, {"type": "setup", "gmId": gm_id, "side": side, "setup": setup})
    return {"status": "ok", "gmId": gm_id, "side": side, "inputSetup": setup}
