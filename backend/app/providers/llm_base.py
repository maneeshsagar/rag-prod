"""
The interface every LLM provider follows, and the errors they raise.

Messages are plain {"role": "user" | "assistant", "content": str} dicts, so the
rest of the code never sees provider-specific formats.
"""

from collections.abc import Iterator
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderUnavailable(Exception):
    """The provider is down, rate-limited, overloaded or not configured -> try the fallback."""


class ModelRefusal(Exception):
    """The model declined to answer."""


class LLMProvider(Protocol):
    def complete_json(
        self, model: str, system: str, user: str, schema: type[T], max_tokens: int,
        effort: str = "low",
    ) -> T:
        """Return a validated instance of `schema` (structured output)."""

    def stream_text(
        self, model: str, system: str, messages: list[dict], max_tokens: int, effort: str
    ) -> Iterator[str]:
        """Yield the answer text as it is generated."""
