"""BigQuery store (TARGET=cloud): GCP_PROJECT_ID.BIGQUERY_DATASET.<table>, for the features and the
ingestion state (`repository/ingestion_state.py`). The dataset belongs to Terraform (infra/), the tables
to the code.

Authenticates via Application Default Credentials: `gcloud auth application-default login` locally, or
GOOGLE_APPLICATION_CREDENTIALS pointing at a service account key file.
"""
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from pfas_import.domain.sql_dialect import BIGQUERY
from pfas_import.storage.base import FeatureStore

logger = logging.getLogger(__name__)


class BigQueryStore(FeatureStore):
    dialect = BIGQUERY

    def __init__(self, client: bigquery.Client, project: str, dataset: str):
        self.client = client
        self.project = project
        self.dataset = dataset

    def close(self) -> None:
        self.client.close()

    def table_id(self, table: str) -> str:
        return f"{self.project}.{self.dataset}.{table}"

    def describe(self, table: str) -> str:
        return f"BigQuery {self.table_id(table)}"

    def table_sql(self, table: str) -> str:
        return BIGQUERY.quote(self.table_id(table))

    def columns(self, table: str) -> list[str]:
        try:
            return [field.name for field in self.client.get_table(self.table_id(table)).schema]
        except NotFound:
            return []

    def query(self, sql: str, params: Mapping[str, str] | None = None) -> list[Sequence[Any]]:
        """`params` are STRING query parameters, referenced as @name in `sql`."""
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, "STRING", value) for name, value in (params or {}).items()
            ]
        )
        return [tuple(row.values()) for row in self.client.query(sql, job_config=job_config).result()]

    def replace(self, table: str, df: pd.DataFrame) -> None:
        """Replace the table with `df` in one load job.

        WRITE_TRUNCATE only takes effect once the job succeeds, so a failed load leaves the previous
        table intact - the BigQuery equivalent of the atomic rebuild in `storage/postgres.py`.
        """
        destination = self.table_id(table)
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        )

        logger.info("Starting BigQuery load: %s rows -> %s", len(df), destination)
        started_at = time.monotonic()

        self.client.load_table_from_dataframe(df, destination, job_config=job_config).result()

        logger.info("BigQuery load completed: %s rows in %.1f s", len(df), time.monotonic() - started_at)
