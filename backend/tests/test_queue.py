"""Ingestion queue decisions: skip reasons and retry limits (no database needed)."""

from app.db.models import Document, IngestionJob
from app.ingestion import queue


def _doc(source_name: str, status: str) -> Document:
    return Document(source_name=source_name, status=status)


def test_skip_reasons():
    assert queue._skip_reason(_doc("hr/policy.pdf", "indexed"), "hr/policy.pdf") == "unchanged"
    assert queue._skip_reason(_doc("hr/policy.pdf", "pending"), "hr/policy.pdf") == "already queued"
    assert queue._skip_reason(_doc("hr/policy.pdf", "indexed"), "copy/policy.pdf") == "duplicate"


def test_retry_until_out_of_attempts():
    job = IngestionJob(status="running", stage="embed", attempts=1)
    assert queue.retry_later(job, "rate limited")
    assert (job.status, job.stage, job.error) == ("queued", "queued", "rate limited")

    job.attempts = queue.MAX_ATTEMPTS
    assert not queue.retry_later(job, "rate limited again")


def test_dead_worker_on_this_machine_is_detected():
    assert not queue.worker_alive(None)
    assert not queue.worker_alive(f"{queue.WORKER.rpartition(':')[0]}:999999")
    assert queue.worker_alive(queue.WORKER)  # this test process
