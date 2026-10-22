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
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("S3_RAW_BUCKET", raising=False)
        monkeypatch.delenv("S3_IMPORT_BUCKET", raising=False)

        assert storage.raw_bucket() == "raw"
        assert storage.import_bucket() == "import"

    def test_respects_env_override(self, monkeypatch):
        monkeypatch.setenv("S3_RAW_BUCKET", "custom-bucket")
        monkeypatch.setenv("S3_IMPORT_BUCKET", "custom-bucket-2")

        assert storage.raw_bucket() == "custom-bucket"
        assert storage.import_bucket() == "custom-bucket-2"


class TestGetS3Fs:
    def test_builds_filesystem_from_settings(self, monkeypatch, s3_env):
        mock_s3_filesystem = MagicMock()

        monkeypatch.setenv("S3_RAW_BUCKET", "custom-bucket")
        monkeypatch.setenv("S3_IMPORT_BUCKET", "custom-bucket-2")
        monkeypatch.setattr(storage, "S3FileSystem", mock_s3_filesystem)

        storage.setup()

        mock_s3_filesystem.assert_any_call()
