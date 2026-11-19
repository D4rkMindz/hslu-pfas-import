"""Read access to the published feature table: PostgreSQL (`local`) or BigQuery (`online`)."""
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

import psycopg

from pfas_import.config import settings
from pfas_import.db import get_postgres_connection
from pfas_import.ingestion.constants import FEATURE_TABLE
from pfas_import.verification.checks import BIGQUERY, POSTGRES, Dialect


class Target(Protocol):
    dialect: Dialect
    name: str  # human-readable table name for the log
    table_sql: str  # table reference to use in SQL

    def columns(self) -> list[str]:
        """Column names in table order; empty if the table does not exist."""
        ...

    def query(self, sql: str) -> list[Sequence[Any]]: ...


class PostgresTarget:
    dialect = POSTGRES

    def __init__(self, conn: psycopg.Connection, schema: str = "public", table: str = FEATURE_TABLE):
        self.conn = conn
        self.schema = schema
        self.table = table
        self.name = f"PostgreSQL {schema}.{table}"
        self.table_sql = f"{POSTGRES.quote(schema)}.{POSTGRES.quote(table)}"

    def columns(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (self.schema, self.table),
        ).fetchall()
        return [row[0] for row in rows]

    def query(self, sql: str) -> list[Sequence[Any]]:
        return self.conn.execute(sql).fetchall()  # pyright: ignore[reportArgumentType]


class BigQueryTarget:
    dialect = BIGQUERY

    def __init__(self, client: Any, table_id: str):
        self.client = client
        self.table_id = table_id
        self.name = f"BigQuery {table_id}"
        self.table_sql = BIGQUERY.quote(table_id)

    def columns(self) -> list[str]:
        from google.api_core.exceptions import NotFound

        try:
            return [field.name for field in self.client.get_table(self.table_id).schema]
        except NotFound:
            return []

    def query(self, sql: str) -> list[Sequence[Any]]:
        return [tuple(row.values()) for row in self.client.query(sql).result()]


@contextmanager
def open_target(target: str) -> Iterator[Target]:
    """`local` = PostgreSQL, `online` = BigQuery (same meaning as `pfas_import load --target`)."""
    if target == "online":
        # Imported here, so the local path works without the BigQuery client being configured.
        from google.cloud import bigquery

        client = bigquery.Client(project=settings.gcp_project_id, location=settings.bigquery_location)
        try:
            yield BigQueryTarget(
                client, f"{settings.gcp_project_id}.{settings.bigquery_dataset}.{FEATURE_TABLE}"
            )
        finally:
            client.close()
        return

    with get_postgres_connection() as conn:
        yield PostgresTarget(conn)
