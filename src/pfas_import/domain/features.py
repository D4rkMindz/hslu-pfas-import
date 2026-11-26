"""Implements the whole data transformation process.

`transform_all` builds the two published tables from the raw dataset:

- `features` (pfas_features): one row per measurement with the nearest-source distances and band counts
  per source group, plus `year` and `location_id`
- `facility_tokens` (pfas_facility_tokens): one row per (location, rank) for the K_FACILITIES nearest
  facilities within MAX_FACILITY_KM, the input of the facility transformer in `pfas_trace`

`transform` returns only the features, for callers that need just that table.
"""
import logging
import re
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from pfas_import.domain.errors import EmptySourceGroupPrefix, SourceGroupCollision

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM: float = 6371.0088
BATCH_SIZE: int = 10_000
COUNT_BANDS_KM: tuple[tuple[float, float], ...] = (
    (0.0, 5.0),
    (5.0, 25.0),
    (25.0, 50.0),
)
# Mainland France including Corsica. Drops the overseas departments (Guadeloupe, Martinique,
# French Guiana, Réunion, Mayotte) and a few mainland records with broken coordinates.
MAINLAND_FRANCE_LAT: tuple[float, float] = (41.3, 51.1)
MAINLAND_FRANCE_LON: tuple[float, float] = (-5.2, 9.6)
# PostgreSQL's identifier limit minus the longest feature suffix (`_count_25_50km`).
POSTGRES_MAX_IDENTIFIER_LENGTH: int = 63
MAX_PREFIX_LENGTH: int = POSTGRES_MAX_IDENTIFIER_LENGTH - max(
    len(f"_count_{lower:g}_{upper:g}km") for lower, upper in COUNT_BANDS_KM
)
NOT_ELSEWHERE_CLASSIFIED: str = "_n_e_c"
MEASUREMENT_COLUMNS: list[str] = [
    "date", "year", "lat", "lon", "name", "city", "pfas_sum", "matrix", "unit", "type", "sector",
]
LOCATION_ID_COLUMN: str = "location_id"

# Facility tokens: the K nearest facilities of a measurement location, up to MAX_FACILITY_KM away.
# With K=32 the median location keeps all facilities within 50 km (median count 17, p90 51).
K_FACILITIES: int = 32
MAX_FACILITY_KM: float = 100.0
PRODUCER_CATEGORY: str = "PFAS production facility"
USER_CATEGORY: str = "Known PFAS user"
PRESUMPTIVE_CATEGORY: str = "Presumptive"
# Token groups of the tier-1 categories; presumptive facilities use their tier-2 prefix.
TIER1_GROUPS: dict[str, str] = {PRODUCER_CATEGORY: "producer", USER_CATEGORY: "user"}
FACILITY_TOKEN_COLUMNS: list[str] = [
    LOCATION_ID_COLUMN, "rank", "source_group", "category", "type",
    "distance_km", "bearing_sin", "bearing_cos",
]


@dataclass(frozen=True)
class TransformOutput:
    """Both published tables of one transform run; they share `location_id`."""

    features: pd.DataFrame
    facility_tokens: pd.DataFrame


def _filter_france(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the records of France. Runs before `transform`, like the France filter in `pfas_trace`."""
    return df.query('country=="France"')


def _filter_mainland_france(df: pd.DataFrame) -> pd.DataFrame:
    df = _filter_france(df)
    """Keep measurements and sources inside mainland France (incl. Corsica)."""
    lat: pd.Series = pd.to_numeric(df["lat"], errors="coerce")
    lon: pd.Series = pd.to_numeric(df["lon"], errors="coerce")
    inside: pd.Series = lat.between(*MAINLAND_FRANCE_LAT) & lon.between(*MAINLAND_FRANCE_LON)

    logger.info("Mainland France filter: keeping %s of %s records", int(inside.sum()), len(df))
    return df.loc[inside]


def _clean_invalid_records(df: pd.DataFrame) -> pd.DataFrame:
    """Remove records without coordinates or measurement dates."""
    return df.dropna(subset=["lat", "lon", "date"]).copy()


def _normalize_feature_name(value: str) -> str:
    """Convert a source-group name into a readable column-name prefix.

    PostgreSQL silently cuts identifiers to 63 characters, so a prefix that is too long for its
    longest column (`<prefix>_count_25_50km`) is shortened: first the "not elsewhere classified"
    suffix `_n_e_c` is dropped, then it is cut to the maximum length.
    """
    normalized: str = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = normalized.strip("_")

    if len(normalized) > MAX_PREFIX_LENGTH:
        normalized = normalized.removesuffix(NOT_ELSEWHERE_CLASSIFIED)
        normalized = normalized[:MAX_PREFIX_LENGTH].rstrip("_")

    return normalized


def _add_source_features(
    feature_df: pd.DataFrame,
    source_df: pd.DataFrame,
    prefix: str,
    valid_measurements: np.ndarray,
    measurement_coords: np.ndarray,
) -> None:
    """Add nearest-source distance and counts in disjoint distance bands."""

    distance_column: str = f"{prefix}_distance_min"

    feature_df[distance_column] = np.nan

    for lower_km, upper_km in COUNT_BANDS_KM:
        feature_name: str = (
            f"{prefix}_count_{lower_km:g}_{upper_km:g}km"
        )
        feature_df[feature_name] = np.nan

    source_coordinates: pd.DataFrame = source_df[
        ["lat", "lon"]
    ].apply(pd.to_numeric, errors="coerce")

    valid_sources: pd.Series = (
        source_coordinates.notna().all(axis=1)
        & source_coordinates["lat"].between(-90, 90)
        & source_coordinates["lon"].between(-180, 180)
    )

    source_coords: np.ndarray = np.radians(
        source_coordinates.loc[valid_sources].to_numpy(
            dtype=np.float64
        )
    )

    valid_indices: np.ndarray = np.flatnonzero(valid_measurements)

    if len(source_coords) == 0:
        for lower_km, upper_km in COUNT_BANDS_KM:
            feature_df.loc[
                valid_indices,
                f"{prefix}_count_{lower_km:g}_{upper_km:g}km",
            ] = 0
        return

    tree: BallTree = BallTree(source_coords, metric="haversine")

    for start in range(0, len(valid_indices), BATCH_SIZE):
        indices: np.ndarray = valid_indices[
            start : start + BATCH_SIZE
        ]
        batch_coords: np.ndarray = measurement_coords[indices]

        distances_rad, _ = tree.query(batch_coords, k=1)

        feature_df.loc[indices, distance_column] = (
            distances_rad[:, 0] * EARTH_RADIUS_KM
        )

        # Count sources cumulatively within each upper boundary.
        cumulative_counts: dict[float, np.ndarray] = {}

        for _, upper_km in COUNT_BANDS_KM:
            counts: np.ndarray = np.asarray(
                tree.query_radius(
                    batch_coords,
                    r=upper_km / EARTH_RADIUS_KM,
                    count_only=True,
                ),
                dtype=np.int64,
            )
            cumulative_counts[upper_km] = counts

        # Convert cumulative counts into disjoint distance bands.
        for band_index, (lower_km, upper_km) in enumerate(
            COUNT_BANDS_KM
        ):
            upper_counts: np.ndarray = cumulative_counts[upper_km]

            if band_index == 0:
                band_counts: np.ndarray = upper_counts
            else:
                lower_counts: np.ndarray = cumulative_counts[lower_km]
                band_counts = upper_counts - lower_counts

            feature_df.loc[
                indices,
                f"{prefix}_count_{lower_km:g}_{upper_km:g}km",
            ] = band_counts


def _get_measurement_coordinates(
    feature_df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    """Return valid measurement indices and coordinates in radians."""

    coordinates: pd.DataFrame = feature_df[
        ["lat", "lon"]
    ].apply(pd.to_numeric, errors="coerce")

    valid_measurements: np.ndarray = (
        coordinates.notna().all(axis=1)
        & coordinates["lat"].between(-90, 90)
        & coordinates["lon"].between(-180, 180)
    ).to_numpy()

    measurement_coords: np.ndarray = np.radians(
        coordinates.to_numpy(dtype=np.float64)
    )

    return valid_measurements, measurement_coords


def _extract_tier1_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract TierA: proximity to PFAS source categories."""

    cleaned_df: pd.DataFrame = _clean_invalid_records(df)

    tier1_feature_df: pd.DataFrame = (
        cleaned_df.loc[
            cleaned_df["category"] == "Measurement",
            MEASUREMENT_COLUMNS,
        ]
        .copy()
        .reset_index(drop=True)
    )

    # Source records must not be filtered by measurement-date requirements.
    producers_df: pd.DataFrame = df.loc[
        df["category"] == PRODUCER_CATEGORY
    ]

    users_df: pd.DataFrame = df.loc[
        df["category"] == USER_CATEGORY
    ]

    presumptives_df: pd.DataFrame = df.loc[
        df["category"] == PRESUMPTIVE_CATEGORY
    ]

    valid_measurements, measurement_coords = (
        _get_measurement_coordinates(tier1_feature_df)
    )

    source_categories: dict[str, pd.DataFrame] = {
        "producer": producers_df,
        "user": users_df,
        "presumptive": presumptives_df,
    }

    for prefix, source_df in source_categories.items():
        _add_source_features(
            feature_df=tier1_feature_df,
            source_df=source_df,
            prefix=prefix,
            valid_measurements=valid_measurements,
            measurement_coords=measurement_coords,
        )

    return tier1_feature_df


def _presumptive_with_groups(df: pd.DataFrame) -> pd.DataFrame:
    """Presumptive sources with their tier-2 source group in `_source_group`; rows without one are dropped."""
    presumptive_df: pd.DataFrame = df.loc[
        df["category"] == PRESUMPTIVE_CATEGORY
    ].copy()

    # Use sector when available; otherwise fall back to type.
    sectors: pd.Series = (
        presumptive_df["sector"]
        .astype("string")
        .str.strip()
    )
    sectors = sectors.mask(sectors == "", pd.NA)

    source_types: pd.Series = (
        presumptive_df["type"]
        .astype("string")
        .str.strip()
    )
    source_types = source_types.mask(source_types == "", pd.NA)

    presumptive_df["_source_group"] = sectors.fillna(source_types)

    return presumptive_df.loc[
        presumptive_df["_source_group"].notna()
    ]


def _tier2_prefixes(presumptive_df: pd.DataFrame) -> dict[str, str]:
    """Map every tier-2 column prefix to its source group; fail on empty prefixes and collisions."""
    group_names: dict[str, str] = {}

    for source_group in sorted(
        presumptive_df["_source_group"].unique().tolist()
    ):
        prefix: str = _normalize_feature_name(str(source_group))

        if not prefix:
            raise EmptySourceGroupPrefix(
                f"Source group {source_group!r} produces an empty prefix."
            )

        if prefix in group_names and group_names[prefix] != source_group:
            raise SourceGroupCollision(
                "Source-group naming collision: "
                f"{group_names[prefix]!r} and {source_group!r} "
                f"both normalize to {prefix!r}."
            )

        group_names[prefix] = str(source_group)

    return group_names


def _extract_tier2_features(
    df: pd.DataFrame,
    feature_df: pd.DataFrame,
) -> pd.DataFrame:
    """Extract TierB: proximity to dynamic presumptive source groups."""

    presumptive_df = _presumptive_with_groups(df)

    valid_measurements, measurement_coords = (
        _get_measurement_coordinates(feature_df)
    )

    group_names = _tier2_prefixes(presumptive_df)

    for prefix, source_group in group_names.items():
        source_df: pd.DataFrame = presumptive_df.loc[
            presumptive_df["_source_group"] == source_group
        ]

        _add_source_features(
            feature_df=feature_df,
            source_df=source_df,
            prefix=prefix,
            valid_measurements=valid_measurements,
            measurement_coords=measurement_coords,
        )

    return feature_df

def _compress_dataframe(df : pd.DataFrame) -> pd.DataFrame:
    """Reduces size of dataframe by downcasting numerical columns.

    Args:
        df (pd.DataFrame): DataFrame to compress.

    Returns:
        pd.DataFrame: Compressed DataFrame.
    """
    def _human_readable_size(num_bytes: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if num_bytes < 1024:
                return f"{num_bytes:.2f} {unit}"
            num_bytes //= 1024
        return f"{num_bytes:.2f} TB"

    in_bytes = df.memory_usage(index=True, deep=True).sum()
    logger.info("unoptimized dataframe size: %s",_human_readable_size(int(in_bytes)))

    downcast_types: list[Literal["float", "integer"]] = ["float", "integer"]

    for downcast_type in downcast_types:
        cols: list[str] = list(df.select_dtypes(include=downcast_type).columns)

        for col in cols:
            df[col] = pd.to_numeric(df[col], downcast=downcast_type)

    out_bytes = df.memory_usage(index=True, deep=True).sum()
    reduction_pct = 0.0
    if in_bytes:
        reduction_pct = (1 - (out_bytes / in_bytes)) * 100

    logger.info("optimized dataframe size: %s (-%.2f%%)",
                _human_readable_size(int(out_bytes)),reduction_pct)

    return df


def _add_location_ids(feature_df: pd.DataFrame) -> None:
    """Number the unique (lat, lon) of the measurements 0..n-1 in sorted order (dense rank)."""
    feature_df[LOCATION_ID_COLUMN] = (
        feature_df.groupby(["lat", "lon"], sort=True).ngroup().astype(np.int64)
    )


def _facilities(df: pd.DataFrame) -> pd.DataFrame:
    """All facilities with valid coordinates: lat, lon, source_group, category, type."""
    tier1 = df.loc[df["category"].isin(list(TIER1_GROUPS))].copy()
    tier1["source_group"] = tier1["category"].map(TIER1_GROUPS)

    presumptive = _presumptive_with_groups(df)
    prefix_of = {group: prefix for prefix, group in _tier2_prefixes(presumptive).items()}
    presumptive["source_group"] = presumptive["_source_group"].astype(str).map(prefix_of)

    facilities = pd.concat([tier1, presumptive], ignore_index=True)
    facilities["lat"] = pd.to_numeric(facilities["lat"], errors="coerce")
    facilities["lon"] = pd.to_numeric(facilities["lon"], errors="coerce")
    valid = (
        facilities["lat"].between(-90, 90)
        & facilities["lon"].between(-180, 180)
    )
    facilities = facilities.loc[valid, ["lat", "lon", "source_group", "category", "type"]]
    facilities["type"] = facilities["type"].astype("string").str.strip()
    # Sorted so that ties in distance always resolve to the same facility order.
    return facilities.sort_values(
        ["lat", "lon", "source_group", "category", "type"], kind="mergesort"
    ).reset_index(drop=True)


def _bearings(from_rad: np.ndarray, to_rad: np.ndarray) -> np.ndarray:
    """Initial great-circle bearing (radians, clockwise from north) from each `from` point to each `to` point.

    Both arrays hold (lat, lon) in radians on the last axis and broadcast against each other.
    """
    lat1, lon1 = from_rad[..., 0], from_rad[..., 1]
    lat2, lon2 = to_rad[..., 0], to_rad[..., 1]
    d_lon = lon2 - lon1
    return np.arctan2(
        np.sin(d_lon) * np.cos(lat2),
        np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(d_lon),
    )


def _facility_tokens(mainland_df: pd.DataFrame, feature_df: pd.DataFrame) -> pd.DataFrame:
    """Long table of the K_FACILITIES nearest facilities within MAX_FACILITY_KM per measurement location.

    One row per (location_id, rank), rank 1 = nearest. Locations without a facility in range have no
    rows. Must run before `_compress_dataframe`, which may round the coordinates.
    """
    locations = (
        feature_df[[LOCATION_ID_COLUMN, "lat", "lon"]]
        .drop_duplicates(LOCATION_ID_COLUMN)
        .sort_values(LOCATION_ID_COLUMN)
    )
    facilities = _facilities(mainland_df)
    empty = pd.DataFrame({column: pd.Series(dtype=dtype) for column, dtype in (
        (LOCATION_ID_COLUMN, "int64"), ("rank", "int64"), ("source_group", "string"),
        ("category", "string"), ("type", "string"), ("distance_km", "float64"),
        ("bearing_sin", "float64"), ("bearing_cos", "float64"),
    )})
    if facilities.empty or locations.empty:
        return empty

    facility_rad = np.radians(facilities[["lat", "lon"]].to_numpy(dtype=np.float64))
    location_rad = np.radians(locations[["lat", "lon"]].to_numpy(dtype=np.float64))
    location_ids = locations[LOCATION_ID_COLUMN].to_numpy()
    k = min(K_FACILITIES, len(facilities))
    tree = BallTree(facility_rad, metric="haversine")

    parts: list[pd.DataFrame] = []
    for start in range(0, len(locations), BATCH_SIZE):
        batch = location_rad[start:start + BATCH_SIZE]
        distances_rad, indices = tree.query(batch, k=k)
        distances_km = distances_rad * EARTH_RADIUS_KM
        in_range = distances_km <= MAX_FACILITY_KM

        rows, slots = np.nonzero(in_range)
        facility_index = indices[rows, slots]
        bearings = _bearings(batch[rows], facility_rad[facility_index])

        chosen = facilities.iloc[facility_index]
        parts.append(pd.DataFrame({
            LOCATION_ID_COLUMN: location_ids[start:start + BATCH_SIZE][rows],
            "rank": slots + 1,
            "source_group": chosen["source_group"].to_numpy(),
            "category": chosen["category"].to_numpy(),
            "type": chosen["type"].to_numpy(),
            "distance_km": distances_km[rows, slots],
            "bearing_sin": np.sin(bearings),
            "bearing_cos": np.cos(bearings),
        }))

    tokens = pd.concat(parts, ignore_index=True) if parts else empty
    for column in ("source_group", "category", "type"):
        tokens[column] = tokens[column].astype("string")
    return tokens[FACILITY_TOKEN_COLUMNS]


def transform_all(df: pd.DataFrame) -> TransformOutput:
    """Extract the feature table and the facility tokens from the raw dataset."""
    mainland_df = _filter_mainland_france(df)

    logger.info("Starting tier1 feature extraction...")
    feature_df: pd.DataFrame = _extract_tier1_features(mainland_df)

    logger.info("Starting tier2 feature extraction...")
    feature_df = _extract_tier2_features(mainland_df, feature_df)

    logger.info("Converting to datetime and adding year and location ids...")
    feature_df["date"] = pd.to_datetime(feature_df["date"])
    feature_df["year"] = pd.to_numeric(feature_df["year"], errors="coerce").astype("Int64")
    _add_location_ids(feature_df)

    logger.info("Starting facility token extraction...")
    tokens_df = _facility_tokens(mainland_df, feature_df)
    logger.info(
        "Facility tokens: %s rows for %s of %s locations",
        len(tokens_df), tokens_df[LOCATION_ID_COLUMN].nunique(), feature_df[LOCATION_ID_COLUMN].nunique(),
    )

    logger.info("Compressing dataframes...")
    feature_df = _compress_dataframe(feature_df)
    tokens_df = _compress_dataframe(tokens_df)

    return TransformOutput(features=feature_df, facility_tokens=tokens_df)


def transform(df: pd.DataFrame) -> pd.DataFrame:
    """Extract TierA and TierB features from the raw dataset (the `features` of `transform_all`)."""
    return transform_all(df).features
