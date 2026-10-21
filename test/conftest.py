import pytest
from s3fs import S3FileSystem

from pfas_import import config


@pytest.fixture
def s3_env(monkeypatch):
    """A complete, fake set of S3 env vars so Settings() never touches the real .env.

    Also patches the `storage.s` settings singleton in place: it's bound at
    import time (before this fixture's env vars exist), so code under test
    that reads `storage.s` would otherwise still see the real .env values.
    """
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:8333")
    monkeypatch.setenv("FSSPEC_S3_ENDPOINT_URL", "http://localhost:8333")
    monkeypatch.setenv("S3_ACCESS_KEY", "test-key")
    monkeypatch.setenv("FSSPEC_S3_KEY", "test-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("FSSPEC_S3_SECRET", "test-secret")
    monkeypatch.setenv("S3_RAW_BUCKET", "raw")
    monkeypatch.setenv("S3_IMPORT_BUCKET", "import")
    test_settings = config.Settings()
    monkeypatch.setattr(config, "settings", test_settings)
    return test_settings


@pytest.fixture(scope="session")
def live_s3_fs():
    """A filesystem pointed at the local docker-compose SeaweedFS, using real
    credentials from .env.

    .env's S3_ENDPOINT_URL points at the `seaweedfs` Docker service name, which
    only resolves for containers on the `development` network (e.g. Kestra task
    runs). Tests run on the host, so override it to the host-published port.

    Skips the test instead of failing when SeaweedFS isn't reachable, since this
    fixture talks to real local infra rather than a mock.
    """
    fs = S3FileSystem(endpoint_url="http://localhost:8333")
    try:
        fs.ls("/")
    except Exception as e:  # noqa: BLE001 - any failure here means "not reachable"
        pytest.skip(f"SeaweedFS not reachable: {e}")
    return fs
