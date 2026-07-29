variable "name" { type = string }

variable "database_url" {
  description = "Seeded once from the data module's output. Never re-read afterwards."
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Seeded once from the data module's output. Never re-read afterwards."
  type        = string
  sensitive   = true
}

variable "recovery_window_days" {
  description = <<-EOT
    Days a deleted secret can be recovered. 0 allows immediate reuse of the name
    (convenient for staging teardown/rebuild); production should use 7+, because
    it is the undo for an accidental deletion.
  EOT
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
