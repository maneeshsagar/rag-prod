"""Shared paths and helpers for the eval scripts."""

import json
import re
from pathlib import Path

from app.core.config import PROJECT_ROOT

EVAL_DIR = PROJECT_ROOT / "eval"
GOLDEN_DIR = EVAL_DIR / "golden"
REVIEW_FILE = GOLDEN_DIR / "review.jsonl"  # drafted questions you review
REPORTS_DIR = EVAL_DIR / "reports"

CATEGORIES = ["lookup", "table", "analytical", "multi_hop", "aggregation", "conflicting",
              "unanswerable"]
REVIEWED = {"accepted", "edited"}  # statuses that count as reviewed


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def quote_found(quote: str, text: str, min_share: float = 0.6) -> bool:
    """True if most words of the expected quote appear in the text (survives re-chunking)."""
    quote_words = set(words(quote))
    if not quote_words:
        return False
    return len(quote_words & set(words(text))) / len(quote_words) >= min_share
