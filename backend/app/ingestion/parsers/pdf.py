"""
PDF -> Markdown pages, using PyMuPDF4LLM (fast path; text-based PDFs, no OCR).

PyMuPDF4LLM detects headings from font sizes and keeps tables as Markdown tables.
A Docling path for complex layouts is added in Phase 2, once we see which of the
real sample documents need it.
"""

from pathlib import Path

import pymupdf
import pymupdf4llm

from app.ingestion.parsers.base import Page


def parse_pdf(path: Path) -> tuple[str | None, list[Page]]:
    """Return (title from PDF metadata, one Markdown page per PDF page)."""
    with pymupdf.open(path) as doc:
        title = (doc.metadata or {}).get("title") or None
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            use_ocr=False,  # text-based PDFs only (OCR is out of scope)
            header=False,  # drop repeating page headers / footers ("Confidential - Page 3")
            footer=False,
            show_progress=False,
        )

    pages = [
        Page(number=index + 1, markdown=chunk["text"]) for index, chunk in enumerate(chunks)
    ]
    return title, pages
