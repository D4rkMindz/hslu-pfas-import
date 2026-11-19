# Holds this configuration's own state (see the backend in versions.tf). Terraform can't create the
# bucket it keeps its state in, so `make tf-init` creates a bare one with gcloud and passes its name to
# both the backend and var.state_bucket. The import below then adopts it, so all of its settings are
# defined here only and applied by the first `make tf-apply`.
import {
  id = "${var.project_id}/${var.state_bucket}"
  to = google_storage_bucket.tfstate
}

resource "google_storage_bucket" "tfstate" {
  name                        = var.state_bucket
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Every apply writes a new state version; keeping the old ones allows rolling back a broken state.
  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
