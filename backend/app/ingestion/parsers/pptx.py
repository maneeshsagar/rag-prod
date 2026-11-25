"""
PPTX -> one Markdown page per slide, using python-pptx.

Each slide becomes a section titled by the slide title. Text boxes, tables and the
speaker notes are kept. Citations point to the slide number.
"""

from pathlib import Path

from pptx import Presentation

from app.ingestion.parsers.base import Page
from app.ingestion.parsers.docx import table_to_markdown


def _slide_markdown(slide, number: int) -> str:
    title_shape = slide.shapes.title
    title = title_shape.text.strip() if title_shape is not None and title_shape.text else ""
    parts = [f"# {title or f'Slide {number}'}"]

    for shape in slide.shapes:
        if shape == title_shape:
            continue
        if shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                text = "".join(run.text for run in paragraph.runs).strip()
                if text:
                    parts.append(f"- {text}" if paragraph.level > 0 else text)
        elif shape.has_table:
            rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
            parts.append(table_to_markdown(rows))

    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if notes:
            parts.append(f"Speaker notes: {notes}")

    return "\n\n".join(parts)


def parse_pptx(path: Path) -> tuple[str | None, list[Page]]:
    presentation = Presentation(str(path))
    title = presentation.core_properties.title or None
    pages = [
        Page(number=index + 1, markdown=_slide_markdown(slide, index + 1))
        for index, slide in enumerate(presentation.slides)
    ]
    return title, pages
