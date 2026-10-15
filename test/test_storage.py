import uuid
from unittest.mock import MagicMock, call

from pfas_import import storage


class TestEnsureBucket:
    def test_creates_bucket_when_missing(self):
        fs = MagicMock()
        fs.exists.return_value = False

        storage.ensure_bucket(fs, "raw")

        fs.mkdir.assert_called_once_with("raw")

    def test_does_not_create_bucket_when_present(self):
        fs = MagicMock()
        fs.exists.return_value = True

        storage.ensure_bucket(fs, "raw")

        fs.mkdir.assert_not_called()

    def test_invalidates_cache_before_checking(self):
        fs = MagicMock()
        fs.exists.return_value = False

        storage.ensure_bucket(fs, "raw")

        assert fs.method_calls[0] == call.invalidate_cache()


class TestRawBucket:
    def test_defaults_to_raw(self, monkeypatch):
        monkeypatch.delenv("S3_RAW_BUCKET", raising=False)

        assert storage.raw_bucket() == "raw"

    def test_respects_env_override(self, monkeypatch):
        monkeypatch.setenv("S3_RAW_BUCKET", "custom-bucket")

        assert storage.raw_bucket() == "custom-bucket"


class TestGetS3Fs:
    def test_builds_filesystem_from_settings(self, monkeypatch, s3_env):
        mock_s3_filesystem = MagicMock()
        monkeypatch.setattr(storage.s3fs, "S3FileSystem", mock_s3_filesystem)

        storage.get_s3_fs()

        mock_s3_filesystem.assert_called_once_with(
            key="test-key",
            secret="test-secret",
            endpoint_url="http://localhost:8333",
            client_kwargs={"region_name": "us-east-1"},
        )
