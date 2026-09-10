from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

PathType = Literal["UPP", "UTP", "DTP", "STP"]


@dataclass(frozen=True, slots=True)
class KpiRecord:
    """The calculation-safe projection of a source RecordDoc."""

    id: str
    half: str
    seconds: int
    seq: int
    act: str
    res: str
    area: int
    player_id: str | None = None
    shoot_pos_x: float | None = None
    shoot_pos_y: float | None = None
    shoot_dsp_range: bool | None = None
    is_shot: bool | None = None
    created_by: str = ""


@dataclass(frozen=True, slots=True)
class RecordFlags:
    path_id: str
    is_tap: bool = False
    is_tap_success: bool = False
    is_dap: bool = False
    is_dap_success: bool = False
    is_shot: bool = False
    is_shot_success: bool = False
    is_goal: bool = False
    is_ast: bool = False
    is_dts: bool = False
    is_dta: bool = False
    is_dtm: bool = False
    is_dtb: bool = False
    is_gtm: bool = False
    is_gtb: bool = False


@dataclass(frozen=True, slots=True)
class AttackPath:
    id: str
    record_ids: tuple[str, ...]
    path_type: PathType
    dsp: bool
    ttp: bool
    result: str


@dataclass(frozen=True, slots=True)
class BapEvent:
    record_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class KpiCalculation:
    team_kpi: dict[str, int]
    player_kpis: dict[str, dict[str, int]]
    paths: tuple[AttackPath, ...]
    flags: dict[str, RecordFlags]
    bap_events: tuple[BapEvent, ...]


TEAM_KPI_FIELDS = (
    "TAP", "DAP", "TTP", "DTP", "BAP", "DTB", "DTM", "DTA", "DTS",
    "SHOT", "ASR", "SSR", "GOAL", "OG",
)
PLAYER_KPI_FIELDS = (
    "TAP", "DAP", "UTP", "DTP", "TTP", "SHOT", "AST", "GOAL", "DTB",
    "DTM", "DTA", "DTS", "GTB", "GTM", "ASR", "SSR",
)


def _is_shot_act(act: str) -> bool:
    return act in {"S", "H", "R"}


def _is_goal(result: str) -> bool:
    return result == "GOAL"


def _ordered(records: list[KpiRecord]) -> list[KpiRecord]:
    return sorted(
        records,
        key=lambda item: (item.seconds, item.seq, item.created_by),
    )


def _batch_result(record: KpiRecord, next_record: KpiRecord | None) -> str:
    """Apply the one-record lookahead used by the verified PHP batch job."""
    if not record.act:
        return record.res
    if _is_shot_act(record.act):
        return "O" if next_record and not next_record.act else record.res
    return "O"


def _split_chains(records: list[KpiRecord]) -> list[list[KpiRecord]]:
    """Reproduce dplay_game_path_reset_calc's server-batch path boundaries."""
    chains: list[list[KpiRecord]] = []
    current: list[KpiRecord] = []
    making = False
    ordered = _ordered(records)

    for index, record in enumerate(ordered):
        last_record = ordered[index - 1] if index else None
        next_record = ordered[index + 1] if index + 1 < len(ordered) else None

        # PHP recurses with this same record after ending the preceding path.
        if making and last_record and record.seconds - last_record.seconds > 4:
            chains.append(current)
            current = []
            making = False

        if not making:
            making = True
        current.append(record)

        if (result := _batch_result(record, next_record)) and result != "O":
            chains.append(current)
            current = []
            making = False
    if current:
        chains.append(current)
    return chains


def _blank_flags(path_id: str) -> dict[str, bool | str]:
    return {
        "path_id": path_id, "is_tap": False, "is_tap_success": False,
        "is_dap": False, "is_dap_success": False, "is_shot": False,
        "is_shot_success": False, "is_goal": False, "is_ast": False,
        "is_dts": False, "is_dta": False, "is_dtm": False, "is_dtb": False,
        "is_gtm": False, "is_gtb": False,
    }


def _classify_chain(chain: list[KpiRecord], path_id: str) -> tuple[AttackPath, dict[str, RecordFlags]]:
    mutable_flags = {record.id: _blank_flags(path_id) for record in chain}
    path_type: PathType = "UPP"
    is_goal_chain = False
    dsp = False
    act_count = 0
    utp_begin_id: str | None = None

    for record in chain:
        own_goal = _is_goal(record.res) and record.player_id == "OWN"
        act = "" if own_goal else record.act
        if act:
            act_count += 1
        if record.is_shot or (act and _is_shot_act(act)):
            path_type = "DTP"
            is_goal_chain = is_goal_chain or _is_goal(record.res)
            has_position = bool((record.shoot_pos_x or 0) > 0 or (record.shoot_pos_y or 0) > 0)
            if record.res in {"GOAL", "R", "L", "H", "GB"} or (record.res == "B" and has_position) or record.shoot_dsp_range:
                dsp = True
        elif record.area < 7 and path_type == "UPP":
            path_type = "UTP"
            utp_begin_id = record.id

    if path_type == "DTP" and act_count < 2:
        path_type = "STP"
    if path_type == "UTP" and act_count < 2 and chain[0].act not in {"K", "F"}:
        path_type = "UPP"

    if path_type in {"DTP", "STP", "UTP"}:
        shooter: str | None = None
        assister: str | None = None
        maker: str | None = None
        builder: str | None = None
        utp_index = -1
        for record in reversed(chain):
            flags = mutable_flags[record.id]
            own_goal = _is_goal(record.res) and record.player_id == "OWN"
            act = "" if own_goal else record.act
            if path_type in {"DTP", "STP"}:
                flags["is_dap"] = bool(act)
                if shooter is None and act and _is_shot_act(act):
                    if record.player_id:
                        shooter = record.player_id
                        flags["is_dts"] = True
                elif assister is None and shooter and shooter != record.player_id:
                    if record.player_id:
                        assister = record.player_id
                        flags["is_dta"] = True
                        flags["is_ast"] = is_goal_chain
                elif maker is None and assister and assister != record.player_id:
                    if record.player_id:
                        maker = record.player_id
                        flags["is_dtm"] = True
                        flags["is_gtm"] = is_goal_chain
                elif maker and ((builder is None and maker != record.player_id) or (builder and builder != record.player_id)):
                    builder = record.player_id or ""
                    flags["is_dtb"] = True
                    flags["is_gtb"] = is_goal_chain
            else:
                flags["is_dap"] = bool(act) if utp_index < 0 else bool(act) and utp_index < 2
                if record.id == utp_begin_id:
                    utp_index = 0
                elif utp_index >= 0:
                    utp_index += 1
            flags["is_dap_success"] = bool(flags["is_dap"]) and record.res in {"O", "GOAL"}

    result_flags: dict[str, RecordFlags] = {}
    for record in chain:
        flags = mutable_flags[record.id]
        has_position = bool((record.shoot_pos_x or 0) > 0 or (record.shoot_pos_y or 0) > 0)
        is_shot = _is_shot_act(record.act) or bool(record.is_shot)
        flags["is_tap"] = bool(record.act)
        flags["is_tap_success"] = bool(record.act) and record.res in {"O", "GOAL"}
        flags["is_shot"] = is_shot
        flags["is_shot_success"] = is_shot and (
            record.res in {"GOAL", "R", "L", "H", "GB"}
            or (record.res == "B" and has_position)
            or bool(record.shoot_dsp_range)
        )
        flags["is_goal"] = _is_goal(record.res)
        result_flags[record.id] = RecordFlags(**flags)

    last = chain[-1]
    return AttackPath(path_id, tuple(record.id for record in chain), path_type, dsp, path_type in {"UTP", "DTP"}, last.res), result_flags


def compute_attack_paths(
    records: list[KpiRecord], *, path_prefix: str = ""
) -> tuple[list[AttackPath], dict[str, RecordFlags]]:
    paths: list[AttackPath] = []
    flags: dict[str, RecordFlags] = {}
    for index, chain in enumerate(_split_chains(records), start=1):
        path, chain_flags = _classify_chain(chain, f"{path_prefix}path_{index}")
        paths.append(path)
        flags.update(chain_flags)
    return paths, flags


def compute_bap(records: list[KpiRecord]) -> list[BapEvent]:
    """Reproduce the verified legacy PHP server-batch BAP calculation."""
    events: list[BapEvent] = []
    ready = False
    deferred = False
    ordered = _ordered(records)
    for index, current in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        next_record = ordered[index + 1] if index + 1 < len(ordered) else None
        reason: str | None = None
        previous_area = previous.area if previous else 0
        act = current.act
        result = _batch_result(current, next_record)

        if ready:
            if previous and current.seconds - previous.seconds > 4:
                reason = "4 SECONDS"
            elif result and result != "O":
                reason = "DONE"
            elif current.area < 7:
                reason = "ATTACK"
        elif previous is None and current.area < 10:
            ready = True
        elif current.area > 9 and _is_shot_act(act):
            if result and result != "O":
                reason = "SHOOT"
            else:
                ready = True
        elif previous_area > 9 and current.area < 10:
            if not act and result == "X":
                deferred = True
            else:
                if not act and result == "B":
                    reason = "CROSS B"
                elif current.area < 7:
                    reason = "CROSS ATTACK"
                else:
                    ready = True
        elif deferred and current.area < 10:
            if current.area < 7:
                reason = "KEEP"
            else:
                ready = True
        if reason:
            events.append(BapEvent(current.id, reason))
            ready = False
            deferred = False
        elif ready:
            deferred = False
    return events


def _empty(fields: tuple[str, ...]) -> dict[str, int]:
    return {field: 0 for field in fields}


def _percentage(numerator: int, denominator: int) -> int:
    return round(numerator / denominator * 100) if denominator else 0


def calculate_kpis(records: list[KpiRecord]) -> KpiCalculation:
    paths: list[AttackPath] = []
    flags: dict[str, RecordFlags] = {}
    bap_events: list[BapEvent] = []
    for half in ("H1", "H2", "H3", "H4"):
        half_records = [record for record in records if record.half == half]
        if not half_records:
            continue
        half_paths, half_flags = compute_attack_paths(half_records, path_prefix=f"{half.lower()}_")
        paths.extend(half_paths)
        flags.update(half_flags)
        bap_events.extend(compute_bap(half_records))
    team = _empty(TEAM_KPI_FIELDS)
    players: defaultdict[str, dict[str, int]] = defaultdict(lambda: _empty(PLAYER_KPI_FIELDS))
    path_type_by_id = {path.id: path.path_type for path in paths}
    dap_success = 0
    player_dap_success: Counter[str] = Counter()

    for record in records:
        flag = flags.get(record.id)
        if not flag:
            continue
        team["TAP"] += int(flag.is_tap)
        team["DAP"] += int(flag.is_dap)
        dap_success += int(flag.is_dap_success)
        team["SHOT"] += int(flag.is_shot)
        team["GOAL"] += int(flag.is_goal)
        team["OG"] += int(flag.is_goal and record.player_id == "OWN")
        for field, attr in (("DTB", "is_dtb"), ("DTM", "is_dtm"), ("DTA", "is_dta"), ("DTS", "is_dts")):
            team[field] += int(getattr(flag, attr))
        if not record.player_id or record.player_id == "OWN":
            continue
        player = players[record.player_id]
        player["TAP"] += int(flag.is_tap)
        player["DAP"] += int(flag.is_dap)
        player_dap_success[record.player_id] += int(flag.is_dap_success)
        player["SHOT"] += int(flag.is_shot)
        player["AST"] += int(flag.is_ast)
        player["GOAL"] += int(flag.is_goal)
        for field, attr in (("DTB", "is_dtb"), ("DTM", "is_dtm"), ("DTA", "is_dta"), ("DTS", "is_dts"), ("GTB", "is_gtb"), ("GTM", "is_gtm")):
            player[field] += int(getattr(flag, attr))
        path_type = path_type_by_id[flag.path_id]
        if flag.is_dap:
            player["UTP"] += int(path_type == "UTP")
            player["DTP"] += int(path_type in {"DTP", "STP"})

    team["TTP"] = sum(int(path.ttp) for path in paths)
    team["DTP"] = sum(int(path.path_type in {"DTP", "STP"}) for path in paths)
    team["BAP"] = len(bap_events)
    team["ASR"] = _percentage(dap_success, team["DAP"])
    team["SSR"] = _percentage(team["GOAL"] - team["OG"], team["SHOT"])
    for player_id, player in players.items():
        player["TTP"] = player["UTP"] + player["DTP"]
        player["ASR"] = _percentage(player_dap_success[player_id], player["DAP"])
        player["SSR"] = _percentage(player["GOAL"], player["SHOT"])
    return KpiCalculation(team, dict(players), tuple(paths), flags, tuple(bap_events))


def calculate_team_kpi_5min(records: list[KpiRecord]) -> list[dict[str, int]]:
    """Return 20 cumulative five-minute team snapshots for the rating API."""
    snapshots: list[dict[str, int]] = []
    for half, offset in (("H1", 0), ("H2", 10)):
        completed_halves = [record for record in records if record.half in ({"H1"} if half == "H1" else {"H1", "H2"})]
        for interval in range(1, 11):
            cutoff = interval * 300
            scoped = [record for record in completed_halves if record.half != half or record.seconds <= cutoff]
            kpi = calculate_kpis(scoped).team_kpi
            snapshots.append({field: kpi[field] for field in ("DAP", "DTP", "SHOT", "SSR", "GOAL")})
    return snapshots
