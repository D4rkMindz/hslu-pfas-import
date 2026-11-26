"""Offline tests of `pfas_import verify`.

Every check of both published tables (`pfas_features` and the facility tokens) is shown to fire on
deliberately broken data. The SQL runs in DuckDB on in-memory DataFrames, in the PostgreSQL dialect,
so no database is needed.
"""
import datetime as dt
import math
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest

from pfas_import.domain.features import transform, transform_all
from pfas_import.domain.sql_dialect import BIGQUERY, POSTGRES
from pfas_import.repository.files import FACILITY_TOKENS_KEY, IMPORT_SOURCE_KEY
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


FEATURES_TABLE = "pfas_features"
TOKENS_TABLE = "pfas_facility_tokens"


class DuckDBTarget:
    """One published table in DuckDB; pass `con` to put several tables in one database."""

    dialect = POSTGRES

    def __init__(
        self, df: pd.DataFrame | None, table: str = FEATURES_TABLE, con: duckdb.DuckDBPyConnection | None = None
    ):
        self.df = df
        self.name = f"DuckDB {table}"
        self.table_sql = POSTGRES.quote(table)
        self.con = con or duckdb.connect()
        if df is not None:
            self.con.register(table, df)

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
        # Dense rank of the sorted (lat, lon): Corsica 0, Marseille 1, Lyon 2, Paris 3.
        "location_id": np.array([3, 2, 1, 0], dtype=np.int64),
    })
    df.insert(1, "year", df["date"].dt.year.astype(np.int64))
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


def token_rows(location_id: int, distances: Sequence[float]) -> list[dict]:
    """Tokens of one location with ranks 1..n in the given order; bearings step by 37 degrees."""
    return [
        {
            "location_id": location_id,
            "rank": rank,
            "source_group": "producer",
            "category": "PFAS production facility",
            "type": "Fluorochemicals",
            "distance_km": distance,
            "bearing_sin": math.sin(math.radians(37 * rank)),
            "bearing_cos": math.cos(math.radians(37 * rank)),
        }
        for rank, distance in enumerate(distances, start=1)
    ]


def as_token_frame(rows: list[dict]) -> pd.DataFrame:
    """Token rows with the dtypes the transform publishes (float32 after compression)."""
    df = pd.DataFrame(rows, columns=list(checks.TOKEN_COLUMNS))
    for column in ("distance_km", "bearing_sin", "bearing_cos"):
        df[column] = df[column].astype(np.float32)
    return df


def valid_tokens() -> pd.DataFrame:
    """Paris (location 3) has four facilities in range, two at the same distance; Marseille (1) one."""
    return as_token_frame(token_rows(3, [1.5, 12.0, 12.0, 99.5]) + token_rows(1, [40.0]))


def token_results_for(
    file_df: pd.DataFrame,
    table_df: pd.DataFrame | None = None,
    features: pd.DataFrame | None = None,
) -> dict[str, CheckResult]:
    """Run the token checks: `file_df` is the token import file, `table_df` the published token table
    (default: equal), `features` the published feature table (default: `valid_features()`)."""
    table = file_df.copy() if table_df is None else table_df
    con = duckdb.connect()
    feature_target = DuckDBTarget(valid_features() if features is None else features, FEATURES_TABLE, con)
    token_target = DuckDBTarget(table, TOKENS_TABLE, con)
    return {r.check: r for r in verify.run_tokens(token_target, file_df, feature_target.table_sql)}


def mutated_tokens(change: Callable[[pd.DataFrame], None]) -> pd.DataFrame:
    df = valid_tokens()
    change(df)
    return df


def token_check(name: str) -> str:
    return checks.TOKEN_LABEL + name


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
        table.loc[3, "date"] = pd.Timestamp("2023-11-30")  # same year, so `year` stays consistent

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

    @pytest.mark.parametrize("column", ["year", "location_id"])
    def test_missing_year_or_location_id(self, column):
        df = valid_features().drop(columns=[column])

        result = only_failure(results_for(df))

        assert result.check == "schema: base and tier-1 feature columns are present"
        assert result.detail == f"missing: {column}"

    def test_base_and_not_null_columns_include_year_and_location_id(self):
        assert {"year", "location_id"} <= set(checks.BASE_COLUMNS)
        assert {"year", "location_id"} <= set(checks.NOT_NULL_COLUMNS)


class TestRules:
    def test_null_in_required_column(self):
        result = only_failure(results_for(mutated(lambda df: df.__setitem__("name", [None, "b", "c", "d"]))))

        assert result.check.startswith("no NULL in")
        assert result.failures == 1

    @pytest.mark.parametrize("column", ["year", "location_id"])
    def test_null_year_or_location_id(self, column):
        def change(df):
            df[column] = df[column].astype("float64")
            df.loc[2, column] = None

        result = only_failure(results_for(mutated(change)))

        assert result.check.startswith("no NULL in")
        assert column in result.check
        assert result.failures == 1

    @pytest.mark.parametrize("year", [2019, 2021, 1970])
    def test_year_differs_from_date(self, year):
        def change(df):
            df.loc[0, "year"] = year  # date is 2020-01-01

        result = only_failure(results_for(mutated(change)))

        assert result.check == "year equals the year of date"
        assert result.severity is Severity.ERROR
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
            date = pd.Timestamp.now().normalize() + pd.Timedelta(days=2)
            df.loc[0, ["date", "year"]] = [date, date.year]

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

    def test_bigquery_token_rules_quote_with_backticks(self):
        sql = checks.token_rule_checks_sql(
            "`p.d.tokens`", "`p.d.features`", list(checks.TOKEN_COLUMNS), BIGQUERY
        )

        assert '"' not in sql
        for column in checks.TOKEN_COLUMNS:
            assert f"`{column}`" in sql
        assert "FROM `p.d.features`" in sql


class TestTransformContract:
    """The output of the real transform passes verify (contract between the two)."""

    @staticmethod
    def raw_dataset() -> pd.DataFrame:
        def record(category, lat, lon, **extra):
            return {
                "category": category, "country": "France", "lat": lat, "lon": lon, "date": "2024-05-01",
                "year": 2024, "name": f"{category} {lat}", "city": "x", "pfas_sum": 1.0, "matrix": "Groundwater",
                "unit": "ng/l", "type": "Sampling location", "sector": None, **extra,
            }

        def facility(category, lat, lon, **extra):
            return record(category, lat, lon, date=None, year=None, **extra)

        return pd.DataFrame([
            record("Measurement", 48.85, 2.35),
            record("Measurement", 48.86, 2.36, pfas_sum=0.0),
            record("Measurement", 45.76, 4.83, matrix="Surface water", date="2021-03-02", year=2021),
            record("Measurement", 48.85, 2.35, date="2019-07-08", year=2019),  # same location as the first
            record("Measurement", 16.25, -61.55),  # Guadeloupe: dropped
            facility("PFAS production facility", 48.90, 2.40, type="Fluorochemicals"),
            facility("Known PFAS user", 45.70, 4.80, type="Textiles"),
            facility("Presumptive", 48.80, 2.30, sector="Chemical industry", type="Plant"),
            facility("Presumptive", 48.70, 2.20, type="Airport"),  # no sector: grouped by type
            facility("Presumptive", 16.20, -61.50, sector="Overseas only"),  # dropped
        ])

    def test_transform_output_passes(self):
        features = transform(self.raw_dataset())

        assert len(features) == 4
        assert not any(c.startswith("overseas_only") for c in features.columns)
        assert failed(results_for(features)) == set()

    def test_transform_all_output_passes_both_tables(self):
        output = transform_all(self.raw_dataset())
        con = duckdb.connect()
        features = DuckDBTarget(output.features, FEATURES_TABLE, con)
        tokens = DuckDBTarget(output.facility_tokens, TOKENS_TABLE, con)

        results = verify.run(features, output.features) + verify.run_tokens(
            tokens, output.facility_tokens, features.table_sql
        )

        assert len(output.facility_tokens) > 0
        assert {r.check for r in results if not r.passed} == set()


class TestTokenValidData:
    def test_every_check_passes(self):
        assert failed(token_results_for(valid_tokens())) == set()

    def test_every_check_is_labelled(self):
        results = token_results_for(valid_tokens())

        assert all(name.startswith(checks.TOKEN_LABEL) for name in results)

    def test_every_check_is_an_error(self):
        results = token_results_for(valid_tokens())

        assert {r.severity for r in results.values()} == {Severity.ERROR}

    def test_runs_every_rule(self):
        results = token_results_for(valid_tokens())

        assert set(results) >= {
            token_check("published table exists"),
            token_check("schema: all token columns are present"),
            token_check("reconciliation: row count equals the import file"),
            token_check("reconciliation: column names equal the import file"),
            token_check("reconciliation: non-NULL count per column equals the import file"),
            token_check("reconciliation: sum per numeric column equals the import file"),
            token_check("reconciliation: date range equals the import file"),
            token_check("rank is a whole number in 1..32"),
            token_check("distance within 0..100 km"),
            token_check("bearing_sin within -1..1"),
            token_check("bearing_cos within -1..1"),
            token_check("bearing_sin² + bearing_cos² = 1"),
            token_check("(location_id, rank) is unique"),
            token_check("ranks are consecutive from 1 and sorted by distance"),
            token_check("every location_id exists in the feature table"),
        }
        assert any(name.startswith(token_check("no NULL in")) for name in results)

    def test_32_tokens_at_one_location_pass(self):
        tokens = as_token_frame(token_rows(3, [1.0 + i for i in range(32)]))

        assert failed(token_results_for(tokens)) == set()

    @pytest.mark.parametrize(("row", "distance"), [(0, 0.0), (3, 100.0), (3, 100.0005)])
    def test_distance_on_the_border_is_tolerated(self, row, distance):
        def change(df):
            df.loc[row, "distance_km"] = distance  # row 0 is the nearest, row 3 the farthest token of Paris

        assert failed(token_results_for(mutated_tokens(change))) == set()


class TestTokenReconciliation:
    def test_missing_table(self):
        con = duckdb.connect()
        features = DuckDBTarget(valid_features(), FEATURES_TABLE, con)

        results = verify.run_tokens(DuckDBTarget(None, TOKENS_TABLE, con), valid_tokens(), features.table_sql)

        assert [(r.check, r.passed) for r in results] == [(token_check("published table exists"), False)]

    def test_dropped_row(self):
        table = valid_tokens().iloc[:-1]

        names = failed(token_results_for(valid_tokens(), table))

        assert token_check("reconciliation: row count equals the import file") in names

    def test_changed_value(self):
        table = valid_tokens()
        table.loc[2, "distance_km"] = 13.0

        result = only_failure(token_results_for(valid_tokens(), table))

        assert result.check == token_check("reconciliation: sum per numeric column equals the import file")
        assert "distance_km" in result.detail

    def test_value_lost_to_null(self):
        table = valid_tokens()
        table.loc[0, "category"] = None

        names = failed(token_results_for(valid_tokens(), table))

        assert names == {
            token_check("reconciliation: non-NULL count per column equals the import file"),
            token_check("no NULL in location_id, rank, source_group, category, type, distance_km, "
                        "bearing_sin, bearing_cos"),
        }

    def test_column_missing_in_table(self):
        table = valid_tokens().drop(columns=["type"])

        names = failed(token_results_for(valid_tokens(), table))

        assert names == {
            token_check("reconciliation: column names equal the import file"),
            token_check("schema: all token columns are present"),
        }


class TestTokenSchema:
    @pytest.mark.parametrize("column", checks.TOKEN_COLUMNS)
    def test_missing_column(self, column):
        df = valid_tokens().drop(columns=[column])

        result = token_results_for(df)[token_check("schema: all token columns are present")]

        assert not result.passed
        assert result.detail == f"missing: {column}"

    @pytest.mark.parametrize("column", ["source_group", "category", "type", "bearing_cos"])
    def test_missing_non_key_column_is_the_only_failure(self, column):
        df = valid_tokens().drop(columns=[column])

        result = only_failure(token_results_for(df))

        assert result.check == token_check("schema: all token columns are present")


class TestTokenRules:
    @pytest.mark.parametrize("column", ["source_group", "category", "type", "distance_km", "bearing_sin"])
    def test_null_in_token_column(self, column):
        def change(df):
            df.loc[2, column] = None

        result = only_failure(token_results_for(mutated_tokens(change)))

        assert result.check.startswith(token_check("no NULL in"))
        assert result.failures == 1

    def test_rank_above_32(self):
        tokens = as_token_frame(token_rows(3, [1.0 + i for i in range(33)]))

        result = only_failure(token_results_for(tokens))

        assert result.check == token_check("rank is a whole number in 1..32")
        assert result.failures == 1

    @pytest.mark.parametrize("rank", [0, -1, 1.5])
    def test_rank_not_a_whole_number_in_range(self, rank):
        def change(df):
            df["rank"] = df["rank"].astype("float64")
            df.loc[4, "rank"] = rank  # the only token of Marseille

        names = failed(token_results_for(mutated_tokens(change)))

        # Marseille no longer starts at rank 1, so the consecutive check fires as well.
        assert names == {
            token_check("rank is a whole number in 1..32"),
            token_check("ranks are consecutive from 1 and sorted by distance"),
        }

    @pytest.mark.parametrize(("row", "distance"), [(3, 100.01), (3, 250.0), (0, -0.5)])
    def test_distance_out_of_range(self, row, distance):
        def change(df):
            df.loc[row, "distance_km"] = distance

        result = only_failure(token_results_for(mutated_tokens(change)))

        assert result.check == token_check("distance within 0..100 km")

    @pytest.mark.parametrize(("column", "value"), [("bearing_sin", 1.5), ("bearing_cos", -1.2)])
    def test_bearing_out_of_range(self, column, value):
        def change(df):
            df.loc[1, column] = value

        names = failed(token_results_for(mutated_tokens(change)))

        # A value outside -1..1 can never lie on the unit circle either.
        assert names == {
            token_check(f"{column} within -1..1"),
            token_check("bearing_sin² + bearing_cos² = 1"),
        }

    def test_bearing_off_the_unit_circle(self):
        def change(df):
            df.loc[1, ["bearing_sin", "bearing_cos"]] = [0.6, 0.6]

        result = only_failure(token_results_for(mutated_tokens(change)))

        assert result.check == token_check("bearing_sin² + bearing_cos² = 1")

    def test_bearing_within_the_unit_circle_tolerance(self):
        def change(df):
            df.loc[1, ["bearing_sin", "bearing_cos"]] = [0.002, 1.0]  # sin² + cos² - 1 = 4e-6

        assert failed(token_results_for(mutated_tokens(change))) == set()

    def test_duplicate_location_and_rank(self):
        def change(df):
            df.loc[2, "rank"] = 2

        names = failed(token_results_for(mutated_tokens(change)))

        # Two rows with rank 2 also break the consecutive sequence 1, 2, 3, 4.
        assert names == {
            token_check("(location_id, rank) is unique"),
            token_check("ranks are consecutive from 1 and sorted by distance"),
        }

    @pytest.mark.parametrize(
        ("row", "column", "value"),
        [
            (3, "rank", 5),  # Paris: 1, 2, 3, 5
            (4, "rank", 2),  # Marseille starts at 2
            (0, "distance_km", 50.0),  # Paris rank 1 is farther than rank 2
            (2, "distance_km", 11.0),  # Paris rank 3 is nearer than rank 2
        ],
    )
    def test_ranks_not_consecutive_or_not_sorted(self, row, column, value):
        def change(df):
            df.loc[row, column] = value

        result = only_failure(token_results_for(mutated_tokens(change)))

        assert result.check == token_check("ranks are consecutive from 1 and sorted by distance")

    @pytest.mark.parametrize("location_id", [99, 2])
    def test_location_unknown_to_the_feature_table(self, location_id):
        features = valid_features()
        features = features[features["location_id"] != 2]  # drop Lyon from the feature table

        def change(df):
            df.loc[4, "location_id"] = location_id  # Marseille's token now points elsewhere

        result = only_failure(token_results_for(mutated_tokens(change), features=features))

        assert result.check == token_check("every location_id exists in the feature table")
        assert result.failures == 1


class TestMain:
    @pytest.fixture
    def run_main(self, monkeypatch):
        """Run `verify.main` against both tables in one DuckDB database; returns (exit code, files read)."""

        def _run(
            table: pd.DataFrame,
            file: pd.DataFrame | None = None,
            token_table: pd.DataFrame | None = None,
            token_file: pd.DataFrame | None = None,
            keys: tuple[str, str] = ("features.parquet", "tokens.parquet"),
        ) -> tuple[int, list[str]]:
            files = {
                keys[0]: valid_features() if file is None else file,
                keys[1]: valid_tokens() if token_file is None else token_file,
            }
            read: list[str] = []

            @contextmanager
            def fake_targets():
                con = duckdb.connect()
                yield SimpleNamespace(
                    features=DuckDBTarget(table, FEATURES_TABLE, con),
                    facility_tokens=DuckDBTarget(
                        valid_tokens() if token_table is None else token_table, TOKENS_TABLE, con
                    ),
                )

            def fake_read(key: str) -> pd.DataFrame:
                read.append(key)
                return files[key]

            monkeypatch.setattr(verify, "open_targets", fake_targets)
            monkeypatch.setattr(verify, "read_import_file", fake_read)
            return verify.main(*keys).exit_code, read

        return _run

    def test_exit_code_0_when_valid(self, run_main):
        assert run_main(valid_features())[0] == 0

    def test_exit_code_0_with_warnings_only(self, run_main):
        df = mutated(lambda df: df.__setitem__("unit", ["mg/l"] * 4))

        assert run_main(df, df)[0] == 0

    def test_exit_code_1_on_feature_error(self, run_main):
        assert run_main(valid_features().iloc[:2])[0] == 1

    def test_exit_code_1_on_token_error(self, run_main):
        assert run_main(valid_features(), token_table=valid_tokens().iloc[:2])[0] == 1

    def test_exit_code_1_when_a_token_location_is_missing_in_the_features(self, run_main):
        """The cross-table check runs, so both tables must share one database."""
        tokens = mutated_tokens(lambda df: df.__setitem__("location_id", [3, 3, 3, 3, 99]))

        assert run_main(valid_features(), token_table=tokens, token_file=tokens)[0] == 1

    def test_reads_the_features_file_first_then_the_tokens_file(self, run_main):
        _, read = run_main(valid_features())

        assert read == ["features.parquet", "tokens.parquet"]

    def test_default_keys(self, monkeypatch):
        read: list[str] = []

        @contextmanager
        def fake_targets():
            con = duckdb.connect()
            yield SimpleNamespace(
                features=DuckDBTarget(valid_features(), FEATURES_TABLE, con),
                facility_tokens=DuckDBTarget(valid_tokens(), TOKENS_TABLE, con),
            )

        def fake_read(key: str) -> pd.DataFrame:
            read.append(key)
            return valid_features() if key == IMPORT_SOURCE_KEY else valid_tokens()

        monkeypatch.setattr(verify, "open_targets", fake_targets)
        monkeypatch.setattr(verify, "read_import_file", fake_read)

        assert verify.main().exit_code == 0
        assert read == [IMPORT_SOURCE_KEY, FACILITY_TOKENS_KEY]

    def test_counts_the_checks_of_both_tables(self, run_main, monkeypatch):
        counted: list[int] = []
        original = verify.report
        monkeypatch.setattr(verify, "report", lambda results: (counted.append(len(results)), original(results))[1])

        run_main(valid_features())

        con = duckdb.connect()
        features = DuckDBTarget(valid_features(), FEATURES_TABLE, con)
        expected = len(verify.run(features, valid_features())) + len(
            verify.run_tokens(DuckDBTarget(valid_tokens(), TOKENS_TABLE, con), valid_tokens(), features.table_sql)
        )
        assert counted == [expected]
