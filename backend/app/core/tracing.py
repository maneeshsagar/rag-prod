"""
Tracing with Langfuse Cloud: see every question's steps, prompts, tokens and cost.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set in .env; then every
function here does nothing, so the rest of the code never checks.

    with trace("chat-answer", user_id=..., session_id=conversation_id, input=question):
        with span("retrieve", as_type="retriever") as step:
            ...
            step.update(output=...)

A trace is one question. Spans are its steps (route, search, answer). LLM calls are
recorded as "generation" spans by app/providers/llm.py, with tokens and cost.
"""

import logging
from contextlib import contextmanager
from functools import lru_cache

from app.core.config import get_settings

log = logging.getLogger(__name__)


class _NoSpan:
    """Stands in for a Langfuse span when tracing is off."""

    def update(self, **_) -> None:
        pass


@lru_cache
def _client():
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse, is_langfuse_span  # imported only when tracing is on

    log.info("Langfuse tracing on (%s)", settings.langfuse_host)
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.langfuse_environment,
        sample_rate=settings.langfuse_sample_rate,
        # Only our own spans: the SDKs' automatic spans would show every LLM call twice.
        should_export_span=is_langfuse_span,
    )


def enabled() -> bool:
    return _client() is not None


@contextmanager
def span(name: str, as_type: str = "span", **fields):
    """One step inside the current trace. `fields`: input, output, model, metadata, ..."""
    client = _client()
    if client is None:
        yield _NoSpan()
        return
    with client.start_as_current_observation(name=name, as_type=as_type, **fields) as observation:
        yield observation


@contextmanager
def trace(name: str, user_id: str | None = None, session_id: str | None = None,
          tags: list[str] | None = None, **fields):
    """Start a new trace (one question). session_id groups a conversation's questions."""
    client = _client()
    if client is None:
        yield _NoSpan()
        return
    from langfuse import propagate_attributes

    with client.start_as_current_observation(name=name, **fields) as root, propagate_attributes(
        user_id=user_id, session_id=session_id, tags=tags, trace_name=name,
    ):
        yield root


def flush() -> None:
    """Send buffered traces now (call before a script or the server exits)."""
    if client := _client():
        client.flush()
