"""
Parse any supported file into a ParsedDocument.

Usage:
    doc = parse_file(Path("report.pdf"))
"""

from collections.abc import Callable
from pathlib import Path

from app.ingestion.parsers.base import Page, ParsedDocument, markdown_to_blocks
from app.ingestion.parsers.docx import parse_docx
from app.ingestion.parsers.html import parse_html
from app.ingestion.parsers.pdf import parse_pdf
from app.ingestion.parsers.pptx import parse_pptx


def parse_markdown(path: Path) -> tuple[str | None, list[Page]]:
    return None, [Page(number=None, markdown=path.read_text(errors="replace"))]


PARSERS: dict[str, Callable[[Path], tuple[str | None, list[Page]]]] = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "pptx": parse_pptx,
    "html": parse_html,
    "htm": parse_html,
    "md": parse_markdown,
    "markdown": parse_markdown,
}

SUPPORTED_EXTENSIONS = {f".{ext}" for ext in PARSERS}


def file_format(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return {"htm": "html", "markdown": "md"}.get(ext, ext)


def parse_file(path: Path) -> ParsedDocument:
    ext = path.suffix.lower().lstrip(".")
    if ext not in PARSERS:
        raise ValueError(f"unsupported file type: {path.suffix}")

    title, pages = PARSERS[ext](path)
    blocks = markdown_to_blocks(pages)

    # Title: file metadata, else the first heading, else the file name.
    if not title:
        first_heading = next((b.heading_path[0] for b in blocks if b.heading_path), None)
        title = first_heading or path.stem.replace("_", " ").replace("-", " ")

    page_count = len(pages) if pages and pages[0].number is not None else None
    return ParsedDocument(
        title=title.strip(), file_format=file_format(path), page_count=page_count, blocks=blocks
    )
