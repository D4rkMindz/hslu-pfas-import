# PFAS Import

It is difficult to convey the extent of the problem on an individual level. Therefore we aim to
find an abstract correlation between the PFAS measurements and the surrounding Industries.

# Setup
First, copy ``/.env.example`` to ``/.env`` and ``/.env.docker.example`` to ``/.env.docker``
Then adjust the values in it according to the documentation inside.

## Infrastructure
The infrastructure is managed using [docker compose](https://docs.docker.com/desktop/). Spin it up using [make](https://www.gnu.org/software/make/):

```bash
make up
```

_or alternatively copy the command from the @docker line below_ `up:` _in the file `./Makefile`_

Running all containers might take a while during first setup. The kestra image is ~2.8GB to download.

Stopping all containers without removing the data can be done using

```bash
make down
# or
docker compose down
```

If you want to clear everything, you can run

```bash
make stop
```


### Database
This project uses PostgreSQL (with PostGIS). The database is set up automatically.

The database can be easily managed using [pgAdmin](https://www.pgadmin.org/) in development, available on [localhost:8086](http://localhost:8086). 
Connecting to the PostgreSQL server requires you setting the password (``POSTGRES_PASSWORD`` in ``/.env``) once (click "Save Password").

### Object Storage
This project uses [SeaweedFS](https://s3fs.readthedocs.io/en/latest/api.html) for object storage.

The following backends are made available:

- [Filer UI](http://localhost:8888/)
- [Master UI](http://localhost:9333)
- [Admin UI](http://localhost:23646)

Use it in your script like:

```python
from s3fs import S3FileSystem
# Autoconfigured using FSSPEC_S3_ENDPOINT_URL, FSSPEC_S3_KEY, FSSPEC_S3_SECRET
fs = S3FileSystem()
```

### Orchestrator

This project uses [Kestra](https://kestra.io/) as workflow orchestrator. The reasoning for it is documented in `docs/adr/0001-orchestrator.md`.

Kestra provides a management UI on [localhost:8080](http://localhost:8080). The registration is done by setting the .env variables 
`KESTRA_UI_USER` and `KESTRA_UI_PASSWORD`.

#### Environment Variables
Kestra gets it's environment variables through the .env file. Then, in the `docker/kestra/docker-compose.yaml` file, 
all within Kestra available environment variables are defined with a `ENV_` prefix. Those environment variables are then 
available for flow tasks configuration in the `env:` parameter within `"{{ envs.<name> }}""` while the `<name>` is  
the lowercase name of the `ENV_*` variable (without `ENV_`) from kestra docker compose (e.g. `ENV_S3_ENDPOINT_URL` 
will be `"{{ envs.s3_endpoint_url }}"`)

## Cloud

### BigQuery
This project can publish the features to [BigQuery](https://cloud.google.com/bigquery) instead of the local PostgreSQL
database. To set it up properly, you need to install the [gcloud CLI](https://cloud.google.com/sdk/docs/install). The
Google project needs to have billing and APIs enabled, and must be able to create service accounts and provision roles
so the terraform setup can be run properly.

To setup the gcloud CLI for this project, please use the commands below.

```shell
gcloud auth application-default login
gcloud auth application-default set-quota-project <your-project-id>
```

After setting up the basics, the service account key needs to be created by the following command. It is written to
`/secrets` (ignored folder), which docker compose and the Kestra flow then mount into `/app/secrets` (configured using
`GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/gcp-service-account.json` in `.env.docker`).

```shell
mkdir -p secrets
gcloud iam service-accounts keys create secrets/gcp-service-account.json \
  --iam-account="$(terraform -chdir=infra output -raw service_account_email)"
```

### Terraform

[Terraform](https://developer.hashicorp.com/terraform/install) is used to automatically setup the cloud infrastructure
for this project. To run it, the Google account has to be allowed to enable APIs, create service accounts and grant roles.

Terraform automatically loads configuration from the .env file.
`GCP_PROJECT_ID`, `BIGQUERY_LOCATION` and `BIGQUERY_DATASET` need to be set accordingly.

The three commands setting up the cloud infrastructure are:

```shell
make tf-init
make tf-plan
make tf-apply
```

`make setup` starts the containers, creates the S3 buckets and, with `LOAD_TARGET=online`, also runs `tf-init` and
`tf-apply`.

# Pipeline

Run the **ETL** pipeline using

```bash
uv run --env-file .env pfas_import extract
uv run --env-file .env pfas_import transform
uv run --env-file .env pfas_import load
uv run --env-file .env pfas_import verify
```

`load` publishes to `LOAD_TARGET` from `.env`: `local` (PostgreSQL, default) or `online` (BigQuery, see "Cloud").
Override it per run with `pfas_import load --target online`. `verify` checks the same target and takes the same
`--target` option.

Every command writes log lines for a terminal by default (`--output text`). The Kestra flow runs
`pfas_import --output kestra <command>` instead: the logs become Kestra log lines with their real level, and the
results (`changed`, `sha256`, `size_bytes`, `rows`, `checks`, ...) become the task outputs.

The Parquet files go to S3 or to a local folder, set by `OBJECT_STORE` in `.env`: `auto` (default) uses S3 if
`FSSPEC_S3_ENDPOINT_URL` or `FSSPEC_S3_KEY` is set, otherwise the folder `LOCAL_DATA_DIR` (default `data/`), with
one subfolder per bucket (e.g. `data/raw/last_download.parquet`). Set `OBJECT_STORE=s3` for AWS S3 with an IAM
role, where neither variable is set.

Alternatively, you can run the Kestra flow `import` in the namespace `pfas-trace`
(`docker/kestra/files/flows/main_pfas-trace.import.yml`). It runs `extract`, `transform`, `load` and `verify`,
monthly or on "Execute".

## Verify the result

`pfas_import verify` checks the published feature table: it must hold exactly the content of the import file
(`import/last_import.parquet`), and its values must make sense. It is independent of the other steps: it reads the
import file and the published table itself and shares no logic with `transform` or `load`, so it can run at any
time, and a bug in the transform cannot hide itself.

```bash
uv run --env-file .env pfas_import verify                 # LOAD_TARGET from .env
uv run --env-file .env pfas_import verify --target local  # PostgreSQL
uv run --env-file .env pfas_import verify --target online # BigQuery
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
uv run --env-file .env pfas_import verify --target local --print-sql
```

It returns one row per check, `failures = 0` means PASS. With `--target online` it prints the same query for the
BigQuery console.

The tests in `test/test_verification.py` break a small table on purpose (a dropped row, a negative distance, a
missing column, ...) and show that every check fails as it should: `make test`.

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