"""POST /feedback — thumbs up / down (+ optional comment) on an answer."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.db.models import Feedback, Message, User
from app.db.session import get_db

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/feedback")
def give_feedback(body: FeedbackRequest, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    if db.get(Message, body.message_id) is None:
        raise HTTPException(status_code=404, detail="Message not found")

    # One rating per user per answer: update it if it already exists.
    feedback = db.scalar(select(Feedback).where(
        Feedback.message_id == body.message_id, Feedback.user_id == user.id
    ))
    if feedback is None:
        feedback = Feedback(message_id=body.message_id, user_id=user.id, rating=body.rating)
        db.add(feedback)
    feedback.rating, feedback.comment = body.rating, body.comment
    db.commit()
    return {"saved": True}
