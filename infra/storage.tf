# The data lake for TARGET=cloud: the same two buckets as SeaweedFS locally. Bucket names are global, so they
# carry the project id; the pipeline derives the same names (Settings.buckets in src/pfas_import/config.py).
resource "google_storage_bucket" "data" {
  for_each = toset(["raw", "import"])

  name                        = "${var.project_id}-${each.key}"
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # The data is reproducible from the source, so `make tf-destroy` may delete it (after confirmation).
  force_destroy = true

  depends_on = [google_project_service.services]
}

# The pipeline reaches the buckets through the S3 API with an HMAC key of its service account (created by
# `make setup`, not here: Terraform would keep the secret in its state).
resource "google_storage_bucket_iam_member" "pipeline" {
  for_each = google_storage_bucket.data

  bucket = each.value.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.pipeline.member
}
