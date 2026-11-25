"""
The LLM entry point used by the rest of the app.

    llm = get_llm()
    route = llm.json("fast", system, user, RouteDecision)
    for text in llm.stream("main", system, messages): ...

"fast" and "main" map to models in settings ("anthropic/claude-haiku-5-5", ...).
If the provider fails (down, rate-limited, no key), the call is retried once on the
fallback model for that kind (LLM_FAST_FALLBACK_MODEL for "fast", LLM_FALLBACK_MODEL
otherwise), which should be on the other provider. A stream only falls
back if it failed before sending any text.
"""

import logging
from collections.abc import Iterator
from functools import lru_cache
from typing import Literal

from app.core.config import get_settings
from app.providers.llm_base import LLMProvider, ProviderUnavailable, T

log = logging.getLogger(__name__)

ModelKind = Literal["fast", "main", "judge"]


def split_model(name: str) -> tuple[str, str]:
    """'anthropic/claude-sonnet-5-5' -> ('anthropic', 'claude-sonnet-5-5')."""
    provider, _, model = name.partition("/")
    if provider not in ("anthropic", "openai") or not model:
        raise ValueError(f"model must look like 'anthropic/<id>' or 'openai/<id>', got {name!r}")
    return provider, model


@lru_cache
def get_provider(provider: str) -> LLMProvider:
    # Imported here so a missing key for one provider never breaks the other.
    settings = get_settings()
    if provider == "anthropic":
        from app.providers.anthropic_llm import AnthropicLLM

        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_refusal_fallback)
    from app.providers.openai_llm import OpenAILLM

    return OpenAILLM(settings.openai_api_key)


_warned: set[str] = set()


def _warn_once(name: str, error: Exception) -> None:
    """Warn the first time a model falls back; later fallbacks only go to debug logs."""
    level = logging.DEBUG if name in _warned else logging.WARNING
    _warned.add(name)
    log.log(level, "LLM %s unavailable, using fallback: %s", name, error)


class LLM:
    def __init__(self, models: dict[str, str], fallbacks: dict[str, str], effort: str) -> None:
        self.models = models
        self.fallbacks = fallbacks  # per kind; a missing / empty entry means no fallback
        self.effort = effort

    def model_name(self, kind: ModelKind) -> str:
        return self.models[kind]

    def _candidates(self, kind: ModelKind) -> list[str]:
        primary = self.models[kind]
        fallback = self.fallbacks.get(kind, "")
        return [primary, fallback] if fallback and fallback != primary else [primary]

    def json(self, kind: ModelKind, system: str, user: str, schema: type[T],
             max_tokens: int = 4000) -> T:
        last_error: Exception | None = None
        for name in self._candidates(kind):
            provider, model = split_model(name)
            try:
                return get_provider(provider).complete_json(
                    model, system, user, schema, max_tokens, effort="low"  # quick tasks
                )
            except ProviderUnavailable as error:
                _warn_once(name, error)
                last_error = error
        raise last_error or ProviderUnavailable("no model configured")

    def stream(self, kind: ModelKind, system: str, messages: list[dict],
               max_tokens: int = 8000) -> Iterator[str]:
        last_error: Exception | None = None
        for name in self._candidates(kind):
            provider, model = split_model(name)
            sent_text = False
            try:
                for text in get_provider(provider).stream_text(
                    model, system, messages, max_tokens, self.effort
                ):
                    sent_text = True
                    yield text
                return
            except ProviderUnavailable as error:
                if sent_text:  # half an answer was already shown; don't start over
                    raise
                _warn_once(name, error)
                last_error = error
        raise last_error or ProviderUnavailable("no model configured")


@lru_cache
def get_llm() -> LLM:
    settings = get_settings()
    return LLM(
        models={
            "fast": settings.llm_fast_model,
            "main": settings.llm_main_model,
            "judge": settings.llm_judge_model,
        },
        fallbacks={
            "fast": settings.llm_fast_fallback_model,
            "main": settings.llm_fallback_model,
            "judge": settings.llm_fallback_model,
        },
        effort=settings.answer_effort,
    )
