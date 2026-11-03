# Orchestrator

## Context

The application requires to be managed by an orchestrator. This orchestrator must be able to at least do the following:

- Run python scripts to download data
- Manage downloaded data in a storage
- Manage the import of the downloaded data into a structured format (PostgreSQL database / GCP Cloud Storage)
- Allow scheduling


## Decision

In SW05 the orchestrator [Kestra](https://kestra.io/) was introduced. Since it's compatible with this project by 
providing a [docker compose](https://kestra.io/docs/installation/docker-compose) setup, we decided to go ahead with this
orchestrator.


## Considered alternatives

### GitHub Actions

Allows most of it but could quickly become expensive.


## Consequences

### Compose setup

The docker compose setup requires a PostgreSQL database itself. The PFAS import application itself also requires a
PostgreSQL database, therefore to avoid potential conflicts and keep upgrades between applications without a shared 
liability (a shared database), two database containers are setup.