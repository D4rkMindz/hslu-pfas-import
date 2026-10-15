# PFAS Import

It is difficult to convey the extent of the problem on an individual level. Therefore we aim to
find an abstract correlation between the PFAS measurements and the surrounding Industries.

# Setup
First, copy ``/.env.example`` to ``/.env``
Then adjust the values in it according to the documentation inside

# Infrastructure
The infrastructure is managed using docker compose. Spin it up using [make](https://www.gnu.org/software/make/):

```bash
make up
```

_or alternatively copy the command from the @docker line below_ `up:` _in the file `./Makefile`_


## Database
This project uses PostgreSQL (with PostGIS)

## Object Storage
This project uses SeaweedFS for object storage. 

# Development

## Linting
This project uses [ruff](https://docs.astral.sh/ruff/) as linter.
Ruff can either run once or in the background (with and without fixing errors)

```shell
uv run ruff check --watch --fix
```

`--fix`: automatically fixes errors (that can be fixed)
`--watch`: run in the background on change

# License
proprietary (for now)