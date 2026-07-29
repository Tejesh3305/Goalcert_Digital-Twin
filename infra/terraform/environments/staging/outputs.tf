output "application_url" { value = module.platform.application_url }
output "alb_dns_name" { value = module.platform.alb_dns_name }
output "certificate_validation_records" { value = module.platform.certificate_validation_records }

output "ecr_repository_url" { value = module.platform.ecr_repository_url }
output "ecs_cluster_name" { value = module.platform.ecs_cluster_name }
output "ecs_service_name" { value = module.platform.ecs_service_name }
output "task_definition_family" { value = module.platform.task_definition_family }
output "github_deploy_role_arn" { value = module.platform.github_deploy_role_arn }

output "dashboard_url" { value = module.platform.dashboard_url }
output "next_steps" { value = module.platform.next_steps }
