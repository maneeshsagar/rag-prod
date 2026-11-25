"""
Dense embeddings with OpenAI (text-embedding-3-large, shortened to 1024 dimensions).

- Documents are embedded in batches of 100.
- Query embeddings are cached in Redis for a day (repeated questions cost nothing).
"""

import hashlib
import json
from collections.abc import Callable
from functools import lru_cache

import openai
from redis import Redis

from app.core.config import get_settings
from app.core.usage import record

BATCH_SIZE = 100
QUERY_CACHE_SECONDS = 24 * 3600


class Embedder:
    def __init__(self, api_key: str, model: str, dimensions: int, redis: Redis) -> None:
        # max_retries: the SDK waits and retries on rate limits (429) and server errors;
        # bulk ingestion can hit rate limits, so be patient.
        self.client = openai.OpenAI(api_key=api_key or "missing", max_retries=6, timeout=120)
        self.model = model
        self.dimensions = dimensions
        self.redis = redis

    def embed_documents(self, texts: list[str],
                        on_batch: Callable[[int, int], None] | None = None) -> list[list[float]]:
        """Embed in batches. `on_batch(done, total)` is called after each batch (progress / stop)."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            response = self.client.embeddings.create(
                model=self.model, input=batch, dimensions=self.dimensions
            )
            vectors.extend(item.embedding for item in response.data)
            record(self.model, response.usage.prompt_tokens)
            if on_batch:
                on_batch(len(vectors), len(texts))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        key = "qemb:" + hashlib.sha256(
            f"{self.model}:{self.dimensions}:{text}".encode()
        ).hexdigest()
        cached = self.redis.get(key)
        if cached:
            return json.loads(cached)
        vector = self.embed_documents([text])[0]
        self.redis.set(key, json.dumps(vector), ex=QUERY_CACHE_SECONDS)
        return vector


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    return Embedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        redis=Redis.from_url(settings.redis_url),
    )
