"""
HTML -> Markdown, using BeautifulSoup.

Navigation, headers, footers, scripts and styles are removed. Headings, paragraphs,
list items, tables and code blocks are kept in reading order.
"""

from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.ingestion.parsers.base import Page
from app.ingestion.parsers.docx import table_to_markdown

BOILERPLATE = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]
CONTENT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre", "blockquote"]


def _element_markdown(element: Tag) -> str:
    name = element.name
    if name == "table":
        rows = [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            for row in element.find_all("tr")
        ]
        return table_to_markdown([row for row in rows if row])

    text = element.get_text(" ", strip=True)
    if not text:
        return ""
    if name.startswith("h"):
        return f"{'#' * int(name[1])} {text}"
    if name == "li":
        return f"- {text}"
    return text


def parse_html(path: Path) -> tuple[str | None, list[Page]]:
    soup = BeautifulSoup(path.read_text(errors="replace"), "lxml")
    title = soup.title.get_text(strip=True) if soup.title else None

    for tag in soup.find_all(BOILERPLATE):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup

    parts: list[str] = []
    for element in root.find_all(CONTENT_TAGS):
        # Skip elements inside a table or list item we already output as a whole.
        if element.find_parent(["table", "li", "pre"]) is not None:
            continue
        text = _element_markdown(element)
        if text:
            parts.append(text)

    return title, [Page(number=None, markdown="\n\n".join(parts))]
