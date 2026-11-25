"""
Application settings.

All configuration comes from environment variables (or the project-root `.env` file).
Nothing is hard-coded elsewhere, so moving to AWS later is a config change.

Usage:
    from app.core.config import get_settings
    settings = get_settings()
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> project root is three folders up
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # --- Infrastructure ---
    database_url: str = "postgresql+psycopg://rag:rag@localhost:5433/rag"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    data_dir: Path = PROJECT_ROOT / "data"

    # --- File storage: "local" folder for development, "s3" for SeaweedFS / Amazon S3 ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = PROJECT_ROOT / "data" / "storage"  # used when storage_backend=local

    # S3 settings (used when storage_backend=s3)
    s3_endpoint_url: str = "http://localhost:8333"  # empty = real Amazon S3
    s3_access_key: str = "rag"
    s3_secret_key: str = "ragsecret123"
    s3_bucket: str = "documents"
    s3_region: str = "us-east-1"

    # --- AI provider keys ---
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # --- Models. Format: "<provider>/<model id>", provider = anthropic | openai ---
    llm_fast_model: str = "anthropic/claude-haiku-5-5"  # routing, rewriting, planning, summaries
    llm_main_model: str = "anthropic/claude-sonnet-5-5"  # final answers
    llm_fallback_model: str = "openai/gpt-5"  # used if the main model's provider fails; "" = off
    llm_fast_fallback_model: str = "openai/gpt-5-mini"  # fallback for the quick tasks; "" = off
    llm_judge_model: str = "openai/gpt-5-mini"  # eval judge: the provider NOT used for answers
    answer_effort: Literal["low", "medium", "high"] = "medium"  # Claude effort for answers
    anthropic_refusal_fallback: bool = True  # server-side fallback if Claude declines

    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1024
    reranker_backend: Literal["local", "none"] = "local"  # "none" = skip reranking
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # --- Retrieval tuning (locked in Phase 2 after evals) ---
    chunk_target_tokens: int = 450
    chunk_max_tokens: int = 600
    chunk_overlap_tokens: int = 60
    search_candidates: int = 50  # hybrid search results before reranking
    rerank_top_k: int = 6  # chunks kept after reranking
    min_rerank_score: float = 0.01  # reranker score 0..1; below this a chunk is treated as noise
    context_token_budget: int = 10_000  # hard cap on context sent to the answer model
    short_section_tokens: int = 1_500  # sections shorter than this are sent whole
    agent_max_searches: int = 4

    # --- Qdrant collection names ---
    chunks_collection: str = "chunks"  # alias -> chunks_v1
    documents_collection: str = "documents"  # one summary vector per document

    # --- Web UI origins allowed to call the API (CORS) ---
    cors_origins: list[str] = ["http://localhost:3010"]

    # --- Auth ---
    secret_key: str = "change-me-in-env"  # signs login tokens
    token_ttl_hours: int = 24 * 7

    # --- Tracing (Langfuse Cloud) ---
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"


@lru_cache
def get_settings() -> Settings:
    """Load settings once and reuse them everywhere."""
    return Settings()
