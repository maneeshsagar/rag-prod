"""
Run the eval on the reviewed questions and write a report.

Run with:  make eval                       (reviewed questions only)
           uv run python -m eval.run_eval --include-pending   (also unreviewed drafts)

Per question:
- retrieval hit: the expected passage is in one of the sources given to the model
  (+ reciprocal rank -> MRR)
- correct / faithful: judged by LLM_JUDGE_MODEL (a different provider than the answers)
- unanswerable questions: correct only if the system says it could not find the answer
- citations: share of citations that are valid and supported by their source
Report: eval/reports/<timestamp>.md and .json
"""

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel

from app.core.logging import setup_logging
from app.generation.pipeline import answer_question
from app.providers.llm import get_llm
from eval.common import CATEGORIES, REPORTS_DIR, REVIEW_FILE, REVIEWED, quote_found, read_jsonl

JUDGE_SYSTEM = """You grade answers from a document question-answering system.
Compare the system answer with the expected answer and the sources the system was given.
- correct: the system answer contains the key facts of the expected answer and nothing that contradicts it.
- faithful: every claim in the system answer is supported by the given sources.
Be strict about numbers, dates and names. Give a one-sentence reason."""


class Grade(BaseModel):
    correct: bool
    faithful: bool
    reason: str


def run_question(item: dict) -> dict:
    started = time.monotonic()
    done = next(e for e in answer_question(item["question"], history=[]) if e["type"] == "done")
    sources = done["sources"]

    rank = next((i + 1 for i, s in enumerate(sources)
                 if s.doc_id == item.get("doc_id") and quote_found(item["source_quote"], s.text)), None)
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
        })
    return rows


def markdown_report(rows: list[dict], results: list[dict]) -> str:
    headers = list(rows[0])
    lines = ["# Eval report " + datetime.now().strftime("%Y-%m-%d %H:%M"), "",
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
    args = parser.parse_args()
    setup_logging("WARNING")

    allowed = REVIEWED | ({"pending"} if args.include_pending else set())
    items = [q for q in read_jsonl(REVIEW_FILE) if q.get("status") in allowed]
    if not items:
        raise SystemExit(f"No reviewed questions in {REVIEW_FILE}. Run `make questions`, review "
                         "them, or use --include-pending.")

    results = []
    for item in items:
        result = run_question(item)
        results.append(result)
        mark = "✓" if result["correct"] else "✗"
        print(f"{mark} {item['id']} [{item['category']}] {item['question'][:80]}")

    rows = summarize(results)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (REPORTS_DIR / f"{stamp}.json").write_text(json.dumps({"summary": rows, "results": results}, indent=2))
    report = markdown_report(rows, results)
    (REPORTS_DIR / f"{stamp}.md").write_text(report)
    print("\n" + report.split("## Failures")[0])
    print(f"Report: {REPORTS_DIR / stamp}.md")

    overall = sum(r["correct"] for r in results) / len(results)
    if overall < args.min_correct:
        raise SystemExit(f"Correctness {overall:.0%} is below the gate {args.min_correct:.0%}")


if __name__ == "__main__":
    main()
