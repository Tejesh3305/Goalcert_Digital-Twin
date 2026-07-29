variable "name" {
  description = "Base name. Combined with `environment` to prefix every resource."
  type        = string
  default     = "nextxr"
}

variable "environment" {
  description = "prod | staging | dev. Part of every resource name, so two environments never collide in one account."
  type        = string

  validation {
    condition     = contains(["prod", "staging", "dev"], var.environment)
    error_message = "environment must be prod, staging or dev."
  }
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

# ── Network ──────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  type    = bool
  default = true
}

# ── Compute ──────────────────────────────────────────────────────────────

variable "image" {
  description = <<-EOT
    The image the service starts with. CI replaces it on every deploy and the
    service ignores changes to `task_definition`, so this value only matters for
    the very first apply — before any image has been pushed. It therefore points
    at a public placeholder that starts and stays healthy long enough for the
    infrastructure to converge.
  EOT
  type    = string
  default = "public.ecr.aws/docker/library/busybox:latest"
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "task_cpu" {
  type    = string
  default = "1024"
}

variable "task_memory" {
  type    = string
  default = "2048"
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "min_capacity" {
  type    = number
  default = 2
}

variable "max_capacity" {
  type    = number
  default = 8
}

variable "enable_execute_command" {
  description = "Allow `aws ecs execute-command` — a shell inside a running task."
  type        = bool
  default     = false
}

# ── Data ─────────────────────────────────────────────────────────────────

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "cache_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "multi_az" {
  type    = bool
  default = true
}

variable "backup_retention_days" {
  type    = number
  default = 14
}

variable "deletion_protection" {
  description = "Blocks accidental destruction of the database and the load balancer. Leave true in production."
  type        = bool
  default     = true
}

variable "blob_bucket_name" {
  description = "Globally unique S3 bucket name for generated 3-D artifacts."
  type        = string
}

# ── Edge ─────────────────────────────────────────────────────────────────

variable "domain_name" {
  description = "Hostname served, e.g. twin.example.com."
  type        = string
}

variable "subject_alternative_names" {
  type    = list(string)
  default = []
}

variable "certificate_arn" {
  description = "An existing ACM certificate. Empty requests a new one (DNS validation)."
  type        = string
  default     = ""
}

variable "alb_access_logs_bucket" {
  type    = string
  default = ""
}

variable "enable_waf" {
  type    = bool
  default = true
}

# ── Application configuration ────────────────────────────────────────────

variable "cors_origins" {
  description = <<-EOT
    Origins allowed to call the API. EMPTY IS THE RIGHT ANSWER when the container
    serves the SPA and the API from one origin — the app then permits same-origin
    only. Set this only for a separately-hosted frontend (CloudFront).
  EOT
  type    = list(string)
  default = []
}

variable "allow_signup" {
  description = "Self-service signup. False makes the deployment invite-only, which is the usual choice for a B2B product."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "neo4j_uri" {
  description = "neo4j+s:// URI of the graph database. Not managed by Terraform — see AWS_DEPLOYMENT.md §6."
  type        = string
  default     = ""
}

variable "neo4j_user" {
  type    = string
  default = "neo4j"
}

variable "extra_environment" {
  description = "Additional non-secret environment variables for the task."
  type        = list(object({ name = string, value = string }))
  default     = []
}

# ── Observability ────────────────────────────────────────────────────────

variable "alert_emails" {
  description = "Addresses subscribed to the alarm topic. Each must confirm by email."
  type        = list(string)
  default     = []
}

# ── CI/CD ────────────────────────────────────────────────────────────────

variable "github_oidc_subjects" {
  description = "GitHub OIDC subjects allowed to deploy, e.g. repo:org/repo:ref:refs/heads/main."
  type        = list(string)
}

variable "create_oidc_provider" {
  description = "False if the AWS account already has the GitHub OIDC provider (it is account-wide)."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  type    = string
  default = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
