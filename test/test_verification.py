"""Offline tests of `pfas_import verify`.

Every check is shown to fire on deliberately broken data. The SQL runs in DuckDB on an
in-memory DataFrame, in the PostgreSQL dialect, so no database is needed.
"""
import datetime as dt
from collections.abc import Callable

import duckdb
import numpy as np
import pandas as pd
import pytest

from pfas_import.domain.features import transform
from pfas_import.domain.sql_dialect import BIGQUERY, POSTGRES
from pfas_import.verification import checks, verify
from pfas_import.verification.checks import CheckResult, Severity

PREFIXES = ("producer", "user", "presumptive", "chemical_industry")
# Nearest source distance and the counts in the 0-5, 5-25 and 25-50 km bands, consistent per row.
FEATURES = (
    (2.0, (1, 0, 0)),
    (10.0, (0, 2, 0)),
    (40.0, (0, 0, 3)),
    (80.0, (0, 0, 0)),
)


class DuckDBTarget:
    dialect = POSTGRES
    name = "DuckDB pfas_features"
    table_sql = '"pfas_features"'

    def __init__(self, df: pd.DataFrame | None):
        self.df = df
        self.con = duckdb.connect()
        if df is not None:
            self.con.register("pfas_features", df)

    def columns(self) -> list[str]:
        return [] if self.df is None else [str(c) for c in self.df.columns]

    def query(self, sql: str):
        return self.con.execute(sql).fetchall()


def valid_features() -> pd.DataFrame:
    df = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-01", "2021-06-15", "2022-03-03", "2023-12-31"]),
        "lat": [48.85, 45.76, 43.30, 42.70],  # Paris, Lyon, Marseille, Corsica
        "lon": [2.35, 4.83, 5.37, 9.15],
        "name": ["a", "b", "c", "d"],
        "city": ["Paris", None, "Marseille", None],
        "pfas_sum": [0.0, 0.0, 12.5, 3.25],
        "matrix": ["Surface water", "Groundwater", "Drinking water", "Sediment"],
        "unit": ["ng/l", "ng/l", "ng/l", "ng/kg dw"],
        "type": ["Sampling location"] * 4,
        "sector": [None] * 4,
    })
    for prefix in PREFIXES:
        df[f"{prefix}_distance_min"] = np.array([d for d, _ in FEATURES], dtype=np.float32)
        for band, column in enumerate(checks.band_columns(prefix)):
            df[column] = np.array([c[band] for _, c in FEATURES], dtype=np.float32)
    return df


def results_for(file_df: pd.DataFrame, table_df: pd.DataFrame | None = None) -> dict[str, CheckResult]:
    """Run all checks: `file_df` is the import file, `table_df` the published table (default: equal)."""
    table = file_df.copy() if table_df is None else table_df
    return {r.check: r for r in verify.run(DuckDBTarget(table), file_df)}


def failed(results: dict[str, CheckResult]) -> set[str]:
    return {name for name, r in results.items() if not r.passed}


def only_failure(results: dict[str, CheckResult]) -> CheckResult:
    names = failed(results)
    assert len(names) == 1, names
    return results[names.pop()]


def mutated(change: Callable[[pd.DataFrame], None]) -> pd.DataFrame:
    df = valid_features()
    change(df)
    return df


class TestValidData:
    def test_every_check_passes(self):
        results = results_for(valid_features())

        assert failed(results) == set()

    def test_runs_rules_for_every_prefix(self):
        results = results_for(valid_features())

        for prefix in PREFIXES:
            assert f"{prefix}: nearest distance agrees with the band counts" in results

    def test_tiny_float_differences_in_sums_pass(self):
        table = valid_features()
        table["pfas_sum"] = table["pfas_sum"] * (1 + 1e-12)

        assert failed(results_for(valid_features(), table)) == set()


class TestReconciliation:
    def test_missing_table(self):
        results = verify.run(DuckDBTarget(None), valid_features())

        assert [(r.check, r.passed) for r in results] == [("published table exists", False)]

    def test_dropped_row(self):
        table = valid_features().iloc[:-1]

        names = failed(results_for(valid_features(), table))

        assert "reconciliation: row count equals the import file" in names

    def test_changed_value(self):
        table = valid_features()
        table.loc[2, "pfas_sum"] = 99.0

        result = only_failure(results_for(valid_features(), table))

        assert result.check == "reconciliation: sum per numeric column equals the import file"
        assert "pfas_sum" in result.detail

    def test_value_lost_to_null(self):
        table = valid_features()
        table.loc[0, "city"] = None

        result = only_failure(results_for(valid_features(), table))

        assert result.check == "reconciliation: non-NULL count per column equals the import file"
        assert "city" in result.detail

    def test_shifted_date(self):
        table = valid_features()
        table.loc[3, "date"] = pd.Timestamp("2024-01-01")

        result = only_failure(results_for(valid_features(), table))

        assert result.check == "reconciliation: date range equals the import file"

    def test_column_missing_in_table(self):
        table = valid_features().drop(columns=["matrix"])

        names = failed(results_for(valid_features(), table))

        assert names == {
            "reconciliation: column names equal the import file",
            "schema: base and tier-1 feature columns are present",
        }


class TestSchema:
    def test_missing_measurement_column(self):
        df = valid_features().drop(columns=["unit"])

        result = only_failure(results_for(df))

        assert result.check == "schema: base and tier-1 feature columns are present"
        assert result.detail == "missing: unit"

    def test_incomplete_tier2_prefix(self):
        df = valid_features().drop(columns=["chemical_industry_count_25_50km"])

        result = only_failure(results_for(df))

        assert result.check == "schema: every tier-2 prefix has a distance and three band columns"
        assert "chemical_industry" in result.detail


class TestRules:
    def test_null_in_required_column(self):
        result = only_failure(results_for(mutated(lambda df: df.__setitem__("name", [None, "b", "c", "d"]))))

        assert result.check.startswith("no NULL in")
        assert result.failures == 1

    def test_overseas_coordinates(self):
        def guadeloupe(df):
            df.loc[1, ["lat", "lon"]] = [16.25, -61.55]

        result = only_failure(results_for(mutated(guadeloupe)))

        assert result.check.startswith("coordinates in mainland France")

    def test_negative_pfas_sum(self):
        result = only_failure(results_for(mutated(lambda df: df.__setitem__("pfas_sum", [-1.0, 0, 0, 0]))))

        assert result.check == "pfas_sum is not negative"

    def test_zero_pfas_sum_is_valid(self):
        df = mutated(lambda df: df.__setitem__("pfas_sum", [0.0] * 4))

        assert failed(results_for(df)) == set()

    def test_future_date(self):
        def future(df):
            df.loc[0, "date"] = pd.Timestamp.now().normalize() + pd.Timedelta(days=2)

        result = only_failure(results_for(mutated(future)))

        assert result.check == "date is not in the future"

    @pytest.mark.parametrize("distance", [-0.5, 25_000.0])
    def test_distance_out_of_range(self, distance):
        def change(df):
            df.loc[3, "user_distance_min"] = distance

        names = failed(results_for(mutated(change)))

        assert "user: distance within 0..20,038 km" in names

    @pytest.mark.parametrize("count", [-1.0, 0.5, None])
    def test_invalid_band_count(self, count):
        def change(df):
            df.loc[3, "producer_count_25_50km"] = count

        names = failed(results_for(mutated(change)))

        assert "producer: band counts are whole numbers >= 0" in names

    def test_source_within_band_but_distance_says_no(self):
        def change(df):
            df.loc[3, "presumptive_count_0_5km"] = 1  # nearest source is 80 km away

        result = only_failure(results_for(mutated(change)))

        assert result.check == "presumptive: nearest distance agrees with the band counts"

    def test_distance_within_band_but_no_source_counted(self):
        def change(df):
            df.loc[1, "chemical_industry_count_5_25km"] = 0  # nearest source is 10 km away

        result = only_failure(results_for(mutated(change)))

        assert result.check == "chemical_industry: nearest distance agrees with the band counts"

    def test_distance_on_band_border_is_tolerated(self):
        def change(df):
            df.loc[0, "producer_distance_min"] = np.float32(5.0)  # counted in 0-5 km

        assert failed(results_for(mutated(change))) == set()


class TestWarnings:
    def test_unknown_unit_is_only_a_warning(self):
        result = only_failure(results_for(mutated(lambda df: df.__setitem__("unit", ["mg/l", "ng/l", "ng/l", "ng/l"]))))

        assert result.check == "unit is a known value"
        assert result.severity is Severity.WARNING

    def test_unknown_matrix_is_only_a_warning(self):
        result = only_failure(results_for(mutated(lambda df: df.__setitem__("matrix", ["Air"] * 4))))

        assert result.check == "matrix is a known value"
        assert result.status == "WARN"

    def test_rows_that_only_differ_in_pfas_sum(self):
        df = valid_features()
        df = pd.concat([df, df.iloc[[0]].assign(pfas_sum=7.0), df.iloc[[1]]], ignore_index=True)

        result = only_failure(results_for(df))

        assert result.check == "rows are unique without pfas_sum (the label)"
        assert result.severity is Severity.WARNING
        assert result.failures == 2
        assert result.detail == "2 groups, 1 with different pfas_sum"


class TestSql:
    def test_bigquery_quotes_with_backticks(self):
        sql = checks.rule_checks_sql(
            "`p.d.pfas_features`", list(valid_features().columns), BIGQUERY, dt.date(2026, 11, 15)
        )

        assert '"' not in sql
        assert "`producer_distance_min`" in sql
        assert "AS STRING" in sql

    def test_bigquery_reconciliation_uses_float64(self):
        sql = checks.reconciliation_sql("`p.d.t`", ["lat"], ["lat"], BIGQUERY)

        assert "CAST(`lat` AS FLOAT64)" in sql

    def test_rejects_identifier_with_quote(self):
        with pytest.raises(ValueError):
            POSTGRES.quote('bad"name')


class TestTransformContract:
    """The output of the real transform passes verify (contract between the two)."""

    @staticmethod
    def raw_dataset() -> pd.DataFrame:
        def record(category, lat, lon, **extra):
            return {
                "category": category, "lat": lat, "lon": lon, "date": "2024-05-01",
                "name": f"{category} {lat}", "city": "x", "pfas_sum": 1.0, "matrix": "Groundwater",
                "unit": "ng/l", "type": "Sampling location", "sector": None, **extra,
            }

        return pd.DataFrame([
            record("Measurement", 48.85, 2.35),
            record("Measurement", 48.86, 2.36, pfas_sum=0.0),
            record("Measurement", 45.76, 4.83, matrix="Surface water"),
            record("Measurement", 16.25, -61.55),  # Guadeloupe: dropped
            record("PFAS production facility", 48.90, 2.40, date=None),
            record("Known PFAS user", 45.70, 4.80, date=None),
            record("Presumptive", 48.80, 2.30, date=None, sector="Chemical industry"),
            record("Presumptive", 16.20, -61.50, date=None, sector="Overseas only"),  # dropped
        ])

    def test_transform_output_passes(self):
        features = transform(self.raw_dataset())

        assert len(features) == 3
        assert not any(c.startswith("overseas_only") for c in features.columns)
        assert failed(results_for(features)) == set()


class TestMain:
    @pytest.fixture
    def run_main(self, monkeypatch):
        def _run(table: pd.DataFrame, file: pd.DataFrame | None = None) -> int:
            from contextlib import contextmanager

            @contextmanager
            def fake_target(_):
                yield DuckDBTarget(table)

            monkeypatch.setattr(verify, "open_target", fake_target)
            monkeypatch.setattr(verify, "read_import_file", lambda _: valid_features() if file is None else file)
            return verify.main(target="local").exit_code

        return _run

    def test_exit_code_0_when_valid(self, run_main):
        assert run_main(valid_features()) == 0

    def test_exit_code_0_with_warnings_only(self, run_main):
        df = mutated(lambda df: df.__setitem__("unit", ["mg/l"] * 4))

        assert run_main(df, df) == 0

    def test_exit_code_1_on_error(self, run_main):
        assert run_main(valid_features().iloc[:2]) == 1
