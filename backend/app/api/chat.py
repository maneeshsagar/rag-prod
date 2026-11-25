"""
POST /chat — ask a question; the answer streams back as Server-Sent Events.

Event types: conversation, status, route, sources, token, done, error
(see app/generation/pipeline.py).

The answer is produced in a background thread that always runs to the end and
saves the answer, even if the browser disconnects (closed tab, switched chat).
The HTTP response just relays the thread's events while someone is listening.
"""

import json
import logging
import queue
import threading
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.auth import current_user
from app.db.models import Conversation, Message, User
from app.db.session import SessionLocal
from app.generation.pipeline import answer_question
from app.providers.llm_base import ModelRefusal, ProviderUnavailable

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)

END = None  # marks the end of the event queue


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _load_history(session, conversation_id: str) -> list[dict]:
    messages = session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    ).all()
    # Error notices are shown to the user but are not part of the conversation.
    return [{"role": m.role, "content": m.content} for m in messages if m.route != "error"]


def _error_message(error: Exception) -> str:
    if isinstance(error, ProviderUnavailable):
        return "The AI service is unavailable right now (check the API keys). Please try again."
    if isinstance(error, ModelRefusal):
        return "The model declined to answer this question."
    return "Something went wrong while answering. Please try again."


def _produce_answer(conversation_id: str, question: str, history: list[dict],
                    events: queue.Queue) -> None:
    """Runs in a background thread: answer the question, save it, push SSE events."""
    try:
        for event in answer_question(question, history):
            if event["type"] != "done":
                events.put(sse(event["type"], {k: v for k, v in event.items() if k != "type"}))
                continue

            with SessionLocal() as session:
                message = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=event["answer"],
                    citations=event["citations"],
                    route=event["route"],
                    model=event["model"],
                    latency_ms=event["latency_ms"],
                )
                session.add(message)
                conversation = session.get(Conversation, conversation_id)
                if conversation is not None:
                    conversation.updated_at = func.now()  # moves the chat to the top of the list
                session.commit()
                done = {k: v for k, v in event.items() if k not in ("type", "sources")}
                events.put(sse("done", {**done, "message_id": message.id}))
    except Exception as error:
        log.exception("chat failed")
        # Save the failure too, so the conversation never ends with an unanswered question.
        with SessionLocal() as session:
            session.add(Message(conversation_id=conversation_id, role="assistant",
                                content=f"⚠ {_error_message(error)}", route="error"))
            session.commit()
        events.put(sse("error", {"message": _error_message(error)}))
    finally:
        events.put(END)


INTERRUPTED = "⚠ This answer was interrupted (the server restarted). Please ask again."


def recover_unanswered_questions() -> int:
    """At startup: a conversation ending with a question lost its answer in a restart.
    Add a short notice so the UI does not wait for it forever."""
    with SessionLocal() as session:
        last_messages = session.scalars(
            select(Message)
            .distinct(Message.conversation_id)
            .order_by(Message.conversation_id, Message.created_at.desc())
        ).all()
        unanswered = [m for m in last_messages if m.role == "user"]
        for message in unanswered:
            session.add(Message(conversation_id=message.conversation_id, role="assistant",
                                content=INTERRUPTED, route="error"))
        session.commit()
        return len(unanswered)


def _start_chat(user_id: str, request: ChatRequest) -> tuple[str, str, queue.Queue]:
    """Save the question and start answering it in the background."""
    with SessionLocal() as session:
        if request.conversation_id:
            conversation = session.get(Conversation, request.conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            conversation = Conversation(user_id=user_id, title=request.question[:80])
            session.add(conversation)
            session.commit()

        history = _load_history(session, conversation.id)
        session.add(Message(conversation_id=conversation.id, role="user", content=request.question))
        session.commit()
        conversation_id, title = conversation.id, conversation.title

    events: queue.Queue = queue.Queue()
    threading.Thread(
        target=_produce_answer, args=(conversation_id, request.question, history, events),
        daemon=True, name=f"chat-{conversation_id[:8]}",
    ).start()
    return conversation_id, title, events


def _relay(conversation_id: str, title: str, events: queue.Queue) -> Iterator[str]:
    """Pass the thread's events to the browser. If the browser leaves, the thread keeps going."""
    yield sse("conversation", {"conversation_id": conversation_id, "title": title})
    while (item := events.get()) is not END:
        yield item


@router.post("/chat")
def chat(request: ChatRequest, user: User = Depends(current_user)) -> StreamingResponse:
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty")
    conversation_id, title, events = _start_chat(user.id, request)
    return StreamingResponse(
        _relay(conversation_id, title, events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
