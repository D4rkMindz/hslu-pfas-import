"""The published feature table, in whichever feature store LOAD_TARGET selects. The table is the contract
with the data science project `pfas_trace`."""
from collections.abc import Sequence
from typing import Any

import pandas as pd

from pfas_import.domain.errors import EmptyFeatureTable
from pfas_import.domain.sql_dialect import Dialect
from pfas_import.storage.base import FeatureStore

FEATURE_TABLE: str = "pfas_features"


class FeatureRepository:
    def __init__(self, store: FeatureStore, table: str = FEATURE_TABLE):
        self.store = store
        self.table = table

    @property
    def dialect(self) -> Dialect:
        return self.store.dialect

    @property
    def name(self) -> str:
        return self.store.describe(self.table)

    @property
    def table_sql(self) -> str:
        return self.store.table_sql(self.table)

    def columns(self) -> list[str]:
        return self.store.columns(self.table)

    def query(self, sql: str) -> list[Sequence[Any]]:
        return self.store.query(sql)

    def replace(self, df: pd.DataFrame) -> None:
        if df.empty:
            raise EmptyFeatureTable("Feature DataFrame is empty; refusing to replace current data.")
        self.store.replace(self.table, df)
