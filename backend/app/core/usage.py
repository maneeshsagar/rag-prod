"""
Token usage and cost of AI calls.

    with track_usage() as meter:
        answer = ...                 # every LLM / embedding call inside is counted
    meter.cost_usd, meter.tokens_in, meter.tokens_out

Providers call `record(...)` after each request. Meters can be nested (e.g. the
eval counts a whole run while each answer counts itself): a call is added to every
active meter in the current thread.

Prices are US dollars per 1 million tokens. Check them against the providers'
pricing pages now and then; a model missing here is counted in tokens with cost 0
and logged once.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    input: float  # per 1M uncached input tokens
    output: float  # per 1M output tokens
    cached_input: float  # per 1M input tokens read from the prompt cache


# Sources (2026-11-25): Claude prices from Anthropic's model docs; OpenAI prices from
# developers.openai.com/api/docs/pricing. Haiku 5.5 costs 5x more above 100K-token
# prompts, which this app never sends (context is capped at ~10k tokens).
PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),  # cached: the usual 0.1x of input
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "gpt-5": Price(1.25, 10.00, 0.125),
    "gpt-5-mini": Price(0.25, 2.00, 0.025),
    "text-embedding-3-large": Price(0.13, 0.0, 0.0),
    "text-embedding-3-small": Price(0.02, 0.0, 0.0),
}


@dataclass
class ModelUsage:
    calls: int = 0
    tokens_in: int = 0  # all input tokens, cached ones included
    tokens_cached: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class UsageMeter:
    by_model: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def tokens_in(self) -> int:
        return sum(u.tokens_in for u in self.by_model.values())

    @property
    def tokens_out(self) -> int:
        return sum(u.tokens_out for u in self.by_model.values())

    @property
    def cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.by_model.values())

    def summary(self) -> dict:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "by_model": {name: vars(u) for name, u in self.by_model.items()},
        }


_active: ContextVar[tuple[UsageMeter, ...]] = ContextVar("usage_meters", default=())
_unpriced: set[str] = set()


def cost_of(model: str, tokens_in: int, tokens_out: int, tokens_cached: int = 0) -> float:
    price = PRICES.get(model) or PRICES.get(_base_name(model))
    if price is None:
        if model not in _unpriced:
            log.warning("no price for model %r: counted as $0 (add it to app/core/usage.py)", model)
            _unpriced.add(model)
        return 0.0
    uncached = tokens_in - tokens_cached
    return (uncached * price.input + tokens_cached * price.cached_input
            + tokens_out * price.output) / 1_000_000


def _base_name(model: str) -> str:
    """'gpt-5-mini-2025-08-07' -> 'gpt-5-mini' (dated snapshots share the price)."""
    parts = model.split("-")
    while parts and parts[-1].isdigit() and len(parts[-1]) >= 2:
        parts.pop()
    return "-".join(parts)


def record(model: str, tokens_in: int, tokens_out: int = 0, tokens_cached: int = 0) -> None:
    """Add one API call to every active meter."""
    cost = cost_of(model, tokens_in, tokens_out, tokens_cached)
    for meter in _active.get():
        usage = meter.by_model.setdefault(model, ModelUsage())
        usage.calls += 1
        usage.tokens_in += tokens_in
        usage.tokens_cached += tokens_cached
        usage.tokens_out += tokens_out
        usage.cost_usd += cost


@contextmanager
def track_usage():
    meter = UsageMeter()
    token = _active.set((*_active.get(), meter))
    try:
        yield meter
    finally:
        try:
            _active.reset(token)
        except ValueError:  # a generator closed from another context: nothing to undo
            pass
