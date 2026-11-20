# No defaults: `make tf-*` sets these from .env (TF_VAR_*), so Terraform and the pipeline always
# target the same project and dataset.

variable "project_id" {
  description = "GCP project hosting the BigQuery warehouse (GCP_PROJECT_ID in .env)."
  type        = string
}

variable "location" {
  description = "Location of the dataset and the state bucket, a region or multi-region (BIGQUERY_LOCATION in .env)."
  type        = string
}

variable "dataset_id" {
  description = "BigQuery dataset holding the feature table (BIGQUERY_DATASET in .env)."
  type        = string
}

variable "state_bucket" {
  description = "Bucket holding the Terraform state, the same one the backend uses (set by `make tf-*`)."
  type        = string
}
