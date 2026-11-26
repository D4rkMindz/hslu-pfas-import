"""Reads the configuration and builds the adapters and repositories. The only module that imports
`config` (enforced by test/test_architecture.py); everything else receives its values from here."""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import TYPE_CHECKING

from fsspec.implementations.local import LocalFileSystem

from pfas_import import logging_setup
from pfas_import.config import GCS_S3_ENDPOINT, Target, get_settings
from pfas_import.repository.features import FACILITY_TOKENS_TABLE, FeatureRepository
from pfas_import.repository.files import DatasetFiles
from pfas_import.repository.ingestion_state import (
    BigQueryIngestionState,
    IngestionStateRepository,
    PostgresIngestionState,
)
from pfas_import.source.pfas_hub import PfasHubSource
from pfas_import.storage.object_store import ObjectStore
from pfas_import.storage.postgres import PostgresStore, connect

if TYPE_CHECKING:
    from pfas_import.storage.bigquery import BigQueryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Publication:
    """Where `load` publishes: the features, the facility tokens and the ingestion state, in the same store."""

    features: FeatureRepository
    facility_tokens: FeatureRepository
    state: IngestionStateRepository


@dataclass(frozen=True)
class PublishedTables:
    """What `verify` reads back: both published tables, in the same store."""

    features: FeatureRepository
    facility_tokens: FeatureRepository


def configure_logging(mode: logging_setup.OutputMode) -> None:
    settings = get_settings()
    logging_setup.configure(settings.log_level, settings.log_format, mode)


def default_dataset_url() -> str:
    return get_settings().dataset_url


def target() -> Target:
    return get_settings().target


@lru_cache(maxsize=1)
def object_store() -> ObjectStore:
    settings = get_settings()
    if settings.target == "cloud":
        from s3fs import S3FileSystem

        # GCS through its S3-compatible API with the HMAC key of the pipeline's service account. botocore
        # adds checksums GCS rejects unless they are restricted to the operations that need them.
        logger.info("Object store: GCS (S3 API)")
        return ObjectStore(
            S3FileSystem(
                endpoint_url=GCS_S3_ENDPOINT,
                key=settings.gcs_hmac_access_id,
                secret=settings.gcs_hmac_secret,
                client_kwargs={"region_name": "auto"},
                config_kwargs={
                    "request_checksum_calculation": "when_required",
                    "response_checksum_validation": "when_required",
                },
            )
        )

    backend = settings.object_store
    if backend == "auto":
        backend = "s3" if settings.fsspec_s3_endpoint_url or settings.fsspec_s3_key else "local"

    if backend == "s3":
        from s3fs import S3FileSystem

        # Configured by fsspec from FSSPEC_S3_ENDPOINT_URL, FSSPEC_S3_KEY and FSSPEC_S3_SECRET. Without an
        # endpoint it is AWS S3; without a key, the boto credential chain (e.g. an IAM role).
        logger.info("Object store: S3")
        return ObjectStore(S3FileSystem())

    logger.info("Object store: local folder %s", settings.local_data_dir)
    return ObjectStore(LocalFileSystem(auto_mkdir=True), root=str(settings.local_data_dir))


def dataset_files() -> DatasetFiles:
    return DatasetFiles(object_store(), *get_settings().buckets)


def ensure_buckets() -> bool:
    """Creates the local buckets. The cloud buckets belong to Terraform (infra/storage.tf)."""
    if target() == "cloud":
        return True
    return dataset_files().ensure_buckets()


def pfas_hub(url: str) -> PfasHubSource:
    return PfasHubSource(url)


def _postgres() -> PostgresStore:
    settings = get_settings()
    return PostgresStore(
        partial(
            connect,
            host=settings.postgres_host,  # pyright: ignore[reportArgumentType] - validated for TARGET=local
            port=settings.postgres_port,
            dbname=settings.postgres_db,  # pyright: ignore[reportArgumentType]
            user=settings.postgres_user,  # pyright: ignore[reportArgumentType]
            password=settings.postgres_password,  # pyright: ignore[reportArgumentType]
        )
    )


@contextmanager
def _bigquery() -> Iterator["BigQueryStore"]:
    # Imported here, so the local path works without the BigQuery client being configured.
    from google.cloud import bigquery

    from pfas_import.storage.bigquery import BigQueryStore

    settings = get_settings()
    client = bigquery.Client(project=settings.gcp_project_id, location=settings.bigquery_location)
    store = BigQueryStore(client, settings.gcp_project_id, settings.bigquery_dataset)
    try:
        yield store
    finally:
        store.close()


@contextmanager
def ingestion_state() -> Iterator[IngestionStateRepository]:
    if target() == "cloud":
        with _bigquery() as store:
            yield BigQueryIngestionState(store)
        return

    with _postgres() as store:
        yield PostgresIngestionState(store)


@contextmanager
def publication() -> Iterator[Publication]:
    """Features and ingestion state share one store. `local` shares one PostgreSQL connection, so `load`
    writes both in one transaction."""
    if target() == "cloud":
        with _bigquery() as store:
            yield Publication(
                FeatureRepository(store), FeatureRepository(store, FACILITY_TOKENS_TABLE), BigQueryIngestionState(store)
            )
        return

    with _postgres() as store:
        yield Publication(
            FeatureRepository(store), FeatureRepository(store, FACILITY_TOKENS_TABLE), PostgresIngestionState(store)
        )


@contextmanager
def published_features() -> Iterator[FeatureRepository]:
    """`local` = PostgreSQL, `cloud` = BigQuery."""
    with published_tables() as tables:
        yield tables.features


@contextmanager
def published_tables() -> Iterator[PublishedTables]:
    """Both published tables on one store connection. `local` = PostgreSQL, `cloud` = BigQuery."""
    if target() == "cloud":
        with _bigquery() as store:
            yield PublishedTables(FeatureRepository(store), FeatureRepository(store, FACILITY_TOKENS_TABLE))
        return

    with _postgres() as store:
        yield PublishedTables(FeatureRepository(store), FeatureRepository(store, FACILITY_TOKENS_TABLE))
