"""Every format parses into blocks with headings, pages/slides and tables."""

from pathlib import Path

from app.ingestion.parsers import parse_file
from app.ingestion.parsers.base import Page, markdown_to_blocks
from tests.sample_docs import make_docx, make_html, make_md, make_pdf, make_pptx


def test_markdown_blocks_track_heading_path_and_tables():
    blocks = markdown_to_blocks([Page(1, "# A\n\nintro\n\n## B\n\ntext\n| x | y |\n|---|---|\n| 1 | 2 |")])
    assert [(b.heading_path, b.is_table) for b in blocks] == [
        (["A"], False), (["A", "B"], False), (["A", "B"], True)
    ]
    assert all(b.page == 1 for b in blocks)


def test_pdf(tmp_path: Path):
    doc = parse_file(make_pdf(tmp_path / "handbook.pdf"))
    assert doc.file_format == "pdf" and doc.page_count == 2
    leave = [b for b in doc.blocks if "16 weeks" in b.text]
    assert leave and leave[0].page == 2


def test_docx(tmp_path: Path):
    doc = parse_file(make_docx(tmp_path / "travel.docx"))
    assert doc.title == "Travel Policy"
    table = next(b for b in doc.blocks if b.is_table)
    assert table.heading_path == ["Travel Policy", "Hotels"]
    assert "| Paris | 250 |" in table.text


def test_pptx_one_section_per_slide_with_notes(tmp_path: Path):
    doc = parse_file(make_pptx(tmp_path / "q3.pptx"))
    assert doc.page_count == 2
    assert {b.heading_path[0] for b in doc.blocks} == {"Q3 Results", "Outlook"}
    assert any("Speaker notes: Mention the new EU customers." in b.text for b in doc.blocks)
    assert next(b for b in doc.blocks if "flat growth" in b.text).page == 2


def test_html_drops_navigation(tmp_path: Path):
    doc = parse_file(make_html(tmp_path / "faq.html"))
    text = " ".join(b.text for b in doc.blocks)
    assert doc.title == "Security FAQ"
    assert "14 characters" in text and "- Rotate every 12 months" in text
    assert "Home | About" not in text and "Copyright" not in text


def test_markdown(tmp_path: Path):
    doc = parse_file(make_md(tmp_path / "onboarding.md"))
    assert doc.title == "Onboarding"
    assert any(b.is_table and b.heading_path == ["Onboarding", "First week"] for b in doc.blocks)


def test_non_english_text_is_flagged():
    from app.ingestion.parsers.base import Block, ParsedDocument
    from app.ingestion.quality import check_quality

    german = "Die Reiserichtlinie gilt für alle Mitarbeiter und Hotels werden vorher gebucht. " * 30
    english = "The travel policy applies to all staff and hotels must be booked in advance. " * 30
    flagged = [any("not English" in w for w in check_quality(
        ParsedDocument(title="t", file_format="md", page_count=None,
                       blocks=[Block(text=text, heading_path=[], page=None)])))
               for text in (german, english)]
    assert flagged == [True, False]
