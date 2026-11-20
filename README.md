# PFAS Import

It is difficult to convey the extent of the problem on an individual level. Therefore we aim to
find an abstract correlation between the PFAS measurements and the surrounding Industries.

# Setup

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose, and [make](https://www.gnu.org/software/make/)
- `TARGET=cloud` only: the [gcloud CLI](https://cloud.google.com/sdk/docs/install),
  [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.7 and a Google account with a billing account
- macOS or Linux. On Windows, use [WSL2](https://learn.microsoft.com/windows/wsl/install).
- [uv](https://docs.astral.sh/uv/) is only needed to run the pipeline or the tests outside Docker (Development).

## Quick start

```bash
make setup
```

The first run copies `.env.example` to `.env` and `.env.docker.example` to `.env.docker`, then stops and lists the
`<placeholders>` to replace (passwords of your choice; `KESTRA_UI_PASSWORD` needs 8 characters with an uppercase
letter and a number). Run `make setup` again afterwards. It

1. with `TARGET=cloud`: provisions Google Cloud, see [Cloud](#cloud-targetcloud),
2. builds the pipeline image and starts the containers (the Kestra image is ~2.8 GB on the first run),
3. writes the flow's settings into Kestra (see [Orchestrator](#orchestrator)).

`make setup` is safe to run again, e.g. after changing a value in `.env`. Then open Kestra on
[localhost:8080](http://localhost:8080) (login: `KESTRA_UI_USER`/`KESTRA_UI_PASSWORD`), flow `import` in the namespace
`pfas-trace`, and click "Execute" (see [Pipeline](#pipeline)).

## Local or cloud: `TARGET`

`TARGET` in `.env` decides where the whole pipeline runs against:

| `TARGET`          | Raw and feature files (`raw`, `import`)                | Features and ingestion state |
|-------------------|--------------------------------------------------------|------------------------------|
| `local` (default) | SeaweedFS buckets `S3_RAW_BUCKET`, `S3_IMPORT_BUCKET`  | PostgreSQL                   |
| `cloud`           | GCS buckets `<GCP_PROJECT_ID>-raw`, `<GCP_PROJECT_ID>-import` | BigQuery dataset `BIGQUERY_DATASET` |

Both use the same code: the GCS buckets are reached through GCS's S3-compatible API, like SeaweedFS. In the cloud,
nothing is stored locally: the download is streamed into the raw bucket, and `transform` reads from it. Kestra and
the containers always run locally. To switch, change `TARGET` and run `make setup` again.

## Cloud (`TARGET=cloud`)

With `TARGET=cloud`, `make setup` provisions everything before it starts the containers:

1. logs you in to gcloud and to the Application Default Credentials for Terraform, if needed (opens the browser),
2. uses the project `GCP_PROJECT_ID`, or, if it is empty, creates the project `pfas-import-<random>` and writes its
   id to `.env`,
3. links a billing account if the project has none: `GCP_BILLING_ACCOUNT`, or your only open billing account,
4. runs Terraform (`make tf-init`, `make tf-apply`; you confirm the plan with `yes`), see [Terraform](#terraform),
5. creates a key of the pipeline's service account in `secrets/gcp-service-account.json` (for BigQuery) and an HMAC
   key for the buckets (`GCS_HMAC_*` in `.env`). Existing keys are reused while they are valid.

Your Google account needs to be allowed to create projects (or own `GCP_PROJECT_ID`), link a billing account,
enable APIs, create service accounts and their keys, and grant roles. The project owner role covers everything in an
existing project.

### Credentials

The secrets never enter git (`.env`, `.env.kestra` and `secrets/` are ignored) or the Terraform state (the keys are
created with gcloud). Kestra gets the passwords and keys as Kestra secrets (`SECRET_PFAS_*` in `.env.kestra`, which
`make up` writes from `.env`); the flow writes the service account key into each task's working directory only.

If your organisation blocks service account keys (`constraints/iam.disableServiceAccountKeyCreation`), use your own
login for BigQuery instead and run `make setup` again:

```bash
mkdir -p secrets
cp ~/.config/gcloud/application_default_credentials.json secrets/gcp-service-account.json
```

### Terraform

[Terraform](https://developer.hashicorp.com/terraform/install) provisions the Google Cloud resources (`infra/`): the
APIs, the buckets `<GCP_PROJECT_ID>-raw` and `<GCP_PROJECT_ID>-import`, the BigQuery dataset and the service account
`pfas-import-pipeline` with access to both. Its state is in the bucket `<GCP_PROJECT_ID>-tfstate`, which
`make tf-init` creates if it doesn't exist. The `make tf-*` targets read `GCP_PROJECT_ID`, `BIGQUERY_LOCATION` and
`BIGQUERY_DATASET` from `.env`.

```shell
make tf-init
make tf-plan
make tf-apply
```

`make tf-destroy` deletes the buckets with their files, the dataset with its tables and the service account with its
keys. It asks twice: for the project id, then Terraform's own `yes`. The project and the state bucket stay; to delete
the project, too: `gcloud projects delete <GCP_PROJECT_ID>`.

## Infrastructure

The containers are managed with [docker compose](https://docs.docker.com/desktop/):

```bash
make up    # start (also writes .env.kestra)
make down  # stop, the data stays
make stop  # stop and remove everything, including the data
```

### Database
This project uses PostgreSQL (with PostGIS). The database is set up automatically.

The database can be easily managed using [pgAdmin](https://www.pgadmin.org/) in development, available on [localhost:8086](http://localhost:8086). 
Connecting to the PostgreSQL server requires you setting the password (``POSTGRES_PASSWORD`` in ``/.env``) once (click "Save Password").

### Object Storage
This project uses [SeaweedFS](https://github.com/seaweedfs/seaweedfs) for object storage with `TARGET=local`.

The following backends are made available:

- [Filer UI](http://localhost:8888/)
- [Master UI](http://localhost:9333)
- [Admin UI](http://localhost:23646)

### Orchestrator

This project uses [Kestra](https://kestra.io/) as workflow orchestrator. The reasoning for it is documented in `docs/adr/0001-orchestrator.md`.

Kestra provides a management UI on [localhost:8080](http://localhost:8080). The registration is done by setting the .env variables 
`KESTRA_UI_USER` and `KESTRA_UI_PASSWORD`.

#### Flows and their configuration
Two flows in the namespace `pfas-trace` (`docker/kestra/files/flows/`): `import` runs the steps `extract`,
`transform`, `load` and `verify`, each as a run of the flow `step`, which holds the task settings once (Kestra 2.0
has no shared task defaults any more).

`step` reads its settings from the [KV store](https://kestra.io/docs/concepts/kv-store) of its namespace
(`kv('TARGET')`, `kv('POSTGRES_HOST')`, ...), and passwords and keys from Kestra secrets (`secret('PFAS_...')`).
`make setup` writes the KV store from `.env`/`.env.docker` (see `scripts/setup.sh`, step `kv`), `make up` the secrets.
So the same flow runs unchanged on another Kestra instance with its own KV store and secrets. The values are in the
Kestra UI under Namespaces > `pfas-trace` > KV Store.

# Pipeline

Run the Kestra flow `import` in the namespace `pfas-trace` (`docker/kestra/files/flows/main_pfas-trace.import.yml`):
"Execute" in the Kestra UI, or monthly by its schedule. It runs `extract`, `transform`, `load` and `verify`, each in
the pipeline's container, against `TARGET`. The results of each step (`changed`, `sha256`, `rows`, `checks`, ...) are
the outputs of its task in `import`.

For development, the same steps run on the host with [uv](https://docs.astral.sh/uv/):

```bash
uv run --env-file .env pfas_import extract
uv run --env-file .env pfas_import transform
uv run --env-file .env pfas_import load
uv run --env-file .env pfas_import verify
```

They use `TARGET` from `.env`. With `TARGET=cloud`, BigQuery uses your gcloud login
(`gcloud auth application-default login`).

Every command writes log lines for a terminal by default (`--output text`). The Kestra flow runs
`pfas_import --output kestra <command>` instead: the logs become Kestra log lines with their real level, and the
results (`changed`, `sha256`, `size_bytes`, `rows`, `checks`, ...) become the task outputs.

With `TARGET=local`, the Parquet files go to S3 or to a local folder, set by `OBJECT_STORE` in `.env`: `auto`
(default) uses S3 if `FSSPEC_S3_ENDPOINT_URL` or `FSSPEC_S3_KEY` is set, otherwise the folder `LOCAL_DATA_DIR`
(default `data/`), with one subfolder per bucket (e.g. `data/raw/last_download.parquet`).

## Verify the result

`pfas_import verify` checks the published feature table: it must hold exactly the content of the import file
(`import/last_import.parquet`), and its values must make sense. It is independent of the other steps: it reads the
import file and the published table itself and shares no logic with `transform` or `load`, so it can run at any
time, and a bug in the transform cannot hide itself.

```bash
uv run --env-file .env pfas_import verify  # TARGET from .env: PostgreSQL (local) or BigQuery (cloud)
```

Every check prints one line: `PASS`, `WARN` (a warning, never fails) or `FAIL` (an error). The command exits with
`1` if an error check fails, so the Kestra execution turns red. The published data stays as it is for
investigation, nothing is rolled back. In Kestra, the task also has the outputs `checks`, `passed`, `warnings` and
`errors`.

Expected output (source of 2026-11-15, 729,166 rows, 90 columns; the counts change with the monthly source):

```text
PASS  published table exists
PASS  schema: base and tier-1 feature columns are present
PASS  schema: every tier-2 prefix has a distance and three band columns
PASS  reconciliation: row count equals the import file
...
PASS  producer: nearest distance agrees with the band counts
...
PASS  matrix is a known value
WARN  rows are unique without pfas_sum (the label)  (28334: 15631 groups, 9216 with different pfas_sum)
74 of 75 checks passed, 1 warnings, 0 errors
```

The warning is expected: the source has separate analyses (their per-compound values differ) at the same place and
date. They stay in the table, `pfas_sum = 0` (no PFAS detected) included.

### Checks

| Check                                                    | Severity | Fails when                                                                                       |
|----------------------------------------------------------|----------|--------------------------------------------------------------------------------------------------|
| published table exists                                   | error    | `pfas_features` is missing in the target                                                         |
| schema: base and tier-1 feature columns are present      | error    | one of `date, lat, lon, name, city, pfas_sum, matrix, unit, type, sector` or a `producer_*`, `user_*`, `presumptive_*` column is missing (contract with the data science project) |
| schema: every tier-2 prefix has a distance and three band columns | error | a presumptive sector lacks `_distance_min` or one of `_count_0_5km`, `_count_5_25km`, `_count_25_50km` |
| reconciliation: row count equals the import file         | error    | rows were lost or added                                                                          |
| reconciliation: column names equal the import file       | error    | a column is missing or renamed (e.g. PostgreSQL cuts names longer than 63 characters)            |
| reconciliation: non-NULL count per column equals the import file | error | values were lost to NULL                                                                  |
| reconciliation: sum per numeric column equals the import file | error | a value changed (relative tolerance 1e-9)                                                   |
| reconciliation: date range equals the import file        | error    | min or max `date` differs                                                                        |
| no NULL in date, lat, lon, name, pfas_sum, matrix, unit  | error    | a required value is missing (`city` and `sector` may be NULL)                                    |
| coordinates in mainland France                           | error    | a row is outside lat 41.3..51.1, lon -5.2..9.6 (mainland France with Corsica)                    |
| pfas_sum is not negative                                 | error    | `pfas_sum < 0` (0 is valid: nothing detected)                                                    |
| date is not in the future                                | error    | `date` after today                                                                               |
| `<prefix>`: distance within 0..20,038 km                 | error    | a nearest-source distance is negative or longer than half the Earth's circumference              |
| `<prefix>`: band counts are whole numbers >= 0           | error    | a source count is NULL, negative or fractional                                                   |
| `<prefix>`: nearest distance agrees with the band counts | error    | e.g. the nearest source is 2 km away but no source is counted within 5 km (1 m tolerance)        |
| unit is a known value                                    | warning  | `unit` is not one of `ng/l`, `ng/kg dw`, `ng/kg fw`, `ng/kg`                                     |
| matrix is a known value                                  | warning  | `matrix` is not one of the 10 known matrices                                                     |
| rows are unique without pfas_sum (the label)             | warning  | rows are equal in every column except `pfas_sum`; the detail counts the groups with different `pfas_sum` |

The three `<prefix>` checks run for every source category: `producer`, `user`, `presumptive` and each presumptive
sector (tier 2).

### Run the checks in pgAdmin

The rule checks (all except "published table exists", schema and reconciliation) are one SQL query. Print it for
the current columns and paste it into the pgAdmin Query Tool (database `pfas_import`):

```bash
uv run --env-file .env pfas_import verify --print-sql
```

It returns one row per check, `failures = 0` means PASS. With `TARGET=cloud` it prints the same query for the
BigQuery console.

The tests in `test/test_verification.py` break a small table on purpose (a dropped row, a negative distance, a
missing column, ...) and show that every check fails as it should: `make test`.

# Known limitations

- **One Kestra per GCP project.** Every Kestra instance schedules the flow monthly. Two of them with `TARGET=cloud`
  on the same `GCP_PROJECT_ID` (e.g. a local stack and the server instance atc) both publish to the same buckets and
  dataset. The runs replace the tables as a whole, so the result stays consistent, but the work is done twice. Give
  each stack its own project.
- **No transaction in BigQuery.** `load` replaces the feature table, then the ingestion state. If the second step
  fails, the next run publishes the same data again.
- **Change detection never matches**: `load` stores the hash of the feature file, `extract` and `transform` compare
  the hash of the raw file, so every run transforms and publishes again (`docs/feature-parity.md`).
- **Not yet done** for the final deliverable: partitioning and clustering of the BigQuery table, the documented grain
  and analytical model of the final tables, and the final architecture diagram.
- Service account keys may be blocked by your organisation; see [Credentials](#credentials) for the fallback.
- Windows only through WSL2.

# Development

## Linting
This project uses [ruff](https://docs.astral.sh/ruff/) as linter.
Ruff can either run once or in the background (with and without fixing errors)

```shell
make lint
# or 
uv run ruff check
```

`--fix`: automatically fixes errors (that can be fixed)
`--watch`: run in the background on change

# Testing

This project uses [pytest]() as testing framework
Run all pytests in the `test/ folder` using the following command:

```sh
make test
# or
uv run pytest
```

# License
proprietary (for now)