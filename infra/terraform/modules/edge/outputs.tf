output "alb_arn" { value = aws_lb.main.arn }
output "alb_dns_name" { value = aws_lb.main.dns_name }
output "alb_zone_id" { value = aws_lb.main.zone_id }
output "alb_arn_suffix" { value = aws_lb.main.arn_suffix }

output "target_group_arn" { value = aws_lb_target_group.app.arn }
output "target_group_arn_suffix" { value = aws_lb_target_group.app.arn_suffix }

output "certificate_arn" { value = local.certificate_arn }

# DNS records to create for certificate validation. Empty when an existing
# certificate ARN was supplied. Until these exist the cert stays PENDING and the
# HTTPS listener cannot come up — the most common first-apply stall.
output "certificate_validation_records" {
  value = var.certificate_arn == "" ? [
    for o in aws_acm_certificate.main[0].domain_validation_options : {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  ] : []
}
