output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable in GitHub. Not a secret — an ARN is useless without the OIDC trust."
  value       = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" { value = local.oidc_arn }
