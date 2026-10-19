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
    LOG_LEVEL: Literal["ERROR", "WARNING", "INFO", "DEBUG"] = 'INFO'
    LOG_FORMAT: str = "%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s"

    # PFAS hub
    DATASET_URL: str = "https://pdh.cnrs.fr/download/full.parquet"

    # S3-Hub
    s3_endpoint_url: str = Field(alias="S3_ENDPOINT_URL")
    s3_region: str = Field(alias="S3_REGION")
    s3_access_key: str = Field(alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(alias="S3_SECRET_KEY")
    s3_raw_bucket: str = Field(alias="S3_RAW_BUCKET")

    # PostGres
    postgres_host: str= Field(alias="POSTGRES_HOST")
    postgres_port: str= "5432"
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")
    bucket_raw: str = Field(alias="S3_RAW_BUCKET")
    bucket_import: str = Field(alias="S3_IMPORT_BUCKET")

# crash on error
settings = Settings() # pyright: ignore[reportCallIssue]


def setup_logging():
    """ Configures the logger once at the beginning """
    logging.basicConfig(format=settings.LOG_FORMAT,
                        level=settings.LOG_LEVEL)
