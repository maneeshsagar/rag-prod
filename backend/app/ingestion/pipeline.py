"""
Ingest one file:  store -> parse -> quality check -> chunk -> embed -> index -> summarize.

    ingest_file(Path("data/smoke/handbook.pdf"))

- Same content already indexed (SHA-256 match)  -> skipped.
- Same file name with new content                -> the old version is replaced.
- Any failure                                    -> document marked "failed" with the error.
- "Stop" pressed (cancel_requested on the job)    -> stops at the next checkpoint, removes
                                                     what was indexed, marks it "cancelled".

Stages (shown in the UI): store -> parse -> chunk -> embed -> index -> summarize -> done
"""

import hashlib
import logging
import os
import socket
import uuid
from pathlib import Path

from qdrant_client import models
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.models import CorpusRevision, Document, IngestionJob
from app.db.models import Section as SectionRow
from app.db.session import SessionLocal
from app.ingestion.chunker import (
    ChunkSettings,
    Section,
    chunk_document,
    chunk_header,
    citation_label,
)
from app.ingestion.parsers import file_format, parse_file
from app.ingestion.quality import check_quality
from app.ingestion.summary import summarize
from app.providers.embedder import get_embedder
from app.providers.sparse import sparse_documents
from app.retrieval import vector_store
from app.storage.object_store import get_object_store

log = logging.getLogger(__name__)


WORKER = f"{socket.gethostname()}:{os.getpid()}"  # identifies this process on the job


def _worker_alive(worker: str | None) -> bool:
    """Is the process that owns a job still running? (Only checkable on this machine.)"""
    if not worker:
        return False
    host, _, pid = worker.rpartition(":")
    if host != socket.gethostname():
        return True  # another machine: assume alive
    try:
        os.kill(int(pid), 0)  # signal 0 = "does it exist?", sends nothing
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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
        }
        points.append(models.PointStruct(
            id=chunk_ids[i], vector={"dense": dense[i], "bm25": sparse[i]}, payload=payload
        ))
    return points


def _clean_up_partial(session, doc_id: str, storage_key: str) -> None:
    """Remove what a failed or stopped ingest left behind (the document row stays).

    Best effort: if Qdrant or storage is down, log it and carry on, so the document's
    status is still saved (orphans are removed by the reconciliation job, Phase 3).
    """
    for step in (lambda: vector_store.delete_document(doc_id),
                 lambda: get_object_store().delete(storage_key)):
        try:
            step()
        except Exception:
            log.warning("  cleanup step failed for %s", doc_id, exc_info=True)
    session.execute(delete(SectionRow).where(SectionRow.document_id == doc_id))


def _remove_document(session, doc: Document) -> None:
    vector_store.delete_document(doc.id)
    get_object_store().delete(doc.storage_key)
    session.delete(doc)


def ingest_file(path: Path) -> str:
    """Ingest one file and return its document id."""
    settings = get_settings()
    content_hash = file_hash(path)

    with SessionLocal() as session:
        existing = session.scalar(
            select(Document).where(Document.content_hash == content_hash, Document.status == "indexed")
        )
        if existing:
            log.info("skip (unchanged): %s", path.name)
            return existing.id

        doc = Document(
            title=path.stem, source_name=path.name, storage_key="", file_format=file_format(path),
            content_hash=content_hash, status="pending",
        )
        session.add(doc)
        session.flush()
        doc.storage_key = f"originals/{doc.id}{path.suffix.lower()}"
        job = IngestionJob(document_id=doc.id, stage="store", status="running", attempts=1,
                           worker=WORKER)
        session.add(job)
        session.commit()
        log.info("ingest: %s", path.name)

        try:
            get_object_store().upload_file(path, doc.storage_key)

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

            # Replace older versions of the same file.
            for old in session.scalars(select(Document).where(
                Document.source_name == doc.source_name, Document.id != doc.id
            )):
                log.info("  replacing older version %s", old.id)
                _remove_document(session, old)

            doc.status, doc.chunk_count, doc.indexed_at = "indexed", len(points), func.now()
            job.stage, job.status, job.progress = "done", "done", None
            _bump_corpus_revision(session)
            session.commit()
            log.info("  done: %d sections, %d chunks", len(sections), len(points))
            return doc.id

        except Exception as error:
            session.rollback()
            doc = session.get(Document, doc.id)
            job = session.get(IngestionJob, job.id)
            _clean_up_partial(session, doc.id, doc.storage_key)

            if isinstance(error, IngestionCancelled):
                doc.status = job.status = "cancelled"
                session.commit()
                log.info("  stopped by user at stage %s: %s", job.stage, path.name)
                return doc.id

            doc.status, doc.error = "failed", f"{type(error).__name__}: {error}"
            job.status, job.error = "failed", doc.error
            session.commit()
            log.exception("  failed: %s", path.name)
            raise


def _end_job(session, doc: Document, job: IngestionJob, status: str, error: str | None) -> None:
    _clean_up_partial(session, doc.id, doc.storage_key)
    doc.status = job.status = status
    doc.error = job.error = error


def request_stop(doc_id: str) -> str:
    """Ask the ingest of a document to stop. Returns the document's status afterwards."""
    with SessionLocal() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            raise LookupError(doc_id)
        job = session.scalar(select(IngestionJob).where(IngestionJob.document_id == doc_id))
        if doc.status != "pending" or job is None:
            return doc.status  # already finished: nothing to stop

        if _worker_alive(job.worker):
            job.cancel_requested = True  # the running ingest stops at its next checkpoint
        else:  # the process doing it is gone (e.g. the API restarted): cancel right away
            _end_job(session, doc, job, "cancelled", None)
        session.commit()
        return doc.status


def recover_interrupted_jobs() -> int:
    """Mark ingests whose process died (API restart, crash) as failed. Run at API startup."""
    with SessionLocal() as session:
        running = session.scalars(select(IngestionJob).where(IngestionJob.status == "running")).all()
        count = 0
        for job in running:
            doc = session.get(Document, job.document_id)
            if doc is not None and not _worker_alive(job.worker):
                _end_job(session, doc, job, "failed",
                         f"Interrupted at stage '{job.stage}' (the server restarted). Upload again.")
                count += 1
        session.commit()
        return count


def delete_document(doc_id: str) -> bool:
    with SessionLocal() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            return False
        _remove_document(session, doc)
        _bump_corpus_revision(session)
        session.commit()
        return True
