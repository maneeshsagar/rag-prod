"""
Hierarchical chunking:  document -> sections (follow headings) -> chunks (indexed).

- A section is a run of blocks under the same heading path. Sections are the
  "parent" text: short ones are sent to the answer model whole.
- Chunks never cross a section boundary. Text chunks are ~target tokens with a small
  overlap; tables are their own chunks (long tables are split, repeating the header row).
"""

import re
from dataclasses import dataclass, field

from app.core.tokens import count_tokens, first_tokens, last_tokens
from app.ingestion.parsers.base import Block, ParsedDocument


@dataclass
class ChunkSettings:
    target_tokens: int = 450
    max_tokens: int = 600
    overlap_tokens: int = 60


@dataclass
class Chunk:
    index: int  # position in the whole document (used to find neighbours)
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    is_table: bool = False


@dataclass
class Section:
    position: int
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    text: str
    token_count: int
    chunks: list[Chunk] = field(default_factory=list)


# ---------- labels and headers ----------


def citation_label(file_format: str, page_start: int | None, page_end: int | None,
                   heading_path: list[str]) -> str:
    """PDF -> page, PPTX -> slide, DOCX / HTML / MD -> section heading."""
    if file_format == "pdf" and page_start:
        if page_end and page_end != page_start:
            return f"pp. {page_start}–{page_end}"
        return f"p. {page_start}"
    if file_format == "pptx" and page_start:
        return f"slide {page_start}"
    return f"§ {heading_path[-1]}" if heading_path else "§ (start of document)"


def chunk_header(title: str, heading_path: list[str]) -> str:
    """Short context header put in front of each chunk before embedding / BM25."""
    section = " > ".join(heading_path) if heading_path else "(start of document)"
    return f"Document: {title}\nSection: {section}\n---\n"


# ---------- splitting helpers ----------

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_long_text(text: str, settings: ChunkSettings) -> list[str]:
    """Split one very long paragraph at sentence ends (hard-cut sentences that are too long)."""
    pieces: list[str] = []
    current = ""
    for sentence in SENTENCE_END.split(text):
        while count_tokens(sentence) > settings.max_tokens:  # a giant "sentence"
            head = first_tokens(sentence, settings.target_tokens)
            pieces.append(head)
            sentence = sentence[len(head):]
        candidate = f"{current} {sentence}".strip()
        if current and count_tokens(candidate) > settings.target_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _split_table(table: str, settings: ChunkSettings) -> list[str]:
    """Split a long Markdown table into parts, each starting with the header rows."""
    lines = table.splitlines()
    header, rows = lines[:2], lines[2:]
    parts: list[str] = []
    current: list[str] = []
    for row in rows:
        if current and count_tokens("\n".join(header + current + [row])) > settings.max_tokens:
            parts.append("\n".join(header + current))
            current = []
        current.append(row)
    if current or not parts:
        parts.append("\n".join(header + current))
    return parts


# ---------- main functions ----------


def group_sections(blocks: list[Block]) -> list[list[Block]]:
    """Consecutive blocks with the same heading path form one section."""
    groups: list[list[Block]] = []
    for block in blocks:
        if groups and groups[-1][0].heading_path == block.heading_path:
            groups[-1].append(block)
        else:
            groups.append([block])
    return groups


def _page_range(blocks: list[Block]) -> tuple[int | None, int | None]:
    pages = [b.page for b in blocks if b.page is not None]
    return (min(pages), max(pages)) if pages else (None, None)


def _chunk_section(blocks: list[Block], settings: ChunkSettings) -> list[tuple[str, list[Block], bool]]:
    """Return (text, source blocks, is_table) for each chunk of one section."""
    chunks: list[tuple[str, list[Block], bool]] = []
    current_text, current_blocks = "", []

    def emit() -> None:
        nonlocal current_text, current_blocks
        if current_text.strip():
            chunks.append((current_text.strip(), current_blocks, False))
        current_text, current_blocks = "", []

    for block in blocks:
        if block.is_table:
            emit()
            if count_tokens(block.text) > settings.max_tokens:
                parts = _split_table(block.text, settings)
            else:
                parts = [block.text]
            chunks.extend((part, [block], True) for part in parts)
            continue

        pieces = (
            _split_long_text(block.text, settings)
            if count_tokens(block.text) > settings.max_tokens
            else [block.text]
        )
        for piece in pieces:
            candidate = f"{current_text}\n\n{piece}" if current_text else piece
            if current_text and count_tokens(candidate) > settings.target_tokens:
                overlap = last_tokens(current_text, settings.overlap_tokens)
                emit()
                current_text, current_blocks = f"…{overlap}\n\n{piece}", [block]
            else:
                current_text = candidate
                current_blocks = current_blocks + [block]
    emit()
    return chunks


def chunk_document(doc: ParsedDocument, settings: ChunkSettings) -> list[Section]:
    sections: list[Section] = []
    chunk_index = 0

    for position, blocks in enumerate(group_sections(doc.blocks)):
        page_start, page_end = _page_range(blocks)
        text = "\n\n".join(block.text for block in blocks)
        section = Section(
            position=position,
            heading_path=blocks[0].heading_path,
            page_start=page_start,
            page_end=page_end,
            text=text,
            token_count=count_tokens(text),
        )
        for chunk_text, source_blocks, is_table in _chunk_section(blocks, settings):
            chunk_start, chunk_end = _page_range(source_blocks)
            section.chunks.append(
                Chunk(
                    index=chunk_index,
                    text=chunk_text,
                    token_count=count_tokens(chunk_text),
                    page_start=chunk_start,
                    page_end=chunk_end,
                    is_table=is_table,
                )
            )
            chunk_index += 1
        sections.append(section)

    return sections
