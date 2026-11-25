"""
The ingestion work queue, kept in Postgres (the `ingestion_jobs` table).

    enqueue_file(path)   -> store the original, create a "queued" job      (fast)
    claim_job()          -> a worker takes the next queued job             (safe with many workers)
    retry_later(...)     -> put a failed job back in the queue, with a delay
    recover_interrupted_jobs() -> re-queue jobs whose worker died

Why Postgres and not Celery: the queue survives restarts, every job's state is in
one table the UI already reads, and `SELECT ... FOR UPDATE SKIP LOCKED` lets several
workers take jobs without ever taking the same one. No extra service to run.

Job status:  queued -> running -> done | failed | cancelled
             running -> queued again (retry after a temporary error, or worker died)
"""

import hashlib
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select

from app.db.models import Document, IngestionJob
from app.db.session import SessionLocal
from app.ingestion.parsers import file_format
from app.storage.object_store import get_object_store

log = logging.getLogger(__name__)

WORKER = f"{socket.gethostname()}:{os.getpid()}"  # identifies this process on the job
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)  # a job on another machine with no progress for this long is dead


@dataclass
class Enqueued:
    doc_id: str
    # "queued"         new or changed file, waiting for a worker
    # "unchanged"      this file is already indexed with the same content
    # "already queued" this file is already waiting / being processed
    # "duplicate"      another file with exactly the same content exists (kept once)
    outcome: str


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def worker_alive(worker: str | None) -> bool:
    """Is the process that owns a job still running? (Checkable only on this machine.)"""
    if not worker:
        return False
    host, _, pid = worker.rpartition(":")
    if host != socket.gethostname():
        return True  # another machine: the caller also checks how long the job has been silent
    try:
        os.kill(int(pid), 0)  # signal 0 = "does it exist?", sends nothing
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def _skip_reason(existing: Document, source_name: str) -> str:
    if existing.source_name != source_name:
        return "duplicate"
    return "unchanged" if existing.status == "indexed" else "already queued"


def _forget_earlier_attempts(session, source_name: str, content_hash: str) -> None:
    """Queuing a file that failed or was stopped before: drop the old attempt's record,
    so the library shows one row per file. (Nothing of it is in the search index.)"""
    earlier = session.scalars(select(Document).where(
        Document.source_name == source_name, Document.content_hash == content_hash,
        Document.status.in_(["failed", "cancelled"]),
    ))
    for old in earlier.all():
        get_object_store().delete(old.storage_key)
        session.delete(old)


def enqueue_file(path: Path, source_name: str | None = None) -> Enqueued:
    """Store the original file and queue it for ingestion.

    `source_name` identifies the file across versions: a newer file with the same
    source name replaces the older one once it is indexed. Defaults to the file name;
    bulk ingest passes the path inside the folder (e.g. "hr/leave-policy.pdf").
    """
    content_hash = file_hash(path)
    source_name = source_name or path.name
    with SessionLocal() as session:
        same_content = session.scalar(select(Document).where(
            Document.content_hash == content_hash, Document.status.in_(["indexed", "pending"])
        ))
        if same_content:
            outcome = _skip_reason(same_content, source_name)
            log.info("skip (%s): %s", outcome, source_name)
            return Enqueued(same_content.id, outcome)

        _forget_earlier_attempts(session, source_name, content_hash)
        doc = Document(
            title=path.stem, source_name=source_name, storage_key="",
            file_format=file_format(path), content_hash=content_hash, status="pending",
            source_modified_at=datetime.fromtimestamp(path.stat().st_mtime),
        )
        session.add(doc)
        session.flush()  # assigns doc.id
        doc.storage_key = f"originals/{doc.id}{path.suffix.lower()}"
        get_object_store().upload_file(path, doc.storage_key)

        session.add(IngestionJob(document_id=doc.id, stage="queued", status="queued"))
        session.commit()
        return Enqueued(doc.id, "queued")


def claim_job(job_id: str | None = None) -> str | None:
    """Mark the next queued job (or the given one) as running by this process."""
    with SessionLocal() as session:
        query = select(IngestionJob).where(IngestionJob.status == "queued")
        if job_id:
            query = query.where(IngestionJob.id == job_id)
        else:
            query = query.where(or_(IngestionJob.run_after.is_(None),
                                    IngestionJob.run_after <= func.now()))
        # SKIP LOCKED: if another worker is claiming a row right now, skip it instead of waiting.
        job = session.scalar(
            query.order_by(IngestionJob.created_at).limit(1).with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status, job.worker, job.attempts = "running", WORKER, job.attempts + 1
        job.started_at, job.run_after, job.cancel_requested = func.now(), None, False
        session.commit()
        return job.id


def retry_later(job: IngestionJob, error: str) -> bool:
    """Put a job back in the queue after a temporary error. False if out of attempts.

    Waits 1, 2, 4 ... minutes between attempts. The caller commits.
    """
    if job.attempts >= MAX_ATTEMPTS:
        return False
    job.status, job.stage, job.progress, job.error = "queued", "queued", None, error
    job.run_after = func.now() + timedelta(minutes=2 ** (job.attempts - 1))
    return True


def _is_dead(job: IngestionJob, now) -> bool:
    silent_too_long = job.updated_at is not None and now - job.updated_at > STALE_AFTER
    return not worker_alive(job.worker) or silent_too_long


def recover_interrupted_jobs(clean_up) -> tuple[int, int]:
    """Re-queue jobs whose worker died (crash, restart, Ctrl+C). Run at startup.

    `clean_up(session, doc)` removes what the dead worker had half-indexed.
    Returns (re-queued, failed for good).
    """
    requeued = failed = 0
    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        running = session.scalars(select(IngestionJob).where(IngestionJob.status == "running"))
        for job in running.all():
            doc = session.get(Document, job.document_id)
            if doc is None or not _is_dead(job, now.replace(tzinfo=None)):
                continue
            clean_up(session, doc)
            message = f"Interrupted at stage '{job.stage}' (the worker stopped)."
            if retry_later(job, message):
                requeued += 1
            else:
                doc.status = job.status = "failed"
                doc.error = job.error = message
                failed += 1
        session.commit()
    return requeued, failed


def retry_failed() -> int:
    """Queue every failed document again (e.g. after fixing a parser or an API key)."""
    with SessionLocal() as session:
        jobs = session.scalars(select(IngestionJob).where(IngestionJob.status == "failed")).all()
        for job in jobs:
            doc = session.get(Document, job.document_id)
            doc.status, doc.error = "pending", None
            job.status, job.stage, job.attempts, job.run_after = "queued", "queued", 0, None
        session.commit()
        return len(jobs)


def queued_count() -> int:
    """Jobs still waiting (including retries waiting for their delay)."""
    with SessionLocal() as session:
        return session.scalar(select(func.count()).where(IngestionJob.status == "queued"))


def queue_summary() -> dict:
    """Counts per job status, plus speed: documents done in the last hour, average time."""
    with SessionLocal() as session:
        counts = dict(session.execute(
            select(IngestionJob.status, func.count()).group_by(IngestionJob.status)
        ).all())
        done_last_hour, avg_seconds = session.execute(
            select(func.count(), func.avg(func.extract(
                "epoch", IngestionJob.finished_at - IngestionJob.started_at)))
            .where(IngestionJob.status == "done",
                   IngestionJob.finished_at > func.now() - timedelta(hours=1))
        ).one()
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
        "done_last_hour": done_last_hour,
        "avg_seconds_per_doc": round(float(avg_seconds), 1) if avg_seconds else None,
    }
