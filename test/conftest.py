import pytest

from pfas_import import config, storage
from pfas_import.storage import get_s3_fs


@pytest.fixture
def s3_env(monkeypatch):
    """A complete, fake set of S3 env vars so Settings() never touches the real .env.

    Also patches the `storage.s` settings singleton in place: it's bound at
    import time (before this fixture's env vars exist), so code under test
    that reads `storage.s` would otherwise still see the real .env values.
    """
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:8333")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_ACCESS_KEY", "test-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_RAW_BUCKET", "raw")
    test_settings = config.Settings()
    monkeypatch.setattr(config, "settings", test_settings)
    monkeypatch.setattr(storage, "s", test_settings)
    return test_settings


@pytest.fixture(scope="session")
def live_s3_fs():
    """A filesystem pointed at the local docker-compose SeaweedFS, using real
    credentials from .env.

    Skips the test instead of failing when SeaweedFS isn't reachable, since this
    fixture talks to real local infra rather than a mock.
    """
    fs = get_s3_fs()
    try:
        fs.ls("/")
    except Exception as e:  # noqa: BLE001 - any failure here means "not reachable"
        pytest.skip(f"SeaweedFS not reachable: {e}")
    return fs
