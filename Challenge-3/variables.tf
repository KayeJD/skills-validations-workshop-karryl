variable "project_id" {
  type        = string
  description = "Target Google Cloud Project ID"
  default = "qwiklabs-gcp-00-5ef258aa3bef"
}

variable "region" {
  type        = string
  description = "Target GCP deployment region"
  default     = "us-central1"
}

variable "container_image" {
  type        = string
  description = "URI of the Docker image to deploy"
  default     = "us-docker.pkg.dev/cloudrun/container/hello" # Placeholder fallback image
}