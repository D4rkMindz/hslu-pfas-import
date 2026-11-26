# Feature extraction for hslu-datascience-project

Since 2026-11-20 this pipeline is the only feature extractor. The data science project
(`hslu-datascience-project`, package `pfas_trace`) has no transform of its own any more:

- `FEATURE_STORAGE=bigquery`: it reads the published tables `pfas_import.pfas_features` and
  `pfas_import.pfas_facility_tokens`.
- `FEATURE_STORAGE=parquet`: it reads the import files of this pipeline. `make features` in `pfas_trace` runs
  `pfas_import transform` with the local object store and copies `last_import.parquet` to
  `data/features.parquet` and `last_facility_tokens.parquet` to `data/facility_tokens.parquet`.

Both paths get the output of `domain/features.transform_all`, so there is nothing left to keep in sync.
The earlier differences (missing measurement columns, the mainland France filter, shortened tier-2
prefixes) are resolved by that: `pfas_trace` gets them from here.

## pfas_features

One row per measurement in mainland France (incl. Corsica).

| Column | Type | Meaning |
|---|---|---|
| `date`, `lat`, `lon`, `name`, `city`, `pfas_sum`, `matrix`, `unit`, `type`, `sector` | | measurement columns of the source |
| `year` | integer | year of the measurement, taken from the source column `year` (equals the year of `date`) |
| `location_id` | integer | dense rank 0..n-1 of the sorted unique (`lat`, `lon`); the join key to `pfas_facility_tokens` |
| `<prefix>_distance_min` | float | distance in km to the nearest source of the group |
| `<prefix>_count_0_5km`, `_count_5_25km`, `_count_25_50km` | float | sources of the group in each band |

Prefixes: the tier-1 categories `producer`, `user`, `presumptive`, and one tier-2 prefix per presumptive
sector (falling back to the type). `location_id` values can change when new locations appear in the source,
so always join the two tables of the same publication.

## pfas_facility_tokens

One row per (`location_id`, `rank`): the 32 nearest facilities of a measurement location that lie within
100 km. Locations without a facility in that range have no rows (77 of 38,924 locations on the
2026-10-01 snapshot).

| Column | Type | Meaning |
|---|---|---|
| `location_id` | integer | location in `pfas_features` |
| `rank` | integer | 1 = nearest, consecutive, sorted by distance |
| `source_group` | string | `producer`, `user` or the tier-2 prefix of a presumptive facility (same vocabulary as the feature prefixes, without the aggregate `presumptive`) |
| `category` | string | source category: `PFAS production facility`, `Known PFAS user`, `Presumptive` |
| `type` | string | source facility type, e.g. `Industrial site`, `Airport`, `Military site` |
| `distance_km` | float | haversine distance in km, 0..100 |
| `bearing_sin`, `bearing_cos` | float | initial bearing from the measurement location to the facility, clockwise from north |

`pfas_import verify` checks both tables: reconciliation against the import files, the value rules,
`year` = year of `date`, unique and consecutive ranks sorted by distance, and that every token
`location_id` exists in `pfas_features`.

## Related findings outside the transform

These do not change the features, but they decide whether BigQuery gets them at all.

1. **The BigQuery dataset does not exist yet.** On 2026-11-06 the project `pfas-trace` has no
   datasets. Fix: `TARGET=cloud` in `.env`, then `make setup` (runs Terraform, which creates `pfas_import`, see
   `infra/`).
2. **Kestra only publishes to BigQuery with `TARGET=cloud`.** The flow
   `docker/kestra/files/flows/main_pfas-trace.import.yml` (renamed from `full_etl_five_tasks_demo`) reads `TARGET`
   from the KV store of its namespace (default `local`). Fix: set `TARGET=cloud` in `.env` and run `make setup`
   (see README, "Cloud").
3. **Change detection never matches.** `pipeline/extract.py` and `pipeline/transform.py` compare the SHA-256 of
   the raw file `raw/last_download.parquet` with `pfas_ingestion_state.source_sha256`. `pipeline/load.py`
   stores the SHA-256 of the feature file `import/last_import.parquet` there instead. The hashes
   never match, so every run transforms and loads again. Fix: store the raw file hash in `pipeline/load.py`,
   for example by passing the `sha256` output of the transform task to `load`. The BigQuery path in
   the stash does not update the state table at all; give it the same fix.
4. **Wrong default file in `load.py`** (fixed 2026-11-15, now `pipeline/load.py`). `load()` and `main()` defaulted to
   `RAW_SOURCE_KEY`, so `python -m pfas_import.ingestion.load` read `import/last_download.parquet`, which does not exist.
   The CLI passes `IMPORT_SOURCE_KEY` and is not affected. Fix: default to `IMPORT_SOURCE_KEY`.

## Optional improvements

- The 60 `*_count_*` columns are float32 here, because `_add_source_features` fills them with NaN
  first, and BigQuery stores them as FLOAT64. After `_clean_invalid_records`, every measurement has
  valid coordinates, so no count is ever NaN (checked on the current data: 0 NaN, all whole numbers,
  maximum 77). Initialise them with 0 and cast to an integer type so BigQuery stores INT64.
