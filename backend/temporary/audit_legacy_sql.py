from __future__ import annotations

"""Read-only SQL-to-Firestore audit for legacy match migrations.

The importer deliberately replaces match trees, so it is not a suitable
verification tool.  This module never writes to Firestore: it streams the SQL
dump, compares its canonical values with Firestore, and writes a local report.
"""

import argparse
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.system.system_firestore import JpdDidData, PROJECT_ROOT
from backend.temporary.build_legacy_import import _half, _integer, _number, _string, _tuple_values


TARGET_TABLES = {"ff_game", "ff_game_info", "ff_game_record", "ff_game_player"}
TEAM_KPI_COLUMNS = {
    "TAP": "gi_tmp", "DAP": "gi_tap", "TTP": "gi_ttp", "DTP": "gi_ctp",
    "BAP": "gi_bap", "DTB": "gi_ctb", "DTM": "gi_ctm", "DTA": "gi_cta",
    "DTS": "gi_cts", "SHOT": "gi_sht", "ASR": "gi_asr", "SSR": "gi_ssr",
    "GOAL": "gi_gol",
}
PLAYER_KPI_COLUMNS = {
    "TAP": "gp_tmp", "DAP": "gp_tap", "UTP": "gp_utp", "DTP": "gp_ctp",
    "TTP": "gp_ttp", "SHOT": "gp_sht", "AST": "gp_ast", "GOAL": "gp_gol",
    "DTB": "gp_ctb", "DTM": "gp_ctm", "DTA": "gp_cta", "DTS": "gp_cts",
    "GTB": "gp_gtb", "GTM": "gp_gtm", "ASR": "gp_asr", "SSR": "gp_ssr",
}


def _same(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 0.000001
    return left == right


def _difference(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {"sql": value, "firestore": actual.get(key)}
        for key, value in expected.items()
        if not _same(value, actual.get(key))
    }


def _tuple_stream(source: str) -> Iterator[list[Any]]:
    """Emit SQL VALUES tuples while honouring quotes and escaped quotes."""
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


def stream_sql_rows(path: Path, *, tables: set[str] = TARGET_TABLES) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse explicit-column INSERT rows without loading the dump at once."""
    current_table: str | None = None
    columns: list[str] = []
    values_text = ""
    header = re.compile(
        r"INSERT\s+INTO\s+`?([A-Za-z0-9_]+)`?\s*\(([^)]*)\)\s*VALUES\s*",
        re.IGNORECASE,
    )
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
            if current_table in tables:
                for values in _tuple_stream(values_text):
                    yield current_table, dict(zip(columns, values))
            current_table = None
            columns = []
            values_text = ""


@dataclass(slots=True)
class SqlSource:
    games: dict[str, dict[str, Any]]
    recordings: dict[tuple[str, str], dict[str, Any]]
    records: dict[tuple[str, str], dict[str, dict[str, Any]]]
    player_stats: dict[tuple[str, str], dict[str, dict[str, Any]]]


def read_sql_source(path: Path, *, include_raw: bool = True) -> SqlSource:
    games: dict[str, dict[str, Any]] = {}
    gi_to_recording: dict[str, tuple[str, str]] = {}
    recordings: dict[tuple[str, str], dict[str, Any]] = {}
    records: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    player_stats: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)

    tables = TARGET_TABLES if include_raw else {"ff_game", "ff_game_info", "ff_game_player"}
    for table, row in stream_sql_rows(path, tables=tables):
        if table == "ff_game":
            gm_id = _string(row.get("gm_id"))
            if gm_id:
                games[gm_id] = row
            continue
        if table == "ff_game_info":
            gm_id = _string(row.get("gm_id"))
            match = games.get(gm_id)
            if not match:
                continue
            side = _string(row.get("gi_write_code")).upper()
            if side not in {"H", "A"}:
                side = "H" if _string(row.get("gi_h_t_code")) == _string(match.get("gm_h_t_code")) else "A"
            key = (gm_id, side)
            recordings[key] = row
            gi_to_recording[_string(row.get("gi_id"))] = key
            continue
        key = gi_to_recording.get(_string(row.get("gi_id")))
        if not key:
            continue
        if table == "ff_game_record":
            record_id = _string(row.get("gr_id"))
            if record_id:
                # A raw SQL row contains many legacy-only fields. Retain only
                # the values that survive the Firestore import and affect KPI.
                records[key][record_id] = _expected_record(row)
        elif table == "ff_game_player":
            player_id = _string(row.get("p_id"))
            if player_id:
                player_stats[key][player_id] = _expected_player_kpi(row)
    return SqlSource(games, recordings, dict(records), dict(player_stats))


def _expected_record(row: dict[str, Any]) -> dict[str, Any]:
    result = _string(row.get("gr_res_code"))
    return {
        "half": _half(row.get("gr_half")),
        "halfSeconds": _integer(row.get("gr_half_seconds")),
        "act": _string(row.get("gr_act_code")),
        "res": "GOAL" if result == "G" else result,
        "area": _integer(row.get("gr_area_code")),
        "playerId": _string(row.get("p_id")) or None,
        "shootPosX": _number(row.get("gr_shoot_pos_x")) if row.get("gr_shoot_pos_x") is not None else None,
        "shootPosY": _number(row.get("gr_shoot_pos_y")) if row.get("gr_shoot_pos_y") is not None else None,
    }


def _expected_kpi(row: dict[str, Any]) -> dict[str, Any]:
    return {field: _number(row.get(column)) for field, column in TEAM_KPI_COLUMNS.items()} | {"OG": 0}


def _expected_player_kpi(row: dict[str, Any]) -> dict[str, Any]:
    return {field: _number(row.get(column)) for field, column in PLAYER_KPI_COLUMNS.items()}


class AuditLegacySql:
    """Compare a legacy SQL source dump with the currently imported Firestore data."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    @staticmethod
    def build_manifest(sql_path: Path, output_path: Path) -> Path:
        """Extract the small, repeatable SQL comparison baseline once."""
        source = read_sql_source(sql_path, include_raw=False)
        payload = {
            "games": source.games,
            "recordings": {"|".join(key): value for key, value in source.recordings.items()},
            "playerStats": {"|".join(key): value for key, value in source.player_stats.items()},
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return output_path

    @staticmethod
    def _read_manifest(path: Path) -> SqlSource:
        payload = json.loads(path.read_text(encoding="utf-8"))
        recordings = {tuple(key.split("|", 1)): value for key, value in payload["recordings"].items()}
        player_stats = {tuple(key.split("|", 1)): value for key, value in payload["playerStats"].items()}
        return SqlSource(payload["games"], recordings, {}, player_stats)

    def audit(
        self,
        sql_path: Path,
        *,
        gm_ids: set[str] | None = None,
        manifest_path: Path | None = None,
    ) -> Path:
        # Match, recording, stored KPI and player KPI validation does not need
        # the 255k raw rows in memory. Raw rows are audited separately by the
        # structural raw audit and a per-recording streaming pass.
        source = self._read_manifest(manifest_path) if manifest_path else read_sql_source(sql_path, include_raw=False)
        if gm_ids is not None:
            selected = set(gm_ids)
            source = SqlSource(
                {gm_id: row for gm_id, row in source.games.items() if gm_id in selected},
                {key: row for key, row in source.recordings.items() if key[0] in selected},
                {},
                {key: row for key, row in source.player_stats.items() if key[0] in selected},
            )
        artifacts = self._artifacts()
        firestore_matches = dict(self.data.list_matches(league_id="EPL", season_id="20232024"))
        rows = list(source.recordings)

        # Firestore's gRPC client is reliable here with a small fan-out. A
        # large fan-out can exhaust the Windows local socket pool during a
        # 300-recording audit.
        with ThreadPoolExecutor(max_workers=2) as executor:
            comparisons = list(executor.map(lambda key: self._compare_side(key, source, firestore_matches, artifacts), rows))

        payload = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceSql": str(sql_path),
            "scope": {"firstGmId": min(source.games), "lastGmId": max(source.games)},
            "summary": {
                "sqlMatches": len(source.games),
                "sqlRecordings": len(source.recordings),
                "firestoreMatchesInSource": sum(gm_id in firestore_matches for gm_id in source.games),
                "sidesWithDifferences": sum(bool(item["differences"]) for item in comparisons),
                "kpiPipelineValidationFailures": sum(bool(item["pipelineKpiDifferences"]) for item in comparisons),
            },
            "missingMatches": sorted(gm_id for gm_id in source.games if gm_id not in firestore_matches),
            "sides": comparisons,
        }
        directory = PROJECT_ROOT / "backend" / "data" / "audit"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"legacy_sql_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _artifacts() -> dict[tuple[str, str], dict[str, Any]]:
        artifacts: dict[tuple[str, str], dict[str, Any]] = {}
        for path in (PROJECT_ROOT / "backend" / "data" / "jpd-did").rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                artifacts[(value["gmId"], value["side"])] = value
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        return artifacts

    def _compare_side(
        self,
        key: tuple[str, str],
        source: SqlSource,
        firestore_matches: dict[str, Any],
        artifacts: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        gm_id, side = key
        game = source.games[gm_id]
        info = source.recordings[key]
        differences: dict[str, Any] = {}
        match = firestore_matches.get(gm_id)
        expected_match = {
            "date": _string(game.get("gm_date")).replace(".", "-"), "round": _integer(game.get("gm_round")),
            "homeTeamId": _string(game.get("gm_h_t_code")), "awayTeamId": _string(game.get("gm_a_t_code")),
            "score": {"home": _integer(game.get("gi_goal_home")), "away": _integer(game.get("gi_goal_away"))},
        }
        if not match:
            differences["match"] = {field: {"sql": value, "firestore": None} for field, value in expected_match.items()}
            return {"gmId": gm_id, "side": side, "differences": differences, "pipelineKpiDifferences": {}}
        actual_match = {field: getattr(match, field) for field in expected_match if field != "score"} | {"score": match.score.model_dump()}
        if mismatch := _difference(expected_match, actual_match):
            differences["match"] = mismatch

        try:
            recording = self.data.get_recording(gm_id, side)
            actual_records = (
                {record_id: record.model_dump(mode="json") for record_id, record in self.data.list_records(gm_id, side)}
                if source.records else {}
            )
            actual_players = self.data.list_player_stats(gm_id, side)
        except Exception as exc:  # Report schema/read failures without hiding the rest of the audit.
            differences["read"] = {"error": str(exc)}
            return {"gmId": gm_id, "side": side, "differences": differences, "pipelineKpiDifferences": {}}

        team_id = _string(info.get("gi_h_t_code")) if side == "H" else _string(info.get("gi_a_t_code"))
        opponent_id = _string(info.get("gi_a_t_code")) if side == "H" else _string(info.get("gi_h_t_code"))
        expected_head = {
            "side": side, "teamId": team_id, "opponentTeamId": opponent_id,
            "formationKey": _string(info.get("gi_formation")), "legacyGiId": _string(info.get("gi_id")),
        }
        actual_head = recording.model_dump(mode="json")
        if mismatch := _difference(expected_head, actual_head):
            differences["recording"] = mismatch

        expected_kpi = _expected_kpi(info)
        actual_kpi = recording.kpi.model_dump() if recording.kpi else {}
        if mismatch := _difference(expected_kpi, actual_kpi):
            differences["storedKpi"] = mismatch

        if source.records:
            expected_records = source.records.get(key, {})
            missing = sorted(set(expected_records) - set(actual_records))
            unexpected = sorted(set(actual_records) - set(expected_records))
            field_differences = {
                record_id: mismatch
                for record_id, expected in expected_records.items()
                if record_id in actual_records and (mismatch := _difference(expected, actual_records[record_id]))
            }
            if missing or unexpected or field_differences:
                differences["records"] = {
                    "sqlCount": len(expected_records), "firestoreCount": len(actual_records),
                    "missing": missing[:50], "unexpected": unexpected[:50],
                    "fieldDifferences": dict(list(field_differences.items())[:50]),
                }

        expected_players = source.player_stats.get(key, {})
        player_differences = {
            player_id: mismatch
            for player_id, expected in expected_players.items()
            if player_id in actual_players and (mismatch := _difference(expected, actual_players[player_id]))
        }
        player_missing = sorted(set(expected_players) - set(actual_players))
        if player_missing or player_differences:
            differences["playerStats"] = {
                "sqlCount": len(expected_players), "firestoreCount": len(actual_players),
                "missing": player_missing[:50], "fieldDifferences": dict(list(player_differences.items())[:50]),
            }

        artifact = artifacts.get(key) or {}
        pipeline = artifact.get("teamKpi") or {}
        pipeline_differences = _difference(expected_kpi, pipeline) if pipeline else {}
        return {"gmId": gm_id, "side": side, "differences": differences, "pipelineKpiDifferences": pipeline_differences}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only legacy SQL to Firestore audit.")
    parser.add_argument("sql_file", type=Path)
    args = parser.parse_args()
    print(AuditLegacySql().audit(args.sql_file))


if __name__ == "__main__":
    main()
