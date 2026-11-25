"""
Pick the storage backend from settings (STORAGE_BACKEND in .env).

    local -> files in a folder on this machine (default for development)
    s3    -> any S3-compatible service (SeaweedFS locally, Amazon S3 on AWS)

Usage:
    store = get_object_store()
    store.upload_file(Path("report.pdf"), "originals/<doc_id>.pdf")
"""

from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import ObjectStore
from app.storage.local_store import LocalStore
from app.storage.s3_store import S3Store


@lru_cache
def get_object_store() -> ObjectStore:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Store(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    return LocalStore(root=settings.storage_dir)
