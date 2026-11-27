"""Object storage for the Parquet files: S3 (SeaweedFS locally, AWS S3 in prod) or a local folder.

Both backends are fsspec filesystems with the same API, so one class covers both;
`repository/factory.py` picks the filesystem (OBJECT_STORE). A bucket is a top-level folder under `root`.
"""
import hashlib
import logging
import time
from collections.abc import Callable
from typing import IO, Any

import pandas as pd
import pyarrow.parquet as pq
from fsspec import AbstractFileSystem

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 8 * 1024 * 1024
HASH_LOG_INTERVAL = 64 * 1024 * 1024
PARQUET_BATCH_ROWS = 100_000


class ObjectStore:
    def __init__(self, fs: AbstractFileSystem, root: str = ""):
        self.fs = fs
        self.root = root.rstrip("/")

    def path(self, bucket: str, key: str = "") -> str:
        return "/".join(part for part in (self.root, bucket, key) if part)

    def url(self, bucket: str, key: str = "") -> str:
        """For log lines, e.g. s3://raw/last_download.parquet."""
        return self.fs.unstrip_protocol(self.path(bucket, key))

    def ensure_bucket(self, bucket: str) -> bool:
        """Create `bucket` if it does not exist yet. Safe to call on every startup."""
        path = self.path(bucket)
        self.fs.invalidate_cache()
        if not self.fs.exists(path):
            self.fs.mkdir(path)

        return self.fs.exists(path)

    def open(self, bucket: str, key: str, mode: str = "rb") -> IO[Any]:
        return self.fs.open(self.path(bucket, key), mode)

    def exists(self, bucket: str, key: str) -> bool:
        return self.fs.exists(self.path(bucket, key))

    def remove(self, bucket: str, key: str) -> None:
        self.fs.rm(self.path(bucket, key))

    def sha256(self, bucket: str, key: str) -> str:
        """Calculate SHA-256 without loading the complete object into memory."""
        digest = hashlib.sha256()
        bytes_read = 0
        next_log_at = HASH_LOG_INTERVAL
        started_at = time.monotonic()

        logger.info("Calculating SHA-256 for %s", self.url(bucket, key))

        with self.open(bucket, key) as source:
            while chunk := source.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
                bytes_read += len(chunk)

                if bytes_read >= next_log_at:
                    logger.info(
                        "Hashing progress: %.1f MiB read (%.1f s)",
                        bytes_read / (1024 * 1024),
                        time.monotonic() - started_at,
                    )
                    next_log_at += HASH_LOG_INTERVAL

        result = digest.hexdigest()

        logger.info(
            "SHA-256 completed: %.1f MiB in %.1f s; hash=%s",
            bytes_read / (1024 * 1024),
            time.monotonic() - started_at,
            result,
        )

        return result

    def read_parquet(
        self,
        bucket: str,
        key: str,
        columns: list[str] | None = None,
        batch_filter: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        """Read a Parquet file; with `batch_filter`, read it in batches and keep only the filtered rows."""
        started_at = time.monotonic()
        logger.info("Reading Parquet from %s", self.url(bucket, key))

        with self.open(bucket, key) as source:
            if batch_filter is None:
                df = pd.read_parquet(source, engine="pyarrow", columns=columns)
            else:
                # Only one batch of the whole file is in memory at a time, plus the rows kept so far.
                parquet = pq.ParquetFile(source)
                parts = [
                    batch_filter(batch.to_pandas())
                    for batch in parquet.iter_batches(PARQUET_BATCH_ROWS, columns=columns)
                ]
                empty = parquet.schema_arrow.empty_table().select(columns or parquet.schema_arrow.names)
                df = pd.concat(parts, ignore_index=True) if parts else empty.to_pandas()

        logger.info(
            "Parquet loaded: %s rows, %s columns in %.1f s",
            len(df),
            len(df.columns),
            time.monotonic() - started_at,
        )

        return df

    def write_parquet(self, bucket: str, key: str, df: pd.DataFrame) -> None:
        logger.info("Writing Parquet to %s", self.url(bucket, key))
        with self.open(bucket, key, "wb") as destination:
            df.to_parquet(destination, engine="pyarrow", index=False)
