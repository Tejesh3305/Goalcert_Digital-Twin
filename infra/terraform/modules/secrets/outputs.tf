# The map the compute module feeds into the task definition's `secrets` block:
# environment-variable name -> Secrets Manager ARN.
output "env_secret_arns" {
  value = {
    NXR_DATABASE_URL   = aws_secretsmanager_secret.main["database-url"].arn
    NXR_REDIS_URL      = aws_secretsmanager_secret.main["redis-url"].arn
    NXR_JWT_SECRET     = aws_secretsmanager_secret.main["jwt-secret"].arn
    NXR_SECRET_PEPPER  = aws_secretsmanager_secret.main["secret-pepper"].arn
    ANTHROPIC_API_KEY  = aws_secretsmanager_secret.main["anthropic-api-key"].arn
    NEO4J_PASSWORD     = aws_secretsmanager_secret.main["neo4j-password"].arn
  }
}

# The flat list the execution role is granted `secretsmanager:GetSecretValue` on.
# Explicitly enumerated rather than a wildcard.
output "secret_arns" {
  value = [for s in aws_secretsmanager_secret.main : s.arn]
}

# Named so the deploy runbook and the README can print the exact commands.
output "manual_secrets" {
  description = "Secrets Terraform seeded with a placeholder. Set these before the first deploy or the app will fail on them."
  value = [
    aws_secretsmanager_secret.main["anthropic-api-key"].name,
    aws_secretsmanager_secret.main["neo4j-password"].name,
  ]
}
