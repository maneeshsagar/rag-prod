"""
Create all database tables.

Run with:  make db-init
Reset:     make db-reset   (drops every table first — local development only)

Phase 1 uses `create_all` for simplicity (it only creates missing tables, never drops).
Alembic migrations will replace this once the schema settles.
"""

import sys

from sqlalchemy import text

from app.db import models  # importing registers the tables on Base
from app.db.session import Base, SessionLocal, engine

# Columns added after the first release. `create_all` never alters existing tables,
# so they are added here (safe to run many times). Alembic replaces this later.
ADDED_COLUMNS = [
    "ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS progress VARCHAR(100)",
    "ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN DEFAULT FALSE",
    "ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS worker VARCHAR(100)",
]


def main() -> None:
    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
        print("Dropped all tables.")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for statement in ADDED_COLUMNS:
            conn.execute(text(statement))

    # Make sure the single corpus-revision row exists.
    with SessionLocal() as session:
        if session.get(models.CorpusRevision, 1) is None:
            session.add(models.CorpusRevision(id=1, revision=0))
            session.commit()

    print("Tables ready:", ", ".join(sorted(Base.metadata.tables)))


if __name__ == "__main__":
    main()
