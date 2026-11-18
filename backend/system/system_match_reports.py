from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from backend.system.system_firestore import BackendError, JpdDidData, NotFoundError
from backend.system.system_kpi_rules import TEAM_KPI_FIELDS, KpiRecord, calculate_kpis, reviewed_records
from backend.system.system_schema import LineupEntry, Side

HALF_OFFSETS = {"H1": 0, "H2": 45 * 60, "H3": 90 * 60, "H4": 105 * 60}
RANK_ORDER = {"main": 0, "sub": 1, "manager": 2}
report_reads = ThreadPoolExecutor(max_workers=4, thread_name_prefix="match-report")


def _round_key(match: dict[str, Any]) -> str:
    if match.get("round") is not None:
        return str(match["round"])
    return str(match.get("stage") or "unassigned")


def _date_key(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())[:8]


def _team(teams: dict[str, dict[str, Any]], team_id: str) -> dict[str, Any]:
    team = teams.get(team_id, {})
    return {
        "id": team_id,
        "name": str(team.get("nameKr") or team.get("name") or team.get("nameShort") or team_id),
        "short": str(team.get("nameShort") or team.get("name") or team_id),
        "nameEn": str(team.get("nameFull") or team.get("nameEn") or team.get("name") or team_id),
        "crestUrl": team.get("crestUrl"),
    }


def _analysts(head: dict[str, Any]) -> list[dict[str, str]]:
    """Analysts of a finished side: the RAW roster, or SQL recorder IDs for legacy rows."""
    if head.get("status") != "final":
        return []
    roster = head.get("recorders") or {}
    if roster:
        return [{"id": str(uid), "name": str((entry or {}).get("name") or uid) if isinstance(entry, dict) else str(uid)}
                for uid, entry in roster.items()]
    return [{"id": uid, "name": uid} for uid in dict.fromkeys(head.get("recorderIds") or [])]


def _event_time(half: str | None, seconds: int | None) -> dict[str, Any] | None:
    if half not in HALF_OFFSETS or seconds is None:
        return None
    minute, second = divmod(HALF_OFFSETS[half] + seconds, 60)
    return {"half": half, "clock": f"{minute:02}:{second:02}", "order": HALF_OFFSETS[half] + seconds}


def _order(item: dict[str, Any] | None) -> int:
    return (item or {}).get("order", 0)


def _kpi_view(values: dict[str, Any]) -> dict[str, int | float]:
    return {field: value if isinstance(value := values.get(field), (int, float)) else 0 for field in TEAM_KPI_FIELDS}


class MatchReports:
    """Read-only team reports for finished (final RAW) match recordings."""

    def __init__(self) -> None:
        self.did = JpdDidData()
        self.db = self.did.db

    @lru_cache(maxsize=32)
    def matches(self, league_id: str, season_id: str, cache_window: int) -> dict[str, Any]:
        query = (
            self.db.collection("matches")
            .where(filter=FieldFilter("leagueId", "==", league_id))
            .where(filter=FieldFilter("seasonId", "==", season_id))
            .select(["date", "kickoffTime", "round", "stage", "homeTeamId", "awayTeamId", "score"])
        )
        fixtures = [(snapshot.id, snapshot.to_dict() or {}) for snapshot in query.stream(timeout=30)]
        heads = self.did.get_recording_heads_many([gm_id for gm_id, _ in fixtures])
        team_ids = {str(match.get(key) or "") for _, match in fixtures for key in ("homeTeamId", "awayTeamId")}
        teams = self.did.get_documents_by_ids("teams", team_ids)
        rows = []
        for gm_id, match in fixtures:
            score = match.get("score") if isinstance(match.get("score"), dict) else {}
            status = heads.get(gm_id, {})
            rows.append({
                "gmId": gm_id,
                "date": _date_key(match.get("date")),
                "kickoffTime": match.get("kickoffTime"),
                "round": _round_key(match),
                "score": {"home": score.get("home"), "away": score.get("away")},
                "home": {**_team(teams, str(match.get("homeTeamId") or "")), "final": status.get("H", {}).get("status") == "final",
                         "analysts": _analysts(status.get("H", {}))},
                "away": {**_team(teams, str(match.get("awayTeamId") or "")), "final": status.get("A", {}).get("status") == "final",
                         "analysts": _analysts(status.get("A", {}))},
            })
        rows.sort(key=lambda row: (row["date"], row["kickoffTime"] or "", row["gmId"]), reverse=True)
        return {"matches": rows}

    def report(self, gm_id: str, side: Side) -> dict[str, Any]:
        match_future = report_reads.submit(self.did.get_match, gm_id)
        records_future = report_reads.submit(self.did.list_records, gm_id, side)
        cards_future = report_reads.submit(self._cards, gm_id, side)
        try:
            recording = self.did.get_recording(gm_id, side)
            match = match_future.result()
        except NotFoundError as error:
            raise BackendError("경기 기록을 찾을 수 없습니다.", 404, "report_not_found") from error
        if recording.status != "final":
            raise BackendError("분석이 완료된 팀만 리포트를 볼 수 있습니다.", 409, "report_not_final")

        team_id = match.homeTeamId if side == "H" else match.awayTeamId
        opponent_id = match.awayTeamId if side == "H" else match.homeTeamId
        legacy_ids = [*recording.recorderIds, *([recording.legacyRecorderId] if recording.legacyRecorderId else [])]
        lineup = recording.lineup
        cards_raw = cards_future.result()
        # Legacy lineups keep only player IDs, so names come from the players master.
        unnamed = {player_id for player_id, entry in lineup.items() if not entry.name}
        unnamed |= {str(card.get("playerId")) for card in cards_raw if card.get("playerId") and card.get("playerId") not in lineup}
        lookups = {
            "teams": {team_id, opponent_id},
            "leagues": {match.leagueId},
            "seasons": {match.seasonId},
            "stadiums": {match.stadiumId},
            "recorders": {*recording.recorders, *legacy_ids},
            "players": unnamed - {"OWN"},
        }
        docs = {name: self.did.get_documents_by_ids(name, ids) for name, ids in lookups.items()}

        def recorder_name(uid: str, stored: str | None = None) -> str:
            profile = docs["recorders"].get(uid, {})
            return str(stored or profile.get("name") or profile.get("displayName") or uid)

        recorders = sorted(
            ({"name": recorder_name(uid, entry.name), "rank": entry.rank} for uid, entry in recording.recorders.items()),
            key=lambda row: (RANK_ORDER.get(row["rank"], 9), row["name"]),
        )
        if not recorders:
            recorders = [{"name": recorder_name(uid), "rank": "main" if index == 0 else "sub"}
                         for index, uid in enumerate(dict.fromkeys(legacy_ids))]

        def player(player_id: str | None) -> dict[str, Any]:
            if player_id == "OWN":
                return {"name": "Own goal", "no": "", "pos": ""}
            entry = lineup.get(player_id or "")
            master = docs["players"].get(player_id or "", {})
            return {
                "name": (entry.name if entry else "") or master.get("name") or master.get("nameEn") or player_id or "-",
                "no": (entry.no if entry else "") or "",
                "pos": (entry.pos if entry else "") or "",
            }

        cards = sorted(
            ({**player(card.get("playerId")), "card": card.get("card"), "time": _event_time(card.get("half"), card.get("halfSeconds"))}
             for card in cards_raw),
            key=lambda card: _order(card["time"]),
        )

        kpi_records = reviewed_records(gm_id, side, [
            KpiRecord(
                id=record_id, half=record.half, seconds=record.halfSeconds, seq=record.seq,
                act=record.act, res=record.res, area=record.area, player_id=record.playerId,
                shoot_pos_x=record.shootPosX, shoot_pos_y=record.shootPosY,
                shoot_dsp_range=record.shootDspRange, is_shot=record.isShot,
                created_by=record.createdBy, created_at=record.createdAt.isoformat(),
                legacy_path_id=record.legacyPathId, legacy_path_type=record.legacyPathType,
                legacy_path_ttp=record.legacyPathTtp, legacy_flags=record.legacyKpiFlags,
                bap_reason=record.bapReason,
            )
            for record_id, record in records_future.result()
        ])
        # Attack paths never cross a half, so each half is calculated on its own RAW.
        halves = [half for half in HALF_OFFSETS
                  if half in ("H1", "H2") or any(record.half == half for record in kpi_records)]
        kpi = {half: _kpi_view(calculate_kpis([record for record in kpi_records if record.half == half]).team_kpi)
               for half in halves}
        kpi["total"] = _kpi_view(recording.kpi.model_dump() if recording.kpi else calculate_kpis(kpi_records).team_kpi)
        # Standalone X/B records only: an act record ending in X/B (e.g. P-B) is already in TAP,
        # and goal-zone codes (GX/GB...) are excluded. So ALL = TAP + X + B counts each record once.
        for key, scoped in [*((half, [r for r in kpi_records if r.half == half]) for half in halves), ("total", kpi_records)]:
            kpi[key]["X"] = sum(record.res == "X" and not record.act for record in scoped)
            kpi[key]["B"] = sum(record.res == "B" and not record.act for record in scoped)
            kpi[key]["ALL"] = kpi[key]["TAP"] + kpi[key]["X"] + kpi[key]["B"]

        pairs = self._substitution_pairs(lineup)
        # Legacy SQL lineups have no field positions, so only new DID input gets a pitch.
        starting_eleven = None if recording.legacyGiId is not None else self._starting_eleven(lineup, pairs, player)
        league = docs["leagues"].get(match.leagueId, {})
        season = docs["seasons"].get(match.seasonId, {})
        return {
            "gmId": gm_id,
            "side": side,
            "league": {"id": match.leagueId, "name": league.get("name") or league.get("nameEn") or match.leagueId},
            "season": {"id": match.seasonId, "name": season.get("name") or match.seasonId},
            "round": _round_key(match.model_dump()),
            "date": _date_key(match.date),
            "kickoffTime": match.kickoffTime,
            "stadium": (lambda stadium: stadium.get("name") or stadium.get("nameKr"))(docs["stadiums"].get(match.stadiumId, {})),
            "score": {"home": match.score.home, "away": match.score.away},
            "home": _team(docs["teams"], match.homeTeamId),
            "away": _team(docs["teams"], match.awayTeamId),
            "team": _team(docs["teams"], team_id),
            "recorders": recorders,
            # The final-update button rewrites the recording, so createdAt is the last update time.
            # Legacy SQL rows hold the old registration time instead, so they get none.
            "completedAt": None if recording.legacyGiId is not None else recording.createdAt.isoformat(),
            "formationKey": recording.formationKey,
            "cards": cards,
            "substitutions": self._substitutions(pairs, player),
            "startingEleven": starting_eleven,
            "bench": None if starting_eleven is None else self._bench(lineup, starting_eleven, player),
            "halves": halves,
            "kpi": kpi,
        }

    def _cards(self, gm_id: str, side: Side) -> list[dict[str, Any]]:
        collection = self.db.collection("matches").document(gm_id).collection("recordings").document(side).collection("cards")
        return [snapshot.to_dict() or {} for snapshot in collection.stream(retry=None, timeout=15)]

    @staticmethod
    def _substitution_pairs(lineup: dict[str, LineupEntry]) -> list[tuple[dict[str, Any] | None, str | None, str | None]]:
        """Return (time, out player ID, in player ID) in match order."""
        outs = sorted(((_event_time(entry.outHalf, entry.outSeconds), player_id)
                       for player_id, entry in lineup.items() if entry.outHalf), key=lambda item: _order(item[0]))
        unmatched = sorted(((_event_time(entry.inHalf, entry.inSeconds), player_id)
                            for player_id, entry in lineup.items()
                            # After a substitution the IN player holds a field slot (type START),
                            # so a substitute is anyone who did not start at H1 00:00.
                            if entry.inHalf and (entry.inHalf, entry.inSeconds) != ("H1", 0)),
                           key=lambda item: _order(item[0]))
        pairs = []
        for time, out_id in outs:
            # The input screen stores one substitution as an OUT/IN pair with the same time.
            pair = next((item for item in unmatched if item[0] == time), None)
            if pair:
                unmatched.remove(pair)
            pairs.append((time, out_id, pair[1] if pair else None))
        pairs.extend((time, None, in_id) for time, in_id in unmatched)
        return sorted(pairs, key=lambda item: _order(item[0]))

    @staticmethod
    def _starting_eleven(lineup: dict[str, LineupEntry], pairs, player) -> list[dict[str, Any]]:
        """Recover each field slot's starter by walking substitutions backwards."""
        replaced = {in_id: out_id for _, out_id, in_id in pairs if in_id and out_id}
        out_times = {out_id: time for time, out_id, _ in pairs if out_id}
        starters = []
        for player_id, entry in lineup.items():
            if entry.slot != "gk" and not (entry.slot.startswith("o") and entry.slot[1:].isdigit()):
                continue
            seen = {player_id}
            while player_id in replaced and replaced[player_id] not in seen:
                player_id = replaced[player_id]
                seen.add(player_id)
            starter = lineup.get(player_id)
            # A subbed-off starter sits in a bench slot and loses its H1 00:00 entry
            # time, so only reject a player who actually came on as a substitute.
            if starter is None or (starter.inHalf and (starter.inHalf, starter.inSeconds) != ("H1", 0)):
                continue
            out_time = out_times.get(player_id)
            starters.append({"id": player_id, "slot": entry.slot, **player(player_id), "out": out_time["clock"] if out_time else None})
        return starters

    @staticmethod
    def _bench(lineup: dict[str, LineupEntry], starters: list[dict[str, Any]], player) -> list[dict[str, Any]]:
        """Every listed player who did not start, including substitutes who came on."""
        starter_ids = {item["id"] for item in starters}
        bench = [player(player_id) for player_id in lineup if player_id not in starter_ids]
        return sorted(bench, key=lambda item: (int(item["no"]) if item["no"].isdigit() else 999, item["name"]))

    @staticmethod
    def _substitutions(pairs, player) -> list[dict[str, Any]]:
        return [{"time": time, "out": player(out_id) if out_id else None, "in": player(in_id) if in_id else None}
                for time, out_id, in_id in pairs]
