variable "name" { type = string }
variable "image" {
  description = "Full image reference. CI replaces this per deploy; Terraform only sets the initial value."
  type        = string
}

variable "private_subnet_ids" { type = list(string) }
variable "tasks_security_group_id" { type = string }
variable "target_group_arn" { type = string }
variable "blob_bucket" { type = string }
variable "blob_bucket_arn" { type = string }

variable "secrets" {
  description = <<-EOT
    Environment-variable name -> Secrets Manager ARN. Injected by the ECS agent
    at container start; never plaintext in the task definition.
  EOT
  type    = map(string)
  default = {}
}

variable "secret_arns" {
  description = "The ARNs the EXECUTION role may read. Kept separate from `secrets` so the IAM grant is an explicit list rather than derived from a map whose keys could change."
  type        = list(string)
  default     = []
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "task_cpu" {
  description = "Fargate CPU units. 1024 = 1 vCPU."
  type        = string
  default     = "1024"
}

variable "task_memory" {
  description = "MiB. Must be a valid pairing with task_cpu — Fargate rejects arbitrary combinations."
  type        = string
  default     = "2048"
}

variable "cpu_architecture" {
  description = "X86_64 or ARM64. ARM64 (Graviton) is ~20% cheaper; the image must be built for it."
  type        = string
  default     = "X86_64"
}

variable "desired_count" {
  description = "Initial task count. Autoscaling takes over afterwards, which is why the service ignores changes to it."
  type        = number
  default     = 2
}

variable "min_capacity" {
  type    = number
  default = 2
}

variable "max_capacity" {
  type    = number
  default = 8
}

variable "assign_public_ip" {
  description = "True only when running tasks in public subnets without NAT (a cost-saving staging shape)."
  type        = bool
  default     = false
}

variable "enable_execute_command" {
  description = "`aws ecs execute-command` — a shell in a running production task. A deliberate grant, not a default."
  type        = bool
  default     = false
}

variable "container_insights" {
  type    = bool
  default = true
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "cors_origins" {
  description = "Origins allowed to call the API. Empty means same-origin only, which is correct when the container serves the SPA too."
  type        = list(string)
  default     = []
}

variable "allow_signup" {
  description = "Self-service signup. False makes the deployment invite-only."
  type        = bool
  default     = false
}

variable "extra_environment" {
  description = "Additional non-secret environment variables, e.g. NEO4J_URI."
  type        = list(object({ name = string, value = string }))
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
