"""
The answer pipeline end to end, with search and LLM replaced by test doubles
(no API keys, Qdrant or reranker needed).
"""

import pytest

from app.generation import pipeline
from app.generation.prompts import NOT_FOUND
from app.generation.router import RouteDecision
from app.retrieval.search import Source

LEAVE = Source(number=1, doc_id="d1", title="HR Handbook", citation_label="p. 12",
               file_format="pdf", section_path="Benefits > Leave",
               text="Employees receive 16 weeks of paid parental leave.", score=0.9)


class FakeLLM:
    def model_name(self, kind):
        return "anthropic/claude-sonnet-5-5"

    def stream(self, kind, system, messages):
        assert "<source id=\"1\"" in messages[-1]["content"]
        yield from ["Employees receive 16 weeks ", "of paid parental leave [1]."]


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(pipeline, "get_llm", lambda: FakeLLM())
    monkeypatch.setattr(pipeline, "route_question",
                        lambda q, h: RouteDecision(standalone_question=q, route="simple", reason="fact"))


def test_answer_with_verified_citation(fakes, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda q: [LEAVE])
    events = list(pipeline.answer_question("How long is parental leave?", history=[]))

    types = [e["type"] for e in events]
    assert types[:2] == ["status", "route"] and types[-1] == "done"
    assert "token" in types and "sources" in types

    done = events[-1]
    assert done["answer"] == "Employees receive 16 weeks of paid parental leave [1]."
    assert done["citations"][0]["supported"] and done["citations"][0]["citation_label"] == "p. 12"


def test_no_sources_gives_not_found_without_calling_the_model(fakes, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda q: [])
    done = list(pipeline.answer_question("What is the CEO's shoe size?", history=[]))[-1]
    assert done["answer"] == NOT_FOUND and done["not_found"]


def test_greeting_is_answered_without_searching(monkeypatch):
    class ChatLLM(FakeLLM):
        def stream(self, kind, system, messages):
            assert kind == "fast"
            yield "Hi! Ask me about your documents."

    monkeypatch.setattr(pipeline, "get_llm", lambda: ChatLLM())
    monkeypatch.setattr(pipeline, "route_question",
                        lambda q, h: RouteDecision(standalone_question=q, route="chat", reason="greeting"))
    monkeypatch.setattr(pipeline, "retrieve", lambda q: pytest.fail("greetings must not search"))

    done = list(pipeline.answer_question("hi", history=[]))[-1]
    assert done["route"] == "chat" and done["citations"] == [] and not done["not_found"]
