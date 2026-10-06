from __future__ import annotations

"""Offline regression check for the legacy SQL KPI baseline.

This intentionally has no Firestore dependency.  It feeds the same raw record,
path, BAP and player inputs that the SQL importer receives into the current
Python KPI engine, then compares the output with the historic SQL aggregates.
"""

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.system.system_kpi_rules import KpiRecord, calculate_kpis
from backend.temporary.build_legacy_import import SOURCE_ROW_INDEX, _half, _integer, _number, _string, _tuple_values, legacy_record_sort_key


TEAM_COLUMNS = {
    "TAP": "gi_tmp", "DAP": "gi_tap", "TTP": "gi_ttp", "DTP": "gi_ctp",
    "BAP": "gi_bap", "DTB": "gi_ctb", "DTM": "gi_ctm", "DTA": "gi_cta",
    "DTS": "gi_cts", "SHOT": "gi_sht", "ASR": "gi_asr", "SSR": "gi_ssr",
    "GOAL": "gi_gol",
}
PLAYER_COLUMNS = {
    "TAP": "gp_tmp", "DAP": "gp_tap", "UTP": "gp_utp", "DTP": "gp_ctp",
    "TTP": "gp_ttp", "SHOT": "gp_sht", "AST": "gp_ast", "GOAL": "gp_gol",
    "DTB": "gp_ctb", "DTM": "gp_ctm", "DTA": "gp_cta", "DTS": "gp_cts",
    "GTB": "gp_gtb", "GTM": "gp_gtm", "ASR": "gp_asr", "SSR": "gp_ssr",
}
FLAG_COLUMNS = {
    "tap": "gr_is_tmp", "tapSuccess": "gr_is_tmp_s", "dap": "gr_is_tap",
    "dapSuccess": "gr_is_tap_s", "ast": "gr_is_ast", "shot": "gr_is_sht",
    "shotSuccess": "gr_is_sht_s", "goal": "gr_is_gol", "dtb": "gr_is_ctb",
    "dtm": "gr_is_ctm", "dta": "gr_is_cta", "dts": "gr_is_cts",
    "gtb": "gr_is_gtb", "gtm": "gr_is_gtm",
}
TEAM_FLAG_NAMES = {
    "TAP": "tap", "DAP": "dap", "SHOT": "shot", "GOAL": "goal",
    "DTB": "dtb", "DTM": "dtm", "DTA": "dta", "DTS": "dts",
}
PLAYER_FLAG_NAMES = {
    "TAP": "tap", "DAP": "dap", "SHOT": "shot", "AST": "ast", "GOAL": "goal",
    "DTB": "dtb", "DTM": "dtm", "DTA": "dta", "DTS": "dts",
    "GTB": "gtb", "GTM": "gtm",
}
SQL_TABLES = {"ff_game", "ff_game_info", "ff_game_record", "ff_game_path", "ff_game_bap", "ff_game_player"}


def _tuple_stream(source: str):
    """Yield VALUES tuples without loading a 250 MB SQL dump into memory."""
    depth = 0
    quoted = False
    quote = ""
    escaped = False
    value = ""
    for character in source:
        if quoted:
            value += character
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quoted = False
            continue
        if character in {"'", '"'}:
            quoted, quote = True, character
            if depth:
                value += character
        elif character == "(":
            if depth:
                value += character
            depth += 1
        elif character == ")" and depth:
            depth -= 1
            if depth:
                value += character
            else:
                yield _tuple_values(value)
                value = ""
        elif depth:
            value += character


def _stream_rows(path: Path):
    header = re.compile(r"INSERT\s+INTO\s+`?([A-Za-z0-9_]+)`?\s*\(([^)]*)\)\s*VALUES\s*", re.IGNORECASE)
    current_table: str | None = None
    columns: list[str] = []
    values_text = ""
    row_indexes: dict[str, int] = defaultdict(int)
    with path.open("r", encoding="utf-8-sig", errors="replace") as input_file:
        for line in input_file:
            if current_table is None:
                match = header.search(line)
                if not match:
                    continue
                current_table = match.group(1)
                columns = [column.strip().strip("`") for column in match.group(2).split(",")]
                values_text = line[match.end():]
            else:
                values_text += line
            if ";" not in line:
                continue
            if current_table in SQL_TABLES:
                for values in _tuple_stream(values_text):
                    row = dict(zip(columns, values))
                    row[SOURCE_ROW_INDEX] = row_indexes[current_table]
                    row_indexes[current_table] += 1
                    yield current_table, row
            current_table, columns, values_text = None, [], ""


def _same(left: int | float, right: int | float) -> bool:
    return abs(float(left) - float(right)) < 0.000001


def _numbers(row: dict[str, Any], columns: dict[str, str]) -> dict[str, int | float]:
    return {field: _number(row.get(column)) for field, column in columns.items()}


def _path_type(row: dict[str, Any]) -> str:
    if _integer(row.get("gt_ctp")):
        return "DTP"
    if _integer(row.get("gt_utp")):
        return "UTP"
    if _integer(row.get("gt_stp")):
        return "STP"
    return "UPP"


@dataclass(slots=True)
class SqlRecording:
    gm_id: str
    side: str
    gi_id: str
    expected_team: dict[str, int | float]
    expected_players: dict[str, dict[str, int | float]]
    records: list[KpiRecord]
    record_flags: dict[str, dict[str, bool]]
    path_record_ids: dict[str, list[str]]
    bap_record_ids: set[str]
    sql_path_totals: dict[str, int | float]
    sql_path_rows: dict[str, dict[str, bool]]


def _read_dump(path: Path) -> list[SqlRecording]:
    """Build calculation inputs solely from one SQL dump; never touches Firebase."""
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for table, row in _stream_rows(path):
        rows[table].append(row)
    games = {_string(row.get("gm_id")): row for row in rows["ff_game"]}
    info_by_gi: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for info in rows["ff_game_info"]:
        gm_id = _string(info.get("gm_id"))
        game = games.get(gm_id)
        if not game:
            continue
        side = _string(info.get("gi_write_code")).upper()
        if side not in {"H", "A"}:
            side = "H" if _string(info.get("gi_h_t_code")) == _string(game.get("gm_h_t_code")) else "A"
        info_by_gi[_string(info.get("gi_id"))] = (gm_id, side, info)

    paths = {
        (_string(row.get("gi_id")), _string(row.get("gt_id"))): row
        for row in rows["ff_game_path"]
    }
    bap_by_gi: dict[str, set[str]] = defaultdict(set)
    for row in rows["ff_game_bap"]:
        bap_by_gi[_string(row.get("gi_id"))].add(_string(row.get("gr_id")))

    expected_players: dict[str, dict[str, dict[str, int | float]]] = defaultdict(dict)
    for row in rows["ff_game_player"]:
        gi_id, player_id = _string(row.get("gi_id")), _string(row.get("p_id"))
        if gi_id in info_by_gi and player_id:
            expected_players[gi_id][player_id] = _numbers(row, PLAYER_COLUMNS)

    records_by_gi: dict[str, list[KpiRecord]] = defaultdict(list)
    flags_by_gi: dict[str, dict[str, dict[str, bool]]] = defaultdict(dict)
    path_record_ids_by_gi: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    sequences: dict[tuple[str, str, int], int] = defaultdict(int)
    record_identities = {
        gi_id: (gm_id, side)
        for gi_id, (gm_id, side, _) in info_by_gi.items()
    }
    for row in sorted(
        rows["ff_game_record"],
        key=lambda item: legacy_record_sort_key(item, record_identities.get(_string(item.get("gi_id")))),
    ):
        gi_id = _string(row.get("gi_id"))
        if gi_id not in info_by_gi:
            continue
        half, seconds = _half(row.get("gr_half")), _integer(row.get("gr_half_seconds"))
        sequence_key = (gi_id, half, seconds)
        seq, sequences[sequence_key] = sequences[sequence_key], sequences[sequence_key] + 1
        record_id, path_id = _string(row.get("gr_id")), _string(row.get("gt_id")) or None
        path = paths.get((gi_id, path_id or ""))
        flags = {name: bool(_integer(row.get(column))) for name, column in FLAG_COLUMNS.items()}
        flags_by_gi[gi_id][record_id] = flags
        if path_id:
            path_record_ids_by_gi[gi_id][path_id].append(record_id)
        records_by_gi[gi_id].append(KpiRecord(
            id=record_id,
            half=half,
            seconds=seconds,
            seq=seq,
            act=_string(row.get("gr_act_code")),
            res="GOAL" if _string(row.get("gr_res_code")) == "G" else _string(row.get("gr_res_code")),
            area=_integer(row.get("gr_area_code")),
            player_id=_string(row.get("p_id")) or None,
            created_at=_string(row.get("gr_regdt")),
            legacy_path_id=path_id,
            legacy_path_type=_path_type(path) if path else None,
            legacy_path_ttp=bool(_integer(path.get("gt_ttp"))) if path else None,
            legacy_flags=flags,
            bap_reason="sql" if record_id in bap_by_gi[gi_id] else None,
        ))

    recordings: list[SqlRecording] = []
    for gi_id, (gm_id, side, info) in info_by_gi.items():
        # Historical SQL can retain an aggregate-only path after its raw rows
        # were removed. It affects team TTP/DTP, never player attribution.
        for (path_gi_id, path_id), path in paths.items():
            if path_gi_id != gi_id or path_id in path_record_ids_by_gi[gi_id]:
                continue
            records_by_gi[gi_id].append(KpiRecord(
                id=f"legacy-path:{path_id}", half="H4", seconds=0, seq=0, act="", res="", area=18,
                legacy_path_id=path_id, legacy_path_type=_path_type(path),
                legacy_path_ttp=bool(_integer(path.get("gt_ttp"))), legacy_flags={},
            ))
        recordings.append(SqlRecording(
            gm_id=gm_id,
            side=side,
            gi_id=gi_id,
            expected_team=_numbers(info, TEAM_COLUMNS),
            expected_players=expected_players[gi_id],
            records=records_by_gi[gi_id],
            record_flags=flags_by_gi[gi_id],
            path_record_ids=dict(path_record_ids_by_gi[gi_id]),
            bap_record_ids=bap_by_gi[gi_id],
            sql_path_totals={
                "UTP": _number(info.get("gi_utp")),
                "DTP": _number(info.get("gi_ctp")),
                "STP": _number(info.get("gi_stp")),
                "TTP": _number(info.get("gi_ttp")),
            },
            sql_path_rows={
                path_id: {
                    "UTP": bool(_integer(path.get("gt_utp"))),
                    "DTP": bool(_integer(path.get("gt_ctp"))),
                    "STP": bool(_integer(path.get("gt_stp"))),
                    "TTP": bool(_integer(path.get("gt_ttp"))),
                }
                for (path_gi_id, path_id), path in paths.items()
                if path_gi_id == gi_id
            },
        ))
    return recordings


def _team_evidence(field: str, source: SqlRecording) -> dict[str, list[str]]:
    if field == "BAP":
        return {"recordIds": sorted(source.bap_record_ids)}
    if field in {"TTP", "DTP"}:
        path_ids = [record.legacy_path_id for record in source.records if record.legacy_path_id and (
            field == "TTP" and record.legacy_path_ttp or field == "DTP" and record.legacy_path_type == "DTP"
        )]
        return {"pathIds": sorted(set(path_ids))}
    if field in {"ASR", "SSR"}:
        return {"recordIds": [record.id for record in source.records]}
    flag = TEAM_FLAG_NAMES.get(field)
    return {"recordIds": [record_id for record_id, flags in source.record_flags.items() if flag and flags[flag]]}


def _player_evidence(field: str, player_id: str, source: SqlRecording) -> dict[str, list[str]]:
    player_records = [record for record in source.records if record.player_id == player_id]
    if field in {"UTP", "DTP", "TTP"}:
        path_ids = {record.legacy_path_id for record in player_records if record.legacy_path_id}
        return {"pathIds": sorted(path_ids)}
    if field in {"ASR", "SSR"}:
        return {"recordIds": [record.id for record in player_records]}
    flag = PLAYER_FLAG_NAMES.get(field)
    return {"recordIds": [record.id for record in player_records if flag and source.record_flags[record.id][flag]]}


def _compare_recording(source: SqlRecording) -> dict[str, Any]:
    result = calculate_kpis(source.records)
    path_type_counts: dict[str, int] = defaultdict(int)
    path_ttp_count = 0
    for path in result.paths:
        path_type_counts[path.path_type] += 1
        path_ttp_count += int(path.ttp)
    sql_path_row_totals = {
        field: sum(int(values[field]) for values in source.sql_path_rows.values())
        for field in ("UTP", "DTP", "STP", "TTP")
    }
    referenced_path_ids = set(source.path_record_ids)
    sql_path_ids = set(source.sql_path_rows)
    team_differences = {
        field: {"sql": expected, "python": result.team_kpi[field], "evidence": _team_evidence(field, source)}
        for field, expected in source.expected_team.items()
        if not _same(expected, result.team_kpi[field])
    }
    player_differences: dict[str, dict[str, Any]] = {}
    zero_player = {field: 0 for field in PLAYER_COLUMNS}
    for player_id in sorted(set(source.expected_players) | set(result.player_kpis)):
        expected = source.expected_players.get(player_id, zero_player)
        calculated = result.player_kpis.get(player_id, zero_player)
        differences = {
            field: {"sql": value, "python": calculated.get(field, 0), "evidence": _player_evidence(field, player_id, source)}
            for field, value in expected.items()
            if not _same(value, calculated.get(field, 0))
        }
        if differences:
            player_differences[player_id] = differences
    return {
        "gmId": source.gm_id,
        "side": source.side,
        "giId": source.gi_id,
        "source": {
            "recordCount": len(source.records), "pathCount": len(source.path_record_ids),
            "bapCount": len(source.bap_record_ids), "playerCount": len(source.expected_players),
            "sqlPathTotals": source.sql_path_totals,
            "sqlPathRowTotals": sql_path_row_totals,
            "pythonPathTotals": {"types": dict(path_type_counts), "TTP": path_ttp_count},
            "orphanRecordPathIds": sorted(referenced_path_ids - sql_path_ids),
            "pathRowsWithoutRecords": sorted(sql_path_ids - referenced_path_ids),
        },
        "teamDifferences": team_differences,
        "playerDifferences": player_differences,
    }


def verify(sql_files: list[Path], output: Path) -> dict[str, Any]:
    recordings = [recording for sql_file in sql_files for recording in _read_dump(sql_file)]
    compared = [_compare_recording(recording) for recording in recordings]
    failures = [item for item in compared if item["teamDifferences"] or item["playerDifferences"]]
    payload = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "inputs": [str(path) for path in sql_files],
        "summary": {
            "matches": len({item["gmId"] for item in compared}),
            "recordings": len(compared),
            "passed": len(compared) - len(failures),
            "failed": len(failures),
            "teamKpiDifferences": sum(len(item["teamDifferences"]) for item in failures),
            "playerKpiDifferences": sum(sum(len(fields) for fields in item["playerDifferences"].values()) for item in failures),
        },
        "failures": failures,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare current Python KPI output with legacy SQL KPI values offline.")
    parser.add_argument("sql_files", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("backend/data/audit/legacy_sql_pipeline.json"))
    args = parser.parse_args()
    payload = verify(args.sql_files, args.output)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    print(args.output)
    if payload["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
