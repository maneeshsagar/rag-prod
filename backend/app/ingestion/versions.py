"""
Document versions: find near-copies of a document and decide which one is newest.

    link_versions(session, doc, text)   # after a document is indexed
    forget_versions(session, doc)       # before a document is removed

How "newer" is decided, strongest signal first:
    1. version number in the file name or title ("policy v3.pdf", "Rev 2.1")  -> explicit
    2. the date the document states (from its summary)                         -> explicit
    3. nearly identical text: one is a copy (a name with "copy", "backup" ...
       is the copy; otherwise the one added first is kept)                     -> duplicate
    4. words in the name: "updated", "final", "revised" ... vs "old", "draft"  -> inferred
    5. the file's last-modified time (bulk folders keep it)                    -> inferred
    6. otherwise the one added later is assumed newer                          -> inferred

Older versions are NOT hidden. Search ranks them lower (by how sure we are, see
config: older_version_weight_*) and tells the answer model which document is newer.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from app.db.models import Document, DocumentFingerprint, MinhashBand
from app.ingestion import fingerprint
from app.retrieval import vector_store

log = logging.getLogger(__name__)

NEAR_DUPLICATE = 0.70  # estimated text overlap above which two documents are versions
SAME_CONTENT = 0.95  # above this, with no version signal, it is a copy

VERSION_IN_NAME = re.compile(
    r"(?<![a-z])(?:v|version|rev|revision)[\s._-]*(\d+(?:\.\d+)*)", re.IGNORECASE
)
NEWER_WORDS = {"new", "newer", "latest", "updated", "update", "final", "revised", "current"}
OLDER_WORDS = {"old", "older", "copy", "draft", "backup", "archive", "archived", "previous",
               "obsolete", "superseded"}
DATE_FORMATS = ["%Y-%m-%d", "%Y-%m", "%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
                "%B %Y", "%b %Y"]


@dataclass
class Decision:
    newer: Document
    older: Document
    confidence: str  # explicit / duplicate / inferred
    reason: str


# ---------- reading version signals ----------


def version_label(*names: str) -> str | None:
    """'Leave Policy v3.pdf' -> '3'; 'Handbook rev 2.1' -> '2.1'; nothing found -> None."""
    for name in names:
        if match := VERSION_IN_NAME.search(name or ""):
            return match.group(1)
    return None


def normalize_date(text: str | None) -> str | None:
    """'Oct 14, 2026' -> '2026-10-14', 'March 2024' -> '2024-03', 'unknown' -> None."""
    text = (text or "").strip().rstrip(".")
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        precision = 10 if "%d" in fmt else 7 if ("%m" in fmt or "%b" in fmt or "%B" in fmt) else 4
        return parsed.strftime("%Y-%m-%d")[:precision]
    year = re.search(r"\b(19|20)\d{2}\b", text)
    return year.group(0) if year else None


def name_hint(doc: Document) -> int:
    """+1 if the name suggests a newer version ("updated"), -1 if older ("draft", "copy")."""
    words = set(re.findall(r"[a-z]+", f"{doc.source_name} {doc.title}".lower()))
    return (1 if words & NEWER_WORDS else 0) - (1 if words & OLDER_WORDS else 0)


def _version_number(label: str) -> tuple[int, ...]:
    return tuple(int(part) for part in label.split("."))


def _dates_differ(a: str, b: str) -> bool:
    """Compare only at the precision both have: '2024' vs '2024-03' is not a difference."""
    common = min(len(a), len(b))
    return a[:common] != b[:common]


def decide(new: Document, existing: Document, similarity: float) -> Decision:
    """Which of two near-identical documents is the newer one, and how sure we are."""
    if new.version_label and existing.version_label and new.version_label != existing.version_label:
        newer, older = sorted([new, existing], key=lambda d: _version_number(d.version_label))[::-1]
        return Decision(newer, older, "explicit",
                        f"version {newer.version_label} > {older.version_label}")

    if new.effective_date and existing.effective_date and _dates_differ(
            new.effective_date, existing.effective_date):
        newer, older = sorted([new, existing], key=lambda d: d.effective_date)[::-1]
        return Decision(newer, older, "explicit",
                        f"dated {newer.effective_date} vs {older.effective_date}")

    hints = {new.id: name_hint(new), existing.id: name_hint(existing)}
    by_hint = sorted([new, existing], key=lambda d: hints[d.id])[::-1]

    if similarity >= SAME_CONTENT:
        if hints[new.id] != hints[existing.id]:  # e.g. "policy.pdf" vs "policy copy.pdf"
            return Decision(*by_hint, "duplicate", f"{similarity:.0%} identical text, name says so")
        return Decision(existing, new, "duplicate", f"{similarity:.0%} identical text, kept the first")

    if hints[new.id] != hints[existing.id]:
        return Decision(*by_hint, "inferred", "the file name says so")

    if new.source_modified_at and existing.source_modified_at and abs(
            new.source_modified_at - existing.source_modified_at).total_seconds() > 60:
        newer, older = sorted([new, existing], key=lambda d: d.source_modified_at)[::-1]
        return Decision(newer, older, "inferred", "file modified later")

    return Decision(new, existing, "inferred", "added later; no version number or date to compare")


# ---------- storing fingerprints and links ----------


def _save_fingerprint(session, doc_id: str, signature: list[int]) -> None:
    session.add(DocumentFingerprint(document_id=doc_id, signature=signature))
    for band, bucket in fingerprint.band_keys(signature):
        session.add(MinhashBand(document_id=doc_id, band=band, bucket=bucket))


def _candidates(session, doc: Document, signature: list[int]) -> list[tuple[Document, float]]:
    """Indexed documents sharing an LSH bucket, with their estimated similarity."""
    keys = fingerprint.band_keys(signature)
    candidate_ids = set()
    for band, bucket in keys:
        candidate_ids.update(session.scalars(select(MinhashBand.document_id).where(
            MinhashBand.band == band, MinhashBand.bucket == bucket,
            MinhashBand.document_id != doc.id)))

    found = []
    for other_id in candidate_ids:
        other = session.get(Document, other_id)
        other_print = session.get(DocumentFingerprint, other_id)
        if other is None or other_print is None or other.status != "indexed":
            continue
        found.append((other, fingerprint.similarity(signature, other_print.signature)))
    return found


def _set_older(older: Document, newer: Document | None, confidence: str | None,
               similarity: float | None) -> None:
    """Record (or clear, when newer is None) that `older` has a newer version."""
    older.is_latest = newer is None
    older.superseded_by_id = newer.id if newer else None
    older.version_confidence = confidence
    older.version_similarity = similarity
    vector_store.set_version(older.id, is_latest=newer is None, confidence=confidence,
                             newer_title=_display_name(newer) if newer else None)


def _display_name(doc: Document) -> str:
    """Title plus file name: versions often share the same title."""
    return f"{doc.title} ({Path(doc.source_name).name})"


STRENGTH = {"explicit": 2, "duplicate": 2, "inferred": 1}


def _keeps_stronger_link(older: Document, confidence: str) -> bool:
    """A document already linked with more certainty keeps that link."""
    return (not older.is_latest
            and STRENGTH.get(older.version_confidence, 0) > STRENGTH[confidence])


def link_versions(session, doc: Document, text: str) -> list[Decision]:
    """Fingerprint `doc`, find its near-copies, and mark the older ones. Safe to re-run."""
    session.execute(delete(MinhashBand).where(MinhashBand.document_id == doc.id))
    session.execute(delete(DocumentFingerprint).where(DocumentFingerprint.document_id == doc.id))

    signature = fingerprint.minhash(text)
    if signature is None:
        return []  # too short to compare
    _save_fingerprint(session, doc.id, signature)

    decisions = []
    for other, similarity in _candidates(session, doc, signature):
        if similarity < NEAR_DUPLICATE:
            continue
        decision = decide(doc, other, similarity)
        if _keeps_stronger_link(decision.older, decision.confidence):
            continue
        _set_older(decision.older, decision.newer, decision.confidence, similarity)
        log.info("  version: '%s' is older than '%s' (%s: %s)", decision.older.source_name,
                 decision.newer.source_name, decision.confidence, decision.reason)
        decisions.append(decision)
    return decisions


def forget_versions(session, doc: Document) -> None:
    """`doc` is being removed: documents it had replaced become the latest again."""
    for older in session.scalars(select(Document).where(Document.superseded_by_id == doc.id)):
        _set_older(older, None, None, None)
