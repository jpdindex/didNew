from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from backend.system.system_schema import PlayerKpi, TeamRatingSnapshot
from backend.system.system_firestore import BackendError, JpdDidData, get_settings, utc_now
from backend.system.system_kpi_rules import KpiRecord, calculate_team_kpi_5min


PLAYER_KPI_FIELDS = tuple(PlayerKpi.model_fields)
@dataclass(frozen=True, slots=True)
class BuildMatchRatingResult:
    gm_id: str
    side: str
    kpi_version: int
    player_ratings_saved: int
    unchanged: bool = False


class BuildMatchRating:
    """Send an already-built recording KPI to JPD-RATING and persist its response."""

    def __init__(self, data: JpdDidData | None = None) -> None:
        self.data = data or JpdDidData()

    def build(self, gm_id: str, side: str, *, force: bool = False) -> BuildMatchRatingResult:
        source = self.data.read_match_kpi_source(gm_id, side)
        if not source.records:
            raise BackendError(
                message=f"Raw records are missing for rating: {gm_id}/{side}",
                status_code=409,
                code="rating_raw_missing",
            )
        if source.recording.kpi is None or source.recording.kpiVersion < 1:
            raise BackendError(
                message=f"KPI must be built before rating: {gm_id}/{side}",
                status_code=409,
                code="rating_kpi_missing",
            )
        if source.recording.kpiSourceFingerprint != source.fingerprint:
            raise BackendError(
                message=f"KPI must be rebuilt from current raw before rating: {gm_id}/{side}",
                status_code=409,
                code="rating_kpi_stale",
            )
        if (
            not force
            and source.recording.ratingBasedOn == source.recording.kpiVersion
            and source.recording.teamRating is not None
        ):
            return BuildMatchRatingResult(gm_id, side, source.recording.kpiVersion, 0, unchanged=True)

        player_stats = self.data.list_player_stats(gm_id, side)
        request_body = {
            "gm_id": gm_id,
            "side": side,
            "leagueId": source.match.leagueId,
            "seasonId": source.match.seasonId,
            "matchType": source.match.matchType,
            "round": source.match.round,
            "teamId": source.recording.teamId,
            "opponentTeamId": source.recording.opponentTeamId,
            "kpiVersion": source.recording.kpiVersion,
            "teamKpi": source.recording.kpi.model_dump(mode="json"),
            "teamKpi5min": calculate_team_kpi_5min([
                KpiRecord(
                    record_id, record.half, record.halfSeconds, record.seq,
                    record.act, record.res, record.area, record.playerId,
                    record.shootPosX, record.shootPosY, record.shootDspRange,
                    record.isShot, record.createdBy, record.createdAt.isoformat(),
                )
                for record_id, record in source.records
            ], {
                half: timing.seconds
                for half, timing in source.recording.halves.items()
            }),
            "playerKpis": [
                {field: stat.get(field, 0) for field in PLAYER_KPI_FIELDS}
                for stat in player_stats.values()
            ],
        }
        self._remove_null_match_fields(request_body)
        response_body = self._request_rating(request_body)
        team_rating = self._parse_team_rating(response_body)
        rating_based_on = response_body.get("ratingBasedOn", source.recording.kpiVersion)
        if rating_based_on != source.recording.kpiVersion:
            raise BackendError(
                message=f"JPD-RATING returned an unexpected KPI version for {gm_id}/{side}",
                status_code=502,
                code="rating_version_mismatch",
            )

        current = self.data.read_match_kpi_source(gm_id, side)
        if (
            current.recording.kpiVersion != source.recording.kpiVersion
            or current.fingerprint != source.fingerprint
        ):
            raise BackendError(
                message=f"KPI changed while rating was calculated: {gm_id}/{side}",
                status_code=409,
                code="rating_source_changed",
            )
        player_ratings = response_body.get("playerRatings", [])
        if not isinstance(player_ratings, list):
            raise BackendError("JPD-RATING playerRatings must be a list", status_code=502, code="rating_response_invalid")
        saved = self.data.save_recording_rating(
            gm_id,
            side,
            team_rating=team_rating,
            rating_based_on=rating_based_on,
            player_ratings=player_ratings,
            calculated_at=utc_now(),
        )
        return BuildMatchRatingResult(gm_id, side, source.recording.kpiVersion, saved)

    @staticmethod
    def _remove_null_match_fields(body: dict[str, Any]) -> None:
        for key in ("round", "stage", "group", "leg"):
            if body.get(key) is None:
                body.pop(key, None)

    @staticmethod
    def _parse_team_rating(body: dict[str, Any]) -> TeamRatingSnapshot:
        team_rating = body.get("teamRating")
        if not isinstance(team_rating, dict):
            raise BackendError("JPD-RATING teamRating is missing", status_code=502, code="rating_response_invalid")
        try:
            return TeamRatingSnapshot.model_validate(team_rating)
        except Exception as exc:
            raise BackendError("JPD-RATING teamRating is invalid", status_code=502, code="rating_response_invalid") from exc

    @staticmethod
    def _request_rating(body: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        if not settings.jpd_rating_token:
            raise BackendError("JPD_RATING_TOKEN is not configured", status_code=503, code="rating_token_missing")
        url = f"{settings.jpd_rating_base_url.rstrip('/')}/v1/ratings/calculate"
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {settings.jpd_rating_token}"},
                json=body,
                timeout=60,
            )
        except httpx.HTTPError as exc:
            raise BackendError("JPD-RATING request failed", status_code=503, code="rating_unavailable") from exc
        if response.is_error:
            raise BackendError(
                message=f"JPD-RATING rejected the KPI request ({response.status_code})",
                status_code=502,
                code="rating_request_rejected",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendError("JPD-RATING returned invalid JSON", status_code=502, code="rating_response_invalid") from exc
        if not isinstance(payload, dict):
            raise BackendError("JPD-RATING response must be an object", status_code=502, code="rating_response_invalid")
        return payload
