"""
Router: one fast-model call that (1) rewrites follow-up questions into standalone
ones and (2) picks the route: "chat" (greeting / small talk, no search),
"simple" (one search) or "agentic" (several searches).
"""

from typing import Literal

from pydantic import BaseModel

from app.generation.prompts import ROUTER_SYSTEM
from app.providers.llm import get_llm

HISTORY_CHARS = 600  # per earlier message; enough to resolve references


class RouteDecision(BaseModel):
    standalone_question: str
    route: Literal["chat", "simple", "agentic"]
    reason: str


def route_question(question: str, history: list[dict]) -> RouteDecision:
    lines = [f"{m['role']}: {m['content'][:HISTORY_CHARS]}" for m in history[-6:]]
    chat = "\n".join(lines) if lines else "(no earlier messages)"
    user = f"Chat history:\n{chat}\n\nLatest question: {question}"
    return get_llm().json("fast", ROUTER_SYSTEM, user, RouteDecision, max_tokens=2_000)
