"""
Create the storage folder (local) or bucket (S3).

Run with:  make storage-init
"""

from app.storage.object_store import get_object_store


def main() -> None:
    store = get_object_store()
    store.ensure_ready()
    print(f"Storage ready: {store.name}")


if __name__ == "__main__":
    main()
