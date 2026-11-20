terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }

  # Shared state, so every machine sees what's already provisioned. Backends can't use variables, so
  # `make tf-init` passes the bucket via -backend-config -- see state.tf.
  backend "gcs" {
    prefix = "terraform/state"
  }
}

# No provider region: every resource here is global or sets its location explicitly.
provider "google" {
  project = var.project_id

  # With a user login (gcloud auth application-default login), some APIs need a project to bill the
  # requests to; use this one instead of whatever quota project the login happens to have.
  billing_project       = var.project_id
  user_project_override = true
}
