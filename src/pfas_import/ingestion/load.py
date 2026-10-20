import logging
import time
from collections.abc import Iterator
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql
from s3fs import S3FileSystem

from pfas_import.config import settings, setup_logging
from pfas_import.db import ensure_state_table, get_postgres_connection
from pfas_import.ingestion.constants import RAW_SOURCE_KEY, STATE_TABLE
from pfas_import.ingestion.process_dataset import get_source_sha256

logger = logging.getLogger(__name__)

FEATURE_TABLE: str = "pfas_features"
STAGING_TABLE: str = "_pfas_features_staging"

COPY_LOG_INTERVAL = 50_000

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


def publish_feature_dataframe(
        conn: psycopg.Connection,
        df: pd.DataFrame,
        source_sha256: str,
        source_key: str,
) -> None:
    """Atomically rebuild and publish the feature dataset."""

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
        logger.info("Starting PostgreSQL COPY: %s rows", len(df))

        with conn.cursor().copy(copy_statement) as copy:
            for i, row in enumerate(dataframe_rows(df), start=1):
                copy.write_row(row)

                if i % COPY_LOG_INTERVAL == 0:
                    logger.info(
                        "PostgreSQL COPY progress: %s / %s rows "
                        "(%.1f%%, %.1f s)",
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

        logger.info(
            "Rebuilding production table public.%s from current DataFrame schema",
            FEATURE_TABLE,
        )

        # Drop the old table and create a new one using the current
        # DataFrame's schema, copied from the staging table.
        # Both operations are transactional: on failure, PostgreSQL
        # rolls back and restores the previous production table.
        conn.execute(
            sql.SQL("DROP TABLE IF EXISTS public.{}").format(
                sql.Identifier(FEATURE_TABLE)
            )
        )

        conn.execute(
            sql.SQL("""
                    CREATE TABLE public.{}
                    (
                        LIKE
                        pg_temp
                        .
                    {}
                        INCLUDING
                        ALL
                    )
                    """).format(
                sql.Identifier(FEATURE_TABLE),
                sql.Identifier(STAGING_TABLE),
            )
        )

        logger.info(
            "Production table recreated with %s columns",
            len(columns),
        )

        logger.info("Publishing %s feature rows", len(df))

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
                    INSERT INTO public.{} (singleton_id,
                                           source_sha256,
                                           source_s3_key,
                                           last_ingested_at)
                    VALUES (1, %s, %s, NOW())
                    ON CONFLICT (singleton_id)
                    DO UPDATE SET
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

def read_source_dataframe(fs: Any, file) -> pd.DataFrame:
    """Read the source Parquet file directly from S3."""

    started_at = time.monotonic()
    logger.info("Reading Parquet from s3://%s", file)

    with fs.open(file, "rb") as source:
        df = pd.read_parquet(source, engine="pyarrow")

    logger.info(
        "Parquet loaded: %s rows, %s columns in %.1f s",
        len(df),
        len(df.columns),
        time.monotonic() - started_at,
    )

    return df

def load(import_file: str = RAW_SOURCE_KEY) -> None:
    logger.info("Starting PostgreSQL publication")

    feature_df = read_source_dataframe(S3FileSystem(), f"{settings.bucket_import}/{import_file}")

    with get_postgres_connection() as conn:
        publish_feature_dataframe(
            conn=conn,
            df=feature_df,
            source_sha256=get_source_sha256(S3FileSystem(), f"{settings.bucket_import}/{import_file}"),
            source_key=import_file,
        )


def main(import_file: str = RAW_SOURCE_KEY) -> None:
    setup_logging()
    load(import_file)


if __name__ == '__main__':
    main()
