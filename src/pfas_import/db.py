import logging
import time

import psycopg
from psycopg import sql

from pfas_import.config import settings
from pfas_import.ingestion.constants import STATE_TABLE

logger = logging.getLogger(__name__)


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


def ensure_state_table(conn: psycopg.Connection) -> None:
    """Create ingestion metadata if it does not exist."""

    conn.execute(
        sql.SQL("""
                CREATE TABLE IF NOT EXISTS public.{}
                (
                    singleton_id
                    SMALLINT
                    PRIMARY
                    KEY
                    CHECK
                (
                    singleton_id =
                    1
                ),
                    source_sha256 CHAR
                (
                    64
                ),
                    source_s3_key TEXT,
                    last_ingested_at TIMESTAMPTZ
                    )
                """).format(sql.Identifier(STATE_TABLE))
    )