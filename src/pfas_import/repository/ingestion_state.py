"""The ingestion state: the hash and key of the last published dataset, one row in the store TARGET selects.
PostgreSQL for TARGET=local, BigQuery for TARGET=cloud (`repository/factory.py` picks it)."""
import logging
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Protocol

from psycopg import sql

from pfas_import.storage.postgres import PostgresStore

if TYPE_CHECKING:
    from pfas_import.storage.bigquery import BigQueryStore

logger = logging.getLogger(__name__)

STATE_TABLE: str = "pfas_ingestion_state"


class IngestionStateRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        """The store's transaction, shared with a FeatureRepository on the same store (factory.publication)."""
        ...

    def previous_sha256(self) -> str | None:
        """The hash of the last successfully ingested dataset."""
        ...

    def save(self, sha256: str, source_key: str) -> None: ...


def _log_previous(previous: str | None) -> str | None:
    if previous:
        logger.info("Previous successful SHA-256: %s", previous)
    else:
        logger.info("No previous successful ingestion found.")
    return previous


class PostgresIngestionState:
    def __init__(self, store: PostgresStore, table: str = STATE_TABLE):
        self.store = store
        self.table = sql.Identifier(store.schema, table)

    def transaction(self) -> AbstractContextManager[None]:
        return self.store.transaction()

    def _ensure_table(self) -> None:
        self.store.execute(
            sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    singleton_id SMALLINT PRIMARY KEY
                        CHECK (singleton_id = 1),
                    source_sha256 CHAR(64) NOT NULL,
                    source_s3_key TEXT NOT NULL,
                    last_ingested_at TIMESTAMPTZ NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                )
            """).format(self.table)
        )

    def previous_sha256(self) -> str | None:
        logger.info("Reading hash of the last successful ingestion from PostgreSQL.")

        with self.store.transaction():
            self._ensure_table()
            row = self.store.execute(
                sql.SQL("SELECT source_sha256 FROM {} WHERE singleton_id = 1").format(self.table)
            ).fetchone()

        return _log_previous(row[0].strip() if row and row[0] else None)

    def save(self, sha256: str, source_key: str) -> None:
        logger.info("Updating ingestion state")

        with self.store.transaction():
            self._ensure_table()
            self.store.execute(
                sql.SQL("""
                    INSERT INTO {} (singleton_id, source_sha256, source_s3_key, last_ingested_at)
                    VALUES (1, %s, %s, NOW())
                    ON CONFLICT (singleton_id)
                    DO UPDATE SET
                        source_sha256 = EXCLUDED.source_sha256,
                        source_s3_key = EXCLUDED.source_s3_key,
                        last_ingested_at = EXCLUDED.last_ingested_at
                """).format(self.table),
                (sha256, source_key),
            )


class BigQueryIngestionState:
    """Same columns as in PostgreSQL. The table is created on the first `save`.

    BigQuery has no transaction spanning the feature table and this one: `load` replaces the features first
    and saves the state after. If saving fails, the next run sees the old hash and publishes again, which
    is safe because the publication replaces the whole table.
    """

    def __init__(self, store: "BigQueryStore", table: str = STATE_TABLE):
        self.store = store
        self.table = table

    def transaction(self) -> AbstractContextManager[None]:
        return nullcontext()

    def previous_sha256(self) -> str | None:
        logger.info("Reading hash of the last successful ingestion from %s.", self.store.describe(self.table))

        if not self.store.columns(self.table):
            return _log_previous(None)

        rows = self.store.query(f"SELECT source_sha256 FROM {self.store.table_sql(self.table)} LIMIT 1")
        return _log_previous(rows[0][0] if rows and rows[0][0] else None)

    def save(self, sha256: str, source_key: str) -> None:
        logger.info("Updating ingestion state")

        # One statement: the table holds the new row or, if the job fails, still the previous one.
        self.store.query(
            f"""
            CREATE OR REPLACE TABLE {self.store.table_sql(self.table)} AS
            SELECT @sha256 AS source_sha256, @source_key AS source_s3_key,
                   CURRENT_TIMESTAMP() AS last_ingested_at
            """,
            {"sha256": sha256, "source_key": source_key},
        )
