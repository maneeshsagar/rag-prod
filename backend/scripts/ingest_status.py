"""
Show the ingestion queue, recent failures, speed and cost (with a projection for the
full 30k-50k corpus, from the documents measured so far).

    make ingest-status
    make ingest-retry      # queue every failed document again
"""

import sys
from collections import Counter

from sqlalchemy import func, select

from app.db.models import Document, IngestionJob
from app.db.session import SessionLocal
from app.ingestion.queue import queue_summary, retry_failed


def _failures() -> list[tuple[str, str, str]]:
    """(source name, stage it failed at, error) for every failed document."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Document.source_name, IngestionJob.stage, Document.error)
            .join(IngestionJob, IngestionJob.document_id == Document.id)
            .where(Document.status == "failed")
        ).all()
    return [tuple(row) for row in rows]


FULL_CORPUS = (30_000, 50_000)


def _cost_report() -> None:
    """Embedding + summary cost per document and per page, measured on indexed documents."""
    with SessionLocal() as session:
        docs, pages, cost = session.execute(
            select(func.count(), func.sum(Document.page_count), func.sum(Document.ingest_cost_usd))
            .where(Document.ingest_cost_usd.is_not(None))
        ).one()
    if not docs:
        return
    per_doc = cost / docs
    print(f"\nIngestion cost: ${cost:.4f} for {docs} measured documents "
          f"(${per_doc:.4f} per document"
          + (f", ${cost / pages:.5f} per page)" if pages else ")"))
    low, high = FULL_CORPUS
    print(f"  At this rate {low:,}-{high:,} documents would cost ${per_doc * low:,.0f}-"
          f"${per_doc * high:,.0f} (only meaningful once the sample is representative)")


def main() -> None:
    if "--retry-failed" in sys.argv:
        print(f"Re-queued {retry_failed()} failed document(s). Run `make worker` to process them.")
        return

    summary = queue_summary()
    print("Jobs:", ", ".join(f"{k} {summary[k]}" for k in
                             ("queued", "running", "done", "failed", "cancelled")))
    if summary["done_last_hour"]:
        print(f"Speed: {summary['done_last_hour']} documents in the last hour, "
              f"{summary['avg_seconds_per_doc']} s each on average (per worker)")
        if summary["queued"] and summary["avg_seconds_per_doc"]:
            workers = max(summary["running"], 1)
            minutes = summary["queued"] * summary["avg_seconds_per_doc"] / workers / 60
            print(f"Estimated time left: ~{minutes:.0f} min with {workers} worker(s)")

    _cost_report()

    failures = _failures()
    if failures:
        print(f"\nFailed ({len(failures)}), by error type:")
        for kind, count in Counter((e or "").split(":")[0] for _, _, e in failures).most_common():
            print(f"  {count:4d}  {kind}")
        print("Latest 15:")
        for name, stage, error in failures[-15:]:
            print(f"  - {name} [{stage}]: {(error or '')[:150]}")


if __name__ == "__main__":
    main()
