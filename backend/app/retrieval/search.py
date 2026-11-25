"""
Retrieval: question -> numbered sources for the answer model.

    1. Hybrid search (dense + BM25, merged with RRF)       -> ~50 candidates
    2. Rerank with the cross-encoder                         -> top 6
    3. Expand context: short sections are sent whole, long ones as
       matched chunk + its neighbours, all within a hard token budget.
"""

from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import get_settings
from app.core.tokens import count_tokens
from app.db.models import Section
from app.db.session import SessionLocal
from app.ingestion.chunker import citation_label
from app.providers.embedder import get_embedder
from app.providers.reranker import rerank
from app.providers.sparse import sparse_query
from app.retrieval import vector_store


@dataclass
class Hit:
    """One reranked chunk."""

    chunk_id: str
    score: float
    payload: dict


@dataclass
class Source:
    """A piece of context given to the answer model, cited as [number]."""

    number: int
    doc_id: str
    title: str
    citation_label: str  # "p. 12", "slide 4", "§ Parental leave"
    file_format: str
    section_path: str
    text: str
    chunk_ids: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "doc_id": self.doc_id,
            "title": self.title,
            "citation_label": self.citation_label,
            "file_format": self.file_format,
            "section_path": self.section_path,
            "score": round(self.score, 4),
        }


def search_chunks(query: str, top_k: int | None = None,
                  doc_ids: list[str] | None = None) -> list[Hit]:
    """Steps 1 + 2: hybrid search, then rerank."""
    settings = get_settings()
    candidates = vector_store.hybrid_search(
        dense=get_embedder().embed_query(query),
        sparse=sparse_query(query),
        limit=settings.search_candidates,
        doc_ids=doc_ids,
    )
    if not candidates:
        return []

    texts = [f"{p.payload['title']} — {p.payload['section_path']}\n{p.payload['text']}"
             for p in candidates]
    scores = rerank(query, texts)
    ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [Hit(str(point.id), score, point.payload)
            for point, score in ranked[: top_k or settings.rerank_top_k]]


def _load_sections(section_ids: set[str]) -> dict[str, Section]:
    if not section_ids:
        return {}
    with SessionLocal() as session:
        rows = session.scalars(select(Section).where(Section.id.in_(section_ids))).all()
        return {row.id: row for row in rows}


def _with_neighbours(hit: Hit, neighbours: dict[str, dict]) -> tuple[str, list[str], list[int]]:
    """Matched chunk plus the chunk before and after it (same section only)."""
    parts, ids, pages = [], [], []
    for chunk_id in (hit.payload.get("prev_chunk_id"), hit.chunk_id, hit.payload.get("next_chunk_id")):
        payload = hit.payload if chunk_id == hit.chunk_id else neighbours.get(chunk_id or "")
        if not payload or payload["section_id"] != hit.payload["section_id"]:
            continue
        parts.append(payload["text"])
        ids.append(chunk_id)
        pages += [p for p in (payload.get("page_start"), payload.get("page_end")) if p]
    return "\n\n".join(parts), ids, pages


def build_sources(hits: list[Hit], start_number: int = 1) -> list[Source]:
    """Step 3: turn reranked hits into numbered sources within the token budget."""
    settings = get_settings()
    long_hits = [h for h in hits if h.payload["section_tokens"] > settings.short_section_tokens]
    neighbour_ids = [i for h in long_hits
                     for i in (h.payload.get("prev_chunk_id"), h.payload.get("next_chunk_id")) if i]
    neighbours = vector_store.get_chunks(neighbour_ids)
    sections = _load_sections({h.payload["section_id"] for h in hits
                               if h.payload["section_tokens"] <= settings.short_section_tokens})

    sources: list[Source] = []
    used_sections: set[str] = set()
    used_chunks: set[str] = set()
    budget = settings.context_token_budget

    for hit in hits:
        p = hit.payload
        if p["section_id"] in used_sections or hit.chunk_id in used_chunks:
            continue  # already covered by an earlier source

        section = sections.get(p["section_id"])
        if section is not None:  # short section: send it whole
            text, chunk_ids = section.text, [hit.chunk_id]
            label = citation_label(p["file_format"], section.page_start, section.page_end,
                                   p["heading_path"])
            used_sections.add(p["section_id"])
        else:  # long section: matched chunk + neighbours
            text, chunk_ids, pages = _with_neighbours(hit, neighbours)
            label = citation_label(p["file_format"], min(pages, default=None),
                                   max(pages, default=None), p["heading_path"])

        tokens = count_tokens(text)
        if tokens > budget:  # too big: fall back to just the matched chunk
            text, chunk_ids, tokens = p["text"], [hit.chunk_id], p["token_count"]
            label = p["citation_label"]
        if tokens > budget:
            continue  # does not fit; a smaller, lower-ranked source still might

        budget -= tokens
        used_chunks.update(chunk_ids)
        sources.append(Source(
            number=start_number + len(sources),
            doc_id=p["doc_id"],
            title=p["title"],
            citation_label=label,
            file_format=p["file_format"],
            section_path=p["section_path"],
            text=text,
            chunk_ids=chunk_ids,
            score=hit.score,
        ))
    return sources


def retrieve(query: str, doc_ids: list[str] | None = None) -> list[Source]:
    """Full retrieval for one query."""
    return build_sources(search_chunks(query, doc_ids=doc_ids))
