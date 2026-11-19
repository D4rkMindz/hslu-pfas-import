"""The few SQL differences between PostgreSQL and BigQuery. Shared by the verify checks
(`verification/checks.py`) and the feature store adapters (`storage/`)."""
from dataclasses import dataclass

from pfas_import.domain.errors import InvalidIdentifier


@dataclass(frozen=True)
class Dialect:
    name: str
    quote_char: str
    float_type: str
    text_type: str

    def quote(self, identifier: str) -> str:
        if not identifier or self.quote_char in identifier or "\\" in identifier:
            raise InvalidIdentifier(f"Invalid identifier: {identifier!r}")
        return f"{self.quote_char}{identifier}{self.quote_char}"


POSTGRES = Dialect(name="postgres", quote_char='"', float_type="DOUBLE PRECISION", text_type="TEXT")
BIGQUERY = Dialect(name="bigquery", quote_char="`", float_type="FLOAT64", text_type="STRING")
