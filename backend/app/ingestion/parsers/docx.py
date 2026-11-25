"""
DOCX -> Markdown, using python-docx.

Word headings ("Heading 1", "Heading 2", ... and "Title") become Markdown headings,
list paragraphs become "- " items and tables become Markdown tables, all in reading order.
Word files have no fixed pages, so citations use the section heading.
"""

from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingestion.parsers.base import Page


def _heading_level(paragraph: Paragraph) -> int | None:
    style = (paragraph.style.name or "") if paragraph.style is not None else ""
    if style == "Title":
        return 1
    if style.startswith("Heading "):
        level = style.removeprefix("Heading ").strip()
        return min(int(level), 6) if level.isdigit() else None
    return None


def _paragraph_markdown(paragraph: Paragraph) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""
    level = _heading_level(paragraph)
    if level:
        return f"{'#' * level} {text}"
    style = (paragraph.style.name or "") if paragraph.style is not None else ""
    if "List" in style:
        return f"- {text}"
    return text


def table_to_markdown(rows: list[list[str]]) -> str:
    """Rows of cell text -> Markdown table (first row is the header)."""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [[cell.replace("\n", " ").replace("|", "/").strip() for cell in row] for row in rows]
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def _table_markdown(table: Table) -> str:
    return table_to_markdown([[cell.text for cell in row.cells] for row in table.rows])


def parse_docx(path: Path) -> tuple[str | None, list[Page]]:
    document = docx.Document(str(path))
    title = document.core_properties.title or None

    parts: list[str] = []
    for item in document.iter_inner_content():  # paragraphs and tables in reading order
        text = _paragraph_markdown(item) if isinstance(item, Paragraph) else _table_markdown(item)
        if text:
            parts.append(text)

    return title, [Page(number=None, markdown="\n\n".join(parts))]
