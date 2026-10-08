from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from backend.check.audit_legacy_firestore_import import LegacyFirestoreImportAudit
from backend.temporary.build_legacy_import import BuildLegacyImport, LegacyMetadataRepair
from backend.system.system_firestore import BackendError, JpdDidData, RequiredUser
from backend.system.system_pipeline_jobs import PipelineJob, pipeline_jobs


JobState = Literal["queued", "preparing", "preflight", "resuming", "replacing", "writing", "cleaning", "complete", "failed"]


@dataclass(slots=True)
class LegacyImportJob:
    state: JobState = "queued"
    matches_total: int = 0
    matches_processed: int = 0
    documents_total: int = 0
    documents_written: int = 0
    records_written: int = 0
    error: str | None = None
    lock: Lock = field(default_factory=Lock)

    def update(
        self,
        state: JobState,
        matches_total: int,
        matches_processed: int,
        documents_total: int,
        documents_written: int,
    ) -> None:
        with self.lock:
            self.state = state
            self.matches_total = matches_total
            self.matches_processed = matches_processed
            self.documents_total = documents_total
            self.documents_written = documents_written


class LegacyImportStatusResponse(BaseModel):
    status: Literal["idle", "queued", "preparing", "preflight", "resuming", "replacing", "writing", "cleaning", "complete", "failed"]
    matchesTotal: int
    matchesProcessed: int
    documentsTotal: int
    documentsWritten: int
    recordsWritten: int
    percent: int
    error: str | None = None


class LegacySnapshotStatusResponse(BaseModel):
    status: Literal["idle", "running", "complete", "failed"]
    season: str
    fromGmId: str | None = None
    toGmId: str | None = None
    limit: int | None = None
    matched: int
    processed: int
    percent: int
    scanned: int
    created: int
    unchanged: int
    failed: int
    ready: int | None = None
    missing: int | None = None
    error: str | None = None


class LegacyVerifyStatusResponse(BaseModel):
    status: Literal["idle", "queued", "running", "complete", "failed"]
    matchesTotal: int
    matchesProcessed: int
    documentsTotal: int
    documentsCompared: int
    documentsActual: int | None = None
    percent: int
    passed: bool | None = None
    diagnostics: int = 0
    categoryCounts: dict[str, int] = Field(default_factory=dict)
    examples: list[dict[str, object]] = Field(default_factory=list)
    reportPath: str | None = None
    error: str | None = None


class LegacyMetadataRepairStatusResponse(BaseModel):
    status: Literal["idle", "queued", "running", "complete", "failed"]
    targetsTotal: int
    targetsProcessed: int
    percent: int
    leaguesConfigured: int = 0
    leaguesDeleted: int = 0
    teamSeasonsMerged: int = 0
    teamSeasonsDeleted: int = 0
    teamsRestored: int = 0
    stadiumHomeTeamsNormalized: int = 0
    teamStadiumsRestored: int = 0
    contractsNormalized: int = 0
    contractsDeleted: int = 0
    contractConflicts: int = 0
    contractCompetitionTypesAssigned: int = 0
    error: str | None = None


@dataclass(slots=True)
class LegacySnapshotBackfillJob:
    season: str
    from_gm_id: str | None
    to_gm_id: str | None
    limit: int | None
    state: Literal["running", "complete", "failed"] = "running"
    counts: dict[str, int] = field(default_factory=lambda: {
        "matched": 0, "processed": 0, "scanned": 0, "created": 0, "unchanged": 0, "failed": 0,
    })
    ready: int | None = None
    missing: int | None = None
    error: str | None = None
    lock: Lock = field(default_factory=Lock)


@dataclass(slots=True)
class LegacyVerifyJob:
    state: Literal["queued", "running", "complete", "failed"] = "queued"
    matches_total: int = 0
    matches_processed: int = 0
    documents_total: int = 0
    documents_compared: int = 0
    summary: dict[str, object] = field(default_factory=dict)
    examples: list[dict[str, object]] = field(default_factory=list)
    report_path: str | None = None
    error: str | None = None
    lock: Lock = field(default_factory=Lock)


router = APIRouter(tags=["legacy-import"])
logger = logging.getLogger(__name__)
_import_job: LegacyImportJob | None = None
_import_job_lock = Lock()
_snapshot_jobs: dict[str, LegacySnapshotBackfillJob] = {}
_snapshot_jobs_lock = Lock()
_verify_job: LegacyVerifyJob | None = None
_verify_job_lock = Lock()


def _import_response(job: LegacyImportJob | None) -> LegacyImportStatusResponse:
    if job is None:
        return LegacyImportStatusResponse(
            status="idle", matchesTotal=0, matchesProcessed=0,
            documentsTotal=0, documentsWritten=0, recordsWritten=0, percent=0,
        )
    with job.lock:
        if job.state == "complete":
            percent = 100
        elif job.state == "replacing" and job.matches_total:
            percent = min(40, 5 + int((job.matches_processed / job.matches_total) * 35))
        elif job.state == "preparing":
            percent = 3
        elif job.documents_total:
            percent = min(99, int((job.documents_written / job.documents_total) * 100))
        elif job.matches_total:
            percent = min(35, int((job.matches_processed / job.matches_total) * 35))
        else:
            percent = 0
        return LegacyImportStatusResponse(
            status=job.state,
            matchesTotal=job.matches_total,
            matchesProcessed=job.matches_processed,
            documentsTotal=job.documents_total,
            documentsWritten=job.documents_written,
            recordsWritten=job.records_written,
            percent=percent,
            error=job.error,
        )


def _run_import(job: LegacyImportJob, upload_path: Path) -> None:
    try:
        logger.info("SQL legacy import started")
        sql = upload_path.read_text(encoding="utf-8-sig")

        def progress(state: str, matches_total: int, matches_processed: int, documents_total: int, documents_written: int) -> None:
            job.update(state, matches_total, matches_processed, documents_total, documents_written)

        result = BuildLegacyImport().import_sql(sql, on_progress=progress)
        with job.lock:
            job.state = "complete"
            job.matches_total = result.matches_replaced
            job.matches_processed = result.matches_replaced
            job.documents_total = result.documents_written
            job.documents_written = result.documents_written
            job.records_written = result.records_written
        logger.info("SQL legacy import complete: matches=%s documents=%s records=%s", result.matches_replaced, result.documents_written, result.records_written)
    except Exception as exc:
        logger.exception("SQL legacy import failed")
        with job.lock:
            job.state = "failed"
            job.error = str(exc) or type(exc).__name__
    finally:
        upload_path.unlink(missing_ok=True)


def _snapshot_response(job: LegacySnapshotBackfillJob) -> LegacySnapshotStatusResponse:
    with job.lock:
        counts = dict(job.counts)
        if job.state == "complete":
            percent = 100
        elif counts["matched"]:
            percent = min(99, int((counts["processed"] / counts["matched"]) * 100))
        else:
            percent = 0
        return LegacySnapshotStatusResponse(
            status=job.state,
            season=job.season,
            fromGmId=job.from_gm_id,
            toGmId=job.to_gm_id,
            limit=job.limit,
            matched=counts["matched"],
            processed=counts["processed"],
            percent=percent,
            scanned=counts["scanned"],
            created=counts["created"],
            unchanged=counts["unchanged"],
            failed=counts["failed"],
            ready=job.ready,
            missing=job.missing,
            error=job.error,
        )


def _verify_response(job: LegacyVerifyJob | None) -> LegacyVerifyStatusResponse:
    if job is None:
        return LegacyVerifyStatusResponse(
            status="idle", matchesTotal=0, matchesProcessed=0,
            documentsTotal=0, documentsCompared=0, percent=0,
        )
    with job.lock:
        if job.state == "complete":
            percent = 100
        elif job.documents_total:
            percent = min(99, int((job.documents_compared / job.documents_total) * 100))
        elif job.matches_total:
            percent = min(99, int((job.matches_processed / job.matches_total) * 100))
        else:
            percent = 0
        summary = dict(job.summary)
        category_counts = {
            str(key): int(value)
            for key, value in summary.items()
            if key not in {"matches", "records", "documentsExpected", "documentsCompared", "documentsActual", "passed", "diagnostics"}
            and isinstance(value, int)
        }
        return LegacyVerifyStatusResponse(
            status=job.state,
            matchesTotal=job.matches_total,
            matchesProcessed=job.matches_processed,
            documentsTotal=job.documents_total,
            documentsCompared=job.documents_compared,
            documentsActual=(
                int(summary["documentsActual"])
                if isinstance(summary.get("documentsActual"), int)
                else None
            ),
            percent=percent,
            passed=summary.get("passed") if isinstance(summary.get("passed"), bool) else None,
            diagnostics=int(summary.get("diagnostics", 0)),
            categoryCounts=category_counts,
            examples=list(job.examples),
            reportPath=job.report_path,
            error=job.error,
        )


def _metadata_repair_response(job: PipelineJob | None) -> LegacyMetadataRepairStatusResponse:
    if job is None:
        return LegacyMetadataRepairStatusResponse(
            status="idle", targetsTotal=0, targetsProcessed=0, percent=0,
        )
    snapshot = job.snapshot()
    counts = snapshot["counts"]
    return LegacyMetadataRepairStatusResponse(
        status=str(snapshot["status"]),
        targetsTotal=int(snapshot["targetsTotal"]),
        targetsProcessed=int(snapshot["targetsProcessed"]),
        percent=int(snapshot["percent"]),
        leaguesConfigured=int(counts.get("leaguesConfigured", 0)),
        leaguesDeleted=int(counts.get("leaguesDeleted", 0)),
        teamSeasonsMerged=int(counts.get("teamSeasonsMerged", 0)),
        teamSeasonsDeleted=int(counts.get("teamSeasonsDeleted", 0)),
        teamsRestored=int(counts.get("teamsRestored", 0)),
        stadiumHomeTeamsNormalized=int(counts.get("stadiumHomeTeamsNormalized", 0)),
        teamStadiumsRestored=int(counts.get("teamStadiumsRestored", 0)),
        contractsNormalized=int(counts.get("contractsNormalized", 0)),
        contractsDeleted=int(counts.get("contractsDeleted", 0)),
        contractConflicts=int(counts.get("contractConflicts", 0)),
        contractCompetitionTypesAssigned=int(counts.get("contractCompetitionTypesAssigned", 0)),
        error=snapshot["error"] if isinstance(snapshot["error"], str) else None,
    )


def _run_snapshot_backfill(job: LegacySnapshotBackfillJob) -> None:
    try:
        logger.info(
            "Snapshot backfill started: season=%s from=%s to=%s limit=%s",
            job.season, job.from_gm_id, job.to_gm_id, job.limit,
        )
        def progress(counts: dict[str, int]) -> None:
            with job.lock:
                job.counts = counts

        data = JpdDidData()
        counts = data.backfill_legacy_input_squads(
            season_id=job.season,
            from_gm_id=job.from_gm_id,
            to_gm_id=job.to_gm_id,
            limit=job.limit,
            on_progress=progress,
        )
        summary = data.get_legacy_input_snapshot_status(
            season_id=job.season,
            from_gm_id=job.from_gm_id,
            to_gm_id=job.to_gm_id,
        )
        with job.lock:
            job.counts = counts
            job.ready = summary["ready"]
            job.missing = summary["missing"]
            job.state = "complete"
        logger.info(
            "Snapshot backfill complete: season=%s created=%s unchanged=%s failed=%s missing=%s",
            job.season, counts["created"], counts["unchanged"], counts["failed"], summary["missing"],
        )
    except Exception as exc:
        logger.exception("Snapshot backfill failed: season=%s", job.season)
        with job.lock:
            job.state = "failed"
            job.error = str(exc) or type(exc).__name__


def _run_verify(job: LegacyVerifyJob, upload_path: Path) -> None:
    try:
        logger.info("SQL legacy import verification started")
        sql = upload_path.read_text(encoding="utf-8-sig")

        def progress(values: dict[str, int]) -> None:
            with job.lock:
                job.state = "running"
                job.matches_total = values["matchesTotal"]
                job.matches_processed = values["matchesProcessed"]
                job.documents_total = values["documentsTotal"]
                job.documents_compared = values["documentsCompared"]

        result = LegacyFirestoreImportAudit().verify(sql, on_progress=progress)
        with job.lock:
            job.state = "complete"
            job.summary = dict(result["summary"])
            job.examples = list(result["examples"][:20])
            job.report_path = str(result["reportPath"])
            job.matches_total = int(result["summary"]["matches"])
            job.matches_processed = int(result["summary"]["matches"])
            job.documents_total = int(result["summary"]["documentsExpected"])
            job.documents_compared = int(result["summary"]["documentsCompared"])
        logger.info("SQL legacy import verification complete: matches=%s diagnostics=%s", job.matches_total, result["summary"]["diagnostics"])
    except Exception as exc:
        logger.exception("SQL legacy import verification failed")
        with job.lock:
            job.state = "failed"
            job.error = str(exc) or type(exc).__name__
    finally:
        upload_path.unlink(missing_ok=True)


def _run_metadata_repair(job: PipelineJob) -> None:
    logger.info("Legacy metadata repair started")

    def progress(processed: int, total: int, counts: dict[str, int]) -> None:
        if job.state == "queued":
            job.begin(matches_total=0, targets_total=total)
        job.progress(
            matches_processed=0,
            targets_processed=processed,
            counts=counts,
            errors=[],
        )

    counts = LegacyMetadataRepair().repair(on_progress=progress)
    if job.state == "queued":
        job.begin(matches_total=0, targets_total=0)
    snapshot = job.snapshot()
    job.complete(
        matches_processed=0,
        targets_processed=int(snapshot["targetsTotal"]),
        counts=counts,
        errors=[],
    )
    logger.info("Legacy metadata repair complete: %s", counts)


@router.get("/legacy-import/status", response_model=LegacyImportStatusResponse, summary="Read SQL import status")
def read_legacy_import_status(_: RequiredUser = None) -> LegacyImportStatusResponse:
    with _import_job_lock:
        job = _import_job
    return _import_response(job)


@router.get("/legacy-import/verify-status", response_model=LegacyVerifyStatusResponse, summary="Read SQL import verification status")
def read_legacy_verify_status(_: RequiredUser = None) -> LegacyVerifyStatusResponse:
    with _verify_job_lock:
        job = _verify_job
    return _verify_response(job)


@router.get("/legacy-import/metadata-status", response_model=LegacyMetadataRepairStatusResponse, summary="Read legacy metadata repair status")
def read_legacy_metadata_repair_status(_: RequiredUser = None) -> LegacyMetadataRepairStatusResponse:
    return _metadata_repair_response(pipeline_jobs.get("legacy-metadata-repair"))


@router.get("/legacy-import/snapshot-status/{season}", response_model=LegacySnapshotStatusResponse, summary="Read imported lineup snapshot status")
def read_legacy_snapshot_status(season: str, _: RequiredUser = None) -> LegacySnapshotStatusResponse:
    with _snapshot_jobs_lock:
        job = _snapshot_jobs.get(season)
    if job is not None:
        return _snapshot_response(job)

    counts = JpdDidData().get_legacy_input_snapshot_status(season_id=season)
    return LegacySnapshotStatusResponse(
        status="idle",
        season=season,
        matched=counts["matched"],
        processed=0,
        percent=0,
        scanned=0,
        created=0,
        unchanged=0,
        failed=0,
        ready=counts["ready"],
        missing=counts["missing"],
    )


@router.post("/legacy-import", response_model=LegacyImportStatusResponse, status_code=202, summary="Start SQL dump import")
async def import_legacy_sql(
    dump: UploadFile = File(...),
    _: RequiredUser = None,
) -> LegacyImportStatusResponse:
    if not dump.filename or not dump.filename.lower().endswith((".sql", ".txt")):
        raise BackendError("Upload a .sql dump file", status_code=422, code="legacy_file_invalid")
    with NamedTemporaryFile(delete=False, suffix=".sql") as upload:
        upload.write(await dump.read())
        upload_path = Path(upload.name)
    global _import_job
    with _import_job_lock:
        if _import_job is not None:
            with _import_job.lock:
                if _import_job.state in {"queued", "preparing", "preflight", "resuming", "replacing", "writing", "cleaning"}:
                    upload_path.unlink(missing_ok=True)
                    raise BackendError("SQL legacy import is already running", status_code=409, code="legacy_import_running")
        job = LegacyImportJob()
        _import_job = job
    Thread(target=_run_import, args=(job, upload_path), daemon=True).start()
    return _import_response(job)


@router.post("/legacy-import/verify", response_model=LegacyVerifyStatusResponse, status_code=202, summary="Start SQL import verification")
async def verify_legacy_sql(
    dump: UploadFile = File(...),
    _: RequiredUser = None,
) -> LegacyVerifyStatusResponse:
    if not dump.filename or not dump.filename.lower().endswith((".sql", ".txt")):
        raise BackendError("Upload a .sql dump file", status_code=422, code="legacy_file_invalid")
    with NamedTemporaryFile(delete=False, suffix=".sql") as upload:
        upload.write(await dump.read())
        upload_path = Path(upload.name)
    global _verify_job
    with _verify_job_lock:
        if _verify_job is not None:
            with _verify_job.lock:
                if _verify_job.state in {"queued", "running"}:
                    upload_path.unlink(missing_ok=True)
                    raise BackendError("SQL import verification is already running", status_code=409, code="legacy_verify_running")
        job = LegacyVerifyJob()
        _verify_job = job
    Thread(target=_run_verify, args=(job, upload_path), daemon=True, name="legacy-import-verify").start()
    return _verify_response(job)


@router.post("/legacy-import/metadata-repair", response_model=LegacyMetadataRepairStatusResponse, status_code=202, summary="Start legacy league, team, and contract metadata repair")
def repair_legacy_metadata(_: RequiredUser = None) -> LegacyMetadataRepairStatusResponse:
    job = pipeline_jobs.start("legacy-metadata-repair", {}, _run_metadata_repair)
    if job is None:
        raise BackendError("Legacy metadata repair is already running", status_code=409, code="legacy_metadata_repair_running")
    return _metadata_repair_response(job)


@router.post("/legacy-import/snapshot-backfill", response_model=LegacySnapshotStatusResponse, status_code=202, summary="Backfill fixture roster, schedule summary, and imported lineup snapshots")
def backfill_legacy_snapshots(
    season: str = Form(..., min_length=1, max_length=32),
    from_gm_id: str | None = Form(default=None, max_length=128),
    to_gm_id: str | None = Form(default=None, max_length=128),
    limit: int | None = Form(default=None, ge=1, le=10_000),
    _: RequiredUser = None,
) -> LegacySnapshotStatusResponse:
    with _snapshot_jobs_lock:
        current = _snapshot_jobs.get(season)
        if current is not None and current.state == "running":
            raise BackendError("Snapshot backfill is already running for this season", status_code=409, code="snapshot_backfill_running")
        job = LegacySnapshotBackfillJob(
            season=season,
            from_gm_id=from_gm_id or None,
            to_gm_id=to_gm_id or None,
            limit=limit,
        )
        _snapshot_jobs[season] = job
    Thread(target=_run_snapshot_backfill, args=(job,), daemon=True, name=f"snapshot-backfill-{season}").start()
    return _snapshot_response(job)
