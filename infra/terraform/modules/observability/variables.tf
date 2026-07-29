variable "name" { type = string }
variable "region" { type = string }

variable "alb_arn_suffix" { type = string }
variable "target_group_arn_suffix" { type = string }
variable "cluster_name" { type = string }
variable "service_name" { type = string }
variable "log_group_name" { type = string }
variable "database_identifier" { type = string }
variable "redis_id" { type = string }

variable "alert_emails" {
  description = "Addresses subscribed to the SNS topic. Each must confirm the subscription by email before it delivers anything."
  type        = list(string)
  default     = []
}

variable "extra_alarm_actions" {
  description = "Additional SNS/Lambda/Chatbot ARNs — a PagerDuty or Slack integration."
  type        = list(string)
  default     = []
}

variable "error_threshold" {
  description = "Errors per period before alarming. Not zero: a single transient failure should not page anyone."
  type        = number
  default     = 5
}

variable "latency_threshold_seconds" {
  description = "p95 target-response-time ceiling. Generous by default because the copilot endpoints call an LLM."
  type        = number
  default     = 3
}

variable "rds_free_storage_bytes" {
  description = "Alarm when free storage drops below this. 10 GB by default."
  type        = number
  default     = 10737418240
}

variable "rds_connection_threshold" {
  description = <<-EOT
    Connection count that means the pools are exhausting the instance. Set below
    the instance class's max_connections: db.t4g.medium allows ~410, and each
    task holds up to NXR_DB_POOL_MAX (10).
  EOT
  type    = number
  default = 300
}

variable "tags" {
  type    = map(string)
  default = {}
}
