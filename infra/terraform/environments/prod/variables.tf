variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "domain_name" {
  description = "The hostname this environment serves."
  type        = string
}

variable "blob_bucket_name" {
  description = "Globally unique S3 bucket name."
  type        = string
}

variable "neo4j_uri" {
  description = "neo4j+s:// URI. The PASSWORD goes in Secrets Manager, never here."
  type        = string
  default     = ""
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
  description = "Must name a specific ref or environment — never a ':*' wildcard."
  type        = list(string)
}
