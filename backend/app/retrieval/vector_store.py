"""
Qdrant: where chunks (and document summaries) are indexed.

Collections are versioned ("chunks_v1") and used through an alias ("chunks"), so a
full re-index can build "chunks_v2" and switch the alias with zero downtime.

Each chunk point has two vectors:
    dense -> OpenAI embedding (meaning)
    bm25  -> sparse keyword vector (exact words, codes, names)
"""

from functools import lru_cache

from qdrant_client import QdrantClient, models

from app.core.config import get_settings

CHUNKS_VERSION = "chunks_v1"
DOCUMENTS_VERSION = "documents_v1"


@lru_cache
def get_qdrant() -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url, timeout=30)


def _int8() -> models.ScalarQuantization:
    return models.ScalarQuantization(
        scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, always_ram=True)
    )


def _ensure_alias(client: QdrantClient, alias: str, collection: str) -> None:
    existing = {a.alias_name for a in client.get_aliases().aliases}
    if alias not in existing:
        client.update_collection_aliases(
            change_aliases_operations=[
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(collection_name=collection, alias_name=alias)
                )
            ]
        )


def create_chunks_collection(name: str, dimensions: int) -> None:
    """Dense (cosine) + BM25 vectors, int8-quantized, with an index on doc_id."""
    client = get_qdrant()
    if client.collection_exists(name):
        return
    client.create_collection(
        name,
        vectors_config={"dense": models.VectorParams(size=dimensions,
                                                     distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        quantization_config=_int8(),
    )
    client.create_payload_index(name, "doc_id", models.PayloadSchemaType.KEYWORD)


def create_documents_collection(name: str, dimensions: int) -> None:
    """One summary vector per document."""
    client = get_qdrant()
    if not client.collection_exists(name):
        client.create_collection(
            name, quantization_config=_int8(),
            vectors_config={"dense": models.VectorParams(size=dimensions,
                                                         distance=models.Distance.COSINE)},
        )


def ensure_collections() -> None:
    """Create the collections + aliases if missing. Safe to run many times."""
    settings = get_settings()
    client = get_qdrant()
    create_chunks_collection(CHUNKS_VERSION, settings.embedding_dimensions)
    create_documents_collection(DOCUMENTS_VERSION, settings.embedding_dimensions)
    _ensure_alias(client, settings.chunks_collection, CHUNKS_VERSION)
    _ensure_alias(client, settings.documents_collection, DOCUMENTS_VERSION)


# ---------- writes ----------


def upsert_chunks(points: list[models.PointStruct]) -> None:
    get_qdrant().upsert(get_settings().chunks_collection, points=points, wait=True)


def upsert_document_summary(point: models.PointStruct) -> None:
    get_qdrant().upsert(get_settings().documents_collection, points=[point], wait=True)


def delete_document(doc_id: str) -> None:
    """Remove every chunk and the summary of a document."""
    settings = get_settings()
    client = get_qdrant()
    doc_filter = models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
    )
    client.delete(settings.chunks_collection, points_selector=models.FilterSelector(filter=doc_filter))
    client.delete(settings.documents_collection, points_selector=[doc_id])


def set_version(doc_id: str, is_latest: bool, confidence: str | None,
                newer_title: str | None) -> None:
    """Mark every chunk (and the summary) of a document as latest or older version."""
    settings = get_settings()
    client = get_qdrant()
    payload = {"is_latest": is_latest, "version_confidence": confidence,
               "newer_version_title": newer_title}
    doc_filter = models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
    )
    client.set_payload(settings.chunks_collection, payload=payload, points=doc_filter, wait=True)
    client.set_payload(settings.documents_collection, payload=payload, points=[doc_id], wait=True)


# ---------- reads ----------


def hybrid_search(dense: list[float], sparse: models.SparseVector, limit: int,
                  doc_ids: list[str] | None = None) -> list[models.ScoredPoint]:
    """Dense + BM25 searched in parallel, merged with Reciprocal Rank Fusion (RRF)."""
    query_filter = None
    if doc_ids:
        query_filter = models.Filter(
            must=[models.FieldCondition(key="doc_id", match=models.MatchAny(any=doc_ids))]
        )
    response = get_qdrant().query_points(
        get_settings().chunks_collection,
        prefetch=[
            models.Prefetch(query=dense, using="dense", limit=limit, filter=query_filter),
            models.Prefetch(query=sparse, using="bm25", limit=limit, filter=query_filter),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )
    return response.points


def get_chunks(chunk_ids: list[str]) -> dict[str, dict]:
    """Payloads of the given chunks, by id (used to fetch neighbours)."""
    if not chunk_ids:
        return {}
    records = get_qdrant().retrieve(
        get_settings().chunks_collection, ids=chunk_ids, with_payload=True
    )
    return {str(record.id): record.payload for record in records}


def search_documents(dense: list[float], limit: int) -> list[models.ScoredPoint]:
    """Search the document summary index ("which documents cover X?")."""
    response = get_qdrant().query_points(
        get_settings().documents_collection, query=dense, using="dense", limit=limit,
        with_payload=True,
    )
    return response.points
