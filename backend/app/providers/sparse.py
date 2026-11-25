"""
BM25 sparse vectors (keyword search) with FastEmbed's "Qdrant/bm25" model.

Qdrant applies IDF on its side (the collection uses the IDF modifier), so these
vectors only hold term frequencies. Runs locally on CPU; the model is tiny.
"""

from functools import lru_cache

from fastembed import SparseTextEmbedding
from qdrant_client import models


@lru_cache
def _model() -> SparseTextEmbedding:
    return SparseTextEmbedding(model_name="Qdrant/bm25")


def _to_qdrant(embedding) -> models.SparseVector:
    return models.SparseVector(
        indices=embedding.indices.tolist(), values=embedding.values.tolist()
    )


def sparse_documents(texts: list[str]) -> list[models.SparseVector]:
    return [_to_qdrant(e) for e in _model().embed(texts)]


def sparse_query(text: str) -> models.SparseVector:
    return _to_qdrant(next(iter(_model().query_embed(text))))
