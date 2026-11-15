terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }

  # Shared state, so every machine sees what's already provisioned. Backends can't use variables, so
  # the bucket (<project id>-tfstate) is passed to `terraform init -backend-config=bucket=...` by
  # `make tf-init`, which also creates it if it doesn't exist yet -- see state.tf.
  backend "gcs" {
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
