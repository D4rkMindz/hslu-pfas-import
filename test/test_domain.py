"""The pure domain layer: change detection, the France filter, SQL identifiers and the named errors."""
import dataclasses
from collections.abc import Sequence
from typing import Any

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from pfas_import.domain.change import ChangeDecision, ChangePolicy
from pfas_import.domain.errors import (
    EmptyFeatureTable,
    EmptySourceGroupPrefix,
    InvalidIdentifier,
    SourceGroupCollision,
)
from pfas_import.domain.features import RAW_COLUMNS, transform
from pfas_import.domain.sql_dialect import BIGQUERY, POSTGRES
from pfas_import.repository.features import FeatureRepository
from pfas_import.storage.base import FeatureStore


class TestChangePolicy:
    @pytest.mark.parametrize(
        ("sha256", "previous", "changed"),
        [
            ("aaa", "aaa", False),
            ("aaa", "bbb", True),
            ("aaa", None, True),
        ],
    )
    def test_changed_iff_hashes_differ(self, sha256, previous, changed):
        decision = ChangePolicy().decide(sha256, previous)

        assert decision == ChangeDecision(changed=changed, sha256=sha256, previous_sha256=previous)

    def test_decision_is_frozen(self):
        decision = ChangePolicy().decide("aaa", "bbb")

        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.changed = False  # type: ignore[misc]


class TestErrors:
    @pytest.mark.parametrize(
        "error", [EmptyFeatureTable, SourceGroupCollision, EmptySourceGroupPrefix, InvalidIdentifier]
    )
    def test_is_a_value_error(self, error):
        assert issubclass(error, ValueError)


class TestDialectQuote:
    @pytest.mark.parametrize(
        ("dialect", "expected"),
        [
            (POSTGRES, '"pfas_sum"'),
            (BIGQUERY, "`pfas_sum`"),
        ],
    )
    def test_wraps_in_the_quote_char(self, dialect, expected):
        assert dialect.quote("pfas_sum") == expected

    @pytest.mark.parametrize(
        ("dialect", "identifier"),
        [
            (POSTGRES, 'bad"name'),
            (BIGQUERY, "bad`name"),
            (POSTGRES, ""),
            (BIGQUERY, ""),
            (POSTGRES, "bad\\name"),
            (BIGQUERY, "bad\\name"),
        ],
    )
    def test_rejects_invalid_identifiers(self, dialect, identifier):
        with pytest.raises(InvalidIdentifier):
            dialect.quote(identifier)


def raw_dataset(*presumptive_sectors: str) -> pd.DataFrame:
    """A small raw frame in mainland France with one presumptive source per given sector."""

    def record(category, lat, lon, **extra):
        return {
            "category": category, "country": "France", "lat": lat, "lon": lon, "date": "2024-05-01",
            "year": 2024, "name": f"{category} {lat}", "city": "x", "pfas_sum": 1.0, "matrix": "Groundwater",
            "unit": "ng/l", "type": "Sampling location", "sector": None, **extra,
        }

    return pd.DataFrame([
        record("Measurement", 48.85, 2.35),
        record("Measurement", 45.76, 4.83),
        record("PFAS production facility", 48.90, 2.40, date=None, year=None),
        record("Known PFAS user", 45.70, 4.80, date=None, year=None),
        *(
            record("Presumptive", 48.80 + i / 100, 2.30, date=None, year=None, sector=sector)
            for i, sector in enumerate(presumptive_sectors)
        ),
    ])


class TestTransformErrors:
    def test_valid_sectors_pass(self):
        """The helper frame itself is valid, so the errors below come from the sectors."""
        features = transform(raw_dataset("Metal works", "Chemical industry"))

        assert "metal_works_distance_min" in features.columns

    def test_needs_only_the_raw_columns(self):
        """The pipeline reads only RAW_COLUMNS, so the transform must not use any other column."""
        raw = raw_dataset("Metal works").assign(details="long free text")

        assert_frame_equal(transform(raw[RAW_COLUMNS]), transform(raw))

    @pytest.mark.parametrize(
        "sectors",
        [
            ("Metal works", "Metal-works"),
            ("Chemical industry", "chemical  INDUSTRY"),
        ],
    )
    def test_collision(self, sectors):
        with pytest.raises(SourceGroupCollision):
            transform(raw_dataset(*sectors))

    @pytest.mark.parametrize("sector", ["!!!", "---", "  ()  "])
    def test_empty_prefix(self, sector):
        with pytest.raises(EmptySourceGroupPrefix):
            transform(raw_dataset("Metal works", sector))


class RecordingStore(FeatureStore):
    """A feature store that records `replace` calls and holds nothing."""

    dialect = POSTGRES

    def __init__(self):
        self.replaced: list[tuple[str, pd.DataFrame]] = []

    def describe(self, table: str) -> str:
        return f"recording {table}"

    def table_sql(self, table: str) -> str:
        return POSTGRES.quote(table)

    def columns(self, table: str) -> list[str]:
        return []

    def query(self, sql: str) -> list[Sequence[Any]]:
        return []

    def replace(self, table: str, df: pd.DataFrame) -> None:
        self.replaced.append((table, df))


class TestFeatureRepositoryReplace:
    def test_refuses_an_empty_frame(self):
        store = RecordingStore()

        with pytest.raises(EmptyFeatureTable):
            FeatureRepository(store).replace(pd.DataFrame({"pfas_sum": pd.Series([], dtype=float)}))

        assert store.replaced == []

    def test_passes_a_frame_to_pfas_features(self):
        store = RecordingStore()
        df = pd.DataFrame({"pfas_sum": [1.0, 2.0]})

        FeatureRepository(store).replace(df)

        assert len(store.replaced) == 1
        table, passed = store.replaced[0]
        assert table == "pfas_features"
        assert passed is df
