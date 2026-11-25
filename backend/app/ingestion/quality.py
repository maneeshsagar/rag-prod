"""
Parse quality checks. They never stop ingestion; they return warnings that are
saved on the document so bad parses are easy to find.
"""

import re

from app.ingestion.parsers.base import ParsedDocument

MIN_CHARS_PER_PAGE = 200  # below this a page is probably an image or empty
NO_HEADINGS_PAGE_LIMIT = 20  # long documents should have some headings

# Language check: in English prose roughly 30-45% of words are these common words.
# Other languages (Hindi, German, ...) score close to 0. Cheap and needs no model.
ENGLISH_WORDS = {
    "the", "of", "and", "to", "a", "in", "is", "it", "that", "for", "on", "are", "as", "with",
    "be", "by", "this", "was", "or", "at", "from", "an", "have", "not", "which", "but", "they",
    "their", "will", "can", "has", "all", "if", "more", "must", "may", "should", "any", "each",
    "other",
}
MIN_ENGLISH_SHARE = 0.12
MIN_WORDS_FOR_LANGUAGE = 200  # short texts (a table, a slide) are too small to judge
WORD = re.compile(r"[^\W\d_]+")  # letters only, any alphabet


def english_share(text: str) -> float:
    """Share of very common English words. 1.0 for texts too short to judge."""
    words = WORD.findall(text.lower())[:20_000]
    if len(words) < MIN_WORDS_FOR_LANGUAGE:
        return 1.0
    return sum(w in ENGLISH_WORDS for w in words) / len(words)


def check_quality(doc: ParsedDocument) -> list[str]:
    warnings: list[str] = []
    total_chars = sum(len(block.text) for block in doc.blocks)

    if total_chars == 0:
        return ["no text extracted (scanned or image-only file? OCR is out of scope)"]

    if doc.page_count and doc.file_format == "pdf":  # slides are short by design
        chars_per_page: dict[int, int] = {}
        for block in doc.blocks:
            if block.page is not None:
                chars_per_page[block.page] = chars_per_page.get(block.page, 0) + len(block.text)
        low = [p for p in range(1, doc.page_count + 1) if chars_per_page.get(p, 0) < MIN_CHARS_PER_PAGE]
        if len(low) > doc.page_count * 0.2:
            warnings.append(f"{len(low)} of {doc.page_count} pages have little or no text")

    has_headings = any(block.heading_path for block in doc.blocks)
    if not has_headings and (doc.page_count or 0) >= NO_HEADINGS_PAGE_LIMIT:
        warnings.append("no headings detected in a long document; sections will be coarse")

    share = english_share(" ".join(block.text for block in doc.blocks))
    if share < MIN_ENGLISH_SHARE:
        warnings.append(f"mostly not English ({share:.0%} common English words); "
                        "answers and search are tuned for English")

    return warnings
