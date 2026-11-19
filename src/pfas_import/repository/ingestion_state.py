"""The ingestion state: the hash and key of the last published dataset. One row in PostgreSQL,
for every LOAD_TARGET (the BigQuery path does not write it, see `pipeline/load.py`)."""
import logging
from contextlib import AbstractContextManager

from psycopg import sql

from pfas_import.storage.postgres import PostgresStore

logger = logging.getLogger(__name__)

STATE_TABLE: str = "pfas_ingestion_state"


class IngestionStateRepository:
    def __init__(self, store: PostgresStore, table: str = STATE_TABLE):
        self.store = store
        self.table = sql.Identifier(store.schema, table)

    def transaction(self) -> AbstractContextManager[None]:
        """The store's transaction; shared with a FeatureRepository on the same store (factory.publication)."""
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
        """The hash of the last successfully ingested dataset."""
        logger.info("Reading hash of the last successful ingestion.")

        with self.store.transaction():
            self._ensure_table()
            row = self.store.execute(
                sql.SQL("SELECT source_sha256 FROM {} WHERE singleton_id = 1").format(self.table)
            ).fetchone()

        previous = row[0].strip() if row and row[0] else None

        if previous:
            logger.info("Previous successful SHA-256: %s", previous)
        else:
            logger.info("No previous successful ingestion found.")

        return previous

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
