from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        extra="ignore", # otherwise we maintain a lot of boilerplate code
    )

    bucket_raw: str = Field(alias="S3_RAW_BUCKET")
    bucket_import: str = Field(alias="S3_IMPORT_BUCKET")


# crash on error
settings = Settings()