"""Conversation history: list, open, delete (only your own)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.db.models import Conversation, Message, User
from app.db.session import get_db

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _own_conversation(db: Session, conversation_id: str, user: User) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Conversation).where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc()).limit(100)
    ).all()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in rows]


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    conversation = _own_conversation(db, conversation_id, user)
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
    ).all()
    return {
        "id": conversation.id,
        "title": conversation.title,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "citations": m.citations or [],
             "route": m.route, "created_at": m.created_at}
            for m in messages
        ],
    }


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
    db.delete(_own_conversation(db, conversation_id, user))
    db.commit()
    return {"deleted": True}
