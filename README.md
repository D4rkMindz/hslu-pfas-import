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
```

`load` publishes to `LOAD_TARGET` from `.env`: `local` (PostgreSQL, default) or `online` (BigQuery, see "Cloud").
Override it per run with `pfas_import load --target online`.

Alternatively, you can run the kestra flow.

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