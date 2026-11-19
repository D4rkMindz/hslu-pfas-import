"""Reads the configuration and builds the adapters and repositories. The only module that imports
`config` (enforced by test/test_architecture.py); everything else receives its values from here."""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import TYPE_CHECKING, Literal

from fsspec.implementations.local import LocalFileSystem

from pfas_import import logging_setup
from pfas_import.config import get_settings
from pfas_import.repository.features import FeatureRepository
from pfas_import.repository.files import DatasetFiles
from pfas_import.repository.ingestion_state import IngestionStateRepository
from pfas_import.source.pfas_hub import PfasHubSource
from pfas_import.storage.object_store import ObjectStore
from pfas_import.storage.postgres import PostgresStore, connect

if TYPE_CHECKING:
    from pfas_import.storage.bigquery import BigQueryStore

logger = logging.getLogger(__name__)

LoadTarget = Literal["local", "online"]


@dataclass(frozen=True)
class Publication:
    """Where `load` publishes. `state` is None for BigQuery, which does not record the ingestion state."""

    features: FeatureRepository
    state: IngestionStateRepository | None


def configure_logging(mode: logging_setup.OutputMode) -> None:
    settings = get_settings()
    logging_setup.configure(settings.log_level, settings.log_format, mode)


def default_dataset_url() -> str:
    return get_settings().dataset_url


def default_load_target() -> LoadTarget:
    return get_settings().load_target


@lru_cache(maxsize=1)
def object_store() -> ObjectStore:
    settings = get_settings()
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
    settings = get_settings()
    return DatasetFiles(object_store(), settings.bucket_raw, settings.bucket_import)


def ensure_buckets() -> bool:
    return dataset_files().ensure_buckets()


def pfas_hub(url: str) -> PfasHubSource:
    return PfasHubSource(url)


def _postgres() -> PostgresStore:
    settings = get_settings()
    return PostgresStore(
        partial(
            connect,
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
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
    with _postgres() as store:
        yield IngestionStateRepository(store)


@contextmanager
def publication(target: LoadTarget) -> Iterator[Publication]:
    """`local` shares one PostgreSQL connection between features and state, so `load` writes both in
    one transaction."""
    if target == "online":
        with _bigquery() as store:
            yield Publication(FeatureRepository(store), None)
        return

    with _postgres() as store:
        yield Publication(FeatureRepository(store), IngestionStateRepository(store))


@contextmanager
def published_features(target: LoadTarget) -> Iterator[FeatureRepository]:
    """`local` = PostgreSQL, `online` = BigQuery (same meaning as `pfas_import load --target`)."""
    if target == "online":
        with _bigquery() as store:
            yield FeatureRepository(store)
        return

    with _postgres() as store:
        yield FeatureRepository(store)
