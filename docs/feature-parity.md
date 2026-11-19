# Feature transformation parity with hslu-datascience-project

The data science project (`hslu-datascience-project`, package `pfas_trace`) reads the BigQuery table
`pfas_import.pfas_features` that this pipeline publishes. Both projects must produce the same feature
table, so this file records where `pfas_import` differs from `pfas_trace` and how to fix it.

Compared on 2026-11-06:

- this repo: `src/pfas_import/ingestion/transform.py` at `main` (`6f9a053`)
- data science: `src/pfas_trace/transform.py` at `main` (`b823b20c`)

**Result:** not equal. `pfas_import` drops four measurement columns. Everything else gives the same output.

**Update 2026-11-15** (verification step, `pfas_import verify`):

- Section 1 is fixed in `pfas_import`: the table keeps `matrix`, `unit`, `type` and `sector` again.
  `pfas_import verify` fails if one of them is missing.
- New difference: `pfas_import` now keeps only mainland France, see section 2. `pfas_trace` needs the
  same filter to produce the same table.
- New difference: long tier-2 prefixes are shortened, see section 3.

## 1. Missing measurement columns (fix required)

`_extract_tier1_features` keeps 6 columns of each measurement here and 10 in `pfas_trace`:

| Column   | pfas_trace | pfas_import |
|----------|:----------:|:-----------:|
| `date`, `lat`, `lon`, `name`, `city`, `pfas_sum` | yes | yes |
| `matrix` | yes | **no** |
| `unit`   | yes | **no** |
| `type`   | yes | **no** |
| `sector` | yes | **no** |

The published table has 86 columns instead of 90.

Why it matters:

- `pfas_sum` comes in different units: ng/l for water, ng/kg, ng/kg dw (dry weight) or ng/kg fw
  (fresh weight) for soil, sediment, suspended matter and biota. Units vary even within a matrix:
  7 surface water rows use ng/kg dw and 8 sediment rows use ng/l. Without `matrix` and `unit`, a
  model trained on the table mixes targets that cannot be compared, and nobody can filter them apart.
- The French data spans 10 matrices. Surface water (339,545 rows) and groundwater (318,041 rows)
  make up 90% of it.
- `FeatureRepository.features(matrix=...)` and `train_test(matrix=...)` in `pfas_trace` raise
  `MissingColumns: Table 'pfas_features' lacks the columns ['matrix']` on the current table.

Fix in `src/pfas_import/domain/features.py` (then `ingestion/transform.py`), `_extract_tier1_features`:

```python
    tier1_feature_df: pd.DataFrame = (
        cleaned_df.loc[
            cleaned_df["category"] == "Measurement",
            ["date", "lat", "lon", "name", "city", "pfas_sum", "matrix", "unit", "type", "sector"],
        ]
        .copy()
        .reset_index(drop=True)
    )
```

Then rerun `pfas_import transform` and `pfas_import load` (both targets) so the tables get the new schema.

Note: in the current data, `type` is `Sampling location` for all 734,236 measurement rows and `sector`
is empty for all of them. Neither carries signal. Keep them for parity anyway; if you want to drop
them, drop them in both projects in the same change.

## 2. Mainland France filter (fix required in pfas_trace)

`transform()` in `pfas_import` drops every record (measurements and sources) outside mainland France
including Corsica: lat 41.3..51.1, lon -5.2..9.6. `pfas_trace` keeps them.

Measured on the source snapshot of 2026-10-01 (`data/export.parquet`):

- 12,384 measurements in the overseas departments (Guadeloupe, Martinique, French Guiana, Réunion,
  Mayotte), 1.7% of the French measurements.
- 6 mainland measurements with broken coordinates (Montreuil-Bellay at lat 41.04,
  Louvigné-du-Désert at lon -5.9).
- 19 presumptive sources overseas. A tier-2 group whose sources are all overseas no longer gets columns.

`pfas_import verify` fails if a published row is outside the box.

Fix in `src/pfas_trace/transform.py`, at the start of the transform:

```python
MAINLAND_FRANCE_LAT = (41.3, 51.1)
MAINLAND_FRANCE_LON = (-5.2, 9.6)

lat = pd.to_numeric(df["lat"], errors="coerce")
lon = pd.to_numeric(df["lon"], errors="coerce")
df = df.loc[lat.between(*MAINLAND_FRANCE_LAT) & lon.between(*MAINLAND_FRANCE_LON)]
```

Not changed: rows that are identical in every published column except `pfas_sum` stay in both
projects. They are separate analyses (their per-compound `pfas_values` differ in the source).
`pfas_import verify` reports them as a warning.

## 3. Shortened tier-2 column names (fix required in pfas_trace)

PostgreSQL cuts identifiers to 63 characters without an error. The sector
`Manufacture of other fabricated metal products n.e.c.` gave
`manufacture_of_other_fabricated_metal_products_n_e_c_count_25_50km` (66 characters), which PostgreSQL
published as `..._count_25_5`. `pfas_import verify` found it (column names differ from the import file).

`_normalize_feature_name` in `pfas_import` now shortens a prefix that is longer than 49 characters
(63 minus `_count_25_50km`): it drops a trailing `_n_e_c` ("not elsewhere classified"), then cuts to
49 characters. Today this only renames `manufacture_of_other_fabricated_metal_products_n_e_c_*` to
`manufacture_of_other_fabricated_metal_products_*`, in PostgreSQL and in BigQuery.

Fix in `pfas_trace`, at the end of its prefix normalization:

```python
MAX_PREFIX_LENGTH = 63 - len("_count_25_50km")  # 49

if len(normalized) > MAX_PREFIX_LENGTH:
    normalized = normalized.removesuffix("_n_e_c")
    normalized = normalized[:MAX_PREFIX_LENGTH].rstrip("_")
```

## 4. Naming and logging (no fix needed)

- The entry point is `transform()` here and `load_and_transform()` in `pfas_trace`.
- `transform()` here logs each step.

Neither changes the output.

## What already matches

- France filter: here in `domain/features.filter_france` (called by `pipeline/transform.py`), in `pfas_trace` in
  `notebooks/preprocessing/02_FeatureExtraction.ipynb`. Both use `country=="France"` before the transform.
- Removal of records without `lat`, `lon` or `date`.
- Tier 1 (producer, user, presumptive) and tier 2 (presumptive sector, falling back to type) features:
  nearest distance and counts in the 0-5, 5-25 and 25-50 km bands via a haversine `BallTree`.
- `date` conversion to datetime and numeric downcasting.

## Related findings outside transform.py

These do not change the features, but they decide whether BigQuery gets them at all.

1. **The BigQuery dataset does not exist yet.** On 2026-11-06 the project `pfas-trace` has no
   datasets. Fix: `make tf-init && make tf-apply` (creates `pfas_import`, see `infra/`), then
   `pfas_import load --target online`.
2. **Kestra only publishes to BigQuery with `LOAD_TARGET=online`.** The flow
   `docker/kestra/files/flows/main_pfas-trace.import.yml` (renamed from `full_etl_five_tasks_demo`) runs
   `pfas_import load`, which publishes to `LOAD_TARGET` (default `local`). Fix: set
   `LOAD_TARGET=online` in `.env` and create the service account key in `secrets/` (see README, "Cloud").
3. **Change detection never matches.** `pipeline/extract.py` and `pipeline/transform.py` compare the SHA-256 of
   the raw file `raw/last_download.parquet` with `pfas_ingestion_state.source_sha256`. `pipeline/load.py`
   stores the SHA-256 of the feature file `import/last_import.parquet` there instead. The hashes
   never match, so every run transforms and loads again. Fix: store the raw file hash in `pipeline/load.py`,
   for example by passing the `sha256` output of the transform task to `load`. The BigQuery path in
   the stash does not update the state table at all; give it the same fix.
4. **Wrong default file in `load.py`** (fixed 2026-11-15, now `pipeline/load.py`). `load()` and `main()` defaulted to
   `RAW_SOURCE_KEY`, so `python -m pfas_import.ingestion.load` read `import/last_download.parquet`, which does not exist.
   The CLI passes `IMPORT_SOURCE_KEY` and is not affected. Fix: default to `IMPORT_SOURCE_KEY`.

## Optional improvements (apply to both projects)

- The 60 `*_count_*` columns are float32 here, because `_add_source_features` fills them with NaN
  first, and BigQuery stores them as FLOAT64. After `_clean_invalid_records`, every measurement has
  valid coordinates, so no count is ever NaN (checked on the current data: 0 NaN, all whole numbers,
  maximum 77). Initialise them with 0 and cast to an integer type so BigQuery stores INT64.
- The transform now exists twice. A parity test that runs both on a small fixture and compares the
  frames with `pandas.testing.assert_frame_equal` would catch the next drift. A shared package
  would remove the copy entirely.
