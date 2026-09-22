from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.system.system_firestore import BackendError, JpdDidData, NotFoundError, PROJECT_ROOT, round_key_for
from backend.system.system_schema import Side


@dataclass(frozen=True, slots=True)
class LegacyRawAuditItem:
    gmId: str
    league: str
    season: str
    round: int | None
    status: str
    issues: list[str]
    expectedHomeTeamId: str
    expectedAwayTeamId: str
    homeRecordingTeamId: str | None
    awayRecordingTeamId: str | None
    homeRecordCount: int | None
    awayRecordCount: int | None
    sharedRecordCount: int | None


@dataclass(frozen=True, slots=True)
class LegacyRawAuditResult:
    total: int
    normal: int
    needsReview: int
    jsonPath: str
    csvPath: str


class BuildAuditLegacyRaw:
    """One-time, read-only audit for SQL-to-Firestore raw-record migration."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def audit(
        self,
        *,
        league: str | None = None,
        season: str | None = None,
        round_number: int | None = None,
    ) -> LegacyRawAuditResult:
        items = [
            self._audit_match(gm_id, match)
            for gm_id, match in self.data.list_matches(
                league_id=league,
                season_id=season,
                round_number=round_number,
            )
        ]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = PROJECT_ROOT / "backend" / "data" / "audit"
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / f"legacy_raw_{timestamp}.json"
        csv_path = directory / f"legacy_raw_{timestamp}.csv"
        payload = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "scope": {"league": league, "season": season, "round": round_number},
            "summary": {
                "total": len(items),
                "normal": sum(item.status == "normal" for item in items),
                "needsReview": sum(item.status != "normal" for item in items),
            },
            "matches": [asdict(item) for item in items],
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_csv(csv_path, items)
        return LegacyRawAuditResult(
            total=payload["summary"]["total"],
            normal=payload["summary"]["normal"],
            needsReview=payload["summary"]["needsReview"],
            jsonPath=str(json_path),
            csvPath=str(csv_path),
        )

    def inspect(self, gm_ids: list[str]) -> list[LegacyRawAuditItem]:
        """Read selected matches without creating an audit report file."""
        def inspect_one(gm_id: str) -> LegacyRawAuditItem:
            return self._audit_match(gm_id, self.data.get_match(gm_id))

        with ThreadPoolExecutor(max_workers=min(8, len(gm_ids) or 1)) as executor:
            return list(executor.map(inspect_one, gm_ids))

    def _audit_match(self, gm_id: str, match) -> LegacyRawAuditItem:
        issues: list[str] = []
        try:
            home = self.data.get_recording(gm_id, "H")
        except NotFoundError:
            home = None
            issues.append("home_recording_missing")
        except BackendError as exc:
            home = None
            issues.append(f"home_recording_read_failed:{exc.code}")
        try:
            away = self.data.get_recording(gm_id, "A")
        except NotFoundError:
            away = None
            issues.append("away_recording_missing")
        except BackendError as exc:
            away = None
            issues.append(f"away_recording_read_failed:{exc.code}")

        home_ids: set[str] | None = None
        away_ids: set[str] | None = None
        try:
            recordings = self.data.db.collection("matches").document(gm_id).collection("recordings")
            if home:
                home_ids = {reference.id for reference in recordings.document("H").collection("records").list_documents()}
            if away:
                away_ids = {reference.id for reference in recordings.document("A").collection("records").list_documents()}
        except BackendError as exc:
            issues.append(f"raw_read_failed:{exc.code}")

        if home and (home.side != "H" or home.teamId != match.homeTeamId or home.opponentTeamId != match.awayTeamId):
            issues.append("home_metadata_mismatch")
        if away and (away.side != "A" or away.teamId != match.awayTeamId or away.opponentTeamId != match.homeTeamId):
            issues.append("away_metadata_mismatch")
        if home_ids is not None and not home_ids:
            issues.append("home_raw_missing")
        if away_ids is not None and not away_ids:
            issues.append("away_raw_missing")

        shared = (home_ids & away_ids) if home_ids is not None and away_ids is not None else None
        if shared:
            issues.append("home_away_raw_duplicate")
        status = "normal" if not issues else self._status_for(issues)
        return LegacyRawAuditItem(
            gmId=gm_id,
            league=match.leagueId,
            season=match.seasonId,
            round=match.round,
            status=status,
            issues=issues,
            expectedHomeTeamId=match.homeTeamId,
            expectedAwayTeamId=match.awayTeamId,
            homeRecordingTeamId=home.teamId if home else None,
            awayRecordingTeamId=away.teamId if away else None,
            homeRecordCount=len(home_ids) if home_ids is not None else None,
            awayRecordCount=len(away_ids) if away_ids is not None else None,
            sharedRecordCount=len(shared) if shared is not None else None,
        )

    @staticmethod
    def _status_for(issues: list[str]) -> str:
        if "home_away_raw_duplicate" in issues:
            return "raw_duplicate"
        if "home_raw_missing" in issues or "away_raw_missing" in issues:
            return "raw_missing"
        if "home_recording_missing" in issues or "away_recording_missing" in issues:
            return "recording_missing"
        if "home_metadata_mismatch" in issues or "away_metadata_mismatch" in issues:
            return "metadata_mismatch"
        return "raw_read_failed"

    @staticmethod
    def _write_csv(path, items: list[LegacyRawAuditItem]) -> None:
        fieldnames = list(LegacyRawAuditItem.__dataclass_fields__)
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for item in items:
                row = asdict(item)
                row["issues"] = ",".join(row["issues"])
                writer.writerow(row)


class BuildMatchRaw:
    """One-time raw export used while validating a legacy migration."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def export(self, gm_id: str, side: Side) -> Path:
        source = self.data.read_match_kpi_source(gm_id, side)
        path = (
            PROJECT_ROOT / "backend" / "data" / "jpd-did" / source.match.leagueId
            / source.match.seasonId / source.recording.teamId / round_key_for(source.match)
            / f"{gm_id}_{side}_raw.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "gmId": gm_id,
            "side": side,
            "teamId": source.recording.teamId,
            "opponentTeamId": source.recording.opponentTeamId,
            "recordCount": len(source.records),
            "records": [
                {"id": record_id, **record.model_dump(mode="json", exclude_none=False)}
                for record_id, record in source.records
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
