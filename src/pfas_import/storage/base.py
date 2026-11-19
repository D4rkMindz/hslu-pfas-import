"""The contract of a feature store: where `pipeline/load.py` publishes the features and
`verification/verify.py` reads them back. `repository/factory.py` picks the backend (LOAD_TARGET)."""
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

import pandas as pd

from pfas_import.domain.sql_dialect import Dialect


class FeatureStore(ABC):
    dialect: Dialect

    @abstractmethod
    def describe(self, table: str) -> str:
        """Human-readable table name for the log."""

    @abstractmethod
    def table_sql(self, table: str) -> str:
        """Table reference to use in SQL."""

    @abstractmethod
    def columns(self, table: str) -> list[str]:
        """Column names in table order; empty if the table does not exist."""

    @abstractmethod
    def query(self, sql: str) -> list[Sequence[Any]]: ...

    @abstractmethod
    def replace(self, table: str, df: pd.DataFrame) -> None:
        """Replace `table` with `df` atomically: a failure leaves the previous table intact."""
