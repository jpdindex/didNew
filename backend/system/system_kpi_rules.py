from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, ROUND_DOWN
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
    created_at: str = ""
    legacy_path_id: str | None = None
    legacy_path_type: PathType | None = None
    legacy_path_ttp: bool | None = None
    legacy_flags: dict[str, bool] | None = None
    bap_reason: str | None = None


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
        # PHP batch jobs order by gr_seconds then gr_regdt. seq is retained
        # only as a deterministic fallback for imports without a timestamp.
        key=lambda item: (item.seconds, item.created_at, item.seq, item.created_by),
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
    seen_connect = False  # a C/P/K/F appeared before the shot
    linked_shot = False  # a shot followed a C/P/K/F within the same path

    for record in chain:
        own_goal = _is_goal(record.res) and record.player_id == "OWN"
        act = "" if own_goal else record.act
        if act:
            act_count += 1
        if act and _is_shot_act(act):
            path_type = "DTP"
            # Only a shot linked (<= 4s, same path) from a C/P/K/F counts as DTP.
            linked_shot = linked_shot or seen_connect
            is_goal_chain = is_goal_chain or _is_goal(record.res)
            has_position = bool((record.shoot_pos_x or 0) > 0 or (record.shoot_pos_y or 0) > 0)
            if record.res in {"GOAL", "R", "L", "H", "GB"} or (record.res == "B" and has_position) or record.shoot_dsp_range:
                dsp = True
        elif act:
            seen_connect = True
        if not (act and _is_shot_act(act)) and record.area < 7 and path_type == "UPP":
            path_type = "UTP"
            utp_begin_id = record.id

    if path_type == "DTP" and not linked_shot:
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
        own_goal = _is_goal(record.res) and record.player_id == "OWN"
        effective_act = "" if own_goal else record.act
        has_position = bool((record.shoot_pos_x or 0) > 0 or (record.shoot_pos_y or 0) > 0)
        is_shot = _is_shot_act(effective_act)
        # Legacy PHP clears an own-goal action before deriving TAP and SHOT.
        flags["is_tap"] = bool(effective_act)
        flags["is_tap_success"] = bool(effective_act) and record.res in {"O", "GOAL"}
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


def compute_bap(records: list[KpiRecord], *, close_trailing_ready: bool = False) -> list[BapEvent]:
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
    if close_trailing_ready and ready and ordered:
        # Finalized PHP runs call dplay_game_bap_reset(..., $uplast=true).
        events.append(BapEvent(ordered[-1].id, "LAST READY"))
    return events


def _empty(fields: tuple[str, ...]) -> dict[str, int]:
    return {field: 0 for field in fields}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def calculate_kpis(records: list[KpiRecord], *, close_trailing_bap: bool = True) -> KpiCalculation:
    if records and all(record.legacy_flags is not None for record in records):
        return _calculate_legacy_kpis(records)
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
        bap_events.extend(compute_bap(half_records, close_trailing_ready=close_trailing_bap))
    team = _empty(TEAM_KPI_FIELDS)
    players: defaultdict[str, dict[str, int]] = defaultdict(lambda: _empty(PLAYER_KPI_FIELDS))
    player_path_ids: defaultdict[str, set[str]] = defaultdict(set)
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
        player_path_ids[record.player_id].add(flag.path_id)

    team["TTP"] = sum(int(path.ttp) for path in paths)
    team["DTP"] = sum(int(path.path_type == "DTP") for path in paths)
    team["BAP"] = len(bap_events)
    team["ASR"] = _ratio(dap_success, team["DAP"])
    # GOAL/FPX include OWN, but shot success excludes OWN. Both counts come
    # from RAW here; never subtract an imported OG=0 placeholder.
    team["SSR"] = _ratio(team["GOAL"] - team["OG"], team["SHOT"])
    for player_id, player in players.items():
        # Legacy ff_game_player uses distinct gt_id rows for each player, not
        # the number of DAP records the player created inside one attack path.
        path_types = (path_type_by_id[path_id] for path_id in player_path_ids[player_id])
        for path_type in path_types:
            player["UTP"] += int(path_type == "UTP")
            player["DTP"] += int(path_type == "DTP")
        player["TTP"] = player["UTP"] + player["DTP"]
        player["ASR"] = _ratio(player_dap_success[player_id], player["DAP"])
        player["SSR"] = _ratio(player["GOAL"], player["SHOT"])
    return KpiCalculation(team, dict(players), tuple(paths), flags, tuple(bap_events))


def _calculate_legacy_kpis(records: list[KpiRecord]) -> KpiCalculation:
    """Use PHP's persisted per-record flags for SQL-imported RAW exactly."""
    team = _empty(TEAM_KPI_FIELDS)
    players: defaultdict[str, dict[str, int]] = defaultdict(lambda: _empty(PLAYER_KPI_FIELDS))
    flags: dict[str, RecordFlags] = {}
    paths: dict[str, AttackPath] = {}
    player_paths: defaultdict[str, set[str]] = defaultdict(set)
    dap_success = 0
    player_dap_success: Counter[str] = Counter()
    for record in records:
        legacy = record.legacy_flags or {}
        value = lambda name: bool(legacy.get(name, False))
        flag = RecordFlags(record.legacy_path_id or record.id, value("tap"), value("tapSuccess"), value("dap"), value("dapSuccess"), value("shot"), value("shotSuccess"), value("goal"), value("ast"), value("dts"), value("dta"), value("dtm"), value("dtb"), value("gtm"), value("gtb"))
        flags[record.id] = flag
        for field, enabled in (("TAP", flag.is_tap), ("DAP", flag.is_dap), ("SHOT", flag.is_shot), ("GOAL", flag.is_goal), ("DTB", flag.is_dtb), ("DTM", flag.is_dtm), ("DTA", flag.is_dta), ("DTS", flag.is_dts)):
            team[field] += int(enabled)
        team["OG"] += int(flag.is_goal and record.player_id == "OWN")
        dap_success += int(flag.is_dap_success)
        if record.legacy_path_id and record.legacy_path_type:
            paths.setdefault(record.legacy_path_id, AttackPath(record.legacy_path_id, (), record.legacy_path_type, False, bool(record.legacy_path_ttp), ""))
        if not record.player_id or record.player_id == "OWN":
            continue
        player = players[record.player_id]
        for field, enabled in (("TAP", flag.is_tap), ("DAP", flag.is_dap), ("SHOT", flag.is_shot), ("AST", flag.is_ast), ("GOAL", flag.is_goal), ("DTB", flag.is_dtb), ("DTM", flag.is_dtm), ("DTA", flag.is_dta), ("DTS", flag.is_dts), ("GTB", flag.is_gtb), ("GTM", flag.is_gtm)):
            player[field] += int(enabled)
        player_dap_success[record.player_id] += int(flag.is_dap_success)
        if record.legacy_path_id:
            player_paths[record.player_id].add(record.legacy_path_id)
    team["TTP"] = sum(int(path.ttp) for path in paths.values())
    team["DTP"] = sum(int(path.path_type == "DTP") for path in paths.values())
    team["BAP"] = sum(int(bool(record.bap_reason)) for record in records)
    team["ASR"] = _ratio(dap_success, team["DAP"])
    team["SSR"] = _ratio(team["GOAL"] - team["OG"], team["SHOT"])
    for player_id, player in players.items():
        for path_id in player_paths[player_id]:
            path = paths.get(path_id)
            if path:
                player["UTP"] += int(path.path_type == "UTP")
                player["DTP"] += int(path.path_type == "DTP")
        player["TTP"] = player["UTP"] + player["DTP"]
        player["ASR"] = _ratio(player_dap_success[player_id], player["DAP"])
        player["SSR"] = _ratio(player["GOAL"], player["SHOT"])
    return KpiCalculation(team, dict(players), tuple(paths.values()), flags, ())


def confirmed_dtp_results(records: list[KpiRecord], calculation: KpiCalculation) -> dict[str, KpiRecord]:
    """Locate the final shot result shared by delivery buckets and rating inputs."""
    groups: defaultdict[str, list[KpiRecord]] = defaultdict(list)
    for record in sorted(records, key=lambda r: (r.half, r.seconds, r.created_at, r.seq, r.id)):
        if record.half in ('H1', 'H2'):
            groups[calculation.flags[record.id].path_id].append(record)
    results = {}
    for path in calculation.paths:
        if path.path_type != 'DTP' or path.id not in groups:
            continue
        members = groups[path.id]
        shots = [r for r in members if calculation.flags[r.id].is_shot]
        if not shots:
            raise ValueError('DTP path has no shot; cannot assign a confirmed interval')
        shot = shots[-1]
        terminal = shot
        for following in members[members.index(shot) + 1:]:
            if following.act:
                break
            terminal = following
        if terminal.res in ('', 'O'):
            raise ValueError('DTP final shot result is missing; sequence is not confirmed')
        results[path.id] = terminal
    return results


def calculate_team_kpi_5min(
    records: list[KpiRecord],
    half_end_seconds: dict[str, int] | None = None,
) -> list[dict[str, int]]:
    """Return 20 cumulative five-minute team snapshots for the rating API."""
    snapshots: list[dict[str, int]] = []
    # A persisted legacy DTP path label is visible on its first pass. It must
    # not count in a rating interval before the final shot result is recorded.
    calculation = calculate_kpis(records, close_trailing_bap=False)
    results = confirmed_dtp_results(records, calculation)
    for half, offset in (("H1", 0), ("H2", 10)):
        completed_halves = [record for record in records if record.half in ({"H1"} if half == "H1" else {"H1", "H2"})]
        for interval in range(1, 11):
            cutoff = interval * 300
            if interval == 10 and half_end_seconds:
                cutoff = max(cutoff, half_end_seconds.get(half, cutoff))
            scoped = [record for record in completed_halves
                      if record.half != half or interval == 10 or record.seconds < cutoff]
            # Use the finalized path decisions shared by the delivery bins.
            # Reclassifying an unfinished prefix can count a different DAP
            # path from the same finalized RAW in multiSheet_ALL.
            dap = sum(calculation.flags[r.id].is_dap for r in scoped)
            shots = sum(calculation.flags[r.id].is_shot for r in scoped)
            goals = sum(calculation.flags[r.id].is_goal for r in scoped)
            own = sum(calculation.flags[r.id].is_goal and r.player_id == 'OWN' for r in scoped)
            dtp = sum(result.half < half or (result.half == half and
                             (interval == 10 or result.seconds < cutoff)) for result in results.values())
            snapshots.append({'DAP': dap, 'DTP': dtp, 'SHOT': shots,
                              'SSR': _ratio(goals-own, shots), 'GOAL': goals})
    return snapshots


# Reviewed import RAW exceptions and delivery score precision.
APPROVED_RAW_EXCLUSIONS = {
    ('20242025eplj2rgn13na28rd2b8q0087', 'H'):
        ('023145453dec4d7aa36f3c733ad77ae2', '0000000833', 'H2', 7, 'P', 'X'),
    ('20242025eplj2rgn13na28rd2b8q0326', 'H'):
        ('46e47d4b2bb44e7284736502f16d0387', '0000003732', 'H1', 1, 'C', 'B'),
}
APPROVED_PATH_DELTAS = {
    ('20242025eplj2rgn13na28rd2b8q0070', 'H'): {'DTP': 5, 'TTP': 11},
    ('20242025eplj2rgn13na28rd2b8q0275', 'H'): {'TTP': 1},
}


def reviewed_records(match_id, side, records):
    """Exclude only the two exact reviewed events, refusing changed contents."""
    exception = APPROVED_RAW_EXCLUSIONS.get((match_id, side))
    result = []
    for record in records:
        if exception and record.id == exception[0]:
            signature = (record.id, record.player_id, record.half, record.seconds, record.act, record.res)
            if signature != exception or record.legacy_flags is None:
                raise ValueError('Reviewed RAW exclusion changed; a new review is required')
            continue
        result.append(record)
    return result


def approved_path_difference(match_id, side, field, stored, calculated):
    delta = APPROVED_PATH_DELTAS.get((match_id, side), {}).get(field)
    return delta is not None and abs(stored - calculated - delta) < 1e-6


def approved_apx_difference(match_id, side, team_id, opponent_id, metric,
                            imported, excluded_raw, stored_dap, original_dap,
                            calculated_dap, stored_rating, baseline):
    """Only the approved 0087H rating delta caused by its exact RAW exclusion."""
    return (match_id == '20242025eplj2rgn13na28rd2b8q0087'
            and side == 'H' and team_id == 'E-EV' and opponent_id == 'E-FU'
            and metric == 'APX' and imported and excluded_raw
            and stored_dap == original_dap == 271 and calculated_dap == 270
            and stored_rating == 67.75 and baseline == 400)


def rating_two_decimals(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Rating must be finite')
    return float(number.quantize(Decimal('.01'), rounding=ROUND_DOWN))
