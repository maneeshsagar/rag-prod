"""
Citation checks after the answer is written.

For every [n] in the answer:
- n must be one of the sources given to the model (else it is reported as invalid);
- the answer sentences citing [n] should share key words with source n
  ("supported"); the best-matching source sentence is returned as the quote
  shown in the UI.
"""

import re

from app.generation.prompts import NOT_FOUND
from app.retrieval.search import Source

CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z0-9][a-z0-9.%$-]*")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is",
    "it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with",
    "which", "who", "what", "when", "where", "how", "not", "no", "can", "may", "also", "than",
    "then", "there", "these", "those", "their", "they", "you", "your", "our", "we", "i",
}
SUPPORT_THRESHOLD = 0.5  # share of the sentence's key words found in the source


# Citations written after the full stop ("... payment. [1][2]") belong to that sentence.
CITATIONS_AFTER_STOP = re.compile(r"([.!?])((?:\s*\[\d+(?:\s*[,;]\s*\d+)*\])+)")


def _stem(word: str) -> str:
    """Very light stemming so "bureaus" matches "bureau" and "limits" matches "limit"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(text: str) -> set[str]:
    return {_stem(w.strip(".")) for w in WORD.findall(text.lower())} - STOPWORDS


def _numbers_in(match: str) -> list[int]:
    return [int(n) for n in re.split(r"\s*[,;]\s*", match)]


def cited_numbers(answer: str) -> list[int]:
    """Source numbers in the order they are first cited."""
    seen: list[int] = []
    for match in CITATION.findall(answer):
        for n in _numbers_in(match):
            if n not in seen:
                seen.append(n)
    return seen


def is_not_found(answer: str) -> bool:
    return answer.strip().startswith(NOT_FOUND)


def verify_citations(answer: str, sources: list[Source]) -> dict:
    by_number = {s.number: s for s in sources}
    normalized = CITATIONS_AFTER_STOP.sub(r"\2\1", answer)  # "payment. [1]" -> "payment [1]."
    sentences = [s for s in SENTENCE_END.split(normalized) if s.strip()]
    citations, invalid = [], []

    for number in cited_numbers(answer):
        source = by_number.get(number)
        if source is None:
            invalid.append(number)
            continue

        claim_words: set[str] = set()
        for sentence in sentences:
            if any(number in _numbers_in(m) for m in CITATION.findall(sentence)):
                claim_words |= _words(CITATION.sub("", sentence))

        source_words = _words(source.text)
        support = len(claim_words & source_words) / len(claim_words) if claim_words else 0.0

        source_sentences = [s for s in SENTENCE_END.split(source.text) if len(s.strip()) > 20]
        quote = max(source_sentences, key=lambda s: len(_words(s) & claim_words), default="")

        citations.append({
            **source.to_dict(),
            "quote": re.sub(r"<[^>]+>", "", quote).strip()[:400],
            "supported": support >= SUPPORT_THRESHOLD,
            "support": round(support, 2),
        })

    return {
        "citations": citations,
        "invalid_numbers": invalid,
        "not_found": is_not_found(answer),
        "uncited": not citations and not is_not_found(answer),
    }
