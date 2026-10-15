import pytest

from pfas_import.config import Settings

@pytest.fixture
def s3_env(monkeypatch):
    """A complete, fake set of S3 env vars so Settings() never touches the real .env."""
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:8333")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_ACCESS_KEY", "test-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_RAW_BUCKET", "raw")
    return Settings()
