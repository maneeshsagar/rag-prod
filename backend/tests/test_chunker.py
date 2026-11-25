"""Chunks stay inside sections, respect sizes, keep tables whole and get the right labels."""

from app.core.tokens import count_tokens
from app.ingestion.chunker import ChunkSettings, chunk_document, chunk_header, citation_label
from app.ingestion.parsers.base import Block, ParsedDocument

SETTINGS = ChunkSettings(target_tokens=100, max_tokens=150, overlap_tokens=15)


def _doc(blocks: list[Block], fmt: str = "pdf") -> ParsedDocument:
    return ParsedDocument(title="Handbook", file_format=fmt, page_count=3, blocks=blocks)


def test_long_section_is_split_with_overlap():
    paragraphs = [Block(f"Paragraph {i}. " + "Policy text here. " * 20, ["Leave"], page=1 + i // 3)
                  for i in range(6)]
    sections = chunk_document(_doc(paragraphs), SETTINGS)

    assert len(sections) == 1
    chunks = sections[0].chunks
    assert len(chunks) > 1
    assert all(c.token_count <= SETTINGS.max_tokens + SETTINGS.overlap_tokens + 5 for c in chunks)
    assert chunks[1].text.startswith("…")  # overlap from the previous chunk
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_never_cross_sections():
    blocks = [Block("Intro text.", ["A"], 1), Block("Other text.", ["B"], 2)]
    sections = chunk_document(_doc(blocks), SETTINGS)
    assert [s.heading_path for s in sections] == [["A"], ["B"]]
    assert [len(s.chunks) for s in sections] == [1, 1]


def test_long_table_is_split_and_repeats_header():
    rows = "\n".join(f"| item {i} | {i * 10} EUR |" for i in range(80))
    table = Block(f"| Item | Price |\n|---|---|\n{rows}", ["Prices"], 1, is_table=True)
    chunks = chunk_document(_doc([table]), SETTINGS)[0].chunks

    assert len(chunks) > 1
    assert all(c.is_table and c.text.startswith("| Item | Price |\n|---|---|") for c in chunks)
    assert all(count_tokens(c.text) <= SETTINGS.max_tokens for c in chunks)


def test_citation_labels_per_format():
    assert citation_label("pdf", 12, 12, ["X"]) == "p. 12"
    assert citation_label("pdf", 12, 13, ["X"]) == "pp. 12–13"
    assert citation_label("pptx", 4, 4, ["Q3 Results"]) == "slide 4"
    assert citation_label("docx", None, None, ["Travel", "Hotels"]) == "§ Hotels"


def test_chunk_header_is_short():
    header = chunk_header("Handbook", ["Benefits", "Leave"])
    assert header == "Document: Handbook\nSection: Benefits > Leave\n---\n"
