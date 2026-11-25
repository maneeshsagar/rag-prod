"""If one provider is unavailable, the LLM wrapper falls back to the other."""

import pytest
from pydantic import BaseModel

from app.providers import llm as llm_module
from app.providers.llm import LLM, split_model
from app.providers.llm_base import ProviderUnavailable


class Answer(BaseModel):
    text: str


class DownProvider:
    def complete_json(self, *args, **kwargs):
        raise ProviderUnavailable("down")

    def stream_text(self, *args, **kwargs):
        raise ProviderUnavailable("down")
        yield  # makes this a generator


class WorkingProvider:
    def complete_json(self, model, system, user, schema, max_tokens, effort="low"):
        return schema(text=f"from {model}")

    def stream_text(self, model, system, messages, max_tokens, effort):
        yield from ["Hello ", model]


@pytest.fixture
def providers(monkeypatch):
    fake = {"anthropic": DownProvider(), "openai": WorkingProvider()}
    monkeypatch.setattr(llm_module, "get_provider", lambda name: fake[name])


def _llm() -> LLM:
    return LLM({"fast": "anthropic/claude-haiku-5-5", "main": "anthropic/claude-sonnet-5-5",
                "judge": "openai/gpt-5-mini"},
               fallbacks={"fast": "openai/gpt-5-mini", "main": "openai/gpt-5"}, effort="medium")


def test_json_falls_back(providers):
    assert _llm().json("fast", "sys", "user", Answer).text == "from gpt-5-mini"


def test_stream_falls_back(providers):
    assert "".join(_llm().stream("main", "sys", [])) == "Hello gpt-5"


def test_no_fallback_configured_raises(providers):
    llm = LLM({"fast": "anthropic/x", "main": "anthropic/x", "judge": "openai/y"}, {}, "low")
    with pytest.raises(ProviderUnavailable):
        llm.json("fast", "sys", "user", Answer)


def test_split_model():
    assert split_model("openai/gpt-5") == ("openai", "gpt-5")
    with pytest.raises(ValueError):
        split_model("claude-sonnet-5-5")
