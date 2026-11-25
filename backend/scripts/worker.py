"""
Run one ingestion worker until Ctrl+C.

    make worker

Start several (one per terminal) to ingest faster. Each one parses one document at
a time; mind the 16 GB memory budget (PLAN.md §2.3) — 2 or 3 workers is plenty locally.
"""

import signal
import threading

from app.core.logging import setup_logging
from app.ingestion.worker import run_worker
from app.retrieval.vector_store import ensure_collections


def main() -> None:
    setup_logging()
    ensure_collections()
    stop = threading.Event()
    # First Ctrl+C: finish the current document, then exit. Second Ctrl+C: exit now
    # (the unfinished document is re-queued automatically later).
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: (stop.set(), signal.signal(signal.SIGINT, signal.SIG_DFL)))
    print("Worker started. Ctrl+C to stop after the current document.")
    run_worker(stop)


if __name__ == "__main__":
    main()
