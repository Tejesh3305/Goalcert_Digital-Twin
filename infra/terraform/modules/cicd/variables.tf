variable "name" { type = string }

variable "allowed_subjects" {
  description = <<-EOT
    GitHub OIDC subjects permitted to assume the deploy role. Each should name a
    specific ref or environment:

      repo:my-org/nextxr-ontology-v3:ref:refs/heads/main
      repo:my-org/nextxr-ontology-v3:environment:production

    NEVER `repo:my-org/repo:*` — that lets any branch deploy to production, so
    anyone who can push a branch can ship.
  EOT
  type = list(string)

  validation {
    condition     = alltrue([for s in var.allowed_subjects : !endswith(s, ":*")])
    error_message = "A subject ending in ':*' allows any branch to deploy. Name a ref or an environment."
  }
}

variable "create_oidc_provider" {
  description = "False when the account already has the GitHub OIDC provider (it is account-wide, not per-repo)."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  type    = string
  default = ""
}

variable "ecr_repository_arn" { type = string }
variable "cluster_arn" { type = string }
variable "log_group_arn" { type = string }

variable "passable_role_arns" {
  description = "The task and execution role ARNs. Scoping PassRole to exactly these prevents privilege escalation via task definitions."
  type        = list(string)
}

variable "frontend_bucket_arn" {
  description = "S3 bucket for the built SPA, when it is served from CloudFront instead of the container. Empty omits the grant."
  type        = string
  default     = ""
}

variable "cloudfront_distribution_arn" {
  type    = string
  default = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
