from __future__ import annotations

from dataclasses import dataclass
import json

from backend.system.system_schema import PlayerKpi, RecordingKpi, Side
from backend.system.system_firestore import JpdDidData, PROJECT_ROOT, round_key_for, utc_now
from backend.system.system_kpi_rules import KpiRecord, calculate_kpis, calculate_team_kpi_5min


TEAM_FIELDS = tuple(RecordingKpi.model_fields)
PLAYER_FIELDS = tuple(PlayerKpi.model_fields)
# The legacy PHP aggregate had no own-goal field. SQL imports therefore carry
# OG=0 even where raw records identify own goals; validate the historic fields
# while preserving the newly derived OG value in the rebuilt KPI.
LEGACY_VALIDATION_TEAM_FIELDS = tuple(field for field in TEAM_FIELDS if field != "OG")


@dataclass(frozen=True, slots=True)
class KpiValidation:
    passed: bool
    team_differences: dict[str, dict[str, int | float]]
    player_differences: dict[str, dict[str, dict[str, int | float]]]
    legacy_drift_accepted: bool = False


@dataclass(frozen=True, slots=True)
class BuildMatchKpisResult:
    created: bool
    recalculated: bool
    validated: bool
    validation_failed: bool
    empty: bool = False


def _same_number(left: int | float, right: int | float) -> bool:
    return abs(float(left) - float(right)) < 0.000001


def _team_differences(expected: RecordingKpi, actual: RecordingKpi) -> dict[str, dict[str, int | float]]:
    return {
        field: {"expected": getattr(expected, field), "calculated": getattr(actual, field)}
        for field in LEGACY_VALIDATION_TEAM_FIELDS
        if not _same_number(getattr(expected, field), getattr(actual, field))
    }


def _player_differences(expected_players, actual_players: dict[str, PlayerKpi]) -> dict[str, dict[str, dict[str, int | float]]]:
    differences: dict[str, dict[str, dict[str, int | float]]] = {}
    for player_id in sorted(set(expected_players) | set(actual_players)):
        expected = expected_players.get(player_id)
        actual = actual_players.get(player_id)
        if expected is None or actual is None:
            differences[player_id] = {"player": {"expected": int(expected is not None), "calculated": int(actual is not None)}}
            continue
        fields = {
            field: {"expected": expected.get(field, 0), "calculated": getattr(actual, field)}
            for field in PLAYER_FIELDS
            if field != "playerId" and not _same_number(expected.get(field, 0), getattr(actual, field))
        }
        if fields:
            differences[player_id] = fields
    return differences


def _is_legacy_baseline_drift(
    team_differences: dict[str, dict[str, int | float]],
    player_differences: dict[str, dict[str, dict[str, int | float]]],
) -> bool:
    """Allow the narrowly scoped stale-path discrepancy found in SQL imports.

    Old SQL dumps can retain a prior path/role aggregate after raw records were
    edited. The current engine follows the PHP batch rules from raw records.
    Only one-count differences in the affected path fields may be accepted.
    """
    team_fields = {"TTP", "DTB", "DTM", "DTA", "DTS", "OG"}
    player_fields = {"UTP", "TTP", "DTB", "DTM", "DTA", "DTS", "GTB", "GTM", "AST"}
    if not team_differences or set(team_differences) - team_fields:
        return False
    team_deltas = {
        field: int(float(item["calculated"]) - float(item["expected"]))
        for field, item in team_differences.items()
    }
    # A stale legacy UTP path may not be assigned to any player at all.
    if not player_differences:
        return (
            set(team_differences) in ({"TTP"}, {"OG"})
            and abs(next(iter(team_deltas.values()))) == 1
        )
    for fields in player_differences.values():
        if set(fields) - player_fields:
            return False
        if any(abs(float(item["expected"]) - float(item["calculated"])) != 1 for item in fields.values()):
            return False
    # Role fields must map exactly to the affected player role flags. This
    # catches a whole stale path while refusing unrelated aggregate drift.
    for field, delta in team_deltas.items():
        if field == "TTP":
            if abs(delta) != 1:
                return False
            continue
        if field == "OG":
            return False
        player_deltas = [
            int(float(fields[field]["calculated"]) - float(fields[field]["expected"]))
            for fields in player_differences.values()
            if field in fields
        ]
        if not player_deltas or len(player_deltas) != abs(delta) or any(item != (1 if delta > 0 else -1) for item in player_deltas):
            return False
    return True


class BuildMatchKpis:
    """Build one recording's KPI from raw records and validate legacy imports first."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def build_for_raw_change(self, gm_id: str, side: Side) -> BuildMatchKpisResult:
        return self._build(gm_id, side, logic_changed=False)

    def rebuild_for_logic_change(self, gm_id: str, side: Side) -> BuildMatchKpisResult:
        return self._build(gm_id, side, logic_changed=True)

    def _build(self, gm_id: str, side: Side, *, logic_changed: bool) -> BuildMatchKpisResult:
        source = self.data.read_match_kpi_source(gm_id, side)
        if not source.records:
            return BuildMatchKpisResult(False, False, False, False, empty=True)
        if source.recording.kpiSourceFingerprint == source.fingerprint and not logic_changed:
            self._save_data_artifact(gm_id, source, None, None, None)
            return BuildMatchKpisResult(False, False, False, False)

        records = [
            KpiRecord(
                id=record_id,
                half=record.half,
                seconds=record.halfSeconds,
                seq=record.seq,
                act=record.act,
                res=record.res,
                area=record.area,
                player_id=record.playerId,
                shoot_pos_x=record.shootPosX,
                shoot_pos_y=record.shootPosY,
                shoot_dsp_range=record.shootDspRange,
                is_shot=record.isShot,
                created_by=record.createdBy,
                created_at=record.createdAt.isoformat(),
            )
            for record_id, record in source.records
        ]
        calculation = calculate_kpis(records)
        team_kpi = RecordingKpi(**calculation.team_kpi)
        player_kpis = {
            player_id: PlayerKpi(playerId=player_id, **kpi)
            for player_id, kpi in calculation.player_kpis.items()
        }
        # SQL kept one ff_game_player row for every lineup player, even when
        # that player has no attributed raw event. Preserve those zero KPI rows.
        for player_id in source.recording.lineup:
            player_kpis.setdefault(
                player_id,
                PlayerKpi(playerId=player_id, **{field: 0 for field in PLAYER_FIELDS if field != "playerId"}),
            )

        validation: KpiValidation | None = None
        if source.recording.kpi is not None and source.recording.kpiSourceFingerprint is None:
            expected_players = self.data.list_player_stats(gm_id, side)
            team_differences = _team_differences(source.recording.kpi, team_kpi)
            # Some legacy imports have no trustworthy playerStats baseline.
            # Team KPI remains the acceptance baseline in that case.
            player_differences = _player_differences(expected_players, player_kpis) if expected_players else {}
            legacy_drift_accepted = _is_legacy_baseline_drift(team_differences, player_differences)
            validation = KpiValidation(
                passed=not team_differences and not player_differences,
                team_differences=team_differences,
                player_differences=player_differences,
                legacy_drift_accepted=legacy_drift_accepted,
            )
            if not validation.passed and not validation.legacy_drift_accepted:
                self._save_data_artifact(gm_id, source, team_kpi, player_kpis, validation)
                return BuildMatchKpisResult(False, False, False, True)

        created = source.recording.kpi is None
        recalculated = source.recording.kpiSourceFingerprint is not None
        kpi_version = 1 if created else source.recording.kpiVersion + int(recalculated)
        self.data.save_recording_kpi(
            gm_id,
            side,
            kpi=team_kpi,
            kpi_version=kpi_version,
            source_fingerprint=source.fingerprint,
            player_kpis=player_kpis,
            calculated_at=utc_now(),
        )
        self._save_data_artifact(gm_id, source, team_kpi, player_kpis, validation)
        return BuildMatchKpisResult(created, recalculated, validation is not None, False)

    @staticmethod
    def _save_data_artifact(gm_id: str, source, team_kpi, player_kpis, validation: KpiValidation | None) -> None:
        round_key = round_key_for(source.match)
        path = (
            PROJECT_ROOT / "backend" / "data" / "jpd-did" / source.match.leagueId
            / source.match.seasonId / source.recording.teamId / round_key / f"{gm_id}_{source.recording.side}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "gmId": gm_id,
            "side": source.recording.side,
            "sourceRecordCount": len(source.records),
            "sourceFingerprint": source.fingerprint,
            "teamKpi": team_kpi.model_dump(mode="json") if team_kpi else None,
            "teamKpi5min": calculate_team_kpi_5min([
                KpiRecord(record_id, record.half, record.halfSeconds, record.seq, record.act, record.res, record.area,
                          record.playerId, record.shootPosX, record.shootPosY, record.shootDspRange, record.isShot,
                          record.createdBy, record.createdAt.isoformat())
                for record_id, record in source.records
            ]) if team_kpi else [],
            "playerKpis": {key: value.model_dump(mode="json") for key, value in (player_kpis or {}).items()},
            "validation": None if validation is None else {
                "passed": validation.passed,
                "legacyDriftAccepted": validation.legacy_drift_accepted,
                "teamDifferences": validation.team_differences,
                "playerDifferences": validation.player_differences,
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
