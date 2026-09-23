provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. Service Account for Cloud Run (Principle of Least Privilege)
resource "google_service_account" "cloud_run_sa" {
  account_id   = "customer-service-parser-sa"
  display_name = "Cloud Run Service Account for Parser App"
}

# 2. Networking for Isolation
resource "google_compute_network" "main_vpc" {
  name                    = "parser-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "app_subnet" {
  name          = "parser-subnet"
  ip_cidr_range = "10.0.1.0/28"
  network       = google_compute_network.main_vpc.id
  region        = var.region
}

# Serverless VPC Access Connector (Essential for VPC-SC and internal routing)
resource "google_vpc_access_connector" "connector" {
  name          = "parser-vpc-conn"
  region        = var.region
  ip_cidr_range = "10.8.0.0/28"
  network       = google_compute_network.main_vpc.name
}

# 3. Secure GCS Bucket
resource "google_storage_bucket" "input_storage" {
  name                        = "${var.project_id}-customer-docs"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true # Mandatory for UBLA
  public_access_prevention    = "enforced" # Strict PAP

  versioning {
    enabled = true
  }
}

# 4. BigQuery Dataset
resource "google_bigquery_dataset" "parsed_data" {
  dataset_id = "customer_service_results"
  location   = var.region
}

# 5. Cloud Run Service
resource "google_cloud_run_v2_service" "parser_app" {
  name     = "customer-service-parser"
  location = var.region

  template {
    service_account = google_service_account.cloud_run_sa.email
    
    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "ALL_TRAFFIC"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/apps/parser-image:latest"
      
      env {
        name  = "INPUT_BUCKET"
        value = google_storage_bucket.input_storage.name
      }
      env {
        name  = "OUTPUT_DATASET"
        value = google_bigquery_dataset.parsed_data.dataset_id
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# 6. IAM Bindings (Data Isolation)
resource "google_storage_bucket_iam_member" "gcs_viewer" {
  bucket = google_storage_bucket.input_storage.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_bigquery_dataset_iam_member" "bq_editor" {
  dataset_id = google_bigquery_dataset.parsed_data.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
