from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from backend.temporary.build_legacy_import import BuildLegacyImport
from backend.system.system_firestore import BackendError, RequiredUser


JobState = Literal["queued", "preparing", "replacing", "writing", "complete", "failed"]


@dataclass(slots=True)
class LegacyImportJob:
    job_id: str
    state: JobState = "queued"
    matches_total: int = 0
    matches_processed: int = 0
    documents_total: int = 0
    documents_written: int = 0
    records_written: int = 0
    error: str | None = None
    lock: Lock = field(default_factory=Lock)

    def update(self, state: JobState, matches_total: int, matches_processed: int, documents_total: int, documents_written: int) -> None:
        with self.lock:
            self.state = state
            self.matches_total = matches_total
            self.matches_processed = matches_processed
            self.documents_total = documents_total
            self.documents_written = documents_written


class LegacyImportStartResponse(BaseModel):
    status: str
    jobId: str


class LegacyImportJobResponse(BaseModel):
    status: JobState
    jobId: str
    matchesTotal: int
    matchesProcessed: int
    documentsTotal: int
    documentsWritten: int
    recordsWritten: int
    percent: int
    error: str | None = None


router = APIRouter(tags=["legacy-import"])
_jobs: dict[str, LegacyImportJob] = {}
_jobs_lock = Lock()


def _response(job: LegacyImportJob) -> LegacyImportJobResponse:
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
        return LegacyImportJobResponse(
            status=job.state,
            jobId=job.job_id,
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
    except Exception as exc:
        with job.lock:
            job.state = "failed"
            job.error = str(exc) or type(exc).__name__
    finally:
        upload_path.unlink(missing_ok=True)


@router.post("/legacy-import", response_model=LegacyImportStartResponse, status_code=202, summary="Replace SQL dump matches")
async def import_legacy_sql(
    dump: UploadFile = File(...),
    _: RequiredUser = None,
) -> LegacyImportStartResponse:
    if not dump.filename or not dump.filename.lower().endswith((".sql", ".txt")):
        raise BackendError("Upload a .sql dump file", status_code=422, code="legacy_file_invalid")
    with NamedTemporaryFile(delete=False, suffix=".sql") as upload:
        upload.write(await dump.read())
        upload_path = Path(upload.name)
    job = LegacyImportJob(job_id=uuid4().hex)
    with _jobs_lock:
        _jobs[job.job_id] = job
    Thread(target=_run_import, args=(job, upload_path), daemon=True).start()
    return LegacyImportStartResponse(status="started", jobId=job.job_id)


@router.get("/legacy-import/jobs/{job_id}", response_model=LegacyImportJobResponse, summary="Read SQL import progress")
def read_legacy_import_job(job_id: str, _: RequiredUser = None) -> LegacyImportJobResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise BackendError("Import job not found", status_code=404, code="legacy_job_not_found")
    return _response(job)
