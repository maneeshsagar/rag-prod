"""
FastAPI application entry point.

Run with:  make api   (then open http://localhost:8010/docs)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, conversations, documents, feedback, health
from app.api.chat import recover_unanswered_questions
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.ingestion.pipeline import recover_interrupted_jobs

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    log = logging.getLogger(__name__)
    if get_settings().secret_key == "change-me-in-env":
        log.warning("SECRET_KEY is the default: set it in .env")
    if interrupted := recover_interrupted_jobs():
        log.warning("marked %d interrupted ingest(s) as failed", interrupted)
    if unanswered := recover_unanswered_questions():
        log.warning("closed %d question(s) whose answer was interrupted", unanswered)
    yield


app = FastAPI(title="RAG API", version="0.1.0", lifespan=lifespan)

# Allow the Next.js frontend (separate folder, port 3010) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(feedback.router)
app.include_router(documents.router)
