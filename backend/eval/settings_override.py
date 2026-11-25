"""
Change settings for one eval run:  --set LLM_MAIN_MODEL=openai/gpt-5

Overrides are set as environment variables (which win over .env), then every cached
object built from the old settings (settings, LLM, embedder) is dropped so the new
values take effect.
"""

import os

from app.core.config import Settings, get_settings
from app.providers import embedder, llm


def apply_overrides(pairs: list[str]) -> None:
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or key.lower() not in Settings.model_fields:
            raise SystemExit(f"--set expects KEY=VALUE with a known setting, got {pair!r}")
        os.environ[key.upper()] = value

    get_settings.cache_clear()
    llm.get_llm.cache_clear()
    llm.get_provider.cache_clear()
    embedder.get_embedder.cache_clear()
    get_settings()  # fail early on an invalid value
