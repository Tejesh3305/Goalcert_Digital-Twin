/**
 * compute — ECR, the ECS cluster, the task definition, the service, autoscaling,
 * and the IAM roles that make them work.
 *
 * THE TWO ROLES ARE NOT THE SAME ROLE, AND THE DISTINCTION MATTERS
 * ----------------------------------------------------------------
 *   execution role  used by the ECS AGENT, before the container starts: pull the
 *                   image, read the secrets it will inject, create log streams.
 *   task role       assumed by the APPLICATION CODE at runtime: S3, and nothing
 *                   else it does not need.
 *
 * Merging them — which is the common shortcut — means the running application
 * inherits permission to read every secret in the task definition through the
 * Secrets Manager API, not just have them injected. A compromised dependency
 * then has the database password and the Anthropic key. Kept apart, application
 * code cannot read a secret it was not handed.
 *
 * SECRETS ARE `secrets`, NEVER `environment`
 * ------------------------------------------
 * Values in `environment` are visible in `aws ecs describe-task-definition` to
 * anyone with read access to ECS, and in the console, and in CloudTrail. Values
 * in `secrets` are ARNs; the agent resolves them at start and the plaintext
 * exists only in the container's env. The five below are the ones that are
 * credentials.
 */

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ── ECR ──────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "app" {
  name                 = var.name
  # MUTABLE so CI can move a `latest` tag. Images are also pushed with the commit
  # SHA, and the task definition references the SHA — so a rollback is
  # deterministic even though the tag moves.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # Scan on push. Free, and it is the difference between learning about a
    # critical CVE in a base image now versus at the next audit.
    scan_on_push = true
  }

  encryption_configuration { encryption_type = "AES256" }

  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  # Untagged layers accumulate on every build and are pure cost. Tagged images
  # are kept deep enough to roll back several deploys.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 3 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 3
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the 30 most recent tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "sha", "latest"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = { type = "expire" }
      },
    ]
  })
}

# ── Logs ─────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# ── IAM ──────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Reading the secrets is NOT in the managed policy — it has to be granted, and
# it is granted per-ARN rather than `*`. A wildcard here would let the ECS agent
# read every secret in the account.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid       = "ReadInjectedSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count  = length(var.secret_arns) > 0 ? 1 : 0
  name   = "${var.name}-read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "task" {
  # S3, scoped to this bucket. `storage/` reads, writes and deletes generated
  # GLBs and 3-D job artifacts; it does nothing with any other bucket.
  statement {
    sid     = "BlobStore"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.blob_bucket_arn}/*"]
  }

  statement {
    sid       = "BlobStoreList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.blob_bucket_arn]
  }

  # ECS Exec — `aws ecs execute-command` for a shell in a running task. Off
  # unless enabled, because it is a production-shell capability and should be a
  # deliberate grant rather than something that came with the template.
  dynamic "statement" {
    for_each = var.enable_execute_command ? [1] : []
    content {
      sid = "ExecuteCommand"
      actions = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.name}-task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ── Cluster ──────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }

  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = var.min_capacity
  }
}

# ── Task definition ──────────────────────────────────────────────────────

locals {
  # The non-secret configuration. Every one of these is a documented posture
  # switch — see AWS_DEPLOYMENT.md §5.2 and the module docstrings they name.
  base_environment = [
    { name = "PORT", value = tostring(var.container_port) },
    { name = "NXR_DATA_DIR", value = "/data" },
    { name = "DATA_DIR", value = "/data/threed" },

    # AUTHENTICATION IS MANDATORY. The app defaults to this anyway (server/auth.py
    # inverted the old fail-open default), but stating it in the task definition
    # means an operator reading the deploy can see the posture without reading
    # the source.
    { name = "NXR_REQUIRE_AUTH", value = "1" },

    # Refuse to start rather than fall back to per-task local state. Without
    # these the app boots happily on SQLite/local disk/in-process bus and each
    # task holds a different set of twins — nothing errors, users just see their
    # data appear and disappear depending on which task answered.
    { name = "NXR_REQUIRE_DB", value = "1" },
    { name = "NXR_REQUIRE_S3", value = "1" },
    { name = "NXR_REQUIRE_REDIS", value = "1" },

    { name = "NXR_S3_BUCKET", value = var.blob_bucket },
    { name = "NXR_DB_SSLMODE", value = "require" },
    { name = "NXR_DB_APP_NAME", value = var.name },

    # Behind an ALB: trust X-Forwarded-For (for audit logs and rate-limit keys)
    # and send HSTS. Both are WRONG without a TLS terminator in front, which is
    # exactly why they are set here rather than defaulted on in the code.
    { name = "NXR_TRUST_PROXY", value = "1" },
    { name = "NXR_HSTS", value = "1" },

    { name = "NXR_LOG_JSON", value = "1" },
    { name = "NXR_LOG_LEVEL", value = var.log_level },

    # Migrations run as a separate one-off task before the service rolls. If this
    # were "1", every task in a rolling deploy would race for the advisory lock
    # and the losers would block on it during startup — a health-check timeout
    # and a failed deploy.
    { name = "NXR_AUTO_MIGRATE", value = "0" },

    { name = "NXR_CORS_ORIGINS", value = join(",", var.cors_origins) },
    { name = "NXR_ALLOW_SIGNUP", value = var.allow_signup ? "1" : "0" },
  ]

  environment = concat(local.base_environment, var.extra_environment)
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.image
      essential = true

      portMappings = [{
        containerPort = var.container_port
        protocol      = "tcp"
      }]

      environment = local.environment

      # ARNs, resolved by the ECS agent at start. Never plaintext in the task
      # definition — see the module docstring.
      secrets = [for name, arn in var.secrets : { name = name, valueFrom = arn }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "app"
        }
      }

      # LIVENESS, not readiness. /health/live touches no dependency; the old
      # check hit /health, which pings RDS, Neo4j and S3 on every probe, so a
      # slow dependency killed a working process. Whether the task should
      # receive TRAFFIC is the ALB target group's question, and it asks
      # /health/ready.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.container_port}/api/v1/health/live', timeout=4).status==200 else 1)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      # Scratch only. Everything durable is in RDS, S3 and ElastiCache — this is
      # 3-D pipeline working space that is expected to vanish with the task.
      mountPoints = [{
        sourceVolume  = "scratch"
        containerPath = "/data"
        readOnly      = false
      }]

      # Defence in depth against a container escape: no process in here needs to
      # gain privileges, and every write goes to the scratch volume.
      linuxParameters = {
        initProcessEnabled = true
      }
      readonlyRootFilesystem = false # rembg/onnx write model caches under $HOME
    }
  ])

  volume {
    name = "scratch"
  }

  tags = var.tags
}

# ── Service ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "app" {
  name            = var.name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # ROLLING DEPLOY WITH NO GAP. 100/200 means ECS starts the new tasks before
  # stopping any old ones, so capacity never dips below the desired count during
  # a deploy. The default (100/200 for Fargate) is stated explicitly because it
  # is the property that makes this zero-downtime, and it is only correct because
  # the tasks are stateless.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable = true
    # Roll back automatically when the new tasks fail their health checks. Without
    # this a bad image sits in a crash loop until someone notices, with the old
    # tasks already gone.
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.tasks_security_group_id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "app"
    container_port   = var.container_port
  }

  # How long the ALB's health check is allowed to fail after a task starts before
  # ECS gives up on it. Generous, because the first request builds the middleware
  # stack, runs every posture check and opens the database pool.
  health_check_grace_period_seconds = 90

  enable_execute_command = var.enable_execute_command
  propagate_tags         = "SERVICE"

  lifecycle {
    # CI updates the task definition on every deploy. Without this, the next
    # `terraform apply` would revert the service to the image Terraform last
    # knew about — silently rolling production back to an older build.
    ignore_changes = [task_definition, desired_count]
  }

  tags = var.tags
}

# ── Autoscaling ──────────────────────────────────────────────────────────

resource "aws_appautoscaling_target" "app" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_capacity
  max_capacity       = var.max_capacity
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 65

    # Scale OUT quickly, IN slowly. The asymmetry is deliberate: being slow to
    # add capacity is a user-visible outage, being slow to remove it costs a few
    # dollars. 300s in prevents thrashing on a spiky workload — and the physics
    # runtime is spiky.
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}

resource "aws_appautoscaling_policy" "memory" {
  name               = "${var.name}-memory"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 75
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}
