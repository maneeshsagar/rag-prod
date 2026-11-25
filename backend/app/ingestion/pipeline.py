"""
Ingest documents:  parse -> quality check -> chunk -> embed -> index -> summarize.

    ingest_file(Path("data/smoke/handbook.pdf"))   # queue it and process it right here
    run_job(job_id)                                # what a worker does with a queued job

Files are first queued (ingestion/queue.py stores the original); a worker then runs them.

- Same content already indexed (SHA-256 match)  -> skipped when queued.
- Same source name with new content              -> the old version is replaced once the
                                                    new one is fully indexed.
- Error while parsing / chunking                 -> "failed" (retrying would not help).
- Error later (API down, rate limit, ...)        -> re-queued, up to 3 attempts.
- "Stop" pressed (cancel_requested on the job)    -> stops at the next checkpoint, removes
                                                     what was indexed, marks it "cancelled".

Stages (shown in the UI): queued -> parse -> chunk -> embed -> index -> summarize -> done
"""

import logging
import tempfile
import uuid
from pathlib import Path

from qdrant_client import models
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.core.usage import track_usage
from app.db.models import CorpusRevision, Document, IngestionJob
from app.db.models import Section as SectionRow
from app.db.session import SessionLocal
from app.ingestion import queue, versions
from app.ingestion.chunker import (
    ChunkSettings,
    Section,
    chunk_document,
    chunk_header,
    citation_label,
)
from app.ingestion.parsers import parse_file
from app.ingestion.quality import check_quality
from app.ingestion.summary import summarize
from app.providers.embedder import get_embedder
from app.providers.sparse import sparse_documents
from app.retrieval import vector_store
from app.storage.object_store import get_object_store

log = logging.getLogger(__name__)


class IngestionCancelled(Exception):
    """Raised at a checkpoint when someone pressed Stop for this document."""


def _advance(session, job: IngestionJob, stage: str, progress: str | None = None) -> None:
    """Record the current stage (visible in the UI) and stop if a Stop was requested."""
    if job.stage != stage:
        log.info("  %s", stage)
    job.stage, job.progress = stage, progress
    session.commit()
    session.refresh(job)  # picks up cancel_requested set by another request
    if job.cancel_requested:
        raise IngestionCancelled()


def _bump_corpus_revision(session) -> None:
    revision = session.get(CorpusRevision, 1)
    revision.revision += 1


def _save_sections(session, doc_id: str, sections: list[Section]) -> dict[int, str]:
    """Write sections to Postgres. Returns {section position: section id}."""
    ids: dict[int, str] = {}
    for section in sections:
        row = SectionRow(
            document_id=doc_id,
            position=section.position,
            section_path=" > ".join(section.heading_path),
            page_start=section.page_start,
            page_end=section.page_end,
            token_count=section.token_count,
            text=section.text,
        )
        session.add(row)
        session.flush()  # assigns row.id
        ids[section.position] = row.id
    return ids


def _build_points(doc: Document, sections: list[Section], section_ids: dict[int, str],
                  on_batch) -> list[models.PointStruct]:
    """One Qdrant point per chunk, with dense + BM25 vectors and its metadata."""
    flat = [(section, chunk) for section in sections for chunk in section.chunks]
    # Stable ids: re-indexing the same document overwrites the same points.
    chunk_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.id}:{chunk.index}")) for _, chunk in flat]

    texts = [chunk_header(doc.title, section.heading_path) + chunk.text for section, chunk in flat]
    dense = get_embedder().embed_documents(texts, on_batch=on_batch)
    sparse = sparse_documents(texts)

    points = []
    for i, (section, chunk) in enumerate(flat):
        payload = {
            "doc_id": doc.id,
            "chunk_id": chunk_ids[i],
            "chunk_index": chunk.index,
            "prev_chunk_id": chunk_ids[i - 1] if i > 0 else None,
            "next_chunk_id": chunk_ids[i + 1] if i + 1 < len(flat) else None,
            "section_id": section_ids[section.position],
            "section_tokens": section.token_count,
            "title": doc.title,
            "heading_path": section.heading_path,
            "section_path": " > ".join(section.heading_path),
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "citation_label": citation_label(
                doc.file_format, chunk.page_start, chunk.page_end, section.heading_path
            ),
            "file_format": doc.file_format,
            "is_table": chunk.is_table,
            "text": chunk.text,
            "token_count": chunk.token_count,
            "acl_groups": ["public"],  # everyone sees everything for now
            "is_latest": True,  # changed by ingestion/versions.py when a newer version arrives
        }
        points.append(models.PointStruct(
            id=chunk_ids[i], vector={"dense": dense[i], "bm25": sparse[i]}, payload=payload
        ))
    return points


def _clean_up_partial(session, doc_id: str) -> None:
    """Remove what a failed or stopped ingest left in the index (the document row stays).

    Best effort: if Qdrant is down, log it and carry on, so the document's status is
    still saved (orphans are removed by the reconciliation job, Phase 3).
    """
    try:
        vector_store.delete_document(doc_id)
    except Exception:
        log.warning("  cleanup failed for %s", doc_id, exc_info=True)
    session.execute(delete(SectionRow).where(SectionRow.document_id == doc_id))


def _remove_document(session, doc: Document) -> None:
    versions.forget_versions(session, doc)
    vector_store.delete_document(doc.id)
    get_object_store().delete(doc.storage_key)
    session.delete(doc)


def _index_document(session, doc: Document, job: IngestionJob, path: Path) -> None:
    """All the stages, from parsing the file to replacing older versions."""
    settings = get_settings()

    _advance(session, job, "parse")
    parsed = parse_file(path)
    doc.title, doc.page_count = parsed.title, parsed.page_count
    doc.parse_path = "fast"
    doc.warnings = check_quality(parsed)
    if not parsed.blocks:
        raise ValueError("; ".join(doc.warnings) or "no text extracted")

    _advance(session, job, "chunk")
    sections = chunk_document(parsed, ChunkSettings(
        settings.chunk_target_tokens, settings.chunk_max_tokens, settings.chunk_overlap_tokens
    ))
    section_ids = _save_sections(session, doc.id, sections)

    chunk_total = sum(len(s.chunks) for s in sections)
    _advance(session, job, "embed", f"0/{chunk_total} chunks")
    points = _build_points(
        doc, sections, section_ids,
        on_batch=lambda done, total: _advance(session, job, "embed", f"{done}/{total} chunks"),
    )

    _advance(session, job, "index", f"{len(points)} chunks")
    vector_store.upsert_chunks(points)

    _advance(session, job, "summarize")
    summary = summarize(doc.title, sections)
    doc.summary = summary.summary
    doc.effective_date = versions.normalize_date(summary.document_date)
    doc.version_label = versions.version_label(doc.source_name, doc.title)
    vector_store.upsert_document_summary(models.PointStruct(
        id=doc.id,
        vector={"dense": get_embedder().embed_documents(
            [f"{doc.title}\n{summary.summary}\nTopics: {', '.join(summary.topics)}"]
        )[0]},
        payload={"doc_id": doc.id, "title": doc.title, "summary": summary.summary,
                 "topics": summary.topics, "document_date": summary.document_date,
                 "file_format": doc.file_format},
    ))

    # Last chance to stop. Must come BEFORE older versions are removed, so a
    # Stop here never leaves the user with neither the old nor the new version.
    _advance(session, job, "summarize")

    for old in session.scalars(select(Document).where(
        Document.source_name == doc.source_name, Document.id != doc.id,
        Document.status != "pending",
    )):
        log.info("  replacing older version %s", old.id)
        _remove_document(session, old)

    # Near-copies under other names (e.g. "policy v2.pdf" vs "policy v3.pdf").
    versions.link_versions(session, doc, "\n\n".join(section.text for section in sections))

    doc.status, doc.chunk_count, doc.indexed_at = "indexed", len(points), func.now()
    log.info("  done: %d sections, %d chunks", len(sections), len(points))


PERMANENT_STAGES = {"parse", "chunk"}  # errors here come from the file itself


def _handle_failure(session, doc: Document, job: IngestionJob, error: Exception,
                    temp_folder: str) -> None:
    """Decide what a failed attempt means: stopped, retry later, or failed for good."""
    _clean_up_partial(session, doc.id)
    job.finished_at = func.now()
    stage = job.stage

    if isinstance(error, IngestionCancelled):
        doc.status = job.status = "cancelled"
        get_object_store().delete(doc.storage_key)
        log.info("  stopped by user at stage %s", stage)
        return

    # Parser errors mention the temporary copy; show the file name instead.
    message = f"{type(error).__name__}: {error}".replace(f"{temp_folder}/", "")
    if stage not in PERMANENT_STAGES and queue.retry_later(job, f"[{stage}] {message}"):
        log.warning("  temporary error at stage %s, will retry: %s", stage, message)
        return

    # The original file is kept, so `make ingest-retry` can try again after a fix.
    doc.status, doc.error = "failed", message
    job.status, job.error = "failed", message
    log.error("  failed at stage %s: %s", job.stage, message)


def run_job(job_id: str) -> str:
    """Process one claimed job (see queue.claim_job). Returns the document's final status."""
    with SessionLocal() as session:
        job = session.get(IngestionJob, job_id)
        doc = session.get(Document, job.document_id)
        log.info("ingest: %s (attempt %d)", doc.source_name, job.attempts)

        with tempfile.TemporaryDirectory() as folder:
            try:
                # Named like the original, so parsers and their messages see the real name.
                path = Path(folder) / Path(doc.source_name).name
                get_object_store().download_file(doc.storage_key, path)
                with track_usage() as meter:
                    _index_document(session, doc, job, path)
                doc.ingest_tokens = meter.tokens_in + meter.tokens_out
                doc.ingest_cost_usd = meter.cost_usd
                job.stage, job.status, job.progress, job.error = "done", "done", None, None
                job.finished_at = func.now()
                _bump_corpus_revision(session)
                session.commit()
            except Exception as error:  # noqa: BLE001 — every failure is recorded on the job
                session.rollback()
                doc, job = session.get(Document, doc.id), session.get(IngestionJob, job.id)
                _handle_failure(session, doc, job, error, folder)
                session.commit()
        return doc.status


def ingest_file(path: Path, source_name: str | None = None) -> str:
    """Queue one file and process it in this process. Returns its document id."""
    enqueued = queue.enqueue_file(path, source_name)
    if enqueued.outcome == "queued":
        with SessionLocal() as session:
            job_id = session.scalar(select(IngestionJob.id).where(
                IngestionJob.document_id == enqueued.doc_id))
        if queue.claim_job(job_id):
            run_job(job_id)
    return enqueued.doc_id


def _end_job(session, doc: Document, job: IngestionJob, status: str) -> None:
    _clean_up_partial(session, doc.id)
    get_object_store().delete(doc.storage_key)
    doc.status = job.status = status


def request_stop(doc_id: str) -> str:
    """Ask the ingest of a document to stop. Returns the document's status afterwards."""
    with SessionLocal() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            raise LookupError(doc_id)
        job = session.scalar(select(IngestionJob).where(IngestionJob.document_id == doc_id))
        if doc.status != "pending" or job is None:
            return doc.status  # already finished: nothing to stop

        if job.status == "running" and queue.worker_alive(job.worker):
            job.cancel_requested = True  # the running ingest stops at its next checkpoint
        else:  # still waiting in the queue, or its worker is gone: cancel right away
            _end_job(session, doc, job, "cancelled")
        session.commit()
        return doc.status


def recover_interrupted_jobs() -> tuple[int, int]:
    """Re-queue ingests whose worker died. Returns (re-queued, failed for good)."""
    return queue.recover_interrupted_jobs(lambda session, doc: _clean_up_partial(session, doc.id))


def delete_document(doc_id: str) -> bool:
    with SessionLocal() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            return False
        _remove_document(session, doc)
        _bump_corpus_revision(session)
        session.commit()
        return True
