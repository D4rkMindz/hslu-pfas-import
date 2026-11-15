"""Publishes the feature table to BigQuery: GCP_PROJECT_ID.BIGQUERY_DATASET.pfas_features
(LOAD_TARGET=online). The dataset belongs to Terraform (infra/), the table to this module.
"""
import logging
import time

import pandas as pd
from google.cloud import bigquery

from pfas_import.config import settings
from pfas_import.ingestion.constants import FEATURE_TABLE

logger = logging.getLogger(__name__)


def get_bigquery_client() -> bigquery.Client:
    # Authenticates via Application Default Credentials: `gcloud auth application-default login`
    # locally, or GOOGLE_APPLICATION_CREDENTIALS pointing at a service account key file.
    return bigquery.Client(project=settings.gcp_project_id, location=settings.bigquery_location)


def table_id(table: str) -> str:
    return f"{settings.gcp_project_id}.{settings.bigquery_dataset}.{table}"


def publish_feature_dataframe(df: pd.DataFrame, client: bigquery.Client | None = None) -> None:
    """Replace the BigQuery feature table with `df` in one load job.

    WRITE_TRUNCATE only takes effect once the job succeeds, so a failed load leaves the previous
    table intact - the BigQuery equivalent of the atomic rebuild `load.publish_feature_dataframe`
    does for PostgreSQL.
    """
    if df.empty:
        raise ValueError("Feature DataFrame is empty; refusing to replace current data.")

    client = client or get_bigquery_client()
    destination = table_id(FEATURE_TABLE)
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        autodetect=True,
    )

    logger.info("Starting BigQuery load: %s rows -> %s", len(df), destination)
    started_at = time.monotonic()

    client.load_table_from_dataframe(df, destination, job_config=job_config).result()

    logger.info("BigQuery load completed: %s rows in %.1f s", len(df), time.monotonic() - started_at)
