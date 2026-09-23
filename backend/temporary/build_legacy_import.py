from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import argparse
import re
from typing import Any, Callable

from backend.system.system_firestore import BackendError, JpdDidData


SUPPORTED_TABLES = {
    "ff_team", "ff_player", "ff_league", "ff_season", "ff_stadium", "ff_player_team",
    "ff_game", "ff_game_info", "ff_game_record", "ff_game_player", "ff_game_player_log",
    "ff_game_card", "ff_game_path", "ff_game_bap",
}


@dataclass(slots=True)
class ParsedTable:
    columns: list[str] | None
    rows: list[list[Any]]


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    matches_replaced: int
    documents_written: int
    records_written: int


ImportProgress = Callable[[str, int, int, int, int], None]


def _string(value: Any) -> str:
    return "" if value is None else str(value)




def _normalize_position(value: Any) -> str:
    raw = re.sub(r"[^A-Z]", "", _string(value).upper())
    if raw in {"GK", "G", "GOALKEEPER", "KEEPER"}:
        return "GK"
    if raw in {"FW", "F", "ST", "CF", "SS", "LW", "RW", "LF", "RF", "FORWARD", "STRIKER"}:
        return "FW"
    if raw in {"MF", "M", "DM", "CDM", "CM", "CAM", "AM", "LM", "RM", "MIDFIELDER"}:
        return "MF"
    if raw in {"DF", "D", "CB", "LB", "RB", "LWB", "RWB", "SW", "DEFENDER"}:
        return "DF"
    return _string(value).upper()

def _number(value: Any) -> int | float:
    try:
        return float(value) if "." in str(value) else int(value)
    except (TypeError, ValueError):
        return 0


def _integer(value: Any) -> int:
    return int(_number(value))


def _timestamp(value: Any) -> datetime | None:
    if not value or value in {"0000-00-00", "0000-00-00 00:00:00"}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _half(value: Any) -> str:
    return {"1": "H1", "H1": "H1", "2": "H2", "H2": "H2", "3": "H3", "H3": "H3"}.get(str(value), "H4")


def _literal(token: str, quoted: bool) -> Any:
    if quoted:
        return token.strip().replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\").replace("''", "'")
    token = token.strip()
    if not token or token.upper() == "NULL":
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return float(token) if "." in token else int(token)
    return token.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


def _tuple_values(source: str) -> list[Any]:
    values: list[Any] = []
    current = ""
    quoted = False
    quote = ""
    was_quoted = False
    index = 0
    while index < len(source):
        character = source[index]
        if quoted:
            if character == "\\":
                current += character
                index += 1
                if index < len(source):
                    current += source[index]
            elif character == quote:
                quoted = False
            else:
                current += character
        elif character in {"'", '"'}:
            quoted, quote, was_quoted = True, character, True
        elif character == ",":
            values.append(_literal(current, was_quoted))
            current, was_quoted = "", False
        else:
            current += character
        index += 1
    values.append(_literal(current, was_quoted))
    return values


def _split_tuples(source: str) -> list[list[Any]]:
    tuples: list[list[Any]] = []
    depth = 0
    current = ""
    quoted = False
    quote = ""
    index = 0
    while index < len(source):
        character = source[index]
        if quoted:
            current += character
            if character == "\\":
                index += 1
                if index < len(source):
                    current += source[index]
            elif character == quote:
                quoted = False
        elif character in {"'", '"'}:
            quoted, quote = True, character
            current += character
        elif character == "(":
            depth += 1
            if depth == 1:
                current = ""
            else:
                current += character
        elif character == ")":
            depth -= 1
            if depth == 0:
                tuples.append(_tuple_values(current))
            else:
                current += character
        elif depth:
            current += character
        index += 1
    return tuples


def parse_sql_dump(sql: str) -> dict[str, ParsedTable]:
    tables: dict[str, ParsedTable] = {}
    for match in re.finditer(r"CREATE TABLE\s+`?(\w+)`?\s*\(([\s\S]*?)\)\s*ENGINE", sql, re.IGNORECASE):
        columns = [line_match.group(1) for line in match.group(2).split(",\n") if (line_match := re.match(r"\s*`([^`]+)`", line))]
        tables[match.group(1)] = ParsedTable(columns, [])
    pattern = re.compile(r"INSERT INTO\s+`?(\w+)`?\s*(?:\(([^)]+)\))?\s*VALUES\s*([\s\S]*?);", re.IGNORECASE)
    for match in pattern.finditer(sql):
        name, explicit_columns, values = match.group(1), match.group(2), match.group(3)
        table = tables.setdefault(name, ParsedTable(None, []))
        if explicit_columns:
            table.columns = [column.strip().strip("`") for column in explicit_columns.split(",")]
        table.rows.extend(_split_tuples(values))
    return tables


def _rows(table: ParsedTable | None) -> list[dict[str, Any]]:
    if not table or not table.columns:
        return []
    return [dict(zip(table.columns, row)) for row in table.rows]


class BuildLegacyImport:
    """Replace only the matches present in one mysqldump, then write its legacy data."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()
        self.db = self.data.db

    def import_sql(self, sql: str, *, on_progress: ImportProgress | None = None) -> LegacyImportResult:
        tables = parse_sql_dump(sql)
        games = _rows(tables.get("ff_game"))
        if not games:
            raise BackendError("SQL dump must include ff_game rows", status_code=422, code="legacy_games_missing")
        unsupported = [name for name, table in tables.items() if name in SUPPORTED_TABLES and table.columns is None]
        if unsupported:
            raise BackendError(f"SQL dump has no column mapping: {', '.join(sorted(unsupported))}", status_code=422, code="legacy_columns_missing")
        if on_progress:
            on_progress("preparing", len(games), 0, 0, 0)
        items, gm_ids, record_count = self._build_items(tables, games)
        if not gm_ids:
            raise BackendError("SQL dump has no valid gm_id", status_code=422, code="legacy_games_missing")
        ordered_gm_ids = sorted(gm_ids)
        if on_progress:
            on_progress("replacing", len(ordered_gm_ids), 0, len(items), 0)
        for index, gm_id in enumerate(ordered_gm_ids, start=1):
            self._delete_match_tree(gm_id)
            if on_progress:
                on_progress("replacing", len(ordered_gm_ids), index, len(items), 0)
        self._write_items(items, on_progress=on_progress, total_matches=len(ordered_gm_ids))
        return LegacyImportResult(len(gm_ids), len(items), record_count)

    def _write_items(
        self,
        items: list[tuple[str, dict[str, Any], bool]],
        *,
        on_progress: ImportProgress | None = None,
        total_matches: int = 0,
    ) -> None:
        for offset in range(0, len(items), 200):
            batch = self.db.batch()
            for path, payload, merge in items[offset:offset + 200]:
                batch.set(self.db.document(path), payload, merge=merge)
            batch.commit(timeout=60)
            if on_progress:
                on_progress("writing", total_matches, total_matches, len(items), min(offset + 200, len(items)))

    def _delete_match_tree(self, gm_id: str) -> None:
        reference = self.db.collection("matches").document(gm_id)
        self.db.recursive_delete(reference, chunk_size=200)

    def _build_items(self, tables: dict[str, ParsedTable], games: list[dict[str, Any]]) -> tuple[list[tuple[str, dict[str, Any], bool]], set[str], int]:
        now = datetime.now(timezone.utc)
        items: list[tuple[str, dict[str, Any], bool]] = []
        append = items.append
        gm_ids = {_string(row.get("gm_id")) for row in games if _string(row.get("gm_id"))}
        player_names = {_string(row.get("p_id")): _string(row.get("p_name")) for row in _rows(tables.get("ff_player"))}
        contracts: dict[str, list[dict[str, str | None]]] = defaultdict(list)

        for row in _rows(tables.get("ff_team")):
            team_id = _string(row.get("t_code"))
            if team_id:
                append((f"teams/{team_id}", {"name": _string(row.get("t_name")), "nameKr": _string(row.get("t_name_kr")) or None, "nameFull": _string(row.get("t_name_full")) or None, "nameShort": _string(row.get("t_name_short")) or None, "stadiumId": _string(row.get("s_code")) or None, "currentLeagueId": _string(row.get("l_code")) or None, "active": True, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_player")):
            player_id = _string(row.get("p_id"))
            if player_id:
                append((f"players/{player_id}", {"name": _string(row.get("p_name")), "nameEn": _string(row.get("p_name_en")) or None, "nameFull": _string(row.get("p_name_full")) or None, "foot": _string(row.get("p_foot")) or None, "birth": _string(row.get("p_birth")) or None, "height": _number(row.get("p_height")) if row.get("p_height") is not None else None, "nation": _string(row.get("p_nation")) or None, "legacyPlayerId": _integer(row.get("p_id_old")) if row.get("p_id_old") is not None else None, "active": True, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_league")):
            league_id = _string(row.get("league_code"))
            if league_id:
                append((f"leagues/{league_id}", {"name": _string(row.get("league_name")), "nameEn": _string(row.get("league_name_en")) or None, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_season")):
            league_id, name = _string(row.get("l_code")), _string(row.get("s_name")).replace(" ", "")
            if league_id and name:
                append((f"seasons/{league_id}_{name}", {"leagueId": league_id, "name": _string(row.get("s_name")), "from": _string(row.get("s_fr_date")), "to": _string(row.get("s_to_date")), "alias": _string(row.get("s_alias")) or None, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_stadium")):
            stadium_id = _string(row.get("s_code"))
            if stadium_id:
                append((f"stadiums/{stadium_id}", {"name": _string(row.get("s_name")), "nameKr": _string(row.get("s_name_kr")) or None, "seats": _integer(row.get("s_seats")) if row.get("s_seats") is not None else None, "country": _string(row.get("s_country")) or None, "city": _string(row.get("s_city")) or None, "homeTeamId": _string(row.get("s_hometeam")) or None, "surface": _string(row.get("s_ground")) or None, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
        for row in _rows(tables.get("ff_player_team")):
            player_id, team_id, begin = _string(row.get("p_id")), _string(row.get("t_code")), _string(row.get("pt_begin"))
            if not player_id or not team_id or not begin:
                continue
            contract = {"from": begin, "to": _string(row.get("pt_end")) or None, "no": _string(row.get("pt_num")), "pos": _string(row.get("pt_pos"))}
            contracts[f"{player_id}_{team_id}"].append(contract)
            append((f"players/{player_id}/contracts/{team_id}_{begin}", {"teamId": team_id, "leagueId": _string(row.get("l_code")) or None, **contract, "createdAt": now, "createdBy": "legacy-import", "updatedAt": now, "updatedBy": "legacy-import"}, True))
            if not contract["to"]:
                append((f"teams/{team_id}/squad/{player_id}", {"name": player_names.get(player_id, ""), "no": contract["no"], "pos": contract["pos"], "contractId": f"{team_id}_{begin}", "since": begin}, True))

        matches: dict[str, dict[str, Any]] = {}
        for row in games:
            gm_id = _string(row.get("gm_id"))
            if not gm_id:
                continue
            match = {"home": _string(row.get("gm_h_t_code")), "away": _string(row.get("gm_a_t_code")), "date": _string(row.get("gm_date")), "realtime": _string(row.get("is_realtime")).upper() == "Y" or _integer(row.get("is_realtime")) == 1}
            matches[gm_id] = match
            append((f"matches/{gm_id}", {"date": match["date"], "kickoffTime": _string(row.get("gm_time")) or None, "leagueId": _string(row.get("gm_league")), "seasonId": gm_id[:8], "matchType": "league", "round": _integer(row.get("gm_round")) if row.get("gm_round") is not None else None, "group": _string(row.get("gm_sub_league")) or None, "stadiumId": _string(row.get("gm_s_code")), "homeTeamId": match["home"], "awayTeamId": match["away"], "score": {"home": _integer(row.get("gi_goal_home")), "away": _integer(row.get("gi_goal_away"))}, "createdAt": now, "updatedAt": now}, False))
            for team_id in (match["home"], match["away"]):
                append((f"teams/{team_id}/seasons/{gm_id[:8]}", {"leagueId": _string(row.get("gm_league"))}, True))

        recordings: dict[str, tuple[str, str]] = {}
        for row in _rows(tables.get("ff_game_info")):
            gm_id = _string(row.get("gm_id"))
            if gm_id not in matches:
                continue
            side = _string(row.get("gi_write_code")).upper()
            if side not in {"H", "A"}:
                side = "H" if matches[gm_id]["home"] == _string(row.get("gi_h_t_code")) else "A"
            home, away = _string(row.get("gi_h_t_code")), _string(row.get("gi_a_t_code"))
            team_id, opponent_id = (home, away) if side == "H" else (away, home)
            recordings[_string(row.get("gi_id"))] = (gm_id, side)
            append((f"matches/{gm_id}/recordings/{side}", {"side": side, "teamId": team_id, "opponentTeamId": opponent_id, "recorders": {}, "recorderIds": [_string(row.get("gi_user_id"))] if _string(row.get("gi_user_id")) else [], "status": "final", "inputMode": "실시간" if matches[gm_id]["realtime"] else "분석", "fieldSide": "right" if _string(row.get("gi_part")) == "R" else "left", "fieldSideEx": "right" if _string(row.get("gi_part_ex")) == "R" else ("left" if _string(row.get("gi_part_ex")) else None), "formationKey": _string(row.get("gi_formation")), "lineup": {}, "halves": {"H1": {"startedAt": _timestamp(row.get("gi_h1_begin")), "seconds": _integer(row.get("gi_h1_seconds"))}, "H2": {"startedAt": _timestamp(row.get("gi_h2_begin")), "seconds": _integer(row.get("gi_h2_seconds"))}}, "h1Locked": True, "h2Locked": True, "maxSeq": 0, "kpi": {"TAP": _number(row.get("gi_tmp")), "DAP": _number(row.get("gi_tap")), "TTP": _number(row.get("gi_ttp")), "DTP": _number(row.get("gi_ctp")), "BAP": _number(row.get("gi_bap")), "DTB": _number(row.get("gi_ctb")), "DTM": _number(row.get("gi_ctm")), "DTA": _number(row.get("gi_cta")), "DTS": _number(row.get("gi_cts")), "SHOT": _number(row.get("gi_sht")), "ASR": _number(row.get("gi_asr")), "SSR": _number(row.get("gi_ssr")), "GOAL": _number(row.get("gi_gol")), "OG": 0}, "kpiComputedAt": now, "kpiVersion": 1, "teamRating": None, "ratingBasedOn": None, "legacyGiId": _string(row.get("gi_id")), "syncedAt": None, "createdAt": _timestamp(row.get("gi_regdt")) or now, "updatedAt": _timestamp(row.get("gi_moddt")) or now}, False))

        path_records: dict[str, list[str]] = defaultdict(list)
        counters: dict[str, int] = defaultdict(int)
        record_count = 0
        for row in _rows(tables.get("ff_game_record")):
            recording = recordings.get(_string(row.get("gi_id")))
            if not recording:
                continue
            gm_id, side = recording
            half, seconds = _half(row.get("gr_half")), _integer(row.get("gr_half_seconds"))
            key = f"{gm_id}:{side}:{half}:{seconds}"
            seq, counters[key] = counters[key], counters[key] + 1
            record_id = _string(row.get("gr_id"))
            if row.get("gt_id") is not None:
                path_records[f"{gm_id}:{side}:{_string(row.get('gt_id'))}"].append(record_id)
            append((f"matches/{gm_id}/recordings/{side}/records/{record_id}", {"half": half, "halfSeconds": seconds, "seq": seq, "act": _string(row.get("gr_act_code")), "res": _string(row.get("gr_res_code")), "area": _integer(row.get("gr_area_code")), "posX": _number(row.get("gr_pos_x")) / 971 * 100 if row.get("gr_pos_x") is not None else None, "posY": _number(row.get("gr_pos_y")) / 634 * 100 if row.get("gr_pos_y") is not None else None, "shootPosX": _number(row.get("gr_shoot_pos_x")) if row.get("gr_shoot_pos_x") is not None else None, "shootPosY": _number(row.get("gr_shoot_pos_y")) if row.get("gr_shoot_pos_y") is not None else None, "playerId": _string(row.get("p_id")) or None, "createdBy": "legacy-import", "source": "did", "createdAt": _timestamp(row.get("gr_regdt")) or now}, False))
            record_count += 1

        lineups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        player_rows = _rows(tables.get("ff_game_player"))
        for row in player_rows:
            recording = recordings.get(_string(row.get("gi_id")))
            if not recording:
                continue
            gm_id, side, player_id, team_id = *recording, _string(row.get("p_id")), _string(row.get("t_code"))
            candidates = contracts.get(f"{player_id}_{team_id}", [])
            date = matches[gm_id]["date"]
            contract = next((item for item in candidates if item["from"] <= date and (not item["to"] or date <= item["to"])), candidates[0] if candidates else {"no": "", "pos": ""})
            player_type = "BENCH" if _string(row.get("gp_type")) == "ST" else "START"
            position = _normalize_position(contract["pos"])
            lineups[f"{gm_id}:{side}"][player_id] = {
                "slot": "gk" if player_type == "START" and position == "GK" else ("bench" if player_type == "BENCH" else "start"),
                "order": 1 if player_type == "START" and position == "GK" else _integer(row.get("gp_order")),
                "type": player_type,
                "no": contract["no"] or "", "name": player_names.get(player_id, ""), "pos": position,
                "inHalf": None if player_type == "BENCH" else "H1",
                "inSeconds": None if player_type == "BENCH" else 0,
                "outHalf": None, "outSeconds": None,
            }
            append((f"matches/{gm_id}/recordings/{side}/playerStats/{player_id}", {"playerId": player_id, "TAP": _number(row.get("gp_tmp")), "DAP": _number(row.get("gp_tap")), "UTP": _number(row.get("gp_utp")), "DTP": _number(row.get("gp_ctp")), "TTP": _number(row.get("gp_ttp")), "SHOT": _number(row.get("gp_sht")), "AST": _number(row.get("gp_ast")), "GOAL": _number(row.get("gp_gol")), "DTB": _number(row.get("gp_ctb")), "DTM": _number(row.get("gp_ctm")), "DTA": _number(row.get("gp_cta")), "DTS": _number(row.get("gp_cts")), "GTB": _number(row.get("gp_gtb")), "GTM": _number(row.get("gp_gtm")), "ASR": _number(row.get("gp_asr")), "SSR": _number(row.get("gp_ssr")), "scoreRel": None, "scoreAbs": None, "score": None, "jmx": None, "apx": None, "apxGrade": None, "tpx": None, "tpxGrade": None, "fpx": None, "fpxGrade": None, "ratingBasedOn": None}, False))
        for row in _rows(tables.get("ff_game_player_log")):
            recording = recordings.get(_string(row.get("gi_id")))
            if not recording:
                continue
            gm_id, side = recording
            lineup = lineups[f"{gm_id}:{side}"]
            half, seconds = _half(row.get("pl_in_half")), _integer(row.get("pl_in_seconds"))
            if (player_id := _string(row.get("pl_in_p_id"))) in lineup:
                lineup[player_id]["inHalf"], lineup[player_id]["inSeconds"] = half, seconds
                if _string(row.get("pl_in_position")):
                    lineup[player_id]["pos"] = _string(row.get("pl_in_position"))
            if (player_id := _string(row.get("pl_out_p_id"))) in lineup:
                lineup[player_id]["outHalf"], lineup[player_id]["outSeconds"] = half, seconds
        for key, lineup in lineups.items():
            gm_id, side = key.split(":")
            append((f"matches/{gm_id}/recordings/{side}", {"lineup": lineup}, True))

        for row in _rows(tables.get("ff_game_card")):
            if (recording := recordings.get(_string(row.get("gi_id")))):
                gm_id, side = recording
                append((f"matches/{gm_id}/recordings/{side}/cards/{_string(row.get('c_id'))}", {"playerId": _string(row.get("gp_id")), "half": _half(row.get("gp_card_half")), "halfSeconds": _integer(row.get("gp_card_time")), "card": "R" if _string(row.get("gp_card_card")) == "R" else "Y", "createdBy": "legacy-import", "createdAt": now}, False))
        for row in _rows(tables.get("ff_game_path")):
            if (recording := recordings.get(_string(row.get("gi_id")))):
                gm_id, side, path_id = *recording, _string(row.get("gt_id"))
                ptype = "DTP" if _integer(row.get("gt_ctp")) else ("UTP" if _integer(row.get("gt_utp")) else ("STP" if _integer(row.get("gt_stp")) else "UPP"))
                append((f"matches/{gm_id}/recordings/{side}/paths/{path_id}", {"gtId": path_id, "resCode": _string(row.get("gt_res_code")), "dsp": bool(_integer(row.get("gt_csp"))), "ttp": bool(_integer(row.get("gt_ttp"))), "ptype": ptype, "recordIds": path_records[f"{gm_id}:{side}:{path_id}"]}, False))
        for row in _rows(tables.get("ff_game_bap")):
            if (recording := recordings.get(_string(row.get("gi_id")))):
                gm_id, side = recording
                append((f"matches/{gm_id}/recordings/{side}/records/{_string(row.get('gr_id'))}", {"bapReason": "legacy-import"}, True))
        return items, gm_ids, record_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace legacy SQL dump matches in JPD-DID Firestore.")
    parser.add_argument("sql_file", type=Path, help="Path to a UTF-8 mysqldump file")
    args = parser.parse_args()
    try:
        sql = args.sql_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        parser.error(f"Cannot read SQL file: {exc}")
    result = BuildLegacyImport().import_sql(sql)
    print(
        f"Imported {result.matches_replaced} matches, "
        f"{result.documents_written} documents, {result.records_written} raw records."
    )


if __name__ == "__main__":
    main()
