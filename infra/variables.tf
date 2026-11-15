# No defaults: `make tf-*` sets these from .env (TF_VAR_*), so Terraform and the pipeline always
# target the same project and dataset.

variable "project_id" {
  description = "GCP project hosting the BigQuery warehouse (GCP_PROJECT_ID in .env)."
  type        = string
}

variable "region" {
  description = "Location of the dataset and the state bucket (BIGQUERY_LOCATION in .env)."
  type        = string
}

variable "dataset_id" {
  description = "BigQuery dataset holding the feature table (BIGQUERY_DATASET in .env)."
  type        = string
}
