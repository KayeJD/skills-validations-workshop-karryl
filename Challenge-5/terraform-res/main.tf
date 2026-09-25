############################################
# Terraform / Provider setup
############################################

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.40"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Model Armor resources currently ship under the google-beta provider.
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" {}

############################################
# Enable required APIs
############################################

locals {
  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "eventarc.googleapis.com",
    "modelarmor.googleapis.com",
    "vpcaccess.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
    "pubsub.googleapis.com", # Eventarc GCS triggers rely on Pub/Sub under the hood
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

############################################
# Service Account (Cloud Run runtime identity)
############################################

resource "google_service_account" "snow_app" {
  account_id   = var.service_account_id
  display_name = "snow-app Cloud Run runtime identity"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

# Least-privilege IAM bindings for the runtime service account
resource "google_project_iam_member" "vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.snow_app.email}"
}

resource "google_project_iam_member" "bq_data_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.snow_app.email}"
}

resource "google_project_iam_member" "bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.snow_app.email}"
}

resource "google_storage_bucket_iam_member" "gcs_object_viewer" {
  bucket = google_storage_bucket.ingestion.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.snow_app.email}"
}

############################################
# Artifact Registry (holds the snow-app container image)
############################################

resource "google_artifact_registry_repository" "snow_app" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository_id
  description   = "Container images for snow-app"
  format        = "DOCKER"
  labels        = var.labels

  depends_on = [google_project_service.apis]
}

############################################
# GCS bucket (Eventarc ingestion path)
############################################

resource "google_storage_bucket" "ingestion" {
  project                     = var.project_id
  name                        = var.gcs_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.gcs_force_destroy
  labels                      = var.labels

  depends_on = [google_project_service.apis]
}

############################################
# BigQuery: dataset + application storage/log tables
############################################

resource "google_bigquery_dataset" "snow_app" {
  project    = var.project_id
  dataset_id = var.bq_dataset_id
  location   = var.bq_dataset_location
  labels     = var.labels

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_table" "documents" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.snow_app.dataset_id
  table_id   = var.bq_documents_table_id

  deletion_protection = false

  schema = jsonencode([
    { name = "document_id", type = "STRING", mode = "NULLABLE" },
    { name = "date", type = "STRING", mode = "NULLABLE" },
    { name = "customer", type = "STRING", mode = "NULLABLE" },
    { name = "agent", type = "STRING", mode = "NULLABLE" },
    { name = "product", type = "STRING", mode = "NULLABLE" },
    { name = "summary", type = "STRING", mode = "NULLABLE" },
    { name = "resolution", type = "STRING", mode = "NULLABLE" },
    { name = "escalate", type = "STRING", mode = "NULLABLE" },
    { name = "source_file_name", type = "STRING", mode = "NULLABLE" },
    { name = "original_content", type = "STRING", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "request_logs" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.snow_app.dataset_id
  table_id   = var.bq_logs_table_id

  deletion_protection = false

  schema = jsonencode([
    { name = "event_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "source_file_name", type = "STRING", mode = "NULLABLE" },
    { name = "source_type", type = "STRING", mode = "NULLABLE" },
    { name = "success", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "detail", type = "STRING", mode = "NULLABLE" },
    { name = "model", type = "STRING", mode = "NULLABLE" },
  ])
}

############################################
# Model Armor template (prompt + response screening)
############################################

resource "google_model_armor_template" "snow_app" {
  count    = var.enable_model_armor ? 1 : 0
  provider = google-beta

  project     = var.project_id
  location    = var.region
  template_id = var.model_armor_template_id

  filter_config {
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
  }

  depends_on = [google_project_service.apis]

  # NOTE: the Model Armor Terraform resource surface is new and evolving.
  # Verify field names/blocks against the current hashicorp/google-beta
  # provider docs before applying, and adjust filter_config to your policy.
}

locals {
  model_armor_template_name = var.enable_model_armor ? (
    "projects/${var.project_id}/locations/${var.region}/templates/${var.model_armor_template_id}"
  ) : var.model_armor_prompt_template
}

############################################
# Optional networking: VPC + Serverless VPC Access Connector
############################################

resource "google_compute_network" "snow_app" {
  count                   = var.enable_vpc_connector ? 1 : 0
  project                 = var.project_id
  name                    = "snow-app-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "snow_app" {
  count         = var.enable_vpc_connector ? 1 : 0
  project       = var.project_id
  name          = "snow-app-subnet"
  region        = var.region
  network       = google_compute_network.snow_app[0].id
  ip_cidr_range = "10.10.0.0/24"

  private_ip_google_access = true
}

resource "google_vpc_access_connector" "snow_app" {
  count         = var.enable_vpc_connector ? 1 : 0
  project       = var.project_id
  name          = "snow-app-connector"
  region        = var.region
  ip_cidr_range = var.vpc_connector_cidr
  network       = google_compute_network.snow_app[0].name

  depends_on = [google_project_service.apis]
}

############################################
# Cloud Run service
############################################

resource "google_cloud_run_v2_service" "snow_app" {
  project  = var.project_id
  name     = var.service_name
  location = var.region
  labels   = var.labels

  template {
    service_account = google_service_account.snow_app.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    dynamic "vpc_access" {
      for_each = var.enable_vpc_connector ? [1] : []
      content {
        connector = google_vpc_access_connector.snow_app[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_LOCATION"
        value = var.region
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "MODEL_ARMOR_PROMPT_TEMPLATE"
        value = local.model_armor_template_name
      }
      env {
        name  = "MODEL_ARMOR_RESPONSE_TEMPLATE"
        value = local.model_armor_template_name
      }
      env {
        name  = "BIGQUERY_DATASET_ID"
        value = google_bigquery_dataset.snow_app.dataset_id
      }
      env {
        name  = "BIGQUERY_TABLE_ID"
        value = google_bigquery_table.documents.table_id
      }
      env {
        name  = "BIGQUERY_LOG_TABLE_ID"
        value = google_bigquery_table.request_logs.table_id
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.vertex_ai_user,
    google_project_iam_member.bq_data_editor,
    google_project_iam_member.bq_job_user,
  ]
}

# Public HTTPS access (toggle off + front with IAP/Load Balancer for internal-only)
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count = var.allow_unauthenticated ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.snow_app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

############################################
# Eventarc trigger: GCS upload -> Cloud Run (automated ingestion path)
############################################

# Eventarc's GCS triggers require the GCS service agent to have Pub/Sub publish rights.
resource "google_project_iam_member" "gcs_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_project.current.number}@gs-project-accounts.iam.gserviceaccount.com"

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "eventarc_trigger" {
  account_id   = "snow-app-eventarc"
  display_name = "snow-app Eventarc trigger identity"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "eventarc_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.snow_app.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.eventarc_trigger.email}"
}

resource "google_project_iam_member" "eventarc_event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.eventarc_trigger.email}"
}

resource "google_eventarc_trigger" "gcs_upload" {
  project  = var.project_id
  name     = "snow-app-gcs-upload"
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.ingestion.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.snow_app.name
      region  = var.region
      path    = "/"
    }
  }

  service_account = google_service_account.eventarc_trigger.email

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.gcs_pubsub_publisher,
    google_cloud_run_v2_service_iam_member.eventarc_invoker,
  ]
}

############################################
# Outputs
############################################

output "cloud_run_url" {
  description = "Public HTTPS URL of the deployed snow-app Cloud Run service."
  value       = google_cloud_run_v2_service.snow_app.uri
}

output "artifact_registry_repository" {
  description = "Artifact Registry repository path for pushing the container image."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.snow_app.repository_id}"
}

output "gcs_ingestion_bucket" {
  description = "Bucket to upload documents to for the automated Eventarc ingestion path."
  value       = google_storage_bucket.ingestion.name
}

output "bigquery_dataset" {
  description = "BigQuery dataset holding application storage and log tables."
  value       = google_bigquery_dataset.snow_app.dataset_id
}

output "service_account_email" {
  description = "Cloud Run runtime service account email."
  value       = google_service_account.snow_app.email
}

output "model_armor_template" {
  description = "Model Armor template resource name in use."
  value       = local.model_armor_template_name
}
