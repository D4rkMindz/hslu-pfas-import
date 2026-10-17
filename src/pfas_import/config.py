import sys

from dotenv import find_dotenv, load_dotenv
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(find_dotenv())

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid",
    )

    s3_endpoint_url: str = Field(alias="S3_ENDPOINT_URL")
    s3_region: str = Field(alias="S3_REGION")
    s3_access_key: str = Field(alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(alias="S3_SECRET_KEY")
    s3_raw_bucket: str = Field(alias="S3_RAW_BUCKET")


# crash on error
settings = Settings()