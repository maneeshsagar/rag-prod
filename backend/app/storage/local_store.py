"""
Local-folder storage (default for development).

A key like "originals/abc.pdf" is saved as <root>/originals/abc.pdf.
"""

import shutil
from pathlib import Path


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.name = f"local:{self.root}"

    def _path_for(self, key: str) -> Path:
        """Turn a key into a file path, refusing keys that escape the root (e.g. '../x')."""
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"invalid storage key: {key!r}")
        return path

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def check(self) -> None:
        if not self.root.is_dir():
            raise RuntimeError(f"storage folder missing: {self.root} (run `make init`)")

    def upload_file(self, local_path: Path, key: str) -> None:
        target = self._path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, target)

    def download_file(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path_for(key), local_path)

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)
