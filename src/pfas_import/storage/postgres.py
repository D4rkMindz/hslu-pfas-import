"""PostgreSQL store (TARGET=local), for the features and the ingestion state
(`repository/ingestion_state.py`).

The connection opens on first use, so a step that only needs the database after a long download
does not hold an idle connection during it.
"""
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Self

import pandas as pd
import psycopg
from psycopg import sql

from pfas_import.domain.sql_dialect import POSTGRES
from pfas_import.storage.base import FeatureStore

logger = logging.getLogger(__name__)

COPY_LOG_INTERVAL = 50_000


def connect(host: str, port: str, dbname: str, user: str, password: str) -> psycopg.Connection:
    logger.info("Connecting to PostgreSQL at %s:%s, database=%s", host, port, dbname)

    started_at = time.monotonic()

    conn = psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=15,
    )

    logger.info("PostgreSQL connection established in %.1f s", time.monotonic() - started_at)

    return conn


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


class PostgresStore(FeatureStore):
    dialect = POSTGRES

    def __init__(self, connect: Callable[[], psycopg.Connection], schema: str = "public"):
        self._connect = connect
        self._conn: psycopg.Connection | None = None
        self.schema = schema

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        # Same as `with psycopg.connect() as conn`: commit on success, roll back on error, then close.
        if self._conn is not None:
            self._conn.__exit__(exc_type, exc, tb)
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Nested calls become savepoints of the outermost transaction."""
        with self.conn.transaction():
            yield

    def execute(self, statement: sql.Composable, params: Sequence[Any] | None = None) -> psycopg.Cursor:
        return self.conn.execute(statement, params)

    def describe(self, table: str) -> str:
        return f"PostgreSQL {self.schema}.{table}"

    def table_sql(self, table: str) -> str:
        return f"{POSTGRES.quote(self.schema)}.{POSTGRES.quote(table)}"

    def columns(self, table: str) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (self.schema, table),
        ).fetchall()
        return [row[0] for row in rows]

    def query(self, sql: str) -> list[Sequence[Any]]:
        return self.conn.execute(sql).fetchall()  # pyright: ignore[reportArgumentType]

    def replace(self, table: str, df: pd.DataFrame) -> None:
        """Atomically rebuild `table` from `df` (staging table, then drop and recreate)."""

        if len(df.columns) == 0:
            raise ValueError("Feature DataFrame contains no columns.")

        if not df.columns.is_unique:
            raise ValueError("Feature DataFrame contains duplicate column names.")

        columns = [str(column) for column in df.columns]

        if any(not column or "\x00" in column for column in columns):
            raise ValueError("Feature DataFrame contains an invalid column name.")

        staging_table = f"_{table}_staging"
        target = sql.Identifier(self.schema, table)

        logger.info(
            "Preparing PostgreSQL publication: %s rows, %s columns, table=%s.%s",
            len(df),
            len(columns),
            self.schema,
            table,
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

        with self.transaction():
            logger.info("Creating temporary staging table")

            self.conn.execute(
                sql.SQL("DROP TABLE IF EXISTS pg_temp.{}").format(
                    sql.Identifier(staging_table)
                )
            )

            self.conn.execute(
                sql.SQL("CREATE TEMP TABLE {} ({}) ON COMMIT DROP").format(
                    sql.Identifier(staging_table),
                    column_definitions,
                )
            )

            copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(staging_table),
                column_list,
            )

            copy_started_at = time.monotonic()
            logger.info("Starting PostgreSQL COPY: %s rows", len(df))

            with self.conn.cursor().copy(copy_statement) as copy:
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
                "Rebuilding production table %s.%s from current DataFrame schema",
                self.schema,
                table,
            )

            # Drop the old table and create a new one using the current
            # DataFrame's schema, copied from the staging table.
            # Both operations are transactional: on failure, PostgreSQL
            # rolls back and restores the previous production table.
            self.conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {}").format(target)
            )

            self.conn.execute(
                sql.SQL("""
                        CREATE TABLE {}
                        (
                            LIKE
                            pg_temp
                            .
                        {}
                            INCLUDING
                            ALL
                        )
                        """).format(
                    target,
                    sql.Identifier(staging_table),
                )
            )

            logger.info(
                "Production table recreated with %s columns",
                len(columns),
            )

            logger.info("Publishing %s feature rows", len(df))

            self.conn.execute(
                sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(
                    target,
                    column_list,
                    column_list,
                    sql.Identifier(staging_table),
                )
            )
