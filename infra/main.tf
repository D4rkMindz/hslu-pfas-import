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

# The warehouse dataset. The feature table in it stays with the pipeline code
# (pfas_import.storage.bigquery), which replaces it on every `pfas_import load --target online`.
resource "google_bigquery_dataset" "pfas_import" {
  dataset_id = var.dataset_id
  location   = var.region

  # Refuse to destroy the dataset while it still holds tables.
  delete_contents_on_destroy = false

  depends_on = [google_project_service.services]
}

# The identity the pipeline runs as where there is no gcloud login, e.g. the Docker container.
# Its key is created with gcloud, not here: Terraform would keep the private key in its state.
resource "google_service_account" "pipeline" {
  account_id   = "pfas-import-pipeline"
  display_name = "PFAS import pipeline"

  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "pipeline" {
  for_each = toset([
    "roles/bigquery.dataEditor", # read and write the feature table
    "roles/bigquery.jobUser",    # run load jobs and queries
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.pipeline.member
}
