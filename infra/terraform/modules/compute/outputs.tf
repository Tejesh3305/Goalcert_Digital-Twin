output "cluster_name" { value = aws_ecs_cluster.main.name }
output "cluster_arn" { value = aws_ecs_cluster.main.arn }
output "service_name" { value = aws_ecs_service.app.name }
output "task_definition_family" { value = aws_ecs_task_definition.app.family }
output "task_definition_arn" { value = aws_ecs_task_definition.app.arn }

output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
output "ecr_repository_arn" { value = aws_ecr_repository.app.arn }

output "log_group_name" { value = aws_cloudwatch_log_group.app.name }

output "task_role_arn" { value = aws_iam_role.task.arn }
output "execution_role_arn" { value = aws_iam_role.execution.arn }
