"""
What every parser produces.

Each parser turns its file into Markdown "pages" (a PDF page, a PPTX slide, or the
whole document for DOCX / HTML / MD). `markdown_to_blocks` then turns that Markdown
into blocks that know their heading path — the same code for every format.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Page:
    number: int | None  # PDF page or PPTX slide number; None for DOCX / HTML / MD
    markdown: str


@dataclass
class Block:
    """A paragraph, list or table, plus where it sits in the document."""

    text: str
    heading_path: list[str]  # e.g. ["Benefits", "Leave", "Parental leave"]
    page: int | None
    is_table: bool = False


@dataclass
class ParsedDocument:
    title: str
    file_format: str  # pdf, docx, pptx, html, md
    page_count: int | None
    blocks: list[Block] = field(default_factory=list)


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
INLINE_HTML = re.compile(r"</?(?:u|b|i|em|strong|sup|sub|span|br|mark|small)\b[^>]*>", re.IGNORECASE)


def clean_heading(text: str) -> str:
    """Remove Markdown emphasis from a heading: '**1. Scope**' -> '1. Scope'."""
    return re.sub(r"[*_`]+", "", text).strip()


def _add_block(blocks: list[Block], lines: list[str], headings: list[tuple[int, str]],
               page: int | None, is_table: bool) -> None:
    """Turn the collected lines into one block (if any), then clear them."""
    if lines:
        path = [text for _, text in headings]
        blocks.append(Block("\n".join(lines).strip(), path, page, is_table))
        lines.clear()


def markdown_to_blocks(pages: list[Page]) -> list[Block]:
    """Split Markdown into blocks, tracking the heading path across pages."""
    blocks: list[Block] = []
    headings: list[tuple[int, str]] = []  # stack of (level, text)

    for page in pages:
        paragraph: list[str] = []
        table: list[str] = []

        for line in page.markdown.splitlines():
            stripped = INLINE_HTML.sub("", line).strip()
            match = HEADING.match(stripped)

            if match:  # a heading closes whatever came before it
                _add_block(blocks, paragraph, headings, page.number, is_table=False)
                _add_block(blocks, table, headings, page.number, is_table=True)
                level, text = len(match.group(1)), clean_heading(match.group(2))
                if text:
                    while headings and headings[-1][0] >= level:
                        headings.pop()
                    headings.append((level, text))
            elif stripped.startswith("|"):  # table row
                _add_block(blocks, paragraph, headings, page.number, is_table=False)
                table.append(stripped)
            elif not stripped:  # blank line ends a paragraph or table
                _add_block(blocks, paragraph, headings, page.number, is_table=False)
                _add_block(blocks, table, headings, page.number, is_table=True)
            else:  # text
                _add_block(blocks, table, headings, page.number, is_table=True)
                paragraph.append(stripped)

        _add_block(blocks, paragraph, headings, page.number, is_table=False)
        _add_block(blocks, table, headings, page.number, is_table=True)

    return [block for block in blocks if block.text]
