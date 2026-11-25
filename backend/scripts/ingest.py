"""
Ingest files or folders from the command line.

    make ingest DIR=data/sample            # queue every file, then process them here
    make ingest DIR=data/sample QUEUE=1    # only queue them (run `make worker` separately)

More speed: while this runs, start extra workers in other terminals with `make worker`.
Safe to stop and run again: unchanged files are skipped and unfinished ones resume.
"""

import argparse
import threading
from collections import Counter
from pathlib import Path

from app.core.logging import setup_logging
from app.ingestion.parsers import SUPPORTED_EXTENSIONS
from app.ingestion.queue import enqueue_file, queue_summary
from app.ingestion.worker import run_worker
from app.retrieval.vector_store import ensure_collections


def find_files(raw_paths: list[str]) -> list[tuple[Path, str]]:
    """(file, source name). Inside a folder the source name is the path within it,
    so "hr/policy.pdf" and "finance/policy.pdf" are different documents."""
    found: list[tuple[Path, str]] = []
    for raw in raw_paths:
        root = Path(raw)
        if root.is_file():
            found.append((root, root.name))
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                found.append((path, path.relative_to(root).as_posix()))
    return found


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="files or folders")
    parser.add_argument("--queue-only", action="store_true", help="queue, do not process")
    args = parser.parse_args()

    ensure_collections()
    files = find_files(args.paths)
    if not files:
        raise SystemExit(f"No supported files. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    outcomes: Counter[str] = Counter()
    for path, source_name in files:
        outcomes[enqueue_file(path, source_name).outcome] += 1
    print(f"\n{len(files)} files: " + ", ".join(
        f"{outcomes[name]} {name}" for name in ("queued", "unchanged", "already queued", "duplicate")))

    if not args.queue_only:
        processed = run_worker(threading.Event(), exit_when_empty=True)
        print(f"\nProcessed {processed} job(s) in this process.")
    print("Queue:", queue_summary())


if __name__ == "__main__":
    main()
