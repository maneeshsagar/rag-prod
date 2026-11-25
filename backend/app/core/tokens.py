"""
Token counting for chunk sizes and context budgets.

Uses the tokenizer of the OpenAI embedding model (cl100k_base). Counts are close
enough for sizing chunks; we never need exact Claude token counts here.
"""

from functools import lru_cache

import tiktoken


@lru_cache
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _encode(text: str) -> list[int]:
    return _encoder().encode(text, disallowed_special=())


def count_tokens(text: str) -> int:
    return len(_encode(text))


def first_tokens(text: str, max_tokens: int) -> str:
    """The beginning of `text`, at most `max_tokens` long."""
    tokens = _encode(text)
    return text if len(tokens) <= max_tokens else _encoder().decode(tokens[:max_tokens])


def last_tokens(text: str, max_tokens: int) -> str:
    """The end of `text`, at most `max_tokens` long (used for chunk overlap)."""
    tokens = _encode(text)
    return text if len(tokens) <= max_tokens else _encoder().decode(tokens[-max_tokens:])
