########################################################################################################################
# Setup pfas-import
########################################################################################################################
# The steps are in scripts/setup.sh. TARGET in .env decides: local (SeaweedFS + PostgreSQL) or cloud (GCS + BigQuery,
# provisioned with Terraform first). Safe to run again, e.g. after changing TARGET.
.PHONY: setup
setup:
	@scripts/setup.sh env
	@scripts/setup.sh cloud
	@$(MAKE) --no-print-directory build up
	@scripts/setup.sh kv

########################################################################################################################
# Docker
########################################################################################################################
COMPOSE := docker compose --env-file .env --env-file .env.docker

.PHONY: up build down stop
# Kestra reads the pipeline's credentials from .env.kestra (Kestra secrets), so it is written from .env first.
up:
	@scripts/setup.sh secrets
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

.PHONY: tf-init tf-plan tf-apply tf-destroy
# Creates the bare state bucket if it doesn't exist yet (idempotent); infra/state.tf owns its settings.
# -reconfigure: another GCP_PROJECT_ID means another state bucket, whose state is used as it is.
tf-init:
	@gcloud storage buckets describe gs://$(TF_VAR_state_bucket) --project=$(TF_VAR_project_id) >/dev/null 2>&1 || \
		gcloud storage buckets create gs://$(TF_VAR_state_bucket) --project=$(TF_VAR_project_id) --location=$(TF_VAR_location)
	@$(TERRAFORM) init -reconfigure -backend-config="bucket=$(TF_VAR_state_bucket)"

tf-plan:
	@$(TERRAFORM) plan

# Shows the plan once more and asks for confirmation before changing anything.
tf-apply:
	@$(TERRAFORM) apply

# Deletes the data buckets with their files, the dataset with its tables and the service account with its keys.
# Asks twice: for the project id here, then Terraform's own "yes". The state bucket (prevent_destroy) is only
# forgotten, the next tf-apply imports it again; the project stays (README: deleting the project).
tf-destroy:
	@printf 'This deletes ALL data in gs://%s-raw, gs://%s-import and the BigQuery dataset %s.%s.\n' \
		"$(TF_VAR_project_id)" "$(TF_VAR_project_id)" "$(TF_VAR_project_id)" "$(TF_VAR_dataset_id)"
	@printf 'Type the project id to continue: '; read answer; \
		test "$$answer" = "$(TF_VAR_project_id)" -a -n "$$answer" || { echo "Aborted."; exit 1; }
	@$(TERRAFORM) state rm google_storage_bucket.tfstate >/dev/null 2>&1 || true
	@$(TERRAFORM) destroy
	@scripts/setup.sh forget-keys
