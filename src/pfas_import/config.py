"""  Handles all static global variables (loaded vom .env and project-interns)
config.py = systemic Defaults + persistend values
.env = Enduser-overridable Settings + dynamische Runtime-Results

Only `repository/factory.py` imports this module (enforced by test/test_architecture.py). Settings are read
on first use, so importing a module never needs a complete .env.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# GCS's S3-compatible (XML) API, used with an HMAC key (TARGET=cloud)
GCS_S3_ENDPOINT = "https://storage.googleapis.com"

Target = Literal["local", "cloud"]


class Settings(BaseSettings):
    """ All settings that can be defined in .env or have a default value. """
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        extra="ignore", # otherwise we maintain a lot of boilerplate code
    )

    # LOGGING
    log_level: Literal["ERROR", "WARNING", "INFO", "DEBUG"] = 'INFO'
    log_format: str = "%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s"

    # PFAS hub
    dataset_url: str = "https://pdh.cnrs.fr/download/full.parquet"

    # Where the whole pipeline runs against:
    # "local": SeaweedFS (S3) buckets S3_RAW_BUCKET/S3_IMPORT_BUCKET + PostgreSQL (features and ingestion state)
    # "cloud": GCS buckets <GCP_PROJECT_ID>-raw/-import + BigQuery (features and ingestion state)
    target: Target = Field(default="local", alias="TARGET")

    # Object storage for TARGET=local: "s3" (SeaweedFS) or "local" (a folder, LOCAL_DATA_DIR, for development and
    # tests). "auto" uses S3 if s3fs gets an endpoint or a key (FSSPEC_S3_*), else the local folder.
    object_store: Literal["auto", "s3", "local"] = Field(default="auto", alias="OBJECT_STORE")
    local_data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="LOCAL_DATA_DIR")
    fsspec_s3_endpoint_url: str | None = Field(default=None, alias="FSSPEC_S3_ENDPOINT_URL")
    fsspec_s3_key: str | None = Field(default=None, alias="FSSPEC_S3_KEY")

    # S3 buckets for TARGET=local (folder names for OBJECT_STORE=local)
    bucket_raw: str | None = Field(default=None, alias="S3_RAW_BUCKET")
    bucket_import: str | None = Field(default=None, alias="S3_IMPORT_BUCKET")

    # PostgreSQL (TARGET=local)
    postgres_host: str | None = Field(default=None, alias="POSTGRES_HOST")
    postgres_port: str = Field(default="5432", alias="POSTGRES_PORT")
    postgres_db: str | None = Field(default=None, alias="POSTGRES_DB")
    postgres_user: str | None = Field(default=None, alias="POSTGRES_USER")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")

    # Google Cloud (TARGET=cloud). Terraform (`make tf-*`) reads the same values from .env; `make setup` writes
    # GCP_PROJECT_ID (if it creates the project) and the HMAC key.
    gcp_project_id: str | None = Field(default=None, alias="GCP_PROJECT_ID")
    bigquery_dataset: str = Field(default="pfas_import", alias="BIGQUERY_DATASET")
    bigquery_location: str = Field(default="europe-west6", alias="BIGQUERY_LOCATION")
    gcs_hmac_access_id: str | None = Field(default=None, alias="GCS_HMAC_ACCESS_ID")
    gcs_hmac_secret: str | None = Field(default=None, alias="GCS_HMAC_SECRET")

    @field_validator("*", mode="before")
    @classmethod
    def empty_is_unset(cls, value: object) -> object:
        """`VAR=` in .env and the empty values the Kestra flow passes for the other TARGET mean "not set": the
        field's default."""
        if value == "":
            raise PydanticUseDefault()
        return value

    @model_validator(mode="after")
    def required_for_target(self) -> "Settings":
        required = {
            "local": {
                "S3_RAW_BUCKET": self.bucket_raw,
                "S3_IMPORT_BUCKET": self.bucket_import,
                "POSTGRES_HOST": self.postgres_host,
                "POSTGRES_DB": self.postgres_db,
                "POSTGRES_USER": self.postgres_user,
                "POSTGRES_PASSWORD": self.postgres_password,
            },
            "cloud": {
                "GCP_PROJECT_ID": self.gcp_project_id,
                "GCS_HMAC_ACCESS_ID": self.gcs_hmac_access_id,
                "GCS_HMAC_SECRET": self.gcs_hmac_secret,
            },
        }[self.target]
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"TARGET={self.target} needs {', '.join(missing)} (see .env.example)")
        return self

    @property
    def buckets(self) -> tuple[str, str]:
        """(raw, import). In the cloud, Terraform creates them with the same names (infra/storage.tf)."""
        if self.target == "cloud":
            return f"{self.gcp_project_id}-raw", f"{self.gcp_project_id}-import"
        return self.bucket_raw, self.bucket_import  # pyright: ignore[reportReturnType]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Crashes on missing required settings, at first use instead of at import."""
    return Settings()  # pyright: ignore[reportCallIssue]
