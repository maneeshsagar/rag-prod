"""
Create the Qdrant collections (chunks_v1, documents_v1) and their aliases.

Run with:  make index-init   (also part of `make init`)
"""

from app.retrieval.vector_store import ensure_collections


def main() -> None:
    ensure_collections()
    print("Qdrant collections ready: chunks -> chunks_v1, documents -> documents_v1")


if __name__ == "__main__":
    main()
