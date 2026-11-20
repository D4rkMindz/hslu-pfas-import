"""`ObjectStore` and `DatasetFiles` against the real local fsspec backend in a temporary folder."""
import hashlib

import pandas as pd
import pytest
from fsspec.implementations.local import LocalFileSystem
from pandas.testing import assert_frame_equal

from pfas_import.repository.files import DatasetFiles
from pfas_import.storage.object_store import ObjectStore


@pytest.fixture
def store(tmp_path) -> ObjectStore:
    return ObjectStore(LocalFileSystem(auto_mkdir=True), root=str(tmp_path))


@pytest.fixture
def files(store) -> DatasetFiles:
    return DatasetFiles(store, "raw", "import")


def small_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["a", "b", "c"],
        "pfas_sum": [0.0, 1.5, 12.25],
        "count": [1, 2, 3],
    })


class TestObjectStore:
    def test_ensure_bucket_creates_the_folder(self, store, tmp_path):
        assert store.ensure_bucket("raw") is True
        assert (tmp_path / "raw").is_dir()

    def test_ensure_bucket_is_idempotent(self, store, tmp_path):
        store.ensure_bucket("raw")

        assert store.ensure_bucket("raw") is True
        assert (tmp_path / "raw").is_dir()

    def test_parquet_round_trip(self, store):
        store.ensure_bucket("import")
        store.write_parquet("import", "features.parquet", small_frame())

        assert_frame_equal(store.read_parquet("import", "features.parquet"), small_frame())

    def test_sha256_matches_the_bytes_on_disk(self, store, tmp_path):
        store.ensure_bucket("import")
        store.write_parquet("import", "features.parquet", small_frame())

        on_disk = (tmp_path / "import" / "features.parquet").read_bytes()

        assert store.sha256("import", "features.parquet") == hashlib.sha256(on_disk).hexdigest()

    def test_exists_and_remove(self, store):
        store.ensure_bucket("raw")
        assert store.exists("raw", "file.bin") is False

        with store.open("raw", "file.bin", "wb") as destination:
            destination.write(b"data")
        assert store.exists("raw", "file.bin") is True

        store.remove("raw", "file.bin")
        assert store.exists("raw", "file.bin") is False

    def test_path_joins_root_bucket_and_key(self, store, tmp_path):
        assert store.path("raw", "file.parquet") == f"{tmp_path}/raw/file.parquet"

    def test_url_ends_with_the_path(self, store, tmp_path):
        assert store.url("raw", "file.parquet").endswith(f"{tmp_path}/raw/file.parquet")


class TestDatasetFiles:
    def test_ensure_buckets_creates_both(self, files, tmp_path):
        assert files.ensure_buckets() is True
        assert (tmp_path / "raw").is_dir()
        assert (tmp_path / "import").is_dir()

    def test_import_file_lands_in_the_import_bucket(self, files, tmp_path):
        files.ensure_buckets()
        files.write_import("last_import.parquet", small_frame())

        assert (tmp_path / "import" / "last_import.parquet").is_file()
        assert not (tmp_path / "raw" / "last_import.parquet").exists()
        assert_frame_equal(files.read_import("last_import.parquet"), small_frame())

    def test_raw_methods_use_the_raw_bucket(self, files, tmp_path):
        files.ensure_buckets()
        payload = b"raw download bytes"

        with files.open_raw_for_writing("last_download.parquet") as destination:
            destination.write(payload)

        assert (tmp_path / "raw" / "last_download.parquet").read_bytes() == payload
        assert not (tmp_path / "import" / "last_download.parquet").exists()
        assert files.raw_exists("last_download.parquet") is True
        assert files.raw_sha256("last_download.parquet") == hashlib.sha256(payload).hexdigest()

        files.remove_raw("last_download.parquet")

        assert files.raw_exists("last_download.parquet") is False
        assert not (tmp_path / "raw" / "last_download.parquet").exists()
