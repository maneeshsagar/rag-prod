"""
S3 storage. Works with anything that speaks the S3 API:
- SeaweedFS locally  (S3_ENDPOINT_URL=http://localhost:8333, started with `make s3-up`)
- Amazon S3 on AWS   (S3_ENDPOINT_URL empty; credentials come from the IAM role)
"""

from pathlib import Path

import boto3
from botocore.exceptions import ClientError


class S3Store:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str,
    ) -> None:
        self.bucket = bucket
        self.name = f"s3:{endpoint_url or 'aws'}/{bucket}"
        # Empty values become None so boto3 falls back to AWS defaults (IAM role, real S3).
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
        )

    def ensure_ready(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)

    def upload_file(self, local_path: Path, key: str) -> None:
        self.client.upload_file(str(local_path), self.bucket, key)

    def download_file(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(local_path))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
