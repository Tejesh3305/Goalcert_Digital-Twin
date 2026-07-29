/**
 * environments/prod — production, as a thin wrapper over the root composition.
 *
 * Nothing is DEFINED here; everything is chosen here. That separation is what
 * makes staging a copy of production with three numbers changed rather than a
 * different system that stops predicting its behaviour.
 */

terraform {
  required_version = ">= 1.6"

  backend "s3" {
    bucket = "nextxr-tfstate"
    key    = "prod/terraform.tfstate"
    region = "eu-west-1"

    # State holds the RDS and Redis passwords and the generated JWT key.
    encrypt = true
    # Two people running apply at once corrupts state. The lock table makes the
    # second one wait instead.
    dynamodb_table = "nextxr-tflock"
  }
}

module "platform" {
  source = "../.."

  environment = "prod"
  region      = var.region

  domain_name      = var.domain_name
  blob_bucket_name = var.blob_bucket_name

  # Production posture: Multi-AZ everywhere, deletion protection on, no
  # self-service signup, no production shell.
  multi_az               = true
  deletion_protection    = true
  allow_signup           = false
  enable_execute_command = false
  enable_waf             = true
  backup_retention_days  = 30

  desired_count = 2
  min_capacity  = 2
  max_capacity  = 10

  db_instance_class = "db.t4g.medium"
  cache_node_type   = "cache.t4g.small"

  neo4j_uri  = var.neo4j_uri
  neo4j_user = var.neo4j_user

  alert_emails         = var.alert_emails
  github_oidc_subjects = var.github_oidc_subjects

  tags = { CostCentre = "platform" }
}
