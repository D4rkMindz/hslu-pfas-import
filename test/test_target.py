"""TARGET=local|cloud: required settings per target, the backends `repository/factory.py` picks, and the
BigQuery ingestion state. No infrastructure: the cloud backends are only constructed, never called."""
import pytest
from pydantic import ValidationError
from s3fs import S3FileSystem

from pfas_import.config import GCS_S3_ENDPOINT, Settings
from pfas_import.repository import factory
from pfas_import.repository.ingestion_state import STATE_TABLE, BigQueryIngestionState

CLOUD = {"TARGET": "cloud", "GCP_PROJECT_ID": "my-project", "GCS_HMAC_ACCESS_ID": "id", "GCS_HMAC_SECRET": "s"}


@pytest.fixture(autouse=True)
def fresh_object_store(monkeypatch):
    # Settings also reads the process environment (CI sets S3_*/POSTGRES_* job-wide), so clear every variable
    # it knows: the tests here express "not set" by leaving a value out.
    for name, field in Settings.model_fields.items():
        monkeypatch.delenv(field.alias or name.upper(), raising=False)
    factory.object_store.cache_clear()
    yield
    factory.object_store.cache_clear()


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


class TestSettings:
    def test_local_is_the_default(self, install_settings):
        assert install_settings().target == "local"

    def test_local_needs_buckets_and_postgres(self):
        with pytest.raises(ValidationError, match="TARGET=local needs S3_RAW_BUCKET, S3_IMPORT_BUCKET, POSTGRES_HOST"):
            settings()

    def test_cloud_needs_project_and_hmac_key_but_no_postgres(self):
        with pytest.raises(ValidationError, match="TARGET=cloud needs GCP_PROJECT_ID, GCS_HMAC_ACCESS_ID"):
            settings(TARGET="cloud")

        assert settings(**CLOUD).postgres_host is None

    def test_empty_value_counts_as_not_set(self):
        """The Kestra flow passes the other target's settings as empty values."""
        with pytest.raises(ValidationError, match="GCP_PROJECT_ID"):
            settings(**{**CLOUD, "GCP_PROJECT_ID": ""})

    def test_empty_value_means_the_default(self, install_settings):
        """Locally, the flow passes BIGQUERY_* empty."""
        local = install_settings(BIGQUERY_DATASET="", BIGQUERY_LOCATION="", POSTGRES_PORT="")

        assert (local.bigquery_dataset, local.bigquery_location, local.postgres_port) == (
            "pfas_import", "europe-west6", "5432"
        )

    def test_rejects_other_targets(self):
        with pytest.raises(ValidationError):
            settings(**{**CLOUD, "TARGET": "online"})

    def test_local_buckets_come_from_env(self, install_settings):
        assert install_settings().buckets == ("raw", "import")

    def test_cloud_buckets_are_derived_from_the_project(self):
        """The same names as in infra/storage.tf."""
        assert settings(**CLOUD).buckets == ("my-project-raw", "my-project-import")


class TestFactory:
    def test_cloud_uses_gcs_through_the_s3_api(self, install_settings):
        install_settings(**CLOUD)

        store = factory.object_store()

        assert isinstance(store.fs, S3FileSystem)
        assert store.fs.endpoint_url == GCS_S3_ENDPOINT
        assert store.fs.key == "id"
        assert factory.dataset_files().raw_bucket == "my-project-raw"

    def test_cloud_ignores_object_store(self, install_settings, tmp_path):
        install_settings(**CLOUD, OBJECT_STORE="local", LOCAL_DATA_DIR=str(tmp_path))

        assert isinstance(factory.object_store().fs, S3FileSystem)

    def test_cloud_does_not_create_buckets(self, install_settings, monkeypatch):
        """Terraform owns the cloud buckets; the service account may not create any."""
        install_settings(**CLOUD)
        monkeypatch.setattr(factory, "dataset_files", lambda: pytest.fail("must not touch the buckets"))

        assert factory.ensure_buckets() is True


class FakeBigQuery:
    """Records the queries of `BigQueryIngestionState`; `columns` says whether the table exists."""

    def __init__(self, rows=(), exists=True):
        self.rows = list(rows)
        self.exists = exists
        self.queries: list[tuple[str, dict | None]] = []

    def describe(self, table):
        return f"BigQuery p.d.{table}"

    def table_sql(self, table):
        return f"`p.d.{table}`"

    def columns(self, table):
        return ["source_sha256"] if self.exists else []

    def query(self, sql, params=None):
        self.queries.append((sql, params))
        return self.rows


class TestBigQueryIngestionState:
    def test_no_table_means_no_previous_ingestion(self):
        store = FakeBigQuery(exists=False)

        assert BigQueryIngestionState(store).previous_sha256() is None  # pyright: ignore[reportArgumentType]
        assert store.queries == []

    def test_reads_the_previous_hash(self):
        store = FakeBigQuery(rows=[("abc",)])

        assert BigQueryIngestionState(store).previous_sha256() == "abc"  # pyright: ignore[reportArgumentType]

    def test_empty_table_means_no_previous_ingestion(self):
        assert BigQueryIngestionState(FakeBigQuery()).previous_sha256() is None  # pyright: ignore[reportArgumentType]

    def test_save_replaces_the_table_with_parameters(self):
        store = FakeBigQuery()

        BigQueryIngestionState(store).save("abc", "last_import.parquet")  # pyright: ignore[reportArgumentType]

        (sql, params), = store.queries
        assert f"CREATE OR REPLACE TABLE `p.d.{STATE_TABLE}`" in sql
        assert params == {"sha256": "abc", "source_key": "last_import.parquet"}
