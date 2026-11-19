import pytest
from s3fs import S3FileSystem


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


REQUIRED_SETTINGS: dict[str, str] = {
    "S3_RAW_BUCKET": "raw",
    "S3_IMPORT_BUCKET": "import",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_DB": "pfas",
    "POSTGRES_USER": "pfas",
    "POSTGRES_PASSWORD": "secret",
}


@pytest.fixture
def install_settings(monkeypatch):
    """Returns a function that makes `factory.get_settings` return settings built from explicit values.

    The settings ignore the repo's `.env` file, so a test can express "variable not set". Keys are the
    environment variable names (aliases); the required ones get harmless defaults.
    """
    from pfas_import.config import Settings
    from pfas_import.repository import factory

    def install(**values):
        settings = Settings(_env_file=None, **{**REQUIRED_SETTINGS, **values})  # pyright: ignore[reportCallIssue]
        monkeypatch.setattr(factory, "get_settings", lambda: settings)
        return settings

    return install
