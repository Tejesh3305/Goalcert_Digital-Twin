variable "name" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "alb_security_group_id" { type = string }

variable "container_port" {
  type    = number
  default = 8080
}

variable "domain_name" {
  description = "The hostname the certificate is issued for, e.g. twin.example.com."
  type        = string
}

variable "subject_alternative_names" {
  type    = list(string)
  default = []
}

variable "certificate_arn" {
  description = "An existing ACM certificate. Empty requests a new one (DNS validation — you must add the CNAME before the listener will come up)."
  type        = string
  default     = ""
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "access_logs_bucket" {
  description = "S3 bucket for ALB access logs. Empty disables them. The bucket needs the ELB service account in its policy."
  type        = string
  default     = ""
}

variable "enable_waf" {
  type    = bool
  default = true
}

variable "waf_rate_limit" {
  description = "Requests per 5 minutes per IP before WAF blocks. Deliberately far above the app's own limiter."
  type        = number
  default     = 10000
}

variable "tags" {
  type    = map(string)
  default = {}
}
