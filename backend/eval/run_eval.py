"""
Run the eval on the reviewed questions and write a report.

Run with:  make eval                                   (reviewed questions only)
           uv run python -m eval.run_eval --include-pending   (also unreviewed drafts)

Experiments: change any setting for one run, give the run a name, then compare:
    uv run python -m eval.run_eval --name gpt5 --set LLM_MAIN_MODEL=openai/gpt-5
    uv run python -m eval.run_eval --name no-rerank --set RERANKER_BACKEND=none
    uv run python -m eval.compare eval/reports/<a>.json eval/reports/<b>.json

Per question:
- retrieval hit: the expected passage is in one of the sources given to the model
  (+ reciprocal rank -> MRR)
- correct / faithful: judged by LLM_JUDGE_MODEL (a different provider than the answers)
- unanswerable questions: correct only if the system says it could not find the answer
- citations: share of citations that are valid and supported by their source
- cost: every AI call made to answer (not the judge), in US dollars
Report: eval/reports/<timestamp>[-name].md and .json
"""

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select

from app.core import tracing
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.models import Document
from app.db.session import SessionLocal
from app.generation.pipeline import answer_question
from app.generation.prompts import PROMPT_VERSION
from app.providers.llm import get_llm
from eval.common import CATEGORIES, REPORTS_DIR, REVIEW_FILE, REVIEWED, quote_found, read_jsonl
from eval.settings_override import apply_overrides

JUDGE_SYSTEM = """You grade answers from a document question-answering system.
Compare the system answer with the expected answer and the sources the system was given.
- correct: the system answer contains the key facts of the expected answer and nothing that contradicts it.
- faithful: every claim in the system answer is supported by the given sources.
Be strict about numbers, dates and names. Give a one-sentence reason."""


class Grade(BaseModel):
    correct: bool
    faithful: bool
    reason: str


def document_names() -> dict[str, set[str]]:
    """{doc_id: {title, file name}} for every document, so a question can name its
    document by title or file name. Ids change when documents are re-ingested
    (CI, a re-index, AWS); names do not."""
    with SessionLocal() as session:
        rows = session.execute(select(Document.id, Document.title, Document.source_name)).all()
    return {doc_id: {title, source_name, Path(source_name).name} for doc_id, title, source_name in rows}


def _same_document(source, item: dict, names: dict[str, set[str]]) -> bool:
    return source.doc_id == item.get("doc_id") or item.get("doc_title") in names.get(source.doc_id, ())


def run_question(item: dict, names: dict[str, set[str]]) -> dict:
    started = time.monotonic()
    with tracing.trace("eval", tags=["eval", item["category"]], input=item["question"],
                       metadata={"question_id": item["id"]}) as root:
        done = next(e for e in answer_question(item["question"], history=[])
                    if e["type"] == "done")
        root.update(output=done["answer"])
    sources = done["sources"]

    rank = next((i + 1 for i, s in enumerate(sources)
                 if _same_document(s, item, names) and quote_found(item["source_quote"], s.text)),
                None)
    citations = done["citations"]
    good_citations = sum(c["supported"] for c in citations)
    citation_total = len(citations) + len(done["invalid_numbers"])

    if item["category"] == "unanswerable":
        grade = Grade(correct=done["not_found"], faithful=True,
                      reason="abstained" if done["not_found"] else "answered an unanswerable question")
    else:
        source_text = "\n\n".join(f"[{s.number}] {s.text[:3000]}" for s in sources)
        grade = get_llm().json(
            "judge", JUDGE_SYSTEM,
            f"Question: {item['question']}\n\nExpected answer: {item['expected_answer']}\n\n"
            f"System answer: {done['answer']}\n\nSources given to the system:\n{source_text}",
            Grade,
        )

    return {
        "id": item["id"], "category": item["category"], "question": item["question"],
        "answer": done["answer"], "route": done["route"],
        "retrieval_rank": rank, "correct": grade.correct, "faithful": grade.faithful,
        "reason": grade.reason, "not_found": done["not_found"],
        "citation_accuracy": good_citations / citation_total if citation_total else None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "cost_usd": done["usage"]["cost_usd"],
        "tokens_in": done["usage"]["tokens_in"],
        "tokens_out": done["usage"]["tokens_out"],
    }


def _share(values: list[bool]) -> str:
    return f"{100 * sum(values) / len(values):.0f}%" if values else "–"


def summarize(results: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[r["category"]].append(r)
    groups["ALL"] = results

    rows = []
    for category in [c for c in CATEGORIES if c in groups] + ["ALL"]:
        items = groups[category]
        answerable = [r for r in items if r["category"] != "unanswerable"]
        citation_scores = [r["citation_accuracy"] for r in items if r["citation_accuracy"] is not None]
        rows.append({
            "category": category,
            "n": len(items),
            "retrieval_hit": _share([r["retrieval_rank"] is not None for r in answerable]),
            "mrr": f"{statistics.mean([1 / r['retrieval_rank'] if r['retrieval_rank'] else 0 for r in answerable]):.2f}"
                   if answerable else "–",
            "correct": _share([r["correct"] for r in items]),
            "faithful": _share([r["faithful"] for r in items]),
            "citations_ok": f"{100 * statistics.mean(citation_scores):.0f}%" if citation_scores else "–",
            "p50_latency_s": f"{statistics.median([r['latency_ms'] for r in items]) / 1000:.1f}",
            "cost_per_q": f"${statistics.mean([r['cost_usd'] for r in items]):.4f}",
        })
    return rows


def run_config(name: str, overrides: list[str]) -> dict:
    """What this run used, saved with the results so runs can be compared later."""
    settings = get_settings()
    return {
        "name": name, "overrides": overrides, "prompt_version": PROMPT_VERSION,
        **{key: getattr(settings, key) for key in (
            "llm_fast_model", "llm_main_model", "llm_judge_model", "answer_effort",
            "embedding_model", "embedding_dimensions", "chunks_collection", "reranker_backend",
            "search_candidates", "rerank_top_k", "context_token_budget")},
    }


def markdown_report(rows: list[dict], results: list[dict], config: dict) -> str:
    headers = list(rows[0])
    run_line = (f"Run: **{config['name'] or 'default'}** · answers by "
                f"`{config['llm_main_model']}` · overrides: {', '.join(config['overrides']) or 'none'}")
    lines = ["# Eval report " + datetime.now().strftime("%Y-%m-%d %H:%M"), "", run_line, "",
             "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(row[h]) for h in headers) + " |" for row in rows]
    lines += ["", "## Failures", ""]
    for r in results:
        if not r["correct"] or not r["faithful"]:
            lines.append(f"- **{r['id']}** [{r['category']}] {r['question']}\n"
                         f"  - answer: {r['answer'][:300]}\n  - judge: {r['reason']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-pending", action="store_true")
    parser.add_argument("--min-correct", type=float, default=0.0,
                        help="fail (exit 1) if overall correctness is below this share, e.g. 0.85")
    parser.add_argument("--name", default="", help="label for this run, e.g. gpt5-answers")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="change a setting for this run, e.g. RERANK_TOP_K=8 (repeatable)")
    parser.add_argument("--category", action="append", default=[],
                        help="only these categories (repeatable)")
    parser.add_argument("--tag", help="only questions with this tag, e.g. ci")
    args = parser.parse_args()
    setup_logging("WARNING")
    apply_overrides(args.set)
    config = run_config(args.name, args.set)

    allowed = REVIEWED | ({"pending"} if args.include_pending else set())
    items = [q for q in read_jsonl(REVIEW_FILE) if q.get("status") in allowed
             and (not args.category or q["category"] in args.category)
             and (not args.tag or args.tag in q.get("tags", []))]
    if not items:
        raise SystemExit(f"No reviewed questions in {REVIEW_FILE}. Run `make questions`, review "
                         "them, or use --include-pending.")

    names = document_names()
    results = []
    for item in items:
        result = run_question(item, names)
        results.append(result)
        mark = "✓" if result["correct"] else "✗"
        print(f"{mark} {item['id']} [{item['category']}] {item['question'][:80]}")

    tracing.flush()
    rows = summarize(results)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.name}" if args.name else "")
    (REPORTS_DIR / f"{stamp}.json").write_text(
        json.dumps({"config": config, "summary": rows, "results": results}, indent=2))
    report = markdown_report(rows, results, config)
    (REPORTS_DIR / f"{stamp}.md").write_text(report)
    print("\n" + report.split("## Failures")[0])
    print(f"Report: {REPORTS_DIR / stamp}.md")

    overall = sum(r["correct"] for r in results) / len(results)
    if overall < args.min_correct:
        raise SystemExit(f"Correctness {overall:.0%} is below the gate {args.min_correct:.0%}")


if __name__ == "__main__":
    main()
