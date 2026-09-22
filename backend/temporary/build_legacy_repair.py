from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.build.build_match_kpis import BuildMatchKpis, BuildMatchKpisResult
from backend.system.system_firestore import BackendError, JpdDidData, utc_now


@dataclass(frozen=True, slots=True)
class RepairLegacyRawResult:
    gm_id: str
    h_duplicate_records_removed: int
    h_paths_removed: int
    player_stats_removed: int
    match_kpis_documents_removed: int
    home_kpi: BuildMatchKpisResult
    away_kpi: BuildMatchKpisResult


class BuildRepairLegacyRaw:
    """One-time repair for legacy imports where A records were copied into H."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def repair(self, gm_id: str, *, purge_match_kpis: bool = True) -> RepairLegacyRawResult:
        match = self.data.get_match(gm_id)
        home_records = self.data.list_records(gm_id, "H")
        away_records = self.data.list_records(gm_id, "A")
        home_ids = {record_id for record_id, _ in home_records}
        away_ids = {record_id for record_id, _ in away_records}
        duplicate_ids = home_ids & away_ids

        if duplicate_ids and not away_ids <= home_ids:
            raise BackendError(
                message=f"H/A raw records are not a complete copied set for legacy repair: {gm_id}",
                status_code=409,
                code="legacy_repair_conflict",
            )

        recording_root = self.data.db.collection("matches").document(gm_id).collection("recordings")
        home_ref = recording_root.document("H")
        away_ref = recording_root.document("A")

        # An empty overlap means the raw half of this repair has already run;
        # derived metadata can then be rebuilt safely and idempotently.
        self._delete_references(home_ref.collection("records").document(record_id) for record_id in duplicate_ids)
        h_paths_removed = self._delete_document_trees(home_ref.collection("paths").list_documents())
        player_stats_removed = self._delete_document_trees(home_ref.collection("playerStats").list_documents())
        player_stats_removed += self._delete_document_trees(away_ref.collection("playerStats").list_documents())

        # Rebuild the home playerStats even when its team KPI already has the
        # same raw fingerprint from an earlier partial repair.
        home_ref.update({"kpiSourceFingerprint": None, "updatedAt": utc_now()})

        # The old import copied the home recording metadata onto A as well.
        away_ref.update({
            "side": "A",
            "teamId": match.awayTeamId,
            "opponentTeamId": match.homeTeamId,
            "kpi": None,
            "kpiComputedAt": None,
            "kpiVersion": 0,
            "kpiSourceFingerprint": None,
            "teamRating": None,
            "ratingBasedOn": None,
            "updatedAt": utc_now(),
        })

        builder = BuildMatchKpis(self.data)
        home_kpi = builder.build_for_raw_change(gm_id, "H")
        away_kpi = builder.build_for_raw_change(gm_id, "A")
        if home_kpi.validation_failed or away_kpi.validation_failed:
            raise BackendError(
                message=f"KPI validation failed after raw repair: {gm_id}",
                status_code=409,
                code="legacy_repair_validation_failed",
            )

        match_kpis_documents_removed = self._purge_collection("matchKpis") if purge_match_kpis else 0
        return RepairLegacyRawResult(
            gm_id=gm_id,
            h_duplicate_records_removed=len(duplicate_ids),
            h_paths_removed=h_paths_removed,
            player_stats_removed=player_stats_removed,
            match_kpis_documents_removed=match_kpis_documents_removed,
            home_kpi=home_kpi,
            away_kpi=away_kpi,
        )

    def _purge_collection(self, collection_name: str) -> int:
        return self._delete_document_trees(self.data.db.collection(collection_name).list_documents())

    def _delete_document_trees(self, references: Iterable) -> int:
        descendants = []
        for reference in references:
            descendants.extend(self._document_tree_bottom_up(reference))
        self._delete_references(descendants)
        return len(descendants)

    def _document_tree_bottom_up(self, reference) -> list:
        descendants = []
        for collection in reference.collections():
            for child in collection.list_documents():
                descendants.extend(self._document_tree_bottom_up(child))
        descendants.append(reference)
        return descendants

    def _delete_references(self, references: Iterable) -> None:
        references = list(references)
        for start in range(0, len(references), 400):
            batch = self.data.db.batch()
            for reference in references[start:start + 400]:
                batch.delete(reference)
            batch.commit(retry=None, timeout=30)
