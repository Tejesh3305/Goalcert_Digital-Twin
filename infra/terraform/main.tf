/**
 * The root composition: wires the modules together in dependency order.
 *
 * An environment (`environments/prod`) is a thin wrapper that supplies variables
 * to this. The modules do not reference each other — everything flows through
 * this file — so the dependency graph is readable in one place and a module can
 * be replaced without hunting for hidden couplings.
 *
 * FIRST APPLY WILL NOT FULLY SUCCEED, AND THAT IS EXPECTED.
 * The ACM certificate needs a DNS record that does not exist until Terraform has
 * told you what it is. The sequence is:
 *
 *   1. terraform apply          -> creates everything; the HTTPS listener waits
 *   2. read `certificate_validation_records`, create those CNAMEs
 *   3. terraform apply          -> the certificate validates, the listener comes up
 *   4. set the two manual secrets (see `manual_secrets`)
 *   5. push to main             -> CI builds, migrates and deploys
 */

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

locals {
  tags = merge(
    {
      Application = "nextxr-twin"
      Environment = var.environment
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  name = "${var.name}-${var.environment}"
}

module "network" {
  source = "./modules/network"

  name               = local.name
  region             = var.region
  vpc_cidr           = var.vpc_cidr
  single_nat_gateway = var.single_nat_gateway
  container_port     = var.container_port
  tags               = local.tags
}

module "data" {
  source = "./modules/data"

  name                       = local.name
  private_subnet_ids         = module.network.private_subnet_ids
  database_security_group_id = module.network.database_security_group_id
  cache_security_group_id    = module.network.cache_security_group_id

  db_instance_class     = var.db_instance_class
  db_allocated_storage  = var.db_allocated_storage
  cache_node_type       = var.cache_node_type
  multi_az              = var.multi_az
  backup_retention_days = var.backup_retention_days
  deletion_protection   = var.deletion_protection

  blob_bucket_name  = var.blob_bucket_name
  blob_cors_origins = var.cors_origins

  tags = local.tags
}

module "secrets" {
  source = "./modules/secrets"

  name         = local.name
  database_url = module.data.database_url
  redis_url    = module.data.redis_url
  # Staging is torn down and rebuilt; production keeps the undo window.
  recovery_window_days = var.deletion_protection ? 7 : 0

  tags = local.tags
}

module "edge" {
  source = "./modules/edge"

  name                  = local.name
  vpc_id                = module.network.vpc_id
  public_subnet_ids     = module.network.public_subnet_ids
  alb_security_group_id = module.network.alb_security_group_id
  container_port        = var.container_port

  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  certificate_arn           = var.certificate_arn
  deletion_protection       = var.deletion_protection
  access_logs_bucket        = var.alb_access_logs_bucket
  enable_waf                = var.enable_waf

  tags = local.tags
}

module "compute" {
  source = "./modules/compute"

  name  = local.name
  image = var.image

  private_subnet_ids      = module.network.private_subnet_ids
  tasks_security_group_id = module.network.tasks_security_group_id
  target_group_arn        = module.edge.target_group_arn
  container_port          = var.container_port

  blob_bucket     = module.data.blob_bucket
  blob_bucket_arn = module.data.blob_bucket_arn

  secrets     = module.secrets.env_secret_arns
  secret_arns = module.secrets.secret_arns

  task_cpu               = var.task_cpu
  task_memory            = var.task_memory
  desired_count          = var.desired_count
  min_capacity           = var.min_capacity
  max_capacity           = var.max_capacity
  log_retention_days     = var.log_retention_days
  log_level              = var.log_level
  cors_origins           = var.cors_origins
  allow_signup           = var.allow_signup
  enable_execute_command = var.enable_execute_command

  # Neo4j is not managed here (Aura is a separate signup — AWS_DEPLOYMENT.md §6),
  # so its URI arrives as configuration. The PASSWORD is a secret, injected via
  # the secrets module rather than appearing in this list.
  extra_environment = concat(
    var.neo4j_uri != "" ? [
      { name = "NEO4J_URI", value = var.neo4j_uri },
      { name = "NEO4J_USER", value = var.neo4j_user },
    ] : [],
    var.extra_environment,
  )

  tags = local.tags
}

module "observability" {
  source = "./modules/observability"

  name   = local.name
  region = var.region

  alb_arn_suffix          = module.edge.alb_arn_suffix
  target_group_arn_suffix = module.edge.target_group_arn_suffix
  cluster_name            = module.compute.cluster_name
  service_name            = module.compute.service_name
  log_group_name          = module.compute.log_group_name
  database_identifier     = module.data.database_identifier
  redis_id                = module.data.redis_id

  alert_emails = var.alert_emails

  tags = local.tags
}

module "cicd" {
  source = "./modules/cicd"

  name             = local.name
  allowed_subjects = var.github_oidc_subjects

  create_oidc_provider = var.create_oidc_provider
  existing_oidc_provider_arn = var.existing_oidc_provider_arn

  ecr_repository_arn = module.compute.ecr_repository_arn
  cluster_arn        = module.compute.cluster_arn
  log_group_arn      = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${module.compute.log_group_name}"

  passable_role_arns = [
    module.compute.task_role_arn,
    module.compute.execution_role_arn,
  ]

  tags = local.tags
}

data "aws_caller_identity" "current" {}
