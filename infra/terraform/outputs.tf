output "alb_dns_name" {
  description = "Point your domain's CNAME (or a Route 53 alias) at this."
  value       = module.edge.alb_dns_name
}

output "application_url" {
  value = "https://${var.domain_name}"
}

output "certificate_validation_records" {
  description = "Create these DNS records, then apply again. Until they exist the certificate stays PENDING and the HTTPS listener cannot come up."
  value       = module.edge.certificate_validation_records
}

output "ecr_repository_url" {
  description = "Set as the ECR_REPOSITORY repository variable in GitHub."
  value       = module.compute.ecr_repository_url
}

output "ecs_cluster_name" { value = module.compute.cluster_name }
output "ecs_service_name" { value = module.compute.service_name }
output "task_definition_family" { value = module.compute.task_definition_family }
output "log_group_name" { value = module.compute.log_group_name }

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable in GitHub."
  value       = module.cicd.deploy_role_arn
}

output "dashboard_url" { value = module.observability.dashboard_url }
output "alerts_topic_arn" { value = module.observability.alerts_topic_arn }

output "blob_bucket" { value = module.data.blob_bucket }
output "database_endpoint" { value = module.data.database_endpoint }

output "manual_secrets" {
  description = "Set these before the first deploy — Terraform seeded them with a placeholder on purpose."
  value       = module.secrets.manual_secrets
}

# The exact next steps, printed after apply. A runbook someone has to find in a
# document is a runbook that gets a step skipped.
output "next_steps" {
  value = <<-EOT

    ┌─ NEXT STEPS ────────────────────────────────────────────────────────┐

    1. DNS — create the certificate validation records above, then re-apply.
       Then point ${var.domain_name} at ${module.edge.alb_dns_name}

    2. SECRETS — Terraform created these with a placeholder. Set them:

         aws secretsmanager put-secret-value \
           --secret-id ${local.name}/anthropic-api-key \
           --secret-string 'sk-ant-...'

         aws secretsmanager put-secret-value \
           --secret-id ${local.name}/neo4j-password \
           --secret-string '...'

    3. GITHUB — add these repository VARIABLES (Settings > Secrets and
       variables > Actions > Variables). None is a secret:

         AWS_DEPLOY_ROLE_ARN = ${module.cicd.deploy_role_arn}
         AWS_REGION          = ${var.region}
         ECR_REPOSITORY      = ${module.compute.ecr_repository_url}
         ECS_CLUSTER         = ${module.compute.cluster_name}
         ECS_SERVICE         = ${module.compute.service_name}
         ECS_TASK_FAMILY     = ${module.compute.task_definition_family}

    4. DEPLOY — push to the branch named in github_oidc_subjects. CI builds
       the image, runs the migration task, then rolls the service.

    5. BOOTSTRAP THE FIRST ADMIN — one deploy only, then REMOVE these from
       the task definition (they are a standing "create an admin" instruction
       otherwise):

         NXR_BOOTSTRAP_ADMIN_EMAIL / NXR_BOOTSTRAP_ADMIN_PASSWORD

    6. VERIFY — read the posture lines in CloudWatch after the rollout:

         aws logs tail ${module.compute.log_group_name} --since 5m \
           | grep -E '\[(auth|tenancy|identity|db|blobs|bus|migrations)\]'

       Dashboard: ${module.observability.dashboard_url}

    └─────────────────────────────────────────────────────────────────────┘
  EOT
}
