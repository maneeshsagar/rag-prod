"""
Ingest files or folders from the command line.

Run with:  make ingest DIR=data/smoke
           (or: uv run python -m scripts.ingest path/to/file.pdf other/folder)
"""

import sys
from pathlib import Path

from app.core.logging import setup_logging
from app.ingestion.parsers import SUPPORTED_EXTENSIONS
from app.ingestion.pipeline import ingest_file
from app.retrieval.vector_store import ensure_collections


def find_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        files += [p for p in candidates if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    return files


def main() -> None:
    setup_logging()
    if len(sys.argv) < 2:
        sys.exit("usage: python -m scripts.ingest <file or folder> [...]")

    ensure_collections()
    files = find_files(sys.argv[1:])
    if not files:
        sys.exit(f"No supported files found. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    failed = []
    for path in files:
        try:
            ingest_file(path)
        except Exception as error:  # noqa: BLE001 — keep going; the error is saved on the document
            failed.append((path.name, error))

    print(f"\nIngested {len(files) - len(failed)} of {len(files)} files.")
    for name, error in failed:
        print(f"  FAILED {name}: {error}")


if __name__ == "__main__":
    main()
