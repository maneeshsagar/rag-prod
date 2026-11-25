"""
Parse quality checks. They never stop ingestion; they return warnings that are
saved on the document so bad parses are easy to find.
"""

from app.ingestion.parsers.base import ParsedDocument

MIN_CHARS_PER_PAGE = 200  # below this a page is probably an image or empty
NO_HEADINGS_PAGE_LIMIT = 20  # long documents should have some headings


def check_quality(doc: ParsedDocument) -> list[str]:
    warnings: list[str] = []
    total_chars = sum(len(block.text) for block in doc.blocks)

    if total_chars == 0:
        return ["no text extracted (scanned or image-only file? OCR is out of scope)"]

    if doc.page_count:
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

    return warnings
