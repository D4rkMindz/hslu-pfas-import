"""Download the PFAS-Hub dataset for the Kestra pipeline."""

import hashlib
import logging
import sys
from typing import Any

import psycopg
import requests
from kestra import Kestra

from pfas_import.config import settings, setup_logging
from pfas_import.storage import get_s3_fs

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format=settings.LOG_FORMAT,
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB
LOG_INTERVAL_BYTES = 100 * 1024 * 1024  # 100 MiB
STATE_TABLE = "pfas_ingestion_state"


def get_postgres_connection() -> psycopg.Connection:
    """Connect to PostgreSQL using the project settings."""
    logger.info("Connecting to PostgreSQL.")

    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=10,
    )


def ensure_metadata_table() -> None:
    """Create the ingestion metadata table if it does not exist."""
    logger.info("Ensuring ingestion metadata table exists.")

    with get_postgres_connection() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                singleton_id SMALLINT PRIMARY KEY
                    CHECK (singleton_id = 1),
                source_sha256 CHAR(64) NOT NULL,
                source_s3_key TEXT NOT NULL,
                last_ingested_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    logger.info("Ingestion metadata table is ready.")


def get_last_successful_hash() -> str | None:
    """Return the hash of the last successfully ingested dataset."""
    logger.info("Reading hash of the last successful ingestion.")
    ensure_metadata_table()

    with get_postgres_connection() as conn:
        row = conn.execute(
            f"""
            SELECT source_sha256
            FROM {STATE_TABLE}
            WHERE singleton_id = 1
            """
        ).fetchone()

    previous_hash = row[0].strip() if row else None

    if previous_hash:
        logger.info("Previous successful SHA-256: %s", previous_hash)
    else:
        logger.info("No previous successful ingestion found.")

    return previous_hash


def download_dataset() -> dict[str, Any]:
    """Download to a fixed S3 key and detect changes."""
    s3_key = f"{settings.s3_raw_bucket}/last_download.parquet"

    logger.info("Starting PFAS-Hub dataset download.")
    logger.info("Source URL: %s", settings.DATASET_URL)
    logger.info("Destination: s3://%s", s3_key)

    fs = get_s3_fs()
    logger.info("S3 filesystem initialized.")

    sha256 = hashlib.sha256()
    size_bytes = 0
    next_log_bytes = LOG_INTERVAL_BYTES
    download_complete = False

    try:
        logger.info("Connecting to the dataset source.")

        with requests.get(
            settings.DATASET_URL,
            stream=True,
            timeout=(15, 300),
        ) as response:
            response.raise_for_status()

            logger.info(
                "HTTP response received: status=%s, content_length=%s bytes.",
                response.status_code,
                response.headers.get("Content-Length", "unknown"),
            )
            logger.info("Streaming dataset to S3.")

            with fs.open(s3_key, "wb") as target:
                for chunk in response.iter_content(
                    chunk_size=CHUNK_SIZE
                ):
                    if not chunk:
                        continue

                    target.write(chunk)
                    sha256.update(chunk)
                    size_bytes += len(chunk)

                    if size_bytes >= next_log_bytes:
                        logger.info(
                            "Download progress: %.1f MiB transferred.",
                            size_bytes / (1024 * 1024),
                        )
                        next_log_bytes = (
                            size_bytes // LOG_INTERVAL_BYTES + 1
                        ) * LOG_INTERVAL_BYTES

        download_complete = True
        file_hash = sha256.hexdigest()

        logger.info(
            "Download complete: %.1f MiB; SHA-256: %s",
            size_bytes / (1024 * 1024),
            file_hash,
        )

        previous_hash = get_last_successful_hash()

        if file_hash == previous_hash:
            logger.info("Dataset unchanged; ingestion will be skipped.")
            fs.rm(s3_key)
            logger.info("Removed unchanged download: %s", s3_key)

            return {
                "changed": False,
                "sha256": file_hash,
                "size_bytes": size_bytes,
            }

        logger.info("Dataset has changed; ready for validation and import.")

        return {
            "changed": True,
            "sha256": file_hash,
            "size_bytes": size_bytes,
        }

    except Exception:
        logger.exception("Dataset download or hash comparison failed.")
        raise

    finally:
        if not download_complete:
            try:
                if fs.exists(s3_key):
                    fs.rm(s3_key)
                    logger.info("Removed incomplete download: %s", s3_key)
            except Exception:
                logger.exception(
                    "Could not remove incomplete S3 download: %s",
                    s3_key,
                )


def main() -> None:
    logger.info("PFAS-Hub download task started.")

    result = download_dataset()

    logger.info(
        "Download task finished: changed=%s, size_bytes=%s.",
        result["changed"],
        result["size_bytes"],
    )

    Kestra.outputs(result)


if __name__ == "__main__":
    setup_logging()
    main()
