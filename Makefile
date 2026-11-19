########################################################################################################################
# Docker
########################################################################################################################
COMPOSE := docker compose --env-file .env --env-file .env.docker

up:
	@$(COMPOSE) up -d

build:
	@$(COMPOSE) build

down:
	@$(COMPOSE) down

stop:
	@$(COMPOSE) down -v --rmi all --remove-orphans

########################################################################################################################
# Lint & Tests
########################################################################################################################
.PHONY: test lint lint-ci ci
lint:
	@uv run ruff check --watch --fix

lint-ci:
	@uv run ruff check

test:
	@uv run pytest

ci: lint-ci test

########################################################################################################################
# Terraform
########################################################################################################################
# Reads a value from .env, so Terraform targets the same project and dataset as the pipeline.
env = $(shell sed -n 's/^$(1)=//p' .env 2>/dev/null)

export TF_VAR_project_id := $(call env,GCP_PROJECT_ID)
export TF_VAR_location := $(call env,BIGQUERY_LOCATION)
export TF_VAR_dataset_id := $(call env,BIGQUERY_DATASET)
export TF_VAR_state_bucket := $(TF_VAR_project_id)-tfstate
TERRAFORM := terraform -chdir=infra

.PHONY: tf-init tf-plan tf-apply
# Creates the bare state bucket if it doesn't exist yet (idempotent); infra/state.tf owns its settings.
tf-init:
	@gcloud storage buckets describe gs://$(TF_VAR_state_bucket) --project=$(TF_VAR_project_id) >/dev/null 2>&1 || \
		gcloud storage buckets create gs://$(TF_VAR_state_bucket) --project=$(TF_VAR_project_id) --location=$(TF_VAR_location)
	@$(TERRAFORM) init -backend-config="bucket=$(TF_VAR_state_bucket)"

tf-plan:
	@$(TERRAFORM) plan

# Shows the plan once more and asks for confirmation before changing anything.
tf-apply:
	@$(TERRAFORM) apply

########################################################################################################################
# Setup pfas-import
########################################################################################################################
LOAD_TARGET := $(or $(call env,LOAD_TARGET),local)

.PHONY: setup
setup:
	@$(COMPOSE) up -d
	@$(COMPOSE) up -d --wait postgres seaweedfs
# BigQuery: provision the project first -- the dataset belongs to Terraform.
ifeq ($(LOAD_TARGET),online)
	@$(MAKE) tf-init
	@$(MAKE) tf-apply
endif
	@uv run --env-file .env python -c "from pfas_import.repository import factory; factory.ensure_buckets()"
