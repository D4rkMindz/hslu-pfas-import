
"""Transform the latest Parquet file and publish the current feature dataset."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterator
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql
from kestra import Kestra

from pfas_import.config import settings, setup_logging
from pfas_import.storage import get_s3_fs
from pfas_import.features.transform import load_and_transform
from pfas_import.features.constants import (
    SOURCE_KEY,
    STATE_TABLE,
)

FEATURE_TABLE: str = "pfas_features"
STAGING_TABLE: str = "_pfas_features_staging"

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 8 * 1024 * 1024
HASH_LOG_INTERVAL = 64 * 1024 * 1024
COPY_LOG_INTERVAL = 50_000


def get_source_sha256(fs: Any, bucket: str, key: str) -> str:
    """Calculate SHA-256 without loading the complete object into memory."""

    digest = hashlib.sha256()
    path = f"{bucket}/{key}"
    bytes_read = 0
    next_log_at = HASH_LOG_INTERVAL
    started_at = time.monotonic()

    logger.info("Calculating SHA-256 for s3://%s/%s", bucket, key)

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


def read_source_dataframe(fs: Any, bucket: str, key: str) -> pd.DataFrame:
    """Read the source Parquet file directly from S3."""

    started_at = time.monotonic()
    logger.info("Reading Parquet from s3://%s/%s", bucket, key)

    with fs.open(f"{bucket}/{key}", "rb") as source:
        df = pd.read_parquet(source, engine="pyarrow")

    logger.info(
        "Parquet loaded: %s rows, %s columns in %.1f s",
        len(df),
        len(df.columns),
        time.monotonic() - started_at,
    )

    return df


def get_postgres_connection() -> psycopg.Connection:
    """Use the existing application settings."""

    logger.info(
        "Connecting to PostgreSQL at %s:%s, database=%s",
        settings.postgres_host,
        settings.postgres_port,
        settings.postgres_db,
    )

    started_at = time.monotonic()

    conn = psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=15,
    )

    logger.info(
        "PostgreSQL connection established in %.1f s",
        time.monotonic() - started_at,
    )

    return conn


def postgres_type(series: pd.Series) -> str:
    """Map DataFrame dtypes to PostgreSQL column types."""

    dtype = series.dtype

    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        if isinstance(dtype, pd.DatetimeTZDtype):
            return "TIMESTAMPTZ"
        return "TIMESTAMP"

    return "TEXT"


def normalize_value(value: Any) -> Any:
    """Convert pandas/NumPy scalar values to DB-adaptable Python values."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None

    if not isinstance(value, (list, tuple, dict)):
        try:
            missing = pd.isna(value)
            if isinstance(missing, bool) and missing:
                return None
            if getattr(missing, "ndim", 0) == 0 and bool(missing):
                return None
        except (TypeError, ValueError):
            pass

    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass

    return value


def dataframe_rows(df: pd.DataFrame) -> Iterator[tuple[Any, ...]]:
    """Yield normalized rows for PostgreSQL COPY."""

    for row in df.itertuples(index=False, name=None):
        yield tuple(normalize_value(value) for value in row)


def ensure_state_table(conn: psycopg.Connection) -> None:
    """Create ingestion metadata if it does not exist."""

    conn.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS public.{} (
                singleton_id SMALLINT PRIMARY KEY
                    CHECK (singleton_id = 1),
                source_sha256 CHAR(64),
                source_s3_key TEXT,
                last_ingested_at TIMESTAMPTZ
            )
        """).format(sql.Identifier(STATE_TABLE))
    )


def publish_feature_dataframe(
    conn: psycopg.Connection,
    df: pd.DataFrame,
    source_sha256: str,
    source_key: str,
) -> None:
    """Stage, validate and atomically publish the feature dataset."""

    if df.empty:
        raise ValueError(
            "Feature DataFrame is empty; refusing to replace current data."
        )

    if len(df.columns) == 0:
        raise ValueError("Feature DataFrame contains no columns.")

    if not df.columns.is_unique:
        raise ValueError("Feature DataFrame contains duplicate column names.")

    columns = [str(column) for column in df.columns]

    if any(not column or "\x00" in column for column in columns):
        raise ValueError("Feature DataFrame contains an invalid column name.")

    logger.info(
        "Preparing PostgreSQL publication: %s rows, %s columns, table=public.%s",
        len(df),
        len(columns),
        FEATURE_TABLE,
    )

    column_definitions = sql.SQL(", ").join(
        sql.SQL("{} {}").format(
            sql.Identifier(column),
            sql.SQL(postgres_type(df[column])),
        )
        for column in columns
    )

    column_list = sql.SQL(", ").join(
        sql.Identifier(column) for column in columns
    )

    transaction_started_at = time.monotonic()

    with conn.transaction():
        logger.info("Starting PostgreSQL publication transaction")

        ensure_state_table(conn)

        logger.info("Creating temporary staging table")

        conn.execute(
            sql.SQL("DROP TABLE IF EXISTS pg_temp.{}").format(
                sql.Identifier(STAGING_TABLE)
            )
        )

        conn.execute(
            sql.SQL("CREATE TEMP TABLE {} ({}) ON COMMIT DROP").format(
                sql.Identifier(STAGING_TABLE),
                column_definitions,
            )
        )

        copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier(STAGING_TABLE),
            column_list,
        )

        copy_started_at = time.monotonic()
        logger.info(
            "Starting PostgreSQL COPY: %s rows",
            len(df),
        )

        with conn.cursor().copy(copy_statement) as copy:
            for i, row in enumerate(dataframe_rows(df), start=1):
                copy.write_row(row)

                if i % COPY_LOG_INTERVAL == 0:
                    logger.info(
                        "PostgreSQL COPY progress: %s / %s rows (%.1f%%, %.1f s)",
                        i,
                        len(df),
                        100 * i / len(df),
                        time.monotonic() - copy_started_at,
                    )

        logger.info(
            "PostgreSQL COPY completed: %s rows in %.1f s",
            len(df),
            time.monotonic() - copy_started_at,
        )

        logger.info("Ensuring production table public.%s exists", FEATURE_TABLE)

        conn.execute(
            sql.SQL("""
                CREATE TABLE IF NOT EXISTS public.{} (
                    LIKE pg_temp.{} INCLUDING ALL
                )
            """).format(
                sql.Identifier(FEATURE_TABLE),
                sql.Identifier(STAGING_TABLE),
            )
        )

        existing_columns = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (FEATURE_TABLE,),
        ).fetchall()

        existing_names = [row[0] for row in existing_columns]

        if existing_names != columns:
            raise ValueError(
                f"Schema mismatch for public.{FEATURE_TABLE}. "
                f"Existing columns: {existing_names}; "
                f"new columns: {columns}. "
                "No production data was replaced."
            )

        logger.info("Production schema validated")

        logger.info("Replacing current feature data")

        conn.execute(
            sql.SQL("TRUNCATE TABLE public.{}").format(
                sql.Identifier(FEATURE_TABLE)
            )
        )

        conn.execute(
            sql.SQL("INSERT INTO public.{} ({}) SELECT {} FROM {}").format(
                sql.Identifier(FEATURE_TABLE),
                column_list,
                column_list,
                sql.Identifier(STAGING_TABLE),
            )
        )

        logger.info("Updating ingestion state")

        conn.execute(
            sql.SQL("""
                INSERT INTO public.{} (
                    singleton_id,
                    source_sha256,
                    source_s3_key,
                    last_ingested_at
                )
                VALUES (1, %s, %s, NOW())
                ON CONFLICT (singleton_id) DO UPDATE SET
                    source_sha256 = EXCLUDED.source_sha256,
                    source_s3_key = EXCLUDED.source_s3_key,
                    last_ingested_at = EXCLUDED.last_ingested_at
            """).format(sql.Identifier(STATE_TABLE)),
            (source_sha256, source_key),
        )

    logger.info(
        "Publication transaction committed successfully in %.1f s",
        time.monotonic() - transaction_started_at,
    )


def main() -> None:
    """Read, transform and publish the latest downloaded dataset."""

    started_at = time.monotonic()

    logger.info("========== process_dataset started ==========")
    logger.info("Source object: s3://%s/%s", settings.s3_raw_bucket, SOURCE_KEY)

    logger.info("Initializing S3 filesystem")
    fs = get_s3_fs()
    bucket = settings.s3_raw_bucket
    logger.info("S3 filesystem initialized")

    source_sha256 = get_source_sha256(fs, bucket, SOURCE_KEY)

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

    raw_df = read_source_dataframe(fs, bucket, SOURCE_KEY)

    logger.info("Starting feature transformation")
    transform_started_at = time.monotonic()

    feature_df = load_and_transform(raw_df)

    logger.info(
        "Feature transformation completed: %s rows, %s columns in %.1f s",
        len(feature_df),
        len(feature_df.columns),
        time.monotonic() - transform_started_at,
    )

    # Release the raw DataFrame before PostgreSQL publication.
    del raw_df

    logger.info("Starting PostgreSQL publication")

    with get_postgres_connection() as conn:
        publish_feature_dataframe(
            conn=conn,
            df=feature_df,
            source_sha256=source_sha256,
            source_key=SOURCE_KEY,
        )

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
    setup_logging()

    try:
        main()
    except Exception:
        logger.exception("process_dataset failed")
        raise
