"""
Compare two eval runs side by side.

    uv run python -m eval.compare eval/reports/A.json eval/reports/B.json

Shows each metric per category for both runs, then every question whose outcome
changed (fixed or broken) so you can read why.
"""

import json
import sys
from pathlib import Path

METRICS = ["retrieval_hit", "mrr", "correct", "faithful", "citations_ok", "p50_latency_s",
           "cost_per_q"]


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def label(run: dict, path: str) -> str:
    return run.get("config", {}).get("name") or Path(path).stem


def table(a: dict, b: dict, name_a: str, name_b: str) -> list[str]:
    rows_b = {row["category"]: row for row in b["summary"]}
    lines = [f"| category | metric | {name_a} | {name_b} |", "|---|---|---|---|"]
    for row_a in a["summary"]:
        row_b = rows_b.get(row_a["category"], {})
        for metric in METRICS:
            left, right = row_a.get(metric, "–"), row_b.get(metric, "–")
            mark = "" if left == right else " ←"
            lines.append(f"| {row_a['category']} | {metric} | {left} | {right}{mark} |")
    return lines


def changed_questions(a: dict, b: dict) -> list[str]:
    results_b = {r["id"]: r for r in b["results"]}
    lines = []
    for r_a in a["results"]:
        r_b = results_b.get(r_a["id"])
        if r_b is None or r_a["correct"] == r_b["correct"]:
            continue
        change = "FIXED" if r_b["correct"] else "BROKEN"
        lines.append(f"- {change} {r_a['id']} [{r_a['category']}] {r_a['question']}\n"
                     f"    before: {r_a['reason']}\n    after:  {r_b['reason']}")
    return lines


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m eval.compare <run A .json> <run B .json>")
    a, b = load(sys.argv[1]), load(sys.argv[2])
    name_a, name_b = label(a, sys.argv[1]), label(b, sys.argv[2])
    print("\n".join(table(a, b, name_a, name_b)))
    changes = changed_questions(a, b)
    print(f"\nQuestions whose correctness changed ({len(changes)}):")
    print("\n".join(changes) or "  none")


if __name__ == "__main__":
    main()
