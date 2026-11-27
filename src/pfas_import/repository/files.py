"""Where the Parquet files live: raw downloads in the raw bucket, feature files in the import bucket."""
from collections.abc import Callable
from typing import IO, Any

import pandas as pd

from pfas_import.storage.object_store import ObjectStore

RAW_SOURCE_KEY: str = "last_download.parquet"
IMPORT_SOURCE_KEY: str = "last_import.parquet"
# The facility tokens of the same transform run, published as their own table.
FACILITY_TOKENS_KEY: str = "last_facility_tokens.parquet"


class DatasetFiles:
    def __init__(self, store: ObjectStore, raw_bucket: str, import_bucket: str):
        self.store = store
        self.raw_bucket = raw_bucket
        self.import_bucket = import_bucket

    def ensure_buckets(self) -> bool:
        return self.store.ensure_bucket(self.raw_bucket) and self.store.ensure_bucket(self.import_bucket)

    def raw_url(self, key: str) -> str:
        return self.store.url(self.raw_bucket, key)

    def import_url(self, key: str) -> str:
        return self.store.url(self.import_bucket, key)

    def open_raw_for_writing(self, key: str) -> IO[Any]:
        return self.store.open(self.raw_bucket, key, "wb")

    def raw_exists(self, key: str) -> bool:
        return self.store.exists(self.raw_bucket, key)

    def remove_raw(self, key: str) -> None:
        self.store.remove(self.raw_bucket, key)

    def raw_sha256(self, key: str) -> str:
        return self.store.sha256(self.raw_bucket, key)

    def import_sha256(self, key: str) -> str:
        return self.store.sha256(self.import_bucket, key)

    def read_raw(
        self,
        key: str,
        columns: list[str] | None = None,
        batch_filter: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        return self.store.read_parquet(self.raw_bucket, key, columns, batch_filter)

    def read_import(self, key: str) -> pd.DataFrame:
        return self.store.read_parquet(self.import_bucket, key)

    def write_import(self, key: str, df: pd.DataFrame) -> None:
        self.store.write_parquet(self.import_bucket, key, df)
