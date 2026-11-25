# Production RAG System — Build Plan (v5)

**Scope:** 30k–50k documents (100+ pages each) · 2,000 users (at launch) · high accuracy
**Approach:** **Build everything locally first** (Phases 1–3) → **move to AWS in Phase 4** → launch
**Timeline:** ~12 weeks · 2–3 engineers
**Last updated:** 2026-11-24

> No system has zero issues. The goal is a system that is **accurate by design, catches its own failures fast, and is cheap to fix**. Every change is measured against an evaluation set before shipping.

---

## 0. Decisions (confirmed)

| Topic | Decision | Impact on design |
|---|---|---|
| Build approach | **Everything built and tuned locally; AWS only in Phase 4** | All components behind interfaces + env config so the AWS move is configuration + Terraform, not a rewrite |
| Local documents | **5–6 docs to build the pipeline → 1–2k doc sample for accuracy tuning** | Full 30k–50k ingest happens on AWS (Phase 4) |
| Local ports | **Postgres 5433, Redis 6379, Qdrant 6333 (v1.20), API 8010, UI 3010** (5432 / 8000 / 3000 are used by other projects on this Mac) | — |
| Local runtime | **Postgres, Redis, Qdrant in Docker; files in a local folder (`data/storage/`), or optional SeaweedFS (S3-compatible) via `make s3-up`; backend + frontend run on the Mac, connecting on localhost** | Less memory, fast reload, reranker can use the Apple GPU in-process. Containers for the backend come in Phase 4 |
| Local hardware | **Mac, 16 GB RAM** | Lean Docker stack, memory budget (§2.3), Langfuse optional, Docling single-worker |
| Models | **OpenAI + Anthropic API keys** (official `anthropic` + `openai` SDKs behind one small interface, with automatic fallback) | Claude or GPT for generation (switchable by config); OpenAI for embeddings; local reranker |
| Formats | **PDF, DOCX, PPTX, HTML, Markdown** | PyMuPDF4LLM (layout engine, no OCR), python-docx, python-pptx, BeautifulSoup; Docling only if the sample shows a need |
| Images / scans | **None** | No OCR, no vision LLM |
| Doc length | **100+ pages** | Hierarchical chunking, doc summary index, neighbour expansion, context cap |
| Permissions | **Everyone sees everything (for now)** | `acl_groups` field kept (default `public`), filter path exists but is a no-op |
| Language | **English only** | English BM25; English-strong embedding models |
| Questions | **All kinds** (lookup, table, analytical, multi-hop, aggregation) | Router + simple RAG + agentic RAG + SQL route |
| Test questions | **Claude drafts from the documents, you review and correct** | Eval tooling includes a question generator + review file |

---

## 1. Goals & Non-Goals

### Goals
- Accurate, grounded answers with **verifiable citations** (document + location + passage; location = page for PDF, slide for PPTX, section heading for DOCX/HTML/MD)
- Explicit **"I don't know"** when evidence is insufficient
- Handles lookup, table, analytical, multi-hop and aggregation questions
- Fully working and evaluated **locally** before any AWS work
- Local → AWS is configuration + Terraform, not a rewrite

### Non-Goals (v1)
- OCR / image / scanned-document handling
- Access control enforcement (designed for, not enabled)
- SSO before the AWS phase (simple local login until then)
- GraphRAG, Kubernetes, multi-region
- Semantic answer cache (accuracy risk — see §7)
- User uploads, chat sharing, mobile app

---

## 2. Capacity Sizing

### 2.1 Full Corpus (AWS, Phase 4)
| Item | Estimate |
|---|---|
| Documents | 30k–50k |
| Pages | ~100–150 per doc → **~5M pages** |
| Chunks (~500 tokens, child level) | **~5–7M** |
| Parent sections | ~500k–1M |
| Vectors (1024-dim) | ~25 GB float32 · **~6 GB int8** |

### 2.2 Local Workload
| | Stage A: build | Stage B: accuracy tuning |
|---|---|---|
| Documents | **5–6** (mix of all formats; at least one 100+ page PDF with tables) | **1–2k** representative sample |
| Pages | ~500 | ~100k–200k |
| Chunks | ~1–2k | ~200k–400k |
| Vectors (int8) | trivial | ~0.2–0.4 GB |
| Ingest time (estimate, verify) | minutes | **several hours to ~1 day** (fast parser for most PDFs; Docling only for table-heavy docs; resumable) |
| Embedding cost (OpenAI) | negligible | low (~100–200M tokens) — check current pricing |

### 2.3 16 GB Mac Memory Budget
| Component | Where | RAM |
|---|---|---|
| Postgres | Docker | ~0.5 GB |
| Redis | Docker | ~0.1 GB |
| Qdrant | Docker | ~1–2 GB |
| SeaweedFS (optional) | Docker | ~0.2 GB |
| API (FastAPI) | Docker | ~0.5 GB |
| Worker (Docling, concurrency 1) | Docker | ~3–4 GB |
| Web (Next.js dev) | Host or Docker | ~0.5–1 GB |
| Reranker model server (MPS) | Host (native) | ~1.5–2 GB |
| macOS + browser + IDE | Host | ~4–5 GB |
| **Total** | | **~12–15 GB** |

- Docker Desktop memory limit: **~8 GB**
- **Tracing uses Langfuse Cloud free tier** (decided) — self-hosted Langfuse would add ~3 GB RAM, too much for 16 GB
- Run ingestion and heavy eval runs separately from UI development when memory is tight

### 2.4 Traffic (production target)
| Item | Estimate |
|---|---|
| Users | 2,000 (local: developers only) |
| Peak QPS | ~10–20 → load-test at **40** (on AWS) |
| Concurrent streams | ~200–400 |

---

## 3. Architecture

### 3.1 Local (Phases 1–3)
```
 Browser ──► Next.js web (localhost:3000)
                    │
          ┌─────────▼──────────── API (FastAPI, docker) ──────────────────┐
          │ 1. Auth (simple local login)                                   │
          │ 2. Exact cache lookup (Redis)                                  │
          │ 3. Router: SQL route | simple RAG | agentic RAG                │
          │ 4. Query rewrite (follow-ups only)                             │
          │ 5. Hybrid search ──► Qdrant (dense + BM25, version down-rank)  │
          │ 6. Rerank ──► local reranker (host) │ Cohere (optional)        │
          │ 7. LLM (own wrapper) ► Anthropic (Claude) │ OpenAI (GPT)       │
          │ 8. Citation verification ──► SSE stream                        │
          └──────┬────────────────────────┬────────────────────────────────┘
                 │                        │
     Redis · Postgres · Qdrant · local folder or SeaweedFS (S3)          ← docker-compose
     Tracing: Langfuse Cloud (free tier)

     ┌──── HOST (native, uses Apple GPU via MPS) ─────────────┐
     │ Reranker server → /rerank  (FastAPI + sentence-        │
     │                   transformers CrossEncoder)           │
     └────────────────────────────────────────────────────────┘

     ┌──────── INGESTION WORKER (Celery, docker, concurrency 1) ─────────┐
     │ data/ or storage → Parse (tiered) → Quality check → Dedup/version → │
     │ Hierarchical chunk → Headers → Doc summary → Embed (OpenAI) →     │
     │ Upsert (versioned collection) → DLQ on failure                    │
     └───────────────────────────────────────────────────────────────────┘

     External APIs: Anthropic · OpenAI (· Cohere optional)
```

> **Why the reranker runs on the host:** Docker on macOS can't use the Apple GPU. The reranker runs natively and containers reach it via `host.docker.internal`. On AWS it becomes a container (CPU or GPU) or a hosted rerank API.

### 3.2 AWS (Phase 4)
Same services, mapped 1:1 (§13): ECS Fargate (API, worker, web), RDS Postgres, ElastiCache, S3, Qdrant cluster, reranker container or hosted rerank, same LLM/embedding APIs (or Bedrock — decided in Phase 4).

### 3.3 Principles
- **Everything behind interfaces + env config**: storage (local folder / SeaweedFS / S3), LLM (Anthropic/OpenAI/Bedrock), embedder, reranker
- Ingestion and query are separate processes — re-indexing never blocks queries
- Every pipeline change is gated by eval scores

---

## 4. Ingestion Pipeline

### 4.1 Sources
- Local: drop files into `data/inbox/` (watched) or upload through the API
- Change detection via content hash; deletions detected by reconciliation
- AWS: S3 + event notifications → queue

### 4.2 Parsing (tiered, no OCR)
| Format | Parser |
|---|---|
| PDF (text-based, simple layout) | **PyMuPDF4LLM** → Markdown (fast) |
| PDF (tables / complex layout) | **Docling** (OCR disabled) |
| DOCX | Docling (python-docx fallback) |
| PPTX | Docling — one section per slide, keep slide titles + speaker notes |
| HTML | Docling / trafilatura (strip nav, boilerplate) |
| Markdown | Native (split on headings) |

- Detection heuristic: table density / layout complexity on sampled pages chooses fast vs Docling path
- Output: Markdown with heading hierarchy, **page / slide numbers**, intact tables

### 4.3 Quality Gates
- Text chars per page above threshold (catches image-only pages)
- Heading structure detected (warn if a 100-page doc has no headings)
- Table extraction sanity check
- Language check (flag non-English)

Failures → **dead-letter queue** + admin page. Track **parse coverage %**.

### 4.4 Deduplication & Versioning
- Exact duplicates: SHA-256 content hash
- Near-duplicates: MinHash / LSH (`datasketch`)
- Metadata: `effective_date`, `is_latest`, `supersedes_doc_id`, `version_confidence` (`explicit` | `inferred`), `source_authority`
- **Default: down-rank older versions, don't hide them.** Hard-filter only when `version_confidence = explicit`
- Log how many results each query filters or down-ranks for version reasons

### 4.5 Hierarchical Chunking
```
Document  ── summary (1 per doc, in the document summary index)
  └─ Section (parent, follows headings)
       └─ Chunk (child, 300–600 tokens, 10–15% overlap)  ← indexed for search
```
- Split on headings first; token windows for very long sections
- Tables kept intact as their own chunks (repeat header row if split)
- Chunks linked to neighbours (`prev_chunk_id`, `next_chunk_id`) for context expansion
- **Lock chunking strategy on the 1–2k sample (Phase 2)** before the full AWS ingest

### 4.6 Contextual Chunk Headers
Prepend a **short** header to every chunk before embedding and BM25 indexing:
```
Document: <title> (<doc_type>, <effective_date>)
Section: <H1 > H2 > H3 breadcrumb>
---
<chunk text>
```
- **No document summary in chunk headers** — repeating it in every chunk of a long document makes chunks look alike and hurts retrieval
- **Per-chunk LLM contextualization** (1–2 sentences specific to each chunk): A/B on the 1–2k sample in Phase 2 using the **Anthropic Message Batches API** or **OpenAI Batch API** with a small model; adopt only if evals show a meaningful gain worth the cost

### 4.7 Embedding
- Default: **OpenAI `text-embedding-3-large`** with `dimensions=1024` (shortened vectors, lower storage); compare against `text-embedding-3-small` and, optionally, Voyage/Cohere in Phase 2
- **BM25** sparse vectors in Qdrant (English, IDF modifier)
- int8 quantization in Qdrant
- Embedding model + dimensions stored with each collection
- Batched requests with retry/backoff; embeddings cached by content hash (re-ingest of unchanged chunks costs nothing)

### 4.8 Document Summary Index
- One LLM-generated summary per document (purpose, key topics, date)
- Embedded into a separate `documents` collection
- Used for "which documents cover X?", narrowing multi-hop searches, and listing questions

### 4.9 Structured Field Extraction (Phase 3)
- Field schemas defined after seeing the 1–2k sample
- **Typed tables per document type** (e.g. `contracts(doc_id, party, value_usd, start_date, expiry_date)`), not a generic name/value table
- Exposed to text-to-SQL only via read-only views with column descriptions
- Small LLM with **structured output (JSON schema)**; validate types + store confidence
- Powers the **SQL route** for "how many…", "list all…"

### 4.10 Indexing & Consistency
- Versioned Qdrant collections (`chunks_v1`) behind an alias → zero-downtime re-index
- **Outbox pattern** for Postgres → Qdrant writes
- Idempotent Celery tasks (`acks_late`, idempotency keys), resumable ingest
- **Reconciliation job**: Postgres ⇄ Qdrant ⇄ storage; report drift, purge orphans
- Every document upsert/delete increments a **corpus revision counter** (used by the answer cache)

### 4.11 Chunk Metadata
```
doc_id, chunk_id, parent_id, prev_chunk_id, next_chunk_id, title, section_path,
page_start, page_end (PDF only), slide_no (PPTX only), citation_label,
doc_type, file_format, effective_date, is_latest, version_confidence, source_authority,
acl_groups[] (default ["public"]), content_hash, doc_version, index_version
```

---

## 5. Query Pipeline

### 5.1 Request Flow
1. **Auth** — simple local login (identity for history/feedback)
2. **Rate limit** — per user (2 concurrent, N/min)
3. **Exact cache** — first-turn standalone questions only; key = normalized query + corpus revision + model/prompt version
4. **Router** (small LLM, structured output) → one of:
   - **SQL route** (Phase 3) — aggregation / listing over typed field views. **Fallback:** on validation failure, error, or empty result → agentic RAG with a note that the count may be incomplete
   - **Simple RAG** — single-pass lookup
   - **Agentic RAG** — analytical, comparative, multi-hop: a code-controlled workflow (plan 1–3 queries → search → coverage check → follow-ups), **max 4 searches**
5. **Query rewrite** — follow-up turns only
6. **Filter extraction** — dates, doc types, document names
7. **Hybrid retrieval** — dense + BM25 in parallel → **RRF** → top 50–100 (hard version filter only where explicit)
8. **Rerank** — local cross-encoder (**bge-reranker-v2-m3**, or a smaller base model if latency on 16 GB is too high) → top 5–8; down-rank superseded versions; recency/authority boost. Optional: Cohere Rerank via API key
9. **Expand context within budget** — matched chunk + neighbours; full parent section only if short (< ~1.5k tokens); dedupe; **hard cap ~10–12k tokens**
10. **Generate** — grounded prompt, inline citations
11. **Verify citations** — cited IDs ∈ retrieved set; quoted spans exist in the chunk; else regenerate/flag
12. **Stream** via SSE

### 5.2 Agentic RAG Tools (read-only)
| Tool | Purpose |
|---|---|
| `search_chunks(query, filters)` | Hybrid search + rerank |
| `search_documents(query)` | Find relevant documents via summary index |
| `read_section(doc_id, section)` | Read a section of a long document (within the token budget) |
| `query_fields(sql)` | Aggregation over typed field views (Phase 3) |

### 5.3 Generation Rules
- Answer only from context; cite every claim `[n]`; each citation = chunk ID + short supporting quote
- Citation labels: **PDF → page**, **PPTX → slide**, **DOCX / HTML / MD → section heading**
- Insufficient evidence → *"I couldn't find this in the documents."*
- Conflicting sources → state both with dates
- Retrieved text in a delimited **untrusted** block (prompt-injection defense)

### 5.4 Models (switch by config: `<provider>/<model id>`)
| Task | Default | Alternative |
|---|---|---|
| Router, rewrite, filter extraction | Claude Haiku-class (Anthropic) | GPT mini-class (OpenAI) |
| Answer synthesis | Claude Sonnet-class | GPT flagship-class |
| Hard agentic queries | Claude Opus-class (capped) | GPT flagship-class |
| Doc summaries, field extraction, contextualization | Small model via **Batch API** | Either provider |
| Eval judge | The provider **not** used for generation (reduces self-preference bias) | — |
| Embedding | OpenAI `text-embedding-3-large` (1024 dims) | `text-embedding-3-small`; Voyage/Cohere if benchmarks win |
| Rerank | Local bge-reranker (host, MPS) | Cohere Rerank API |

- Both providers configured; **automatic fallback** to the other provider if one is down or rate-limited
- All model versions **pinned**; any change requires a full eval run
- Generation-model choice (Claude vs GPT) **decided by eval in Phase 2**

### 5.5 Conversation Handling
- Summarize history after ~6 turns; cap context budget
- Max output tokens + max agent steps enforced

### 5.6 Latency
| | Local (16 GB Mac, indicative) | AWS target |
|---|---|---|
| Route | ~0.5 s | 300 ms |
| Hybrid search | ~100 ms | 100 ms |
| Rerank (50 pairs) | ~0.5–1.5 s on MPS | 200 ms |
| First token | ~1 s | ~1 s |
| **p95 TTFT (simple)** | **< 4 s** | **< 2.5 s** |
| p95 agentic completion | < 30 s | < 20 s |

---

## 6. Access Control & Security

### 6.1 Now (everyone sees everything)
- Simple local login; every request tied to a user
- `acl_groups = ["public"]` on all chunks; filter path exists but is a no-op

### 6.2 Ready for later (no re-architecture)
- Enable ACL filter in the Qdrant query (payload index already on `acl_groups`)
- ACL sync from source systems; revocations ≤ 5 min; group cache TTL ≤ 5 min
- Re-check ACL on citation click; cross-user leak tests in CI
- Cache keys include ACL scope hash

### 6.3 Security (applies now)
- **API keys** (Anthropic, OpenAI, Cohere) only in `.env` (git-ignored) locally; GitHub Actions secrets in CI; AWS Secrets Manager in Phase 4. Separate keys per environment, with spend limits set in each provider console
- Document content is sent to Anthropic/OpenAI — confirm this is acceptable for your documents and check each provider's data-retention settings
- Prompt injection: untrusted-context delimiting, **read-only** agent tools, SQL route on a read-only DB role + views
- Output scanning for links / exfiltration patterns
- Audit log: user, query, returned documents

### 6.4 AWS Security Baseline (Phase 4)
- RDS, ElastiCache, Qdrant in private subnets; only the ALB is public
- NAT gateway (or egress proxy) for outbound calls to Anthropic/OpenAI; VPC endpoints for S3 / Secrets Manager / CloudWatch (and Bedrock if adopted)
- Least-privilege IAM role per service
- KMS encryption (RDS, S3, EBS, ElastiCache); TLS everywhere
- WAF on the ALB; CloudTrail enabled

---

## 7. Caching

| Cache | Key | Notes |
|---|---|---|
| Exact answer cache | normalized query + **corpus revision** + model/prompt version | First-turn standalone questions only; TTL 24 h safety net |
| Embedding cache | content hash / query + model + dims | Avoids paying twice for unchanged chunks and repeated queries |
| Retrieval cache | rewritten query + filters | Short TTL |

**Not included:** semantic answer cache — "Q3 2024" vs "Q3 2023" would hit the same entry and return a wrong answer.

---

## 8. Reliability & Operations

### 8.1 Graceful Degradation
| Failure | Behavior |
|---|---|
| Reranker down | Use RRF results directly |
| Anthropic down / rate-limited | Automatic fallback to `LLM_FALLBACK_MODEL` on the other provider (stream falls back only before the first token) |
| All LLMs down | Show search results with notice |
| Embedding API down | Queries: retry then show error; ingestion: retry with backoff → DLQ |
| Ingestion task fails | Retry with backoff → DLQ |

### 8.2 Targets (AWS)
| Target | Value |
|---|---|
| p95 TTFT (simple) | < 2.5 s |
| p95 agentic completion | < 20 s |
| Availability | ≥ 99.9% with a 2-node Qdrant cluster; 99.5% with a single node |
| Ingestion freshness | < 15 min (incremental updates use normal API calls, not Batch API) |
| Cost per query | Target set in Phase 2 from measured costs |

### 8.3 Cost Controls
- Token caps per request, ~10–12k context cap, agent step cap, per-user quotas
- **Spend limits + alerts in the Anthropic and OpenAI consoles from day one**
- Batch APIs for bulk jobs (summaries, contextualization, extraction)
- Before the full-corpus ingest (Phase 4): cost estimate from the 1–2k sample, signed off

### 8.4 Load Testing
- Local (Phase 3): small k6 test (5–10 concurrent users) to find bottlenecks
- AWS staging (Phase 4): k6 at **40 QPS** + outage drills

### 8.5 Backups & Disaster Recovery (Phase 4)
- RDS automated backups + point-in-time recovery
- Qdrant snapshots to S3 (daily); index can be rebuilt from Postgres + S3
- S3 versioning on the documents bucket
- **Rehearse one full restore in staging before launch**; target RPO ≤ 24 h, RTO ≤ 4 h

---

## 9. Evaluation & Observability

### 9.1 Golden Dataset
- **Claude drafts, you correct:** a generator script reads documents and drafts questions + expected answers + source passages per category; you review in a simple file (`eval/golden/review.jsonl`) — accept, edit, or reject each
- **Stage A (5–6 docs):** ~20–30 questions — checks the pipeline works, not for tuning
- **Stage B (1–2k docs):** **200–400 questions**, reviewed by you / subject experts
- Add **real user questions** whenever available — generated questions alone tend to be easier than real ones
- Labels by **document + passage text** (survives re-chunking)
- **Category mix:** Lookup (~35%) · Table (~10%) · Analytical / comparison (~20%) · Multi-hop (~10%) · Aggregation / listing (~10%) · Conflicting sources (~5%) · **Unanswerable (~10%)**
- Include questions whose answers sit deep inside 100+ page documents

### 9.2 Metrics
| Layer | Metrics |
|---|---|
| Retrieval | Recall@k, MRR, nDCG — per category |
| Generation | Faithfulness, correctness, citation accuracy, abstention accuracy |
| SQL route | Execution accuracy vs gold answers |
| Online | 👍/👎, citation clicks, "no answer" rate, latency, cost/query |

### 9.3 Process
- `make eval` locally; **CI** runs the eval on every PR (API keys as GitHub secrets) — regressions block merges
- LLM-as-judge (RAGAS / DeepEval) using the provider not used for generation; calibrate vs your corrections
- Weekly bad-answer review → root cause → fix → add to golden set

### 9.4 Observability
- **Langfuse Cloud** (free tier) — decided; re-evaluate (paid tier or self-hosted) in Phase 4 based on trace volume
- Structured JSON logs; CloudWatch on AWS

---

## 10. User Interface

### 10.1 Chat Screen
```
┌──────────────┬──────────────────────────────────────┬─────────────────┐
│ + New chat   │  Q: What is our parental leave policy?│  SOURCES        │
│              │                                       │                 │
│ Today        │  A: Employees get 16 weeks paid       │ [1] HR Policy   │
│ • Leave pol..│  leave [1], extendable by 4 weeks     │     2024, p.12  │
│ • Vendor co..│  unpaid [2]...                        │     "...16 wks" │
│              │                                       │                 │
│ Yesterday    │  👍 👎  📋 Copy                        │ [2] Leave FAQ   │
│ • Q3 report..│                                       │     §3.2        │
│              ├──────────────────────────────────────┤                 │
│              │ [ Ask a question...            ] Send │                 │
└──────────────┴──────────────────────────────────────┴─────────────────┘
```

| Feature | Phase |
|---|---|
| Streaming answers + progress status | 1 |
| Inline citations → source panel (page / slide / section, highlighted passage) | 1 |
| Local login, conversation history, 👍/👎 + comment | 1 |
| Clear "not found" state | 1 |
| Open original document at the cited page | 3 |
| Admin page: ingestion status, DLQ, re-index, recent 👎, usage | 3 |
| SSO | 4 |

### 10.2 Build
- **Next.js 16** (client components, plain CSS), streaming via `fetch` + SSE parsing; `react-pdf` source preview in Phase 3

---

## 11. API Design

| Method | Endpoint | Description |
|---|---|---|
| POST | `/auth/login` | Local login |
| POST | `/chat` | SSE stream: `status`, `token`, `citation`, `done`, `error` |
| GET | `/conversations` | List conversations |
| GET | `/conversations/{id}` | Conversation + messages |
| DELETE | `/conversations/{id}` | Delete |
| POST | `/feedback` | 👍/👎 + comment |
| GET | `/documents/{id}` | Metadata + file link (page anchor) |
| GET | `/admin/ingestion` | Stats, DLQ (Phase 3) |
| POST | `/admin/reindex` | Trigger re-index (Phase 3) |
| GET | `/admin/usage` | Usage + cost summary (Phase 3) |
| GET | `/health` | Checks Qdrant, Postgres, Redis, reranker, LLM + embedding APIs |

---

## 12. Data Model (Postgres)

```
users            (id, email, name, password_hash, role, created_at)
groups           (id, name, source)                        -- unused until ACLs
user_groups      (user_id, group_id)
documents        (id, source_uri, storage_key, title, file_format, doc_type,
                  page_count, effective_date, is_latest, supersedes_doc_id,
                  version_confidence, authority, content_hash, version, summary,
                  parse_path, parse_quality, status, indexed_at, deleted_at)
document_acls    (document_id, group_id)                   -- unused until ACLs
sections         (id, document_id, section_path, page_start, page_end, token_count, text)
corpus_revision  (id, revision, updated_at)                -- bumped on every doc change
conversations    (id, user_id, title, created_at, updated_at)
messages         (id, conversation_id, role, content, citations jsonb, route,
                  model, tokens_in, tokens_out, cost_usd, latency_ms, trace_id)
feedback         (id, message_id, user_id, rating, comment, created_at)
ingestion_jobs   (id, document_id, stage, status, error, attempts, updated_at)
outbox           (id, aggregate_id, event_type, payload jsonb, processed_at)
audit_log        (id, user_id, action, query, document_ids, created_at)
-- Phase 3: typed field tables per document type (defined from the 1–2k sample),
-- exposed to text-to-SQL only via read-only views
```

---

## 13. Tech Stack & AWS Mapping

| Layer | Local (Phases 1–3) | AWS (Phase 4) |
|---|---|---|
| API | FastAPI in Docker | ECS Fargate behind ALB + WAF |
| Frontend | Next.js | ECS Fargate or Amplify, CloudFront |
| Workers | Celery in Docker (concurrency 1) | ECS Fargate (autoscale on queue depth) |
| Broker / cache | Redis | ElastiCache Redis |
| Relational DB | Postgres 16 | RDS Postgres (Multi-AZ) |
| Vector DB | Qdrant | 2-node Qdrant cluster on EC2 or Qdrant Cloud |
| Object storage | Local folder (or SeaweedFS) | S3 |
| LLM | Anthropic + OpenAI APIs (official SDKs) | Same APIs (or Bedrock — decided in Phase 4) |
| Embeddings | OpenAI API | Same (changing model = re-embed; full ingest happens on AWS anyway) |
| Reranker | Native model server on Mac (MPS) | Container (CPU/GPU) or Cohere Rerank API |
| Observability | Langfuse Cloud (free tier) | Langfuse Cloud (paid tier) or self-hosted + CloudWatch |
| Secrets | `.env` | Secrets Manager |
| Auth | Local login | Cognito / SSO provider |
| Environments | laptop | staging + production |
| IaC / CI | docker-compose, Makefile, GitHub Actions | Terraform + GitHub Actions |

### 13.1 Repository Layout
```
rag-prod/
├── PLAN.md
├── docker-compose.yml          # postgres, redis, qdrant, seaweedfs (optional), api, worker, web
├── Makefile                    # up, down, ingest, eval, test, reranker
├── .env.example                # ANTHROPIC_API_KEY, OPENAI_API_KEY, COHERE_API_KEY (optional) ...
├── backend/
│   ├── app/
│   │   ├── api/                # auth, chat (SSE), conversations, feedback, documents, health
│   │   ├── core/               # config, logging, auth, token counting
│   │   ├── ingestion/          # parsers/ (pdf, docx, pptx, html, md), quality, chunker, summary, pipeline
│   │   ├── providers/          # llm (anthropic / openai + fallback), embedder, sparse (bm25), reranker
│   │   ├── retrieval/          # vector_store (Qdrant), search (hybrid + rerank + context expansion)
│   │   ├── generation/         # prompts, router, agent, citations, pipeline (event stream)
│   │   ├── storage/            # local folder / S3 object store
│   │   └── db/                 # models, session (Alembic later)
│   ├── eval/                   # generate_questions.py, run_eval.py
│   ├── scripts/                # init_db, init_storage, init_index, ingest, create_user
│   └── tests/
├── frontend/                   # Next.js chat UI (port 3010)
├── eval/
│   ├── golden/                 # review.jsonl (drafted by Claude, reviewed by you)
│   └── reports/
├── infra/                      # Terraform (Phase 4)
└── data/
    ├── smoke/                  # the 5–6 build documents
    ├── sample/                 # the 1–2k tuning sample
    └── inbox/                  # watched drop folder
```

---

## 14. Roadmap

| Phase | Weeks | Where | Deliverables | Exit criteria |
|---|---|---|---|---|
| **1. Core pipeline** ✅ | 1–2 | Local · 5–6 docs | docker-compose stack; Anthropic + OpenAI SDKs with fallback; OpenAI embeddings; local reranker; tiered parsing (all 5 formats); hierarchical chunking + headers; doc summary index; hybrid search + RRF + rerank; context budget; citations + verification; simple RAG + basic agentic route; chat UI (streaming, sources, history, feedback); question generator + ~20–30 reviewed questions; `make eval` | All formats ingest cleanly; lookup / table / analytical / multi-hop / unanswerable questions answered correctly with citations or properly abstained |
| **2. Accuracy tuning** | 3–5 | Local · 1–2k docs | Ingest 1–2k sample (resumable); golden set 200–400 Qs (drafted + reviewed); benchmark embeddings (3-large vs 3-small, dims), reranker, Claude vs GPT for generation; A/B per-chunk contextualization; dedup/versioning; router tuning; **lock chunking + models**; CI eval gate; cost per query measured | Accuracy targets met per category; models + chunking locked; cost per query known |
| **3. Feature-complete** | 6–7 | Local | Typed field tables + extraction + SQL route (with RAG fallback); admin page; open-original-at-page; exact cache + embedding cache; rate limits; outbox + reconciliation; DLQ; provider fallback; local k6 smoke load test; security review | Every question category handled; all features working locally; eval stable |
| **4. Move to AWS** | 8–10 | AWS | Terraform (staging + prod); security baseline; ECS, RDS, ElastiCache, S3, Qdrant, reranker container; Secrets Manager; SSO; CI/CD; full-corpus cost estimate + sign-off; **full 30k–50k ingest** (Batch APIs, parallel workers); backups + restore rehearsal; k6 at 40 QPS + outage drills | Eval on full corpus ≥ local results; SLOs met under load; restore rehearsed |
| **5. Launch** | 11–12 | AWS production | Pilot 200 → 1,000 → 2,000 users; weekly bad-answer review; enable ACLs when needed | SLOs met; feedback acceptable; eval trend up |


### Phase 1 status: ✅ DONE (2026-11-25)
- **Running end to end with Claude** (Haiku 5.5 for quick steps, Sonnet 5.5 for answers; OpenAI fallback) on a real 87-page PDF plus one small document per format.
- **All 5 formats verified with real answers and correct citation labels:** PDF → p. 2, DOCX → § Laptops, PPTX → slide 2 (incl. speaker notes), HTML → § Visitors (nav/footer dropped), MD → table row. Answers in 7–11 s.
- **Baseline eval** (22 drafted questions, OpenAI-only run): retrieval hit 100%, MRR 0.97, correct 95%, faithful 100%, citations supported 90%.
- **Live-tested:** greetings, lookups, comparisons (agentic), follow-ups, upload stages, Stop, crash recovery, switching chats mid-answer.
- First commit: `2928945` on `main`.

**Carried into Phase 2** (decided when closing Phase 1):
- Review of the 22 drafted questions in `eval/golden/review.jsonl` (q021 flagged) — becomes the start of the 200–400 question golden set.
- First `make eval` run with Claude on reviewed questions.
- Langfuse tracing (needs Langfuse keys).
- Deferred as planned: Docling path (only if the sample needs it), Alembic.

---

## 15. Risk Register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Outdated/duplicate docs → confident wrong answers | Critical | Dedup, version metadata, down-rank superseded (hard filter only when explicit), conflict disclosure |
| 2 | Long (100+ page) docs: answer buried, context lost | Critical | Hierarchical chunks, short headers, doc summary index, `read_section`, eval Qs deep in docs |
| 3 | Aggregation questions answered wrongly | Critical | Typed field tables + SQL route with RAG fallback |
| 4 | Hallucinated citations | Critical | Post-generation citation verification |
| 5 | 16 GB RAM too tight (Docling + Qdrant + reranker + UI) | High | Memory budget (§2.3), worker concurrency 1, Langfuse Cloud, smaller reranker if needed, run ingest and UI work separately |
| 6 | Generated test questions easier than real ones → over-optimistic scores | High | You review every question; add real user questions; include hard categories + unanswerables |
| 7 | Document content sent to external APIs (approved) | Medium | Check provider data-retention settings; separate keys per environment; option to move to Bedrock in Phase 4 |
| 8 | API rate limits / outages during bulk ingest | High | Batch APIs, backoff, resumable tasks, provider fallback |
| 9 | Local → AWS surprises (networking, performance) | High | Interfaces + config, same containers, eval parity check, Phase 4 is 3 weeks |
| 10 | Oversized context → cost, latency, accuracy loss | High | Neighbour expansion, ~10–12k token cap |
| 11 | Stale cached answers after document update | High | Corpus revision in cache key, first-turn-only caching, TTL |
| 12 | Postgres ⇄ Qdrant drift | High | Outbox + reconciliation |
| 13 | Silent parse failures (tables, PPTX layouts) | High | Quality gates, DLQ, coverage on admin page |
| 14 | API cost overrun | Medium | Console spend limits, Batch APIs, embedding cache, token caps |
| 15 | Prompt injection via docs / SQL route | Medium | Untrusted delimiting, read-only tools, read-only DB role |
| 16 | ACLs needed later | Medium | `acl_groups` in schema + filter path in code |
| 17 | Data loss on AWS | Medium | Backups, snapshots, rebuild path, rehearsed restore |

---

## 16. Open Questions

### Resolved (2026-11-24)
| Question | Answer |
|---|---|
| API keys | **Anthropic + OpenAI keys ready**, billing enabled |
| Data sensitivity | **Allowed** to send document content to Anthropic and OpenAI |
| Tracing | **Langfuse Cloud free tier** (no local Langfuse; optional compose profile dropped) |
| 1–2k sample | **Available now** → Phase 2 can start right after Phase 1 |

### Still open (non-blocking)
1. **The 5–6 build documents** — place them in `data/smoke/` (PDF 100+ pages with tables, DOCX, PPTX, HTML, MD).
2. **The 1–2k sample** — place in `data/sample/` (or share location) before Phase 2.
3. **Document versions** — do filenames/metadata indicate version/date, or must we infer it from content? (Needed for Phase 2 dedup/versioning.)
4. **Team & deadline** — number of engineers and any fixed launch date?
