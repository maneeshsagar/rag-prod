"""Citation checks: valid numbers, support by the source text, not-found answers."""

from app.generation.citations import cited_numbers, verify_citations
from app.generation.prompts import NOT_FOUND
from app.retrieval.search import Source


def _source(number: int, text: str) -> Source:
    return Source(number=number, doc_id=f"d{number}", title=f"Doc {number}",
                  citation_label="p. 1", file_format="pdf", section_path="Leave", text=text)


SOURCES = [
    _source(1, "Employees receive 16 weeks of paid parental leave. Other unrelated sentence here."),
    _source(2, "The cafeteria opens at 8am on weekdays and serves breakfast daily."),
]


def test_cited_numbers_in_order():
    assert cited_numbers("A [2]. B [1, 2]. C [3][1].") == [2, 1, 3]


def test_supported_and_invalid_citations():
    answer = "Employees receive 16 weeks of paid parental leave [1]. Parking is free [5]."
    result = verify_citations(answer, SOURCES)

    assert result["invalid_numbers"] == [5]
    [citation] = result["citations"]
    assert citation["number"] == 1 and citation["supported"]
    assert "16 weeks" in citation["quote"]


def test_citation_after_the_full_stop_counts_for_that_sentence():
    result = verify_citations("Employees receive 16 weeks of paid parental leave. [1]", SOURCES)
    assert result["citations"][0]["supported"]


def test_plural_words_match():
    sources = [_source(1, "The bureau score range is 300 to 900.")]
    result = verify_citations("Credit bureaus use a score range of 300 to 900 [1].", sources)
    assert result["citations"][0]["supported"]


def test_unsupported_claim_is_flagged():
    result = verify_citations("Remote work is allowed three days per week [2].", SOURCES)
    assert result["citations"][0]["supported"] is False


def test_not_found_answer():
    result = verify_citations(NOT_FOUND, SOURCES)
    assert result["not_found"] and not result["uncited"] and result["citations"] == []
