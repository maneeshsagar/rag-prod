"""
The question -> answer pipeline, as a stream of events.

    for event in answer_question("What is the leave policy?", history=[]):
        ...

Events (each a dict with a "type"):
    status   {"message"}                      progress text for the UI
    route    {"route", "question"}            chosen route + standalone question
    sources  {"sources": [...]}               sources given to the model
    token    {"text"}                         a piece of the answer
    done     {"answer", "citations", ...}     final answer + citation check

No database access here, so the chat API and the eval can both use it.
"""

import re
import time
from collections.abc import Iterator
from dataclasses import replace

from app.core.config import get_settings
from app.generation.agent import agentic_retrieve
from app.generation.citations import verify_citations
from app.generation.prompts import (
    ANSWER_SYSTEM,
    CHAT_SYSTEM,
    NOT_FOUND,
    PROMPT_VERSION,
    answer_user_message,
)
from app.generation.router import route_question
from app.providers.llm import get_llm
from app.retrieval.search import Source, retrieve

HISTORY_MESSAGES = 6
CITATION_MARK = re.compile(r"\s*\[\d+(?:\s*[,;]\s*\d+)*\]")


def _history_for_model(history: list[dict]) -> list[dict]:
    """Earlier turns without citation marks (they refer to sources of other turns)."""
    return [
        {"role": m["role"], "content": CITATION_MARK.sub("", m["content"])}
        for m in history[-HISTORY_MESSAGES:]
    ]


def _drop_noise(sources: list[Source]) -> list[Source]:
    """Drop sources the reranker scored as irrelevant (helps clean "not found" answers)."""
    settings = get_settings()
    if settings.reranker_backend != "none":
        sources = [s for s in sources
                   if s.score >= settings.min_rerank_score or s.citation_label == "document summary"]
    return [replace(s, number=i + 1) for i, s in enumerate(sources)]  # renumber 1..n


def answer_question(question: str, history: list[dict]) -> Iterator[dict]:
    started = time.monotonic()
    llm = get_llm()

    yield {"type": "status", "message": "Understanding the question"}
    decision = route_question(question, history)
    standalone = decision.standalone_question or question
    yield {"type": "route", "route": decision.route, "question": standalone}

    if decision.route == "chat":  # greeting / small talk: no search, no citations
        parts: list[str] = []
        for text in llm.stream("fast", CHAT_SYSTEM, _history_for_model(history) + [
            {"role": "user", "content": question}
        ]):
            parts.append(text)
            yield {"type": "token", "text": text}
        yield {"type": "done", "answer": "".join(parts), "route": "chat", "question": question,
               "model": llm.model_name("fast"), "prompt_version": PROMPT_VERSION,
               "latency_ms": int((time.monotonic() - started) * 1000), "sources": [],
               "citations": [], "invalid_numbers": [], "not_found": False, "uncited": False}
        return

    sources: list[Source] = []
    if decision.route == "agentic":
        for item in agentic_retrieve(standalone):
            if isinstance(item, dict):
                yield {"type": "status", "message": item["status"]}
            else:
                sources = item
    else:
        yield {"type": "status", "message": "Searching the documents"}
        sources = retrieve(standalone)

    sources = _drop_noise(sources)
    yield {"type": "sources", "sources": [s.to_dict() for s in sources]}

    if not sources:
        answer = NOT_FOUND
        yield {"type": "token", "text": answer}
    else:
        yield {"type": "status", "message": "Writing the answer"}
        messages = _history_for_model(history) + [
            {"role": "user", "content": answer_user_message(standalone, sources)}
        ]
        parts: list[str] = []
        for text in llm.stream("main", ANSWER_SYSTEM, messages):
            parts.append(text)
            yield {"type": "token", "text": text}
        answer = "".join(parts)

    check = verify_citations(answer, sources)
    yield {
        "type": "done",
        "answer": answer,
        "route": decision.route,
        "question": standalone,
        "model": llm.model_name("main"),
        "prompt_version": PROMPT_VERSION,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "sources": sources,  # full Source objects (the API layer drops these)
        **check,
    }
