from __future__ import annotations
import json
import httpx
from dataclasses import dataclass
from typing import Any
from hashlib import sha256
from backend.system.system_schema import PlayerKpi, TeamRatingSnapshot
from backend.system.system_firestore import BackendError, JpdDidData, get_settings, utc_now
from backend.system.system_kpi_rules import (
    KpiRecord,
    calculate_kpis,
    calculate_team_kpi_5min,
    reviewed_records,
    approved_path_difference,
)

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
        records = [
            KpiRecord(
                record_id, record.half, record.halfSeconds, record.seq,
                record.act, record.res, record.area, record.playerId,
                record.shootPosX, record.shootPosY, record.shootDspRange,
                record.isShot, record.createdBy, record.createdAt.isoformat(),
                legacy_path_id=record.legacyPathId, legacy_path_type=record.legacyPathType,
                legacy_path_ttp=record.legacyPathTtp, legacy_flags=record.legacyKpiFlags,
                bap_reason=record.bapReason,
            ) for record_id, record in source.records
        ]
        all_records = records
        normal_records = [r for r in all_records if r.half in ('H1','H2')]
        records = reviewed_records(gm_id, side, normal_records)
        team_kpi = source.recording.kpi.model_dump(mode="json")
        raw_kpi = calculate_kpis(records).team_kpi
        imported = bool(getattr(source.recording, 'legacyGiId', None)) and all(r.legacy_flags is not None for r in records)
        before = calculate_kpis(normal_records).team_kpi if len(records) != len(normal_records) else raw_kpi
        full = calculate_kpis(all_records).team_kpi if len(all_records) != len(normal_records) else before
        def reviewed_import_difference(field, value):
            return imported and (
                (len(records) != len(normal_records) and abs(value-before[field]) < 1e-6)
                or (len(all_records) != len(normal_records) and abs(value-full[field]) < 1e-6)
                or approved_path_difference(gm_id, side, field, value, raw_kpi[field])
                or (field == 'OG' and value == 0 and raw_kpi['OG'] > 0
                    and all(abs(team_kpi[k]-raw_kpi[k]) < 1e-6 for k in ('GOAL','SHOT','SSR'))))
        if (any(field not in team_kpi for field in ("GOAL", "OG", "SHOT", "SSR"))
                or any(abs(value - raw_kpi[field]) >= 1e-6 and not reviewed_import_difference(field, value)
                       for field, value in team_kpi.items() if field in raw_kpi)):
            raise BackendError(
                message=f"Stored KPI differs from reviewed RAW; rebuild KPI before rating: {gm_id}/{side}",
                status_code=409, code="rating_kpi_definition_mismatch",
            )
        if imported:
            # Import deliberately skips KPI POST. Correct only the request,
            # preserving stored KPI/version and validating every other delta.
            team_kpi = raw_kpi
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
            "teamKpi": team_kpi,
            "teamKpi5min": calculate_team_kpi_5min(records, {
                half: timing.seconds
                for half, timing in source.recording.halves.items()
            }),
            "playerKpis": [
                {field: stat.get(field, 0) for field in PLAYER_KPI_FIELDS}
                for _, stat in sorted(player_stats.items())
            ],
        }
        self._remove_null_match_fields(request_body)
        snapshots = request_body['teamKpi5min']
        if len(snapshots) != 20 or any(
                abs(snapshots[-1][field] - raw_kpi[field]) >= 1e-6
                for field in ('DAP','DTP','SHOT','SSR','GOAL')):
            raise BackendError('Final five-minute rating input differs from RAW KPI',
                               status_code=409, code='rating_interval_kpi_mismatch')
        input_fingerprint = sha256(json.dumps(
            {'policy': 'finalized-raw-flags-own-exclusive-ssr-confirmed-dtp-v3', 'request': request_body},
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if (not force and source.recording.ratingBasedOn == source.recording.kpiVersion
                and source.recording.teamRating is not None
                and getattr(source.recording, 'ratingInputFingerprint', None) == input_fingerprint):
            return BuildMatchRatingResult(gm_id, side, source.recording.kpiVersion, 0, unchanged=True)
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
            input_fingerprint=input_fingerprint,
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
