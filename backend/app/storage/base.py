"""
The storage interface every backend follows.

Files are addressed by a "key" such as "originals/<doc_id>.pdf" — the same idea as
an S3 object key. Code that stores files only uses these methods, so the backend
(local folder or S3) can change in .env without touching anything else.
"""

from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    name: str  # where files go, shown in logs, e.g. "local:/.../data/storage"

    def ensure_ready(self) -> None:
        """Create the folder / bucket if it does not exist yet."""

    def check(self) -> None:
        """Raise an error if storage is not reachable (used by /health)."""

    def upload_file(self, local_path: Path, key: str) -> None: ...

    def download_file(self, key: str, local_path: Path) -> None: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...
