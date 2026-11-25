"""
Agentic retrieval for complex questions — a fixed, code-controlled workflow:

    plan (1-3 queries) -> search each -> check coverage -> follow-up searches
    -> rerank everything against the original question -> sources

At most `agent_max_searches` searches in total. The answer itself is written by
the same answer step as the simple route.
"""

from collections.abc import Iterator

from pydantic import BaseModel

from app.core.config import get_settings
from app.generation.prompts import CHECK_SYSTEM, PLAN_SYSTEM
from app.providers.embedder import get_embedder
from app.providers.llm import get_llm
from app.providers.reranker import rerank
from app.retrieval import vector_store
from app.retrieval.search import Hit, Source, build_sources, search_chunks, version_note

AGENT_TOP_K = 10  # more context than the simple route: several topics are involved
SNIPPET_CHARS = 300


class SearchPlan(BaseModel):
    queries: list[str]
    needs_document_list: bool


class CoverageCheck(BaseModel):
    enough: bool
    follow_up_queries: list[str]


def _snippets(hits: dict[str, Hit]) -> str:
    return "\n".join(
        f"- {h.payload['title']} ({h.payload['citation_label']}): {h.payload['text'][:SNIPPET_CHARS]}"
        for h in hits.values()
    )


def _document_list_sources(question: str, start_number: int) -> list[Source]:
    points = vector_store.search_documents(get_embedder().embed_query(question), limit=8)
    return [
        Source(
            number=start_number + i,
            doc_id=p.payload["doc_id"],
            title=p.payload["title"],
            citation_label="document summary",
            file_format=p.payload["file_format"],
            section_path="",
            text=f"{p.payload['summary']}\nTopics: {', '.join(p.payload['topics'])}"
                 f"\nDate: {p.payload['document_date']}",
            score=p.score,
            version_note=version_note(p.payload),
        )
        for i, p in enumerate(points)
    ]


def agentic_retrieve(question: str) -> Iterator[dict | list[Source]]:
    """Yields status events while working; the last item yielded is the source list."""
    settings = get_settings()
    llm = get_llm()
    max_searches = settings.agent_max_searches
    hits: dict[str, Hit] = {}

    def run_search(query: str) -> None:
        for hit in search_chunks(query, top_k=AGENT_TOP_K):
            if hit.chunk_id not in hits:
                hits[hit.chunk_id] = hit

    yield {"status": "Planning searches"}
    plan = llm.json("fast", PLAN_SYSTEM, f"Question: {question}", SearchPlan, max_tokens=2_000)
    queries = plan.queries[:min(3, max_searches)] or [question]
    for query in queries:
        yield {"status": f"Searching: {query}"}
        run_search(query)

    remaining = max_searches - len(queries)
    if remaining > 0:
        yield {"status": "Checking coverage"}
        check = llm.json(
            "fast", CHECK_SYSTEM,
            f"Question: {question}\nAllowed new queries: {remaining}\n\nFound so far:\n{_snippets(hits)}",
            CoverageCheck, max_tokens=2_000,
        )
        if not check.enough:
            for query in check.follow_up_queries[:remaining]:
                yield {"status": f"Searching: {query}"}
                run_search(query)

    # Scores from different queries are not comparable: rerank all against the question.
    candidates = list(hits.values())
    scores = rerank(question, [f"{h.payload['title']}\n{h.payload['text']}" for h in candidates])
    ranked = [Hit(h.chunk_id, s, h.payload)
              for h, s in sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)]
    sources = build_sources(ranked[:AGENT_TOP_K])

    if plan.needs_document_list:
        sources += _document_list_sources(question, start_number=len(sources) + 1)

    yield sources
