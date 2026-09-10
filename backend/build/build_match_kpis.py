from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from backend.system.system_base import MatchKpiDoc, PlayerKpi, RecordingKpi, Side, TeamKpi5Min
from backend.system.system_control import JpdDidData, PROJECT_ROOT, round_key_for, utc_now
from backend.system.system_support import KpiRecord, calculate_kpis, calculate_team_kpi_5min


@dataclass(frozen=True, slots=True)
class BuildMatchKpisResult:
    document: MatchKpiDoc | None
    created: bool
    recalculated: bool
    empty: bool = False


class BuildMatchKpis:
    """Build one team result from raw JPD-DID records.

    This is intentionally idempotent: unchanged raw records return the existing
    document; a changed source fingerprint creates exactly one new KPI version.
    """

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def build_for_raw_change(self, gm_id: str, side: Side) -> BuildMatchKpisResult:
        return self._build(gm_id, side, logic_changed=False)

    def rebuild_for_logic_change(self, gm_id: str, side: Side) -> BuildMatchKpisResult:
        return self._build(gm_id, side, logic_changed=True)

    def _build(self, gm_id: str, side: Side, *, logic_changed: bool) -> BuildMatchKpisResult:
        source = self.data.read_match_kpi_source(gm_id, side)
        if not source.records:
            return BuildMatchKpisResult(document=None, created=False, recalculated=False, empty=True)
        round_key = round_key_for(source.match)
        existing = self.data.get_match_kpi(
            league_id=source.match.leagueId,
            season_id=source.match.seasonId,
            round_key=round_key,
            team_id=source.recording.teamId,
            gm_id=gm_id,
        )
        if existing and existing.sourceFingerprint == source.fingerprint and not logic_changed:
            self._save_data_artifact(existing, round_key)
            return BuildMatchKpisResult(existing, created=False, recalculated=False)

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
            )
            for record_id, record in source.records
        ]
        calculation = calculate_kpis(records)
        now = utc_now()
        document = MatchKpiDoc(
            gmId=gm_id,
            side=side,
            leagueId=source.match.leagueId,
            seasonId=source.match.seasonId,
            matchType=source.match.matchType,
            round=source.match.round,
            stage=source.match.stage,
            group=source.match.group,
            leg=source.match.leg,
            teamId=source.recording.teamId,
            opponentTeamId=source.recording.opponentTeamId,
            sourceRecordCount=len(records),
            sourceFingerprint=source.fingerprint,
            kpiVersion=(existing.kpiVersion + 1) if existing else 1,
            teamKpi=RecordingKpi(**calculation.team_kpi),
            teamKpi5min=[TeamKpi5Min(**snapshot) for snapshot in calculate_team_kpi_5min(records)],
            playerKpis={
                player_id: PlayerKpi(playerId=player_id, **kpi)
                for player_id, kpi in calculation.player_kpis.items()
            },
            status="rating_pending",
            calculatedAt=now,
            updatedAt=now,
        )
        self.data.save_match_kpi(document, round_key=round_key)
        self._save_data_artifact(document, round_key)
        return BuildMatchKpisResult(document, created=existing is None, recalculated=existing is not None)

    @staticmethod
    def _save_data_artifact(document: MatchKpiDoc, round_key: str) -> None:
        path = (
            PROJECT_ROOT / "backend" / "data" / "jpd-did" / document.leagueId
            / document.seasonId / document.teamId / round_key / f"{document.gmId}_{document.side}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
