"""Transform the latest Parquet file and publish the current feature dataset.
- compares hash of last_download_parquet before ingestion
- if changed then it transforms the features and saves it in postgresql "pfas_features" table
- Updates hash of last ingestion an returns usable values for Kestra
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import pandas as pd
import s3fs
from kestra import Kestra
from psycopg import sql

from pfas_import.config import settings, setup_logging
from pfas_import.db import ensure_state_table, get_postgres_connection
from pfas_import.ingestion.constants import (
    IMPORT_SOURCE_KEY,
    RAW_SOURCE_KEY,
    STATE_TABLE,
)
from pfas_import.ingestion.transform import transform
from pfas_import.storage import get_s3_fs

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 8 * 1024 * 1024
HASH_LOG_INTERVAL = 64 * 1024 * 1024


def get_source_sha256(fs: Any, path: str) -> str:
    """Calculate SHA-256 without loading the complete object into memory."""

    digest = hashlib.sha256()
    bytes_read = 0
    next_log_at = HASH_LOG_INTERVAL
    started_at = time.monotonic()

    logger.info("Calculating SHA-256 for s3://%s", path)

    with fs.open(path, "rb") as source:
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


def read_source_dataframe(fs: Any, file) -> pd.DataFrame:
    """Read the source Parquet file directly from S3."""

    started_at = time.monotonic()
    logger.info("Reading Parquet from s3://%s", file)

    with fs.open(file, "rb") as source:
        df = pd.read_parquet(source, engine="pyarrow").query('country=="France"')

    logger.info(
        "Parquet loaded: %s rows, %s columns in %.1f s",
        len(df),
        len(df.columns),
        time.monotonic() - started_at,
    )

    return df


def write_import_dataframe(fs: s3fs.S3FileSystem, file, df: pd.DataFrame) -> None:
    logger.info("Writing Parquet to s3://%s", file)
    with fs.open(file, "wb") as destination:
        df.to_parquet(destination, engine="pyarrow", index=False)


def main(raw_source_file: str = RAW_SOURCE_KEY, target_file: str = IMPORT_SOURCE_KEY) -> None:
    """Read, transform and publish the latest downloaded dataset."""
    setup_logging()

    started_at = time.monotonic()

    logger.info("========== process_dataset started ==========")
    logger.info("Source object: s3://%s/%s", settings.bucket_raw, raw_source_file)

    logger.info("Initializing S3 filesystem")
    fs = get_s3_fs()
    logger.info("S3 filesystem initialized")

    source_sha256 = get_source_sha256(fs, f"{settings.bucket_raw}/{raw_source_file}")

    logger.info("Checking ingestion state in PostgreSQL")

    with get_postgres_connection() as conn:
        with conn.transaction():
            ensure_state_table(conn)

            row = conn.execute(
                sql.SQL("""
                        SELECT source_sha256
                        FROM public.{}
                        WHERE singleton_id = 1
                        """).format(sql.Identifier(STATE_TABLE))
            ).fetchone()

        previous_sha256 = row[0].strip() if row and row[0] else None

    if previous_sha256 == source_sha256:
        logger.info(
            "Source unchanged; skipping transformation and publication"
        )

        Kestra.outputs(
            {
                "changed": False,
                "sha256": source_sha256,
                "rows": 0,
            }
        )

        logger.info(
            "========== process_dataset finished: unchanged (%.1f s) ==========",
            time.monotonic() - started_at,
        )
        return

    logger.info("Source changed or not yet ingested; processing dataset")
    raw_df = read_source_dataframe(fs, f"{settings.bucket_raw}/{raw_source_file}")

    logger.info("Starting feature transformation")
    transform_started_at = time.monotonic()

    feature_df = transform(raw_df)

    logger.info(
        "Feature transformation completed: %s rows, %s columns in %.1f s",
        len(feature_df),
        len(feature_df.columns),
        time.monotonic() - transform_started_at,
    )

    # Release the raw DataFrame before PostgreSQL publication.
    del raw_df

    write_import_dataframe(fs, f"{settings.bucket_import}/{target_file}", feature_df)

    Kestra.outputs(
        {
            "changed": True,
            "sha256": source_sha256,
            "rows": len(feature_df),
        }
    )

    logger.info(
        "========== process_dataset finished successfully: "
        "%s rows in %.1f s ==========",
        len(feature_df),
        time.monotonic() - started_at,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("process_dataset failed")
        raise
