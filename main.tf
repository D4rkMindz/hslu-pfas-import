// Source - https://stackoverflow.com/a/72888812
// Posted by Fábio Filho
// Retrieved 2026-10-22, License - CC BY-SA 4.0

locals {
  envs = { for tuple in regexall("(.*)=(.*)", file(".env")) : tuple[0] => sensitive(tuple[1]) }
}


terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
      version = "6.8.0"
    }
  }
}

provider "google" {
  project = local.envs["GCLOUD_PROJECT"]
  region  = "us-central1"
  zone    = "us-central1-c"
}

resource "google_compute_network" "vpc_network" {
  name = "terraform-network"
}
