"""The checks of `pfas_import verify` and the SQL they run.

The expectations are written down here on purpose instead of being imported from
`domain.features`: a bug in the transform must not be able to change what is checked.

Two kinds of checks:
- reconciliation: the published table holds exactly the content of the import file
  (`reconciliation_sql` + the `compare_*` functions)
- rules: the published values make sense (`rule_checks_sql`, also printed by `--print-sql`)
"""
import datetime as dt
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from pfas_import.domain.sql_dialect import Dialect

# Measurement columns of the feature table (contract with the data science project `pfas_trace`).
BASE_COLUMNS: tuple[str, ...] = (
    "date", "year", "lat", "lon", "name", "city", "pfas_sum", "matrix", "unit", "type", "sector", "location_id",
)
# These may never be NULL. `city` and `sector` may, they are empty in the source.
NOT_NULL_COLUMNS: tuple[str, ...] = (
    "date", "year", "lat", "lon", "name", "pfas_sum", "matrix", "unit", "location_id",
)

# Facility tokens: the nearest facilities per measurement location, joined to the features on location_id.
TOKEN_COLUMNS: tuple[str, ...] = (
    "location_id", "rank", "source_group", "category", "type", "distance_km", "bearing_sin", "bearing_cos",
)
TOKEN_NOT_NULL_COLUMNS: tuple[str, ...] = TOKEN_COLUMNS
MAX_TOKEN_RANK = 32
MAX_TOKEN_DISTANCE_KM = 100
# Bearings are stored as float32, so sin/cos may exceed 1 by a rounding error.
UNIT_TOLERANCE = 1e-6
TOKEN_LABEL = "facility tokens: "

# Tier-1 source categories. Tier-2 prefixes (presumptive sectors) depend on the data.
TIER1_PREFIXES: tuple[str, ...] = ("producer", "user", "presumptive")
DISTANCE_SUFFIX = "distance_min"
BANDS_KM: tuple[tuple[int, int], ...] = ((0, 5), (5, 25), (25, 50))

# Mainland France including Corsica; overseas departments are not part of the dataset.
MAINLAND_LAT: tuple[float, float] = (41.3, 51.1)
MAINLAND_LON: tuple[float, float] = (-5.2, 9.6)

# Half of the Earth's circumference: no two points on Earth are further apart.
MAX_DISTANCE_KM = 20_038
# Distances are stored as float32, so a distance right at a band border may round across it.
BAND_TOLERANCE_KM = 0.001

KNOWN_UNITS: tuple[str, ...] = ("ng/l", "ng/kg dw", "ng/kg fw", "ng/kg")
KNOWN_MATRICES: tuple[str, ...] = (
    "Surface water", "Groundwater", "Drinking water", "Sediment", "Wastewater", "Biota",
    "Suspended matter", "Soil", "Rainwater", "Unknown",
)

# Sums are compared with a relative tolerance: pandas, PostgreSQL and BigQuery add in a different order.
SUM_REL_TOLERANCE = 1e-9

LABEL_COLUMN = "pfas_sum"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class CheckResult:
    check: str
    severity: Severity
    failures: int
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.failures == 0

    @property
    def status(self) -> str:
        if self.passed:
            return "PASS"
        return "FAIL" if self.severity is Severity.ERROR else "WARN"


def band_columns(prefix: str) -> list[str]:
    return [f"{prefix}_count_{lower}_{upper}km" for lower, upper in BANDS_KM]


def feature_columns(prefix: str) -> list[str]:
    return [f"{prefix}_{DISTANCE_SUFFIX}", *band_columns(prefix)]


_FEATURE_PATTERN = re.compile(
    rf"^(?P<prefix>.+)_(?:{DISTANCE_SUFFIX}|"
    + "|".join(f"count_{lower}_{upper}km" for lower, upper in BANDS_KM)
    + r")$"
)


def feature_prefixes(columns: Iterable[str]) -> list[str]:
    """Every prefix that has at least one feature column, in column order."""
    prefixes: dict[str, None] = {}
    for column in columns:
        if match := _FEATURE_PATTERN.match(column):
            prefixes[match["prefix"]] = None
    return list(prefixes)


def complete_prefixes(columns: Sequence[str]) -> list[str]:
    """Prefixes with all four feature columns - only these get the value rules."""
    present = set(columns)
    return [p for p in feature_prefixes(columns) if set(feature_columns(p)) <= present]


# ---------------------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------------------

def check_schema(columns: Sequence[str]) -> list[CheckResult]:
    present = set(columns)

    required = [*BASE_COLUMNS, *(c for p in TIER1_PREFIXES for c in feature_columns(p))]
    missing_required = [c for c in required if c not in present]

    tier2 = [p for p in feature_prefixes(columns) if p not in TIER1_PREFIXES]
    incomplete = {
        p: [c for c in feature_columns(p) if c not in present] for p in tier2
    }
    incomplete = {p: missing for p, missing in incomplete.items() if missing}

    return [
        CheckResult(
            "schema: base and tier-1 feature columns are present",
            Severity.ERROR,
            len(missing_required),
            "missing: " + ", ".join(missing_required) if missing_required else "",
        ),
        CheckResult(
            "schema: every tier-2 prefix has a distance and three band columns",
            Severity.ERROR,
            len(incomplete),
            "; ".join(f"{p} lacks {', '.join(m)}" for p, m in incomplete.items()),
        ),
    ]


# ---------------------------------------------------------------------------------------------
# Reconciliation: published table == import file
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Aggregates:
    """What reconciliation compares; computed once from the file and once from the table."""

    rows: int
    non_null: dict[str, int]
    sums: dict[str, float]
    date_min: Any = None
    date_max: Any = None


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
    ]


def file_aggregates(df: pd.DataFrame, columns: Sequence[str], numeric: Sequence[str]) -> Aggregates:
    has_date = "date" in columns
    dates = pd.to_datetime(df["date"]) if has_date else None
    return Aggregates(
        rows=len(df),
        non_null={c: int(df[c].notna().sum()) for c in columns},
        sums={c: float(df[c].astype("float64").sum()) for c in numeric},
        date_min=dates.min() if has_date else None,
        date_max=dates.max() if has_date else None,
    )


def reconciliation_sql(
    table: str, columns: Sequence[str], numeric: Sequence[str], dialect: Dialect
) -> str:
    """One row: row count, non-NULL count per column, sum per numeric column, min/max date."""
    q = dialect.quote
    expressions = ["COUNT(*)"]
    expressions += [f"COUNT({q(c)})" for c in columns]
    expressions += [f"SUM(CAST({q(c)} AS {dialect.float_type}))" for c in numeric]
    if "date" in columns:
        expressions += [f"MIN({q('date')})", f"MAX({q('date')})"]
    return "SELECT\n    " + ",\n    ".join(expressions) + f"\nFROM {table}"


def table_aggregates(row: Sequence[Any], columns: Sequence[str], numeric: Sequence[str]) -> Aggregates:
    values = list(row)
    rows = int(values.pop(0))
    non_null = {c: int(values.pop(0)) for c in columns}
    sums = {c: float(v) if (v := values.pop(0)) is not None else 0.0 for c in numeric}
    date_min = date_max = None
    if "date" in columns:
        date_min, date_max = values.pop(0), values.pop(0)
    return Aggregates(rows, non_null, sums, date_min, date_max)


def _timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value is pd.NaT:
        return None
    ts = pd.Timestamp(value)
    return ts.tz_convert(None) if ts.tzinfo is not None else ts


def _sums_equal(expected: float, actual: float) -> bool:
    return math.isclose(expected, actual, rel_tol=SUM_REL_TOLERANCE, abs_tol=SUM_REL_TOLERANCE)


def _first(items: Sequence[str], limit: int = 5) -> str:
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def compare(
    file_columns: Sequence[str],
    table_columns: Sequence[str],
    expected: Aggregates,
    actual: Aggregates,
    label: str = "",
) -> list[CheckResult]:
    """Reconciliation results; `label` prefixes every check name (e.g. TOKEN_LABEL)."""
    missing = [c for c in file_columns if c not in table_columns]
    extra = [c for c in table_columns if c not in file_columns]
    column_detail = "; ".join(
        part for part in (
            f"missing in table: {_first(missing)}" if missing else "",
            f"not in file: {_first(extra)}" if extra else "",
        ) if part
    )

    null_diff = [
        f"{c} {expected.non_null[c]} != {actual.non_null[c]}"
        for c in expected.non_null if expected.non_null[c] != actual.non_null.get(c)
    ]
    sum_diff = [
        f"{c} {expected.sums[c]:.6g} != {actual.sums[c]:.6g}"
        for c in expected.sums if not _sums_equal(expected.sums[c], actual.sums.get(c, math.nan))
    ]
    expected_dates = (_timestamp(expected.date_min), _timestamp(expected.date_max))
    actual_dates = (_timestamp(actual.date_min), _timestamp(actual.date_max))

    return [
        CheckResult(
            f"{label}reconciliation: row count equals the import file",
            Severity.ERROR,
            abs(expected.rows - actual.rows),
            f"file {expected.rows}, table {actual.rows}" if expected.rows != actual.rows else "",
        ),
        CheckResult(
            f"{label}reconciliation: column names equal the import file",
            Severity.ERROR,
            len(missing) + len(extra),
            column_detail,
        ),
        CheckResult(
            f"{label}reconciliation: non-NULL count per column equals the import file",
            Severity.ERROR,
            len(null_diff),
            _first(null_diff),
        ),
        CheckResult(
            f"{label}reconciliation: sum per numeric column equals the import file",
            Severity.ERROR,
            len(sum_diff),
            _first(sum_diff),
        ),
        CheckResult(
            f"{label}reconciliation: date range equals the import file",
            Severity.ERROR,
            0 if expected_dates == actual_dates else 1,
            "" if expected_dates == actual_dates
            else f"file {expected_dates[0]}..{expected_dates[1]}, table {actual_dates[0]}..{actual_dates[1]}",
        ),
    ]


# ---------------------------------------------------------------------------------------------
# Rules on the published values
# ---------------------------------------------------------------------------------------------

def _literal(value: str) -> str:
    if "'" in value or "\\" in value:
        raise ValueError(f"Unsupported character in SQL literal: {value!r}")
    return f"'{value}'"


def _count(condition: str) -> str:
    return f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END), 0)"


def _select(order: int, check: str, severity: Severity, failures: str, source: str, detail: str = "''") -> str:
    return (
        f"SELECT {order} AS check_order, {_literal(check)} AS check_name, "
        f"{_literal(severity.value)} AS severity,\n"
        f"       CAST({failures} AS BIGINT) AS failures, {detail} AS detail\n"
        f"FROM {source}"
    )


def rule_checks_sql(table: str, columns: Sequence[str], dialect: Dialect, today: dt.date) -> str:
    """One query, one row per check: (check_order, check_name, severity, failures, detail).

    failures = 0 means PASS. Runs as is in pgAdmin (`pfas_import verify --print-sql`). `today` bounds
    the date check, so the result does not depend on the database clock.
    """
    q = dialect.quote
    present = set(columns)
    selects: list[tuple[str, Severity, str, str, str]] = []

    def add(check: str, severity: Severity, failures: str, source: str = table, detail: str = "''") -> None:
        selects.append((check, severity, failures, source, detail))

    not_null = [c for c in NOT_NULL_COLUMNS if c in present]
    if not_null:
        add(
            f"no NULL in {', '.join(not_null)}",
            Severity.ERROR,
            _count(" OR ".join(f"{q(c)} IS NULL" for c in not_null)),
        )

    if {"lat", "lon"} <= present:
        lat, lon = q("lat"), q("lon")
        add(
            f"coordinates in mainland France (lat {MAINLAND_LAT[0]}..{MAINLAND_LAT[1]}, "
            f"lon {MAINLAND_LON[0]}..{MAINLAND_LON[1]})",
            Severity.ERROR,
            _count(
                f"{lat} < {MAINLAND_LAT[0]} OR {lat} > {MAINLAND_LAT[1]} "
                f"OR {lon} < {MAINLAND_LON[0]} OR {lon} > {MAINLAND_LON[1]}"
            ),
        )

    if LABEL_COLUMN in present:
        add(f"{LABEL_COLUMN} is not negative", Severity.ERROR, _count(f"{q(LABEL_COLUMN)} < 0"))

    if "date" in present:
        add(
            "date is not in the future",
            Severity.ERROR,
            _count(f"CAST({q('date')} AS DATE) > DATE '{today.isoformat()}'"),
        )

    if {"year", "date"} <= present:
        add(
            "year equals the year of date",
            Severity.ERROR,
            _count(f"{q('year')} <> EXTRACT(YEAR FROM {q('date')})"),
        )

    for prefix in complete_prefixes(columns):
        distance = q(f"{prefix}_{DISTANCE_SUFFIX}")
        bands = [q(c) for c in band_columns(prefix)]

        add(
            f"{prefix}: distance within 0..{MAX_DISTANCE_KM:,} km",
            Severity.ERROR,
            _count(f"{distance} < 0 OR {distance} > {MAX_DISTANCE_KM}"),
        )
        add(
            f"{prefix}: band counts are whole numbers >= 0",
            Severity.ERROR,
            _count(" OR ".join(f"{b} IS NULL OR {b} < 0 OR {b} <> FLOOR({b})" for b in bands)),
        )
        # Sources within `upper` km <=> the nearest source is at most `upper` km away.
        mismatches = []
        for i, (_, upper) in enumerate(BANDS_KM):
            within = " + ".join(bands[: i + 1])
            mismatches.append(
                f"({distance} <= {upper - BAND_TOLERANCE_KM} AND {within} < 1)"
                f" OR (({distance} IS NULL OR {distance} > {upper + BAND_TOLERANCE_KM}) AND {within} >= 1)"
            )
        add(
            f"{prefix}: nearest distance agrees with the band counts",
            Severity.ERROR,
            _count("\n          OR ".join(mismatches)),
        )

    for column, known in (("unit", KNOWN_UNITS), ("matrix", KNOWN_MATRICES)):
        if column in present:
            add(
                f"{column} is a known value",
                Severity.WARNING,
                _count(f"{q(column)} NOT IN ({', '.join(_literal(v) for v in known)})"),
            )

    group_columns = [c for c in columns if c != LABEL_COLUMN]
    if LABEL_COLUMN in present and group_columns:
        text = dialect.text_type
        groups = (
            f"(SELECT COUNT(*) AS n, COUNT(DISTINCT {q(LABEL_COLUMN)}) AS labels\n"
            f"      FROM {table}\n"
            f"      GROUP BY {', '.join(q(c) for c in group_columns)}\n"
            f"      HAVING COUNT(*) > 1) AS duplicate_groups"
        )
        add(
            f"rows are unique without {LABEL_COLUMN} (the label)",
            Severity.WARNING,
            "COALESCE(SUM(n - 1), 0)",
            source=groups,
            detail=(
                f"CAST(COUNT(*) AS {text}) || ' groups, ' || "
                f"CAST(COALESCE(SUM(CASE WHEN labels > 1 THEN 1 ELSE 0 END), 0) AS {text}) "
                f"|| ' with different {LABEL_COLUMN}'"
            ),
        )

    return _union(selects)


def _union(selects: Sequence[tuple[str, Severity, str, str, str]]) -> str:
    body = "\nUNION ALL\n".join(
        _select(order, check, severity, failures, source, detail)
        for order, (check, severity, failures, source, detail) in enumerate(selects, start=1)
    )
    return (
        "-- Checks of `pfas_import verify`. One row per check; failures = 0 means PASS.\n"
        f"{body}\nORDER BY check_order"
    )


# ---------------------------------------------------------------------------------------------
# Facility tokens
# ---------------------------------------------------------------------------------------------

def check_token_schema(columns: Sequence[str]) -> list[CheckResult]:
    missing = [c for c in TOKEN_COLUMNS if c not in set(columns)]
    return [
        CheckResult(
            f"{TOKEN_LABEL}schema: all token columns are present",
            Severity.ERROR,
            len(missing),
            "missing: " + ", ".join(missing) if missing else "",
        )
    ]


def token_rule_checks_sql(table: str, features_table: str, columns: Sequence[str], dialect: Dialect) -> str:
    """Rule checks of the facility token table; `features_table` is the published feature table.

    Same result shape as `rule_checks_sql`. Checks only the columns that exist; the schema check
    reports the missing ones.
    """
    q = dialect.quote
    present = set(columns)
    selects: list[tuple[str, Severity, str, str, str]] = []

    def add(check: str, failures: str, source: str = table) -> None:
        selects.append((f"{TOKEN_LABEL}{check}", Severity.ERROR, failures, source, "''"))

    not_null = [c for c in TOKEN_NOT_NULL_COLUMNS if c in present]
    if not_null:
        add(f"no NULL in {', '.join(not_null)}", _count(" OR ".join(f"{q(c)} IS NULL" for c in not_null)))

    if "rank" in present:
        add(
            f"rank is a whole number in 1..{MAX_TOKEN_RANK}",
            _count(f"{q('rank')} < 1 OR {q('rank')} > {MAX_TOKEN_RANK} OR {q('rank')} <> FLOOR({q('rank')})"),
        )

    if "distance_km" in present:
        distance = q("distance_km")
        add(
            f"distance within 0..{MAX_TOKEN_DISTANCE_KM} km",
            _count(f"{distance} < 0 OR {distance} > {MAX_TOKEN_DISTANCE_KM + BAND_TOLERANCE_KM}"),
        )

    for column in ("bearing_sin", "bearing_cos"):
        if column in present:
            add(f"{column} within -1..1", _count(f"ABS({q(column)}) > {1 + UNIT_TOLERANCE}"))

    if {"bearing_sin", "bearing_cos"} <= present:
        add(
            "bearing_sin² + bearing_cos² = 1",
            _count(f"ABS({q('bearing_sin')} * {q('bearing_sin')} + {q('bearing_cos')} * {q('bearing_cos')} - 1)"
                   f" > {10 * UNIT_TOLERANCE}"),
        )

    if {"location_id", "rank"} <= present:
        duplicates = (
            f"(SELECT COUNT(*) AS n FROM {table}\n"
            f"      GROUP BY {q('location_id')}, {q('rank')}\n"
            f"      HAVING COUNT(*) > 1) AS duplicate_ranks"
        )
        add("(location_id, rank) is unique", "COALESCE(SUM(n - 1), 0)", source=duplicates)

    if {"location_id", "rank", "distance_km"} <= present:
        # Rank 1 is the nearest facility: a higher rank is never closer than the rank before it.
        previous = (
            f"(SELECT {q('distance_km')} AS distance_km,\n"
            f"        LAG({q('distance_km')}) OVER (PARTITION BY {q('location_id')} ORDER BY {q('rank')})"
            f" AS previous_km,\n"
            f"        {q('rank')} AS token_rank,\n"
            f"        LAG({q('rank')}) OVER (PARTITION BY {q('location_id')} ORDER BY {q('rank')}) AS previous_rank\n"
            f"      FROM {table}) AS ranked"
        )
        add(
            "ranks are consecutive from 1 and sorted by distance",
            _count(
                "(previous_rank IS NULL AND token_rank <> 1) "
                "OR (previous_rank IS NOT NULL AND token_rank <> previous_rank + 1) "
                f"OR (previous_km IS NOT NULL AND distance_km < previous_km - {BAND_TOLERANCE_KM})"
            ),
            source=previous,
        )

    if "location_id" in present:
        # A join instead of NOT IN (subquery): BigQuery rejects subqueries inside aggregate functions.
        joined = (
            f"(SELECT known.location_id AS known_id\n"
            f"      FROM {table} AS tokens\n"
            f"      LEFT JOIN (SELECT DISTINCT {q('location_id')} AS location_id FROM {features_table}) AS known\n"
            f"        ON tokens.{q('location_id')} = known.location_id) AS token_locations"
        )
        add("every location_id exists in the feature table", _count("known_id IS NULL"), source=joined)

    if not selects:
        return ""
    return _union(selects)


def rule_results(rows: Iterable[Sequence[Any]]) -> list[CheckResult]:
    return [
        CheckResult(str(check), Severity(str(severity)), int(failures), str(detail or ""))
        for _, check, severity, failures, detail in rows
    ]
