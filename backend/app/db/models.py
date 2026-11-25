"""
Database tables.

Chunks and their vectors live in Qdrant, not here. Postgres keeps:
- documents and their sections (the "parent" text used for context)
- users, conversations, messages, feedback
- ingestion job status
- a single corpus revision counter (bumped on every document change; used by the cache)
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def new_id() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")  # user / admin
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(1000))  # file name, or path inside a bulk folder
    storage_key: Mapped[str] = mapped_column(String(1000))  # key in local storage or S3
    file_format: Mapped[str] = mapped_column(String(10))  # pdf, docx, pptx, html, md
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # SHA-256 of the file
    page_count: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    parse_path: Mapped[str | None] = mapped_column(String(20))  # "fast" or "docling"
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/indexed/failed/cancelled
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list | None] = mapped_column(JSON)  # parse quality warnings
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    ingest_tokens: Mapped[int | None] = mapped_column(Integer)  # embeddings + summary
    ingest_cost_usd: Mapped[float | None] = mapped_column(Float)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # --- Versions (see ingestion/versions.py) ---
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime)  # file's mtime
    effective_date: Mapped[str | None] = mapped_column(String(10))  # "2024", "2024-03", "2024-03-01"
    version_label: Mapped[str | None] = mapped_column(String(20))  # "2", "3.1" from "v2", "rev 3.1"
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"))
    version_confidence: Mapped[str | None] = mapped_column(String(10))  # explicit / inferred
    version_similarity: Mapped[float | None] = mapped_column(Float)  # to the newer version


class DocumentFingerprint(Base):
    """MinHash signature of a document's text (near-duplicate detection)."""

    __tablename__ = "document_fingerprints"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    signature: Mapped[list] = mapped_column(JSON)  # 128 numbers


class MinhashBand(Base):
    """LSH buckets: documents sharing a (band, bucket) pair are near-duplicate candidates."""

    __tablename__ = "minhash_bands"
    __table_args__ = (Index("ix_minhash_bands_lookup", "band", "bucket"),)

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    band: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    bucket: Mapped[int] = mapped_column(BigInteger)


class Section(Base):
    """A heading-based part of a document. Chunks point to their section (parent)."""

    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # order inside the document
    section_path: Mapped[str] = mapped_column(Text)  # e.g. "Benefits > Leave > Parental"
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSON)
    route: Mapped[str | None] = mapped_column(String(20))  # simple / agentic / cache
    model: Mapped[str | None] = mapped_column(String(100))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)  # all AI calls for this answer
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)  # +1 or -1
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IngestionJob(Base):
    """One document's trip through ingestion. Also the work queue (see ingestion/queue.py)."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(30))  # queued/parse/chunk/embed/index/summarize/done
    status: Mapped[str] = mapped_column(String(20), index=True)  # queued/running/done/failed/cancelled
    progress: Mapped[str | None] = mapped_column(String(100))  # e.g. "200/450 chunks"
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)  # set by "Stop"
    worker: Mapped[str | None] = mapped_column(String(100))  # "<host>:<pid>" doing the work
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime | None] = mapped_column(DateTime)  # retry not before this time
    started_at: Mapped[datetime | None] = mapped_column(DateTime)  # current / last attempt
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class CorpusRevision(Base):
    """Single-row counter. Increases every time any document is added, changed or removed."""

    __tablename__ = "corpus_revision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
