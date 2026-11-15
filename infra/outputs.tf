output "service_account_email" {
  description = "Pass to `gcloud iam service-accounts keys create --iam-account=...`."
  value       = google_service_account.pipeline.email
}

output "dataset" {
  description = "Fully qualified dataset id, i.e. GCP_PROJECT_ID.BIGQUERY_DATASET."
  value       = "${google_bigquery_dataset.pfas_import.project}.${google_bigquery_dataset.pfas_import.dataset_id}"
}
