locals {
  services = toset([
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    # needed to manage the service account and its project roles below
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
}

resource "google_project_service" "services" {
  for_each = local.services
  service  = each.value

  # On destroy, only forget the API instead of disabling it for the whole project.
  deletion_policy = "ABANDON"
}

# The warehouse dataset. Its tables (features and ingestion state) stay with the pipeline code
# (pfas_import.storage.bigquery), which replaces them on every `pfas_import load` with TARGET=cloud.
resource "google_bigquery_dataset" "pfas_import" {
  dataset_id = var.dataset_id
  location   = var.location

  # The tables are reproducible from the source, so `make tf-destroy` may delete them (after confirmation).
  delete_contents_on_destroy = true

  depends_on = [google_project_service.services]
}

# The identity the pipeline runs as in the Kestra tasks. Its key and HMAC key are created with gcloud by
# `make setup`, not here: Terraform would keep the secrets in its state.
resource "google_service_account" "pipeline" {
  account_id   = "pfas-import-pipeline"
  display_name = "PFAS import pipeline"

  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "pipeline" {
  for_each = toset([
    "roles/bigquery.dataEditor", # read and write the feature and ingestion state tables
    "roles/bigquery.jobUser",    # run load jobs and queries
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.pipeline.member
}
