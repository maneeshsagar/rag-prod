"""
Documents: list (with processing stage), queue summary, details, upload (queued; a
worker processes it), stop processing, delete.
Everyone can see every document for now (no ACLs yet).
"""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.core.config import get_settings
from app.db.models import Document, IngestionJob, User
from app.db.session import get_db
from app.ingestion.parsers import SUPPORTED_EXTENSIONS
from app.ingestion.pipeline import delete_document, request_stop
from app.ingestion.queue import enqueue_file, queue_summary

router = APIRouter(prefix="/documents", tags=["documents"])
log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def job_info(job: IngestionJob | None) -> dict | None:
    if job is None:
        return None
    return {"stage": job.stage, "status": job.status, "progress": job.progress,
            "error": job.error, "attempts": job.attempts,
            "stopping": job.cancel_requested and job.status == "running"}


def _newer_version(db: Session, doc: Document) -> dict | None:
    newer = db.get(Document, doc.superseded_by_id) if doc.superseded_by_id else None
    return newer and {"id": newer.id, "title": newer.title, "source_name": newer.source_name}


def document_info(doc: Document, job: IngestionJob | None = None,
                  newer: dict | None = None) -> dict:
    return {
        "id": doc.id, "title": doc.title, "source_name": doc.source_name,
        "file_format": doc.file_format, "page_count": doc.page_count, "status": doc.status,
        "chunk_count": doc.chunk_count, "warnings": doc.warnings or [], "error": doc.error,
        "summary": doc.summary, "indexed_at": doc.indexed_at, "created_at": doc.created_at,
        "job": job_info(job),
        "effective_date": doc.effective_date, "is_latest": doc.is_latest is not False,
        "version_confidence": doc.version_confidence, "newer_version": newer,
    }


def _jobs_by_document(db: Session, doc_ids: list[str]) -> dict[str, IngestionJob]:
    jobs = db.scalars(select(IngestionJob).where(IngestionJob.document_id.in_(doc_ids))).all()
    return {job.document_id: job for job in jobs}


@router.get("")
def list_documents(limit: int = Query(100, le=500), offset: int = 0, status: str | None = None,
                   _: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Newest first, one page at a time (there can be thousands)."""
    query = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    if status:
        query = query.where(Document.status == status)
    docs = db.scalars(query).all()
    jobs = _jobs_by_document(db, [doc.id for doc in docs])
    return [document_info(doc, jobs.get(doc.id), _newer_version(db, doc)) for doc in docs]


@router.get("/queue")
def ingestion_queue(_: User = Depends(current_user)) -> dict:
    """How many documents are waiting, processing, done or failed."""
    return queue_summary()


@router.get("/{doc_id}")
def get_document(doc_id: str, _: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document_info(doc, _jobs_by_document(db, [doc.id]).get(doc.id), _newer_version(db, doc))


@router.post("")
async def upload_document(file: UploadFile, _: User = Depends(current_user)) -> dict:
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed: {sorted(SUPPORTED_EXTENSIONS)}")

    # Each upload gets its own folder, so the original file name is kept without clashes.
    target = get_settings().data_dir / "inbox" / "uploads" / uuid.uuid4().hex / name
    target.parent.mkdir(parents=True)

    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                break
            out.write(chunk)
    if size > MAX_UPLOAD_BYTES:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="File is larger than 200 MB")

    try:
        enqueued = enqueue_file(target)  # copies it into storage; a worker processes it
    finally:
        target.unlink(missing_ok=True)
        target.parent.rmdir()
    return {"doc_id": enqueued.doc_id, "outcome": enqueued.outcome, "file": name}


@router.post("/{doc_id}/stop")
def stop_processing(doc_id: str, _: User = Depends(current_user)) -> dict:
    """Stop an ingest in progress. It stops at the next checkpoint (within seconds)."""
    try:
        status = request_stop(doc_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Document not found") from None
    return {"status": status}


@router.delete("/{doc_id}")
def remove_document(doc_id: str, user: User = Depends(current_user)) -> dict:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete documents")
    if not delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True}
