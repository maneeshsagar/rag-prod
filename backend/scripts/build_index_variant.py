"""
Build a copy of the search index with a different embedding model, for eval benchmarks.

    uv run python -m scripts.build_index_variant --name small \
        --model text-embedding-3-small --dimensions 1536

Re-embeds the chunks and document summaries that are already indexed (no re-parsing;
BM25 vectors and metadata are copied as they are) into `chunks_<name>` and
`documents_<name>`, then prints the eval command that uses them.
The live index is not touched.
"""

import argparse

from qdrant_client import models
from redis import Redis

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.usage import track_usage
from app.ingestion.chunker import chunk_header
from app.providers.embedder import Embedder
from app.retrieval import vector_store

PAGE = 256


def _all_points(collection: str, vectors: list[str]):
    """Every point of a collection, page by page."""
    offset = None
    while True:
        points, offset = vector_store.get_qdrant().scroll(
            collection, limit=PAGE, offset=offset, with_payload=True, with_vectors=vectors)
        yield from points
        if offset is None:
            return


def _copy_chunks(embedder: Embedder, target: str) -> int:
    count = 0
    page: list = []
    for point in _all_points(get_settings().chunks_collection, ["bm25"]):
        page.append(point)
        if len(page) == PAGE:
            count += _write_chunks(embedder, target, page)
            page = []
    return count + (_write_chunks(embedder, target, page) if page else 0)


def _write_chunks(embedder: Embedder, target: str, points: list) -> int:
    texts = [chunk_header(p.payload["title"], p.payload["heading_path"]) + p.payload["text"]
             for p in points]
    dense = embedder.embed_documents(texts)
    vector_store.get_qdrant().upsert(target, wait=True, points=[
        models.PointStruct(id=p.id, payload=p.payload,
                           vector={"dense": vector, "bm25": p.vector["bm25"]})
        for p, vector in zip(points, dense, strict=True)
    ])
    print(f"  {len(points)} chunks")
    return len(points)


def _copy_summaries(embedder: Embedder, target: str) -> int:
    points = list(_all_points(get_settings().documents_collection, []))
    texts = [f"{p.payload['title']}\n{p.payload['summary']}\nTopics: {', '.join(p.payload['topics'])}"
             for p in points]
    dense = embedder.embed_documents(texts) if texts else []
    vector_store.get_qdrant().upsert(target, wait=True, points=[
        models.PointStruct(id=p.id, payload=p.payload, vector={"dense": vector})
        for p, vector in zip(points, dense, strict=True)
    ])
    return len(points)


def main() -> None:
    setup_logging("WARNING")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="variant name, e.g. small")
    parser.add_argument("--model", required=True, help="OpenAI embedding model")
    parser.add_argument("--dimensions", type=int, required=True)
    args = parser.parse_args()

    settings = get_settings()
    chunks, documents = f"chunks_{args.name}", f"documents_{args.name}"
    vector_store.create_chunks_collection(chunks, args.dimensions)
    vector_store.create_documents_collection(documents, args.dimensions)
    embedder = Embedder(settings.openai_api_key, args.model, args.dimensions,
                        redis=Redis.from_url(settings.redis_url))

    with track_usage() as meter:
        total = _copy_chunks(embedder, chunks)
        summaries = _copy_summaries(embedder, documents)
    print(f"\nBuilt {chunks} ({total} chunks) and {documents} ({summaries} summaries) "
          f"for ${meter.cost_usd:.4f}.\nEvaluate it with:\n"
          f"  uv run python -m eval.run_eval --name {args.name} "
          f"--set CHUNKS_COLLECTION={chunks} --set DOCUMENTS_COLLECTION={documents} "
          f"--set EMBEDDING_MODEL={args.model} --set EMBEDDING_DIMENSIONS={args.dimensions}")


if __name__ == "__main__":
    main()
