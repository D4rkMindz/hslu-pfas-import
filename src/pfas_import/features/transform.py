"""Implements the whole data transformation process."""
import logging
import re
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree


logger = logging.getLogger(__name__)

EARTH_RADIUS_KM: float = 6371.0088
BATCH_SIZE: int = 10_000
COUNT_BANDS_KM: tuple[tuple[float, float], ...] = (
    (0.0, 5.0),
    (5.0, 25.0),
    (25.0, 50.0),
)


def _clean_invalid_records(df: pd.DataFrame) -> pd.DataFrame:
    """Remove records without coordinates or measurement dates."""
    return df.dropna(subset=["lat", "lon", "date"]).copy()


def _normalize_feature_name(value: str) -> str:
    """Convert a source-group name into a readable column-name prefix."""
    normalized: str = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_")


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
            ["date", "lat", "lon", "name", "city", "pfas_sum"],
        ]
        .copy()
        .reset_index(drop=True)
    )

    # Source records must not be filtered by measurement-date requirements.
    producers_df: pd.DataFrame = df.loc[
        df["category"] == "PFAS production facility"
    ]

    users_df: pd.DataFrame = df.loc[
        df["category"] == "Known PFAS user"
    ]

    presumptives_df: pd.DataFrame = df.loc[
        df["category"] == "Presumptive"
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


def _extract_tier2_features(
    df: pd.DataFrame,
    feature_df: pd.DataFrame,
) -> pd.DataFrame:
    """Extract TierB: proximity to dynamic presumptive source groups."""

    presumptive_df: pd.DataFrame = df.loc[
        df["category"] == "Presumptive"
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

    presumptive_df = presumptive_df.loc[
        presumptive_df["_source_group"].notna()
    ]

    valid_measurements, measurement_coords = (
        _get_measurement_coordinates(feature_df)
    )

    # Normalize all source-group names and detect naming collisions.
    group_names: dict[str, str] = {}

    for source_group in sorted(
        presumptive_df["_source_group"].unique().tolist()
    ):
        prefix: str = _normalize_feature_name(str(source_group))

        if not prefix:
            raise ValueError(
                f"Source group {source_group!r} produces an empty prefix."
            )

        if prefix in group_names and group_names[prefix] != source_group:
            raise ValueError(
                "Source-group naming collision: "
                f"{group_names[prefix]!r} and {source_group!r} "
                f"both normalize to {prefix!r}."
            )

        group_names[prefix] = str(source_group)

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


def load_and_transform(df: pd.DataFrame) -> pd.DataFrame:
    """Extract TierA and TierB features from the raw dataset."""

    feature_df: pd.DataFrame = _extract_tier1_features(df)
    feature_df = _extract_tier2_features(df, feature_df)

    feature_df['date'] = pd.to_datetime(feature_df["date"])

    feature_df = _compress_dataframe(feature_df)

    return feature_df
