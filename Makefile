# Common commands. Run `make help` to list them.

.PHONY: help up s3-up down logs install init db-init db-reset storage-init index-init \
        user ingest worker ingest-status ingest-retry versions api web test lint questions eval

help:
	@echo "make up            Start Postgres, Redis, Qdrant (Docker)"
	@echo "make s3-up         Also start SeaweedFS (S3-compatible storage, optional)"
	@echo "make down          Stop them (data is kept)"
	@echo "make logs          Follow infrastructure logs"
	@echo "make install       Install backend + frontend dependencies"
	@echo "make init          Create database tables, storage folder/bucket, Qdrant collections"
	@echo "make db-reset      Drop and recreate all tables (local development only)"
	@echo "make user EMAIL=.. NAME=.. PASSWORD=.. [ROLE=admin]   Create a login"
	@echo "make ingest DIR=data/smoke   Queue a file or folder and process it [QUEUE=1: queue only]"
	@echo "make worker        Run an extra ingestion worker (one per terminal)"
	@echo "make ingest-status Queue counts, speed, failures"
	@echo "make ingest-retry  Queue every failed document again"
	@echo "make versions      Recompute near-duplicate / version links for all documents"
	@echo "make api           Run the backend API on http://localhost:8010"
	@echo "make web           Run the chat UI on http://localhost:3010"
	@echo "make questions     Draft eval questions from indexed documents (you review them)"
	@echo "make eval          Run the eval on the reviewed questions"
	@echo "make test          Run backend tests"
	@echo "make lint          Lint backend code"

up:
	docker compose up -d

s3-up:
	docker compose --profile s3 up -d

down:
	docker compose --profile s3 down

logs:
	docker compose logs -f

install:
	cd backend && uv sync
	cd frontend && npm install

init: db-init storage-init index-init

db-init:
	cd backend && uv run python -m scripts.init_db

db-reset:
	cd backend && uv run python -m scripts.init_db --reset

storage-init:
	cd backend && uv run python -m scripts.init_storage

index-init:
	cd backend && uv run python -m scripts.init_index

ROLE ?= user
user:
	cd backend && uv run python -m scripts.create_user --email "$(EMAIL)" --name "$(NAME)" --password "$(PASSWORD)" --role "$(ROLE)"

DIR ?= data/smoke
ingest:
	cd backend && uv run python -m scripts.ingest "../$(DIR)" $(if $(QUEUE),--queue-only)

worker:
	cd backend && uv run python -m scripts.worker

ingest-status:
	cd backend && uv run python -m scripts.ingest_status

ingest-retry:
	cd backend && uv run python -m scripts.ingest_status --retry-failed

versions:
	cd backend && uv run python -m scripts.link_versions

api:
	cd backend && uv run uvicorn app.main:app --reload --port 8010

web:
	cd frontend && npm run dev

questions:
	cd backend && uv run python -m eval.generate_questions

eval:
	cd backend && uv run python -m eval.run_eval

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check .
