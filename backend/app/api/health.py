"""
GET /health — checks that every dependency is reachable.

Returns 200 when everything is fine, 503 when something is down,
with one line per dependency so you can see what is broken.
"""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from qdrant_client import QdrantClient
from redis import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.storage.object_store import get_object_store

router = APIRouter()


def check_postgres() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def check_redis() -> None:
    Redis.from_url(get_settings().redis_url, socket_timeout=2).ping()


def check_qdrant() -> None:
    QdrantClient(url=get_settings().qdrant_url, timeout=2).get_collections()


def check_object_storage() -> None:
    get_object_store().check()


def check_api_keys() -> None:
    settings = get_settings()
    missing = [
        name
        for name, value in [
            ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
            ("OPENAI_API_KEY", settings.openai_api_key),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing: {', '.join(missing)}")


CHECKS: dict[str, Callable[[], None]] = {
    "postgres": check_postgres,
    "redis": check_redis,
    "qdrant": check_qdrant,
    "object_storage": check_object_storage,
    "api_keys": check_api_keys,
}


def run_check(check: Callable[[], None]) -> str:
    try:
        check()
        return "ok"
    except Exception as error:  # noqa: BLE001 — report any failure as text, never crash
        return f"error: {error}"


@router.get("/health")
def health() -> JSONResponse:
    results = {name: run_check(check) for name, check in CHECKS.items()}
    healthy = all(result == "ok" for result in results.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": results},
    )
