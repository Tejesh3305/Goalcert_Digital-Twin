variable "name" {
  description = "Name prefix for every resource in this module."
  type        = string
}

variable "region" {
  description = "AWS region — needed for the S3 gateway endpoint's service name."
  type        = string
}

variable "vpc_cidr" {
  description = "The VPC's CIDR. /16 leaves room for the /24 subnets carved below."
  type        = string
  default     = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  description = <<-EOT
    One NAT for both AZs (cheaper) or one per AZ (survives an AZ failure without
    losing outbound connectivity). Outbound only — inbound requests keep being
    served either way.
  EOT
  type        = bool
  default     = true
}

variable "container_port" {
  description = "The port the app listens on, opened from the ALB to the tasks."
  type        = number
  default     = 8080
}

variable "tags" {
  description = "Tags applied to everything."
  type        = map(string)
  default     = {}
}
