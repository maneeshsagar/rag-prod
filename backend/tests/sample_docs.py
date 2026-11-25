"""Small sample files in every supported format, generated for tests."""

from pathlib import Path

import docx
import pymupdf
from pptx import Presentation
from pptx.util import Inches

LEAVE_TEXT = (
    "Employees receive 16 weeks of paid parental leave. "
    "Leave can be extended by 4 weeks of unpaid leave on request."
)


def make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for page_no, (heading, body) in enumerate(
        [("Benefits Overview", "This handbook describes employee benefits. " * 20),
         ("Parental Leave", LEAVE_TEXT + " " + "Managers must approve requests in writing. " * 15)],
        start=1,
    ):
        page = doc.new_page()
        page.insert_text((72, 80), heading, fontsize=22)
        page.insert_textbox(pymupdf.Rect(72, 110, 540, 760), body, fontsize=11)
        assert page_no == page.number + 1
    doc.save(path)
    return path


def make_docx(path: Path) -> Path:
    document = docx.Document()
    document.add_heading("Travel Policy", level=1)
    document.add_paragraph("This policy covers business travel.")
    document.add_heading("Hotels", level=2)
    document.add_paragraph("Hotel stays are capped at 200 EUR per night.")
    table = document.add_table(rows=3, cols=2)
    for row, (city, cap) in zip(table.rows, [("City", "Cap"), ("Paris", "250"), ("Berlin", "180")],
                                strict=True):
        row.cells[0].text, row.cells[1].text = city, cap
    document.save(path)
    return path


def make_pptx(path: Path) -> Path:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Q3 Results"
    slide.placeholders[1].text = "Revenue grew 12% year over year."
    slide.notes_slide.notes_text_frame.text = "Mention the new EU customers."
    blank = presentation.slides.add_slide(presentation.slide_layouts[5])
    blank.shapes.title.text = "Outlook"
    box = blank.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(1))
    box.text_frame.text = "We expect flat growth in Q4."
    presentation.save(path)
    return path


def make_html(path: Path) -> Path:
    path.write_text("""<html><head><title>Security FAQ</title></head><body>
<nav>Home | About</nav>
<main><h1>Security FAQ</h1><h2>Passwords</h2><p>Passwords must be at least 14 characters.</p>
<ul><li>Rotate every 12 months</li><li>Never reuse passwords</li></ul></main>
<footer>Copyright</footer></body></html>""")
    return path


def make_md(path: Path) -> Path:
    path.write_text("# Onboarding\n\nWelcome!\n\n## First week\n\nMeet your buddy on day one.\n\n"
                    "| Day | Task |\n|---|---|\n| 1 | Laptop |\n| 2 | Training |\n")
    return path
