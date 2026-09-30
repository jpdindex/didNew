from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock, Thread
from typing import Any, Callable, Literal

from backend.system.system_firestore import utc_now


PipelineJobState = Literal["idle", "queued", "running", "complete", "failed"]


@dataclass(slots=True)
class PipelineJob:
    """In-process state for one administrative pipeline run."""

    pipeline: str
    scope: dict[str, Any]
    state: PipelineJobState = "queued"
    matches_total: int = 0
    matches_processed: int = 0
    targets_total: int = 0
    targets_processed: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    lock: Lock = field(default_factory=Lock)

    def begin(self, *, matches_total: int, targets_total: int) -> None:
        with self.lock:
            self.state = "running"
            self.matches_total = matches_total
            self.targets_total = targets_total
            self.started_at = utc_now()

    def progress(self, *, matches_processed: int, targets_processed: int, counts: dict[str, int], errors: list[str]) -> None:
        with self.lock:
            self.matches_processed = matches_processed
            self.targets_processed = targets_processed
            self.counts = dict(counts)
            self.errors = list(errors)

    def complete(self, *, matches_processed: int, targets_processed: int, counts: dict[str, int], errors: list[str]) -> None:
        with self.lock:
            self.state = "complete"
            self.matches_processed = matches_processed
            self.targets_processed = targets_processed
            self.counts = dict(counts)
            self.errors = list(errors)
            self.completed_at = utc_now()

    def fail(self, exc: Exception) -> None:
        with self.lock:
            self.state = "failed"
            self.error = str(exc) or type(exc).__name__
            self.completed_at = utc_now()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            if self.state == "complete":
                percent = 100
            elif self.targets_total:
                percent = min(99, int((self.targets_processed / self.targets_total) * 100))
            else:
                percent = 0
            return {
                "status": self.state,
                "scope": dict(self.scope),
                "matchesTotal": self.matches_total,
                "matchesProcessed": self.matches_processed,
                "targetsTotal": self.targets_total,
                "targetsProcessed": self.targets_processed,
                "percent": percent,
                "counts": dict(self.counts),
                "errors": list(self.errors),
                "error": self.error,
                "startedAt": self.started_at,
                "completedAt": self.completed_at,
            }


class PipelineJobRegistry:
    """Keeps the latest run per pipeline and prevents duplicate active runs."""

    def __init__(self) -> None:
        self._jobs: dict[str, PipelineJob] = {}
        self._lock = Lock()

    def get(self, pipeline: str) -> PipelineJob | None:
        with self._lock:
            return self._jobs.get(pipeline)

    def start(self, pipeline: str, scope: dict[str, Any], run: Callable[[PipelineJob], None]) -> PipelineJob | None:
        with self._lock:
            current = self._jobs.get(pipeline)
            if current is not None:
                with current.lock:
                    if current.state in {"queued", "running"}:
                        return None
            job = PipelineJob(pipeline=pipeline, scope=scope)
            self._jobs[pipeline] = job

        def target() -> None:
            try:
                run(job)
            except Exception as exc:
                job.fail(exc)

        Thread(target=target, daemon=True, name=f"did-{pipeline}-pipeline").start()
        return job


pipeline_jobs = PipelineJobRegistry()
