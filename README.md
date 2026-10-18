# PFAS Import

It is difficult to convey the extent of the problem on an individual level. Therefore we aim to
find an abstract correlation between the PFAS measurements and the surrounding Industries.

# Setup
First, copy ``/.env.example`` to ``/.env``
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
This project uses PostgreSQL (with PostGIS). The database is setup automatically.


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