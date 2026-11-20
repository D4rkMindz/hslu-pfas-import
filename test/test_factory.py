"""Backend selection of `factory.object_store()` (OBJECT_STORE = auto | s3 | local).

Settings come from explicit values (see the `install_settings` fixture), because the repo's `.env`
sets FSSPEC_* variables and the environment alone cannot express "not set".
"""
import pytest
from fsspec.implementations.local import LocalFileSystem
from s3fs import S3FileSystem

from pfas_import.repository import factory

ENDPOINT = "http://localhost:8333"
KEY = "access-key"


@pytest.fixture(autouse=True)
def fresh_object_store(monkeypatch):
    """Clear the cached store around each test and keep the shell's FSSPEC_* variables out."""
    for name in ("FSSPEC_S3_ENDPOINT_URL", "FSSPEC_S3_KEY", "OBJECT_STORE", "LOCAL_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    factory.object_store.cache_clear()
    yield
    factory.object_store.cache_clear()


def build(install_settings, tmp_path, backend, endpoint, key):
    install_settings(
        OBJECT_STORE=backend,
        LOCAL_DATA_DIR=str(tmp_path),
        FSSPEC_S3_ENDPOINT_URL=endpoint,
        FSSPEC_S3_KEY=key,
    )
    return factory.object_store()


def assert_local(store, tmp_path):
    assert isinstance(store.fs, LocalFileSystem)
    assert not isinstance(store.fs, S3FileSystem)
    assert store.root == str(tmp_path)


class TestAuto:
    @pytest.mark.parametrize(
        ("endpoint", "key"),
        [
            (ENDPOINT, None),
            (None, KEY),
            (ENDPOINT, KEY),
        ],
    )
    def test_s3_when_endpoint_or_key_is_set(self, install_settings, tmp_path, endpoint, key):
        store = build(install_settings, tmp_path, "auto", endpoint, key)

        assert isinstance(store.fs, S3FileSystem)

    def test_local_when_neither_is_set(self, install_settings, tmp_path):
        store = build(install_settings, tmp_path, "auto", None, None)

        assert_local(store, tmp_path)


class TestExplicit:
    def test_s3_without_endpoint_or_key(self, install_settings, tmp_path):
        """AWS S3 with an IAM role sets neither variable."""
        store = build(install_settings, tmp_path, "s3", None, None)

        assert isinstance(store.fs, S3FileSystem)

    def test_local_even_with_endpoint_and_key(self, install_settings, tmp_path):
        store = build(install_settings, tmp_path, "local", ENDPOINT, KEY)

        assert_local(store, tmp_path)
