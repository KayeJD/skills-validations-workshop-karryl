############################################
# Project / Provider
############################################

variable "project_id" {
  description = "GCP project ID to deploy snow-app into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, BigQuery, and networking resources."
  type        = string
  default     = "us-central1"
}

############################################
# Naming
############################################

variable "service_name" {
  description = "Name of the Cloud Run service."
  type        = string
  default     = "snow-app"
}

variable "service_account_id" {
  description = "Account ID (short form) for the Cloud Run runtime service account."
  type        = string
  default     = "snow-app-sa"
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository ID that will hold the snow-app container image."
  type        = string
  default     = "snow-app-repo"
}

############################################
# Container image
############################################

variable "container_image" {
  description = <<-EOT
    Full image reference to deploy, e.g.
    us-central1-docker.pkg.dev/PROJECT_ID/snow-app-repo/snow-app:latest
    Leave as a placeholder on first apply (before an image has been pushed);
    update and re-apply (or use `gcloud run deploy --image`) once Cloud Build
    has pushed a real tag to Artifact Registry.
  EOT
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/placeholder"
}

variable "cloud_run_cpu" {
  description = "CPU allocated to the Cloud Run container."
  type        = string
  default     = "1"
}

variable "cloud_run_memory" {
  description = "Memory allocated to the Cloud Run container."
  type        = string
  default     = "512Mi"
}

variable "cloud_run_min_instances" {
  description = "Minimum number of Cloud Run instances (0 allows scale-to-zero)."
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Maximum number of Cloud Run instances."
  type        = number
  default     = 3
}

variable "allow_unauthenticated" {
  description = "Whether to allow unauthenticated (public) HTTPS access to the Cloud Run service. Set to false and front with IAP/Load Balancer for internal-only access."
  type        = bool
  default     = true
}

############################################
# Gemini / Vertex AI
############################################

variable "gemini_model" {
  description = "Gemini model ID used for document/text extraction."
  type        = string
  default     = "gemini-2.5-flash"
}

############################################
# Model Armor
############################################

variable "enable_model_armor" {
  description = "Whether to provision a Model Armor template via Terraform. If false, supply an existing template's resource name via model_armor_prompt_template instead."
  type        = bool
  default     = true
}

variable "model_armor_template_id" {
  description = "ID for the Model Armor template created by this configuration (used when enable_model_armor = true)."
  type        = string
  default     = "snow-app-template"
}

variable "model_armor_prompt_template" {
  description = "Full resource name of an existing Model Armor prompt template to use instead of provisioning one (only used when enable_model_armor = false)."
  type        = string
  default     = ""
}

############################################
# GCS ingestion bucket
############################################

variable "gcs_bucket_name" {
  description = "Globally-unique name for the GCS bucket that receives uploaded documents for the Eventarc ingestion path."
  type        = string
}

variable "gcs_force_destroy" {
  description = "If true, allows Terraform to delete the ingestion bucket even if it still contains objects. Use with caution."
  type        = bool
  default     = false
}

############################################
# BigQuery
############################################

variable "bq_dataset_id" {
  description = "BigQuery dataset ID for application storage and logging."
  type        = string
  default     = "snow_app"
}

variable "bq_documents_table_id" {
  description = "BigQuery table ID for parsed/extracted document results."
  type        = string
  default     = "documents"
}

variable "bq_logs_table_id" {
  description = "BigQuery table ID for request-level audit/logging records."
  type        = string
  default     = "request_logs"
}

variable "bq_dataset_location" {
  description = "Location for the BigQuery dataset (region or multi-region, e.g. US, EU, us-central1)."
  type        = string
  default     = "US"
}

############################################
# Networking (optional, disabled by default)
############################################

variable "enable_vpc_connector" {
  description = "Whether to provision a VPC + Serverless VPC Access Connector so Cloud Run can reach Vertex AI/BigQuery/GCS over Private Google Access. Off by default since these APIs are reachable over the public Google API surface without it."
  type        = bool
  default     = false
}

variable "vpc_connector_cidr" {
  description = "CIDR range (/28) for the Serverless VPC Access Connector, used only when enable_vpc_connector = true."
  type        = string
  default     = "10.8.0.0/28"
}

############################################
# Labels
############################################

variable "labels" {
  description = "Common labels applied to supported resources."
  type        = map(string)
  default = {
    app       = "snow-app"
    managed_by = "terraform"
  }
}
