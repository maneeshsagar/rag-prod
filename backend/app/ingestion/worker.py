"""
The ingestion worker: takes queued documents one at a time and processes them.

Run as many as the machine allows (each in its own process):

    make worker                 # runs until stopped (Ctrl+C)
    make ingest DIR=data/sample # queues a folder, then works until the queue is empty

The API also runs one worker in a background thread (API_INGEST_WORKER=true), so
uploads from the UI are processed without starting anything else.

Stopping a worker in the middle of a document is safe: the job is re-queued the
next time any worker or the API starts.
"""

import logging
import threading
import time

from app.ingestion import queue
from app.ingestion.pipeline import recover_interrupted_jobs, run_job

log = logging.getLogger(__name__)

IDLE_WAIT_SECONDS = 3  # how often an idle worker checks the queue
RECOVER_EVERY_SECONDS = 300  # how often to look for jobs whose worker died


def run_worker(stop: threading.Event, exit_when_empty: bool = False) -> int:
    """Process queued jobs until `stop` is set (or the queue is empty). Returns jobs done."""
    processed = 0
    last_recover = 0.0
    while not stop.is_set():
        if time.monotonic() - last_recover > RECOVER_EVERY_SECONDS:
            requeued, failed = recover_interrupted_jobs()
            if requeued or failed:
                log.warning("recovered interrupted jobs: %d re-queued, %d failed", requeued, failed)
            last_recover = time.monotonic()

        job_id = queue.claim_job()
        if job_id is None:
            # Retries wait for their delay, so "nothing to claim" is not "queue empty".
            if exit_when_empty and queue.queued_count() == 0:
                break
            stop.wait(IDLE_WAIT_SECONDS)
            continue

        try:
            run_job(job_id)
        except Exception:  # never let one bad job stop the worker
            log.exception("job %s crashed the worker loop", job_id)
        processed += 1
    return processed


def start_background_worker() -> threading.Event:
    """Run a worker in a daemon thread (used by the API). Set the returned event to stop it."""
    stop = threading.Event()
    threading.Thread(target=run_worker, args=(stop,), name="ingest-worker", daemon=True).start()
    return stop
