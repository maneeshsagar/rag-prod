"""
Database connection.

- `engine`       : the connection pool to Postgres
- `SessionLocal` : creates a new session (one per request / task)
- `Base`         : parent class for all table models (see models.py)
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: gives a route its own session and always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
