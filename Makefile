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

LOAD_TARGET := $(or $(call env,LOAD_TARGET),local)
export TF_VAR_project_id := $(call env,GCP_PROJECT_ID)
export TF_VAR_region := $(call env,BIGQUERY_LOCATION)
export TF_VAR_dataset_id := $(call env,BIGQUERY_DATASET)
TF_STATE_BUCKET := $(TF_VAR_project_id)-tfstate
TERRAFORM := terraform -chdir=infra

.PHONY: tf-init tf-plan tf-apply
# Terraform can't create the bucket it keeps its state in, so this does it once (infra/state.tf then
# imports and tracks it). Idempotent.
tf-init:
	@gcloud storage buckets describe gs://$(TF_STATE_BUCKET) --project=$(TF_VAR_project_id) >/dev/null 2>&1 || ( \
		gcloud storage buckets create gs://$(TF_STATE_BUCKET) --project=$(TF_VAR_project_id) \
			--location=$(TF_VAR_region) --uniform-bucket-level-access --public-access-prevention && \
		gcloud storage buckets update gs://$(TF_STATE_BUCKET) --versioning )
	@$(TERRAFORM) init -backend-config="bucket=$(TF_STATE_BUCKET)"

tf-plan:
	@$(TERRAFORM) plan

# Shows the plan once more and asks for confirmation before changing anything.
tf-apply:
	@$(TERRAFORM) apply

########################################################################################################################
# Setup pfas-import
########################################################################################################################
.PHONY: setup
setup:
	@$(COMPOSE) up -d
	@$(COMPOSE) up -d --wait postgres seaweedfs
# BigQuery: provision the project first -- the dataset belongs to Terraform.
ifeq ($(LOAD_TARGET),online)
	@make tf-init
	@make tf-apply
endif
	@uv run --env-file .env python -c "from pfas_import import storage; storage.setup()"
