import pandas as pd

from pfas_import.domain.features import (
    MAX_PREFIX_LENGTH,
    POSTGRES_MAX_IDENTIFIER_LENGTH,
    _normalize_feature_name,
    transform,
)


class TestNormalizeFeatureName:
    def test_short_name_is_only_normalized(self):
        assert _normalize_feature_name("Treatment and coating of metals") == "treatment_and_coating_of_metals"

    def test_short_name_keeps_n_e_c(self):
        assert _normalize_feature_name("Other n.e.c.") == "other_n_e_c"

    def test_long_name_drops_n_e_c(self):
        name = "Manufacture of other fabricated metal products n.e.c."

        assert _normalize_feature_name(name) == "manufacture_of_other_fabricated_metal_products"

    def test_long_name_is_cut_to_max_length(self):
        name = "Very long sector name " * 4

        prefix = _normalize_feature_name(name)

        assert len(prefix) <= MAX_PREFIX_LENGTH
        assert not prefix.endswith("_")
        assert name.lower().replace(" ", "_").startswith(prefix)


def test_every_column_fits_postgres_identifiers():
    def record(category, lat, lon, sector=None, date=None):
        return {
            "category": category, "country": "France", "lat": lat, "lon": lon, "date": date, "name": "n", "city": "c",
            "pfas_sum": 1.0, "matrix": "Groundwater", "unit": "ng/l", "type": "t", "sector": sector,
        }

    raw = pd.DataFrame([
        record("Measurement", 48.85, 2.35, date="2024-01-01"),
        record("PFAS production facility", 48.9, 2.4),
        record("Known PFAS user", 48.8, 2.3),
        record("Presumptive", 48.7, 2.2, sector="Manufacture of other fabricated metal products n.e.c."),
    ])

    features = transform(raw)

    assert "manufacture_of_other_fabricated_metal_products_count_25_50km" in features.columns
    assert max(len(c) for c in features.columns) <= POSTGRES_MAX_IDENTIFIER_LENGTH
