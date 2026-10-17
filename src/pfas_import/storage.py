"""S3-compatible object storage (SeaweedFS locally, AWS S3 in prod)."""

import os

import s3fs
from dotenv import load_dotenv

from pfas_import.config import settings as s

load_dotenv()


def get_s3_fs() -> s3fs.S3FileSystem:
    # S3_ENDPOINT_URL unset -> real AWS S3.
    # Key/secret unset -> boto credential chain (e.g. IAM role in prod).
    return s3fs.S3FileSystem(
        key=s.s3_access_key,
        secret=s.s3_secret_key,
        endpoint_url=s.s3_endpoint_url,
        client_kwargs={"region_name": s.s3_region},
    )


def ensure_bucket(fs: s3fs.S3FileSystem, bucket: str) -> None:
    """Create `bucket` if it does not exist yet. Safe to call on every startup."""
    fs.invalidate_cache()
    if not fs.exists(bucket):
        fs.mkdir(bucket)


def raw_bucket() -> str:
    return os.getenv("S3_RAW_BUCKET", "raw")


if __name__ == "__main__":
    ensure_bucket(get_s3_fs(), raw_bucket())
