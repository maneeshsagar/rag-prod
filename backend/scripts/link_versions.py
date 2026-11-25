"""
Recompute version links for every indexed document (oldest first).

    make versions

New documents are linked automatically during ingestion. Run this after changing
the rules in app/ingestion/versions.py, or for documents indexed before versions existed.
"""

from sqlalchemy import delete, select

from app.core.logging import setup_logging
from app.db.models import Document, DocumentFingerprint, MinhashBand, Section
from app.db.session import SessionLocal
from app.ingestion import versions
from app.retrieval import vector_store


def _stated_date(doc_id: str) -> str | None:
    """The date from the document's summary (kept in the summary index)."""
    settings = vector_store.get_settings()
    records = vector_store.get_qdrant().retrieve(settings.documents_collection, ids=[doc_id])
    return records[0].payload.get("document_date") if records else None


def main() -> None:
    setup_logging()
    with SessionLocal() as session:
        session.execute(delete(MinhashBand))
        session.execute(delete(DocumentFingerprint))
        docs = session.scalars(select(Document).where(Document.status == "indexed")
                               .order_by(Document.created_at)).all()
        for doc in docs:  # start clean: everything is "latest" until shown otherwise
            if not doc.is_latest:
                versions._set_older(doc, None, None, None)

        linked = 0
        for doc in docs:
            doc.effective_date = versions.normalize_date(_stated_date(doc.id))
            doc.version_label = versions.version_label(doc.source_name, doc.title)
            texts = session.scalars(select(Section.text).where(Section.document_id == doc.id)
                                    .order_by(Section.position))
            linked += len(versions.link_versions(session, doc, "\n\n".join(texts)))
            session.flush()
        session.commit()
    print(f"Checked {len(docs)} documents; {linked} older version / copy link(s).")


if __name__ == "__main__":
    main()
