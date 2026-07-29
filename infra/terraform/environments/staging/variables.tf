variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "domain_name" { type = string }
variable "blob_bucket_name" { type = string }

variable "neo4j_uri" {
  type    = string
  default = ""
}

variable "neo4j_user" {
  type    = string
  default = "neo4j"
}

variable "alert_emails" {
  type    = list(string)
  default = []
}

variable "github_oidc_subjects" {
  type = list(string)
}

variable "create_oidc_provider" {
  description = "False when prod already created the account-wide GitHub OIDC provider."
  type        = bool
  default     = false
}

variable "existing_oidc_provider_arn" {
  description = "Required when create_oidc_provider is false. From the prod stack's oidc_provider_arn."
  type        = string
  default     = ""
}
