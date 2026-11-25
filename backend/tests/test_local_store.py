"""The local storage backend saves, finds and deletes files by key."""

from pathlib import Path

import pytest

from app.storage.local_store import LocalStore


def test_upload_download_delete(tmp_path: Path):
    store = LocalStore(root=tmp_path / "storage")
    store.ensure_ready()
    source = tmp_path / "report.pdf"
    source.write_bytes(b"hello")

    store.upload_file(source, "originals/doc1.pdf")
    assert store.exists("originals/doc1.pdf")

    copy = tmp_path / "out" / "copy.pdf"
    store.download_file("originals/doc1.pdf", copy)
    assert copy.read_bytes() == b"hello"

    store.delete("originals/doc1.pdf")
    assert not store.exists("originals/doc1.pdf")


def test_rejects_keys_outside_the_folder(tmp_path: Path):
    store = LocalStore(root=tmp_path / "storage")
    with pytest.raises(ValueError):
        store.exists("../secret.txt")
