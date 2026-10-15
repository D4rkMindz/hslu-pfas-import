import uuid

from pfas_import import storage

class TestEnsureBucketAgainstLiveSeaweedFs:
    """Fixture-based test against the real local SeaweedFS (see docker-compose.yaml).

    Skips automatically if SeaweedFS isn't running.
    Run this test if you want to check your docker setup (instead of running exploration/test-data.ipynb)
    """

    def test_raw_bucket_is_created(self, live_s3_fs):
        bucket = f"test-{uuid.uuid4().hex[:8]}"
        assert not live_s3_fs.exists(bucket)

        try:
            storage.ensure_bucket(live_s3_fs, bucket)

            assert live_s3_fs.exists(bucket)
        finally:
            if live_s3_fs.exists(bucket):
                live_s3_fs.rmdir(bucket)

    def test_is_idempotent(self, live_s3_fs):
        bucket = f"test-{uuid.uuid4().hex[:8]}"

        try:
            storage.ensure_bucket(live_s3_fs, bucket)
            storage.ensure_bucket(live_s3_fs, bucket)  # must not raise

            assert live_s3_fs.exists(bucket)
        finally:
            if live_s3_fs.exists(bucket):
                live_s3_fs.rmdir(bucket)