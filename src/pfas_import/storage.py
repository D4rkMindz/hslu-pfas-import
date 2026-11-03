"""S3-compatible object storage (SeaweedFS locally, AWS S3 in prod)."""
import os

import s3fs
from s3fs import S3FileSystem


def get_s3_fs() -> S3FileSystem:
    # S3_ENDPOINT_URL unset -> real AWS S3.
    # Key/secret unset -> boto credential chain (e.g. IAM role in prod).
    return S3FileSystem()


def ensure_bucket(fs: s3fs.S3FileSystem, bucket: str) -> bool:
    """Create `bucket` if it does not exist yet. Safe to call on every startup."""
    fs.invalidate_cache()
    if not fs.exists(bucket):
        fs.mkdir(bucket)

    return fs.exists(bucket)


def raw_bucket() -> str:
    return os.getenv("S3_RAW_BUCKET", "raw")


def import_bucket() -> str:
    return os.getenv("S3_IMPORT_BUCKET", "import")


def setup() -> bool:
    return ensure_bucket(S3FileSystem(), raw_bucket()) and ensure_bucket(S3FileSystem(), import_bucket())
