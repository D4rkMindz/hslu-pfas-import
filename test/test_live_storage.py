import uuid

from pfas_import.repository import factory
from pfas_import.storage.object_store import ObjectStore


class TestEnsureBucketAgainstLiveSeaweedFs:
    """Fixture-based test against the real local SeaweedFS (see docker-compose.yaml).

    Skips automatically if SeaweedFS isn't running.
    Run this test if you want to check your docker setup (instead of running exploration/test-data.ipynb)
    """

    def test_raw_bucket_is_created(self, live_s3_fs):
        assert live_s3_fs.exists(factory.dataset_files().raw_bucket)

    def test_import_bucket_is_created(self, live_s3_fs):
        assert live_s3_fs.exists(factory.dataset_files().import_bucket)

    def test_bucket_is_created(self, live_s3_fs):
        bucket = f"test-{uuid.uuid4().hex[:8]}"
        assert not live_s3_fs.exists(bucket)

        try:
            ObjectStore(live_s3_fs).ensure_bucket(bucket)

            assert live_s3_fs.exists(bucket)
        finally:
            if live_s3_fs.exists(bucket):
                live_s3_fs.rmdir(bucket)

    def test_is_idempotent(self, live_s3_fs):
        bucket = f"test-{uuid.uuid4().hex[:8]}"

        try:
            store = ObjectStore(live_s3_fs)
            store.ensure_bucket(bucket)
            store.ensure_bucket(bucket)  # must not raise

            assert live_s3_fs.exists(bucket)
        finally:
            if live_s3_fs.exists(bucket):
                live_s3_fs.rmdir(bucket)
