"""
Cross-encoder reranking with bge-reranker-v2-m3, running locally on the Mac GPU (MPS).

The model (~2.3 GB) is downloaded on first use and loaded once per process.
Set RERANKER_BACKEND=none to skip reranking (keeps the hybrid search order).
"""

import threading
from functools import lru_cache

from app.core.config import get_settings

# Each chat answer runs in its own thread. One lock makes sure the 2.3 GB model is
# loaded once and that two threads never use the GPU (MPS) model at the same time.
_lock = threading.Lock()


@lru_cache
def _model():
    import torch  # imported lazily: loading torch takes a few seconds
    from sentence_transformers import CrossEncoder

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return CrossEncoder(get_settings().reranker_model, device=device, max_length=512)


def rerank(query: str, texts: list[str]) -> list[float]:
    """One relevance score per text (higher = more relevant)."""
    if not texts:
        return []
    if get_settings().reranker_backend == "none":
        return [float(len(texts) - i) for i in range(len(texts))]  # keep the given order
    with _lock:
        scores = _model().predict([(query, text) for text in texts], batch_size=16)
    return [float(score) for score in scores]
