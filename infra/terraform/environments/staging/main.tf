/**
 * environments/staging — the same system, cheaper.
 *
 * The point of staging is to PREDICT production, so the differences are only the
 * ones that cost money and do not change behaviour: single-AZ, one task, a
 * smaller instance. The SHAPE is identical — same modules, same middleware
 * stack, same enforced authentication, same migration path — because a staging
 * environment that differs structurally tests a system nobody is going to run.
 *
 * Three deliberate differences that ARE behavioural, each for a reason:
 *   allow_signup           true, so the signup flow can be exercised
 *   enable_execute_command true, so a shell is available for debugging
 *   deletion_protection    false, so it can be torn down and rebuilt
 */

terraform {
  required_version = ">= 1.6"

  backend "s3" {
    bucket         = "nextxr-tfstate"
    key            = "staging/terraform.tfstate"
    region         = "eu-west-1"
    encrypt        = true
    dynamodb_table = "nextxr-tflock"
  }
}

module "platform" {
  source = "../.."

  environment = "staging"
  region      = var.region

  domain_name      = var.domain_name
  blob_bucket_name = var.blob_bucket_name

  # Cost. Roughly a third of production.
  multi_az              = false
  single_nat_gateway    = true
  desired_count         = 1
  min_capacity          = 1
  max_capacity          = 3
  db_instance_class     = "db.t4g.micro"
  cache_node_type       = "cache.t4g.micro"
  backup_retention_days = 7
  task_cpu              = "512"
  task_memory           = "1024"

  # Rebuildable, debuggable, and open for signup so the flow gets exercised.
  deletion_protection    = false
  enable_execute_command = true
  allow_signup           = true
  enable_waf             = false
  log_retention_days     = 7
  log_level              = "DEBUG"

  neo4j_uri  = var.neo4j_uri
  neo4j_user = var.neo4j_user

  alert_emails         = var.alert_emails
  github_oidc_subjects = var.github_oidc_subjects

  # The OIDC provider is account-wide. If prod created it in this account,
  # staging must reuse it rather than fail with EntityAlreadyExists.
  create_oidc_provider       = var.create_oidc_provider
  existing_oidc_provider_arn = var.existing_oidc_provider_arn

  tags = { CostCentre = "engineering" }
}
