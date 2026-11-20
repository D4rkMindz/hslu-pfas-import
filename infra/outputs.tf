output "service_account_email" {
  description = "The pipeline's identity; `make setup` creates its key and HMAC key."
  value       = google_service_account.pipeline.email
}

output "dataset" {
  description = "Fully qualified dataset id, i.e. GCP_PROJECT_ID.BIGQUERY_DATASET."
  value       = "${google_bigquery_dataset.pfas_import.project}.${google_bigquery_dataset.pfas_import.dataset_id}"
}

output "buckets" {
  description = "The data lake buckets (raw, import)."
  value       = [for bucket in google_storage_bucket.data : bucket.name]
}
