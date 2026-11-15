"""  Handles all static global variables (loaded vom .env and project-interns)
config.py = systemic Defaults + persistend values
.env = Enduser-overridable Settings + dynamische Runtime-Results
"""
import logging
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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

    # S3 storage configs
    bucket_raw: str = Field(alias="S3_RAW_BUCKET")
    bucket_import: str = Field(alias="S3_IMPORT_BUCKET")

    # PostGres
    postgres_host: str= Field(alias="POSTGRES_HOST")
    postgres_port: str= "5432"
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")

    # Where `pfas_import load` publishes the features: "local" (PostgreSQL) or "online" (BigQuery)
    load_target: Literal["local", "online"] = Field(default="local", alias="LOAD_TARGET")

    # BigQuery (LOAD_TARGET=online). Terraform (`make tf-*`) reads the same values from .env.
    gcp_project_id: str = Field(default="pfas-trace", alias="GCP_PROJECT_ID")
    bigquery_dataset: str = Field(default="pfas_import", alias="BIGQUERY_DATASET")
    bigquery_location: str = Field(default="europe-west6", alias="BIGQUERY_LOCATION")

# crash on error
settings = Settings() # pyright: ignore[reportCallIssue]


def setup_logging():
    """ Configures the logger once at the beginning """
    logging.basicConfig(format=settings.log_format,
                        level=settings.log_level)
