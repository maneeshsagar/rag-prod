"""Near-duplicate fingerprints and the rules deciding which version is newer."""

import random
import statistics
from datetime import datetime

from app.db.models import Document
from app.ingestion import fingerprint, versions


def _doc(name: str, **fields) -> Document:
    return Document(id=name, source_name=name, title=name.rsplit(".", 1)[0], **fields)


def test_minhash_estimates_similarity_accurately():
    errors = []
    for seed in range(15):
        rng = random.Random(seed)
        words = [f"w{rng.randrange(2000)}" for _ in range(3000)]
        edited = [("edit" if i % rng.choice([10, 20, 40]) == 0 else w) for i, w in enumerate(words)]
        a, b = " ".join(words), " ".join(edited)
        sa, sb = fingerprint.shingles(a), fingerprint.shingles(b)
        true = len(sa & sb) / len(sa | sb)
        errors.append(fingerprint.similarity(fingerprint.minhash(a), fingerprint.minhash(b)) - true)
    assert abs(statistics.mean(errors)) < 0.03 and statistics.pstdev(errors) < 0.06


def test_short_text_has_no_fingerprint():
    assert fingerprint.minhash("only a few words here") is None


def test_version_label_and_dates():
    assert versions.version_label("Leave Policy v3.pdf") == "3"
    assert versions.version_label("handbook_rev2.1.docx") == "2.1"
    assert versions.version_label("revenue report 2024.pdf") is None
    assert versions.normalize_date("Oct 14, 2026") == "2026-10-14"
    assert versions.normalize_date("March 2024") == "2024-03"
    assert versions.normalize_date("unknown") is None


def test_version_number_wins_over_upload_order():
    v3, v10 = _doc("policy v3.pdf", version_label="3"), _doc("policy v10.pdf", version_label="10")
    decision = versions.decide(new=v3, existing=v10, similarity=0.8)
    assert decision.newer is v10 and decision.confidence == "explicit"


def test_stated_date_compared_at_shared_precision():
    old = _doc("a.pdf", effective_date="2023-05-01")
    new = _doc("b.pdf", effective_date="2024")
    assert versions.decide(new=old, existing=new, similarity=0.8).newer is new
    same_year = _doc("c.pdf", effective_date="2024-02")
    assert versions.decide(new=same_year, existing=new, similarity=0.8).confidence == "inferred"


def test_copy_in_the_name_marks_the_duplicate():
    original, copy = _doc("policy.pdf"), _doc("policy copy.pdf")
    decision = versions.decide(new=original, existing=copy, similarity=0.99)
    assert decision.older is copy and decision.confidence == "duplicate"


def test_name_words_then_modified_time():
    updated, plain = _doc("guide updated.md"), _doc("guide.md")
    assert versions.decide(new=plain, existing=updated, similarity=0.85).newer is updated

    older = _doc("a.md", source_modified_at=datetime(2024, 1, 1))
    newer = _doc("b.md", source_modified_at=datetime(2025, 1, 1))
    assert versions.decide(new=older, existing=newer, similarity=0.85).newer is newer
