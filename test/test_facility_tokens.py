"""`transform_all`: the `year` and `location_id` of the features and the facility token table.

Every raw frame here is a handful of synthetic records around Paris, so each test pins down one rule
of the spec: the location ids, the K_FACILITIES and MAX_FACILITY_KM cuts, the rank order, the source
groups, the bearings and the mainland filter. Expected distances come from an independent haversine.
"""
import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from pfas_import.domain.features import (
    FACILITY_TOKEN_COLUMNS,
    K_FACILITIES,
    MAX_FACILITY_KM,
    TransformOutput,
    transform,
    transform_all,
)

EARTH_RADIUS_KM = 6371.0088
PARIS = (48.85, 2.35)
PRODUCER = "PFAS production facility"
USER = "Known PFAS user"
PRESUMPTIVE = "Presumptive"


def measurement(lat, lon, name="m", date="2024-05-01", country="France"):
    return {
        "category": "Measurement", "country": country, "lat": lat, "lon": lon, "date": date,
        "year": int(date[:4]) if date else None, "name": name, "city": "x", "pfas_sum": 1.0,
        "matrix": "Groundwater", "unit": "ng/l", "type": "Sampling location", "sector": None,
    }


def facility(category, lat, lon, type="t", sector=None, country="France"):
    return {
        "category": category, "country": country, "lat": lat, "lon": lon, "date": None, "year": None,
        "name": f"{category} {lat} {lon}", "city": "x", "pfas_sum": None, "matrix": None, "unit": None,
        "type": type, "sector": sector,
    }


def raw(*records) -> pd.DataFrame:
    return pd.DataFrame(list(records))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def north_of(point: tuple[float, float], km: float) -> tuple[float, float]:
    """The point `km` due north of `point` (along the meridian)."""
    return point[0] + math.degrees(km / EARTH_RADIUS_KM), point[1]


def tokens_of(raw_df: pd.DataFrame) -> pd.DataFrame:
    return transform_all(raw_df).facility_tokens


class TestTransformAll:
    def test_returns_a_frozen_output_with_both_tables(self):
        output = transform_all(raw(measurement(*PARIS), facility(PRODUCER, 48.90, 2.40)))

        assert isinstance(output, TransformOutput)
        assert isinstance(output.features, pd.DataFrame)
        assert isinstance(output.facility_tokens, pd.DataFrame)
        with pytest.raises(dataclasses.FrozenInstanceError):
            output.features = pd.DataFrame()  # type: ignore[misc]

    def test_transform_returns_the_features_of_transform_all(self):
        raw_df = raw(
            measurement(*PARIS, name="a"),
            measurement(45.76, 4.83, name="b"),
            facility(PRODUCER, 48.90, 2.40),
            facility(PRESUMPTIVE, 45.70, 4.80, sector="Chemical industry"),
        )

        pd.testing.assert_frame_equal(transform(raw_df), transform_all(raw_df).features)

    def test_token_columns(self):
        tokens = tokens_of(raw(measurement(*PARIS), facility(PRODUCER, 48.90, 2.40)))

        assert FACILITY_TOKEN_COLUMNS == [
            "location_id", "rank", "source_group", "category", "type", "distance_km", "bearing_sin", "bearing_cos",
        ]
        assert list(tokens.columns) == FACILITY_TOKEN_COLUMNS

    def test_constants(self):
        assert (K_FACILITIES, MAX_FACILITY_KM) == (32, 100)


class TestYearAndLocationId:
    @staticmethod
    def features() -> pd.DataFrame:
        return transform_all(raw(
            measurement(48.85, 2.35, name="paris 2024", date="2024-05-01"),
            measurement(45.76, 4.83, name="lyon", date="2021-03-02"),
            measurement(48.85, 2.35, name="paris 2019", date="2019-07-08"),
            measurement(48.85, 2.30, name="paris west", date="2022-11-30"),
            measurement(48.86, 2.30, name="paris north west", date="2023-01-15"),
            # Dropped before numbering: they would take ids 0 and 1 if they were counted.
            measurement(16.25, -61.55, name="guadeloupe"),
            measurement(41.0, 2.0, name="no date", date=None),
            facility(PRODUCER, 48.90, 2.40),
        )).features.set_index("name")

    def test_year_comes_from_the_raw_year(self):
        features = self.features()

        assert pd.api.types.is_integer_dtype(features["year"])
        assert features["year"].to_dict() == {
            "paris 2024": 2024, "lyon": 2021, "paris 2019": 2019, "paris west": 2022, "paris north west": 2023,
        }

    def test_year_equals_the_year_of_date(self):
        features = self.features()

        assert (features["year"] == pd.to_datetime(features["date"]).dt.year).all()

    @pytest.mark.parametrize(
        ("name", "location_id"),
        [
            ("lyon", 0),  # lat 45.76
            ("paris west", 1),  # lat 48.85, lon 2.30
            ("paris 2024", 2),  # lat 48.85, lon 2.35
            ("paris 2019", 2),  # same coordinates as "paris 2024"
            ("paris north west", 3),  # lat 48.86
        ],
    )
    def test_location_id_is_the_dense_rank_of_lat_then_lon(self, name, location_id):
        features = self.features()

        assert pd.api.types.is_integer_dtype(features["location_id"])
        assert features.loc[name, "location_id"] == location_id

    def test_location_ids_are_0_to_n_minus_1(self):
        assert sorted(self.features()["location_id"].unique()) == [0, 1, 2, 3]

    def test_dropped_records_have_no_feature_row(self):
        assert "guadeloupe" not in self.features().index
        assert "no date" not in self.features().index


class TestNearestFacilities:
    def test_keeps_the_32_nearest_of_40_in_range(self):
        distances = {f"f{i:02d}": 2.0 * i for i in range(1, 41)}  # 2..80 km due north
        # Shuffled, so the ranks cannot come from the input order.
        names = sorted(distances, key=lambda n: (int(n[1:]) * 7) % 41)
        tokens = tokens_of(raw(
            measurement(*PARIS),
            *(facility(PRODUCER, *north_of(PARIS, distances[n]), type=n) for n in names),
        ))

        assert len(tokens) == K_FACILITIES
        assert tokens["rank"].tolist() == list(range(1, 33))
        assert tokens["type"].tolist() == [f"f{i:02d}" for i in range(1, 33)]
        assert tokens["distance_km"].to_numpy() == pytest.approx([2.0 * i for i in range(1, 33)], abs=1e-3)

    def test_cuts_at_100_km_and_keeps_fewer_than_32(self):
        tokens = tokens_of(raw(
            measurement(*PARIS),
            *(facility(PRODUCER, *north_of(PARIS, km), type=f"{km:g} km") for km in (5.0, 20.0, 60.0, 99.9999)),
            *(facility(USER, *north_of(PARIS, km), type=f"{km:g} km") for km in (100.1, 150.0, 300.0)),
        ))

        # A point placed at exactly 100 km computes to a hair above 100.0, so the border case uses 99.9999 km.
        assert tokens["type"].tolist() == ["5 km", "20 km", "60 km", "99.9999 km"]
        assert tokens["rank"].tolist() == [1, 2, 3, 4]
        assert (tokens["distance_km"] <= MAX_FACILITY_KM).all()

    def test_distance_is_the_haversine_distance(self):
        target = (48.70, 2.60)
        tokens = tokens_of(raw(measurement(*PARIS), facility(PRODUCER, *target)))

        assert tokens["distance_km"].tolist() == pytest.approx([haversine_km(PARIS, target)], abs=1e-3)

    def test_ranks_are_consecutive_by_distance_per_location(self):
        marseille = (43.30, 5.37)
        tokens = tokens_of(raw(
            measurement(*PARIS),
            measurement(*marseille),
            facility(PRODUCER, *north_of(PARIS, 30.0), type="paris 30"),
            facility(USER, *north_of(PARIS, 3.0), type="paris 3"),
            facility(PRODUCER, *north_of(marseille, 50.0), type="marseille 50"),
            facility(PRESUMPTIVE, *north_of(PARIS, 10.0), sector="Chemical industry", type="paris 10"),
            facility(USER, *north_of(marseille, 7.0), type="marseille 7"),
        ))

        by_location = {
            location: group.sort_values("rank")[["rank", "type"]].values.tolist()
            for location, group in tokens.groupby("location_id")
        }
        # Marseille (lat 43.30) has location_id 0, Paris (lat 48.85) location_id 1.
        assert by_location == {
            0: [[1, "marseille 7"], [2, "marseille 50"]],
            1: [[1, "paris 3"], [2, "paris 10"], [3, "paris 30"]],
        }

    def test_no_rows_for_a_location_without_a_facility_in_range(self):
        marseille = (43.30, 5.37)
        output = transform_all(raw(
            measurement(*PARIS, name="paris"),
            measurement(*marseille, name="marseille"),
            facility(PRODUCER, *north_of(marseille, 20.0)),  # about 640 km from Paris
        ))

        paris_id = output.features.set_index("name").loc["paris", "location_id"]
        marseille_id = output.features.set_index("name").loc["marseille", "location_id"]
        assert paris_id not in set(output.facility_tokens["location_id"])
        assert output.facility_tokens["location_id"].tolist() == [marseille_id]

    def test_no_facility_at_all_gives_an_empty_table(self):
        tokens = tokens_of(raw(measurement(*PARIS)))

        assert tokens.empty
        assert list(tokens.columns) == FACILITY_TOKEN_COLUMNS

    def test_every_token_location_exists_in_the_features(self):
        output = transform_all(raw(
            measurement(*PARIS),
            measurement(45.76, 4.83),
            measurement(43.30, 5.37),
            facility(PRODUCER, 48.90, 2.40),
            facility(USER, 45.70, 4.80),
        ))

        assert set(output.facility_tokens["location_id"]) <= set(output.features["location_id"])
        assert len(output.facility_tokens) == 2


class TestSourceGroups:
    @pytest.mark.parametrize(
        ("record", "source_group", "category", "type"),
        [
            (facility(PRODUCER, 48.90, 2.40, type="Fluorochemicals"), "producer", PRODUCER, "Fluorochemicals"),
            (facility(USER, 48.90, 2.40, type="Textiles"), "user", USER, "Textiles"),
            (
                facility(PRESUMPTIVE, 48.90, 2.40, sector="Chemical industry", type="Plant"),
                "chemical_industry", PRESUMPTIVE, "Plant",
            ),
            (
                facility(PRESUMPTIVE, 48.90, 2.40, sector=None, type="Waste water treatment"),
                "waste_water_treatment", PRESUMPTIVE, "Waste water treatment",
            ),
            (
                facility(PRESUMPTIVE, 48.90, 2.40, sector="  ", type="  Airport  "),
                "airport", PRESUMPTIVE, "Airport",
            ),
            (
                facility(PRESUMPTIVE, 48.90, 2.40, sector="Treatment and coating of metals", type=None),
                "treatment_and_coating_of_metals", PRESUMPTIVE, None,
            ),
        ],
    )
    def test_group_category_and_type(self, record, source_group, category, type):
        tokens = tokens_of(raw(measurement(*PARIS), record))

        assert len(tokens) == 1
        token = tokens.iloc[0]
        assert token["source_group"] == source_group
        assert token["category"] == category
        if type is None:
            assert pd.isna(token["type"])
        else:
            assert token["type"] == type

    def test_presumptive_group_matches_the_tier2_feature_prefix(self):
        output = transform_all(raw(
            measurement(*PARIS),
            facility(PRESUMPTIVE, 48.90, 2.40, sector="Manufacture of basic chemicals, n.e.c."),
        ))

        group = output.facility_tokens.iloc[0]["source_group"]
        assert f"{group}_distance_min" in output.features.columns

    @pytest.mark.parametrize(("sector", "type"), [(None, None), ("", ""), ("  ", None)])
    def test_presumptive_without_sector_and_type_is_no_facility(self, sector, type):
        tokens = tokens_of(raw(
            measurement(*PARIS),
            facility(PRESUMPTIVE, 48.90, 2.40, sector=sector, type=type),
            facility(PRODUCER, 49.00, 2.40, type="kept"),
        ))

        assert tokens["type"].tolist() == ["kept"]

    def test_measurements_are_no_facilities(self):
        tokens = tokens_of(raw(measurement(*PARIS), measurement(48.86, 2.36)))

        assert tokens.empty


class TestBearings:
    @pytest.mark.parametrize(
        ("direction", "offset", "expected"),
        [
            ("north", (0.1, 0.0), (0.0, 1.0)),
            ("east", (0.0, 0.1), (1.0, 0.0)),
            ("south", (-0.1, 0.0), (0.0, -1.0)),
            ("west", (0.0, -0.1), (-1.0, 0.0)),
        ],
    )
    def test_cardinal_directions(self, direction, offset, expected):
        target = (PARIS[0] + offset[0], PARIS[1] + offset[1])

        token = tokens_of(raw(measurement(*PARIS), facility(PRODUCER, *target))).iloc[0]

        assert (token["bearing_sin"], token["bearing_cos"]) == pytest.approx(expected, abs=1e-3), direction

    def test_north_east_is_between(self):
        token = tokens_of(raw(measurement(*PARIS), facility(PRODUCER, 48.95, 2.50))).iloc[0]

        assert 0 < token["bearing_sin"] < 1
        assert 0 < token["bearing_cos"] < 1

    def test_on_the_unit_circle(self):
        tokens = tokens_of(raw(
            measurement(*PARIS),
            *(facility(PRODUCER, 48.85 + dlat, 2.35 + dlon) for dlat, dlon in ((0.2, 0.3), (-0.4, 0.1), (0.05, -0.5))),
        ))

        squared = tokens["bearing_sin"].astype(np.float64) ** 2 + tokens["bearing_cos"].astype(np.float64) ** 2
        assert squared.to_numpy() == pytest.approx([1.0] * 3, abs=1e-5)


class TestMainlandFilter:
    @pytest.mark.parametrize(
        "record",
        [
            facility(PRODUCER, 51.15, 2.35),  # France, north of the mainland box, 17 km from the measurement
            facility(PRODUCER, 51.05, 2.45, country="Belgium"),  # inside the box but not France
            facility(PRODUCER, 16.20, -61.50),  # Guadeloupe
        ],
    )
    def test_facility_outside_mainland_france_is_ignored(self, record):
        tokens = tokens_of(raw(measurement(51.0, 2.35), record, facility(USER, 50.95, 2.35, type="kept")))

        assert tokens["type"].tolist() == ["kept"]
