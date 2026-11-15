# Holds this configuration's own state (see the backend in versions.tf). Terraform can't create the
# bucket it keeps its state in, so `make tf-init` creates it with gcloud first; the import below then
# adopts it, so its settings are tracked like everything else.
import {
  id = "${var.project_id}/${var.project_id}-tfstate"
  to = google_storage_bucket.tfstate
}

resource "google_storage_bucket" "tfstate" {
  name                        = "${var.project_id}-tfstate"
  location                    = var.region
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
