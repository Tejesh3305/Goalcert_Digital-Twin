/**
 * cicd — the IAM role GitHub Actions assumes to deploy. NO ACCESS KEYS.
 *
 * WHY OIDC AND NOT A SECRET ACCESS KEY
 * ------------------------------------
 * The usual setup puts an IAM user's access key in a GitHub secret. That key is
 * a long-lived credential which:
 *   - never expires unless someone remembers to rotate it,
 *   - is readable by anyone who can edit a workflow file (a malicious PR that
 *     adds `run: echo $AWS_SECRET_ACCESS_KEY | curl ...` exfiltrates it),
 *   - and is invisible to CloudTrail as "which workflow used this".
 *
 * OIDC issues a credential that lasts one job. GitHub signs a token asserting
 * the repository, the ref and the workflow; AWS verifies it against the trust
 * policy below and hands back a session. There is no secret to leak, and the
 * assume-role event in CloudTrail names the exact repo and branch.
 *
 * THE TRUST POLICY IS SCOPED TO A BRANCH, WHICH IS THE POINT
 * ----------------------------------------------------------
 * `repo:ORG/REPO:ref:refs/heads/main` — not `repo:ORG/REPO:*`. With a wildcard,
 * ANY branch can assume this role, so anyone who can push a branch (or open a PR
 * from a fork, depending on settings) can deploy to production. Scoping it to
 * the deploy branch means the only path to production is a merge, which is a
 * reviewable event.
 */

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_caller_identity" "current" {}

# The OIDC provider is ACCOUNT-WIDE — one per AWS account, not per repository.
# `create_oidc_provider = false` lets a second environment reuse an existing one
# instead of failing with EntityAlreadyExists.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  # GitHub's intermediate CA thumbprint. AWS stopped verifying this for the
  # GitHub provider specifically, but the field is still required.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = var.tags
}

locals {
  oidc_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringLike, because `var.allowed_subjects` may legitimately contain a
    # pattern such as `repo:org/repo:environment:production`. Every entry should
    # still name a specific ref or environment — see the module docstring.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = var.allowed_subjects
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${var.name}-github-deploy"
  description        = "Assumed by GitHub Actions via OIDC to build, push and deploy. No static credentials."
  assume_role_policy = data.aws_iam_policy_document.assume.json
  # One hour. A deploy job takes minutes; a long session is only useful to
  # someone who has stolen it.
  max_session_duration = 3600

  tags = var.tags
}

data "aws_iam_policy_document" "deploy" {
  # Push images. GetAuthorizationToken cannot be resource-scoped — the API takes
  # no resource — so it is `*` by necessity. Everything else is scoped to this
  # one repository.
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [var.ecr_repository_arn]
  }

  # Register a new task definition revision and roll the service.
  statement {
    sid = "DeployService"
    actions = [
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
      "ecs:RegisterTaskDefinition",
      "ecs:UpdateService",
    ]
    resources = ["*"] # RegisterTaskDefinition and DescribeTaskDefinition are not resource-scopable
  }

  # Run the one-off migration task before the service rolls.
  statement {
    sid       = "RunMigrationTask"
    actions   = ["ecs:RunTask"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.cluster_arn]
    }
  }

  # Registering a task definition means naming the roles it will use, which
  # requires PassRole. Scoped to exactly the two roles — without the condition,
  # this permits passing ANY role to ECS, which is a privilege-escalation path to
  # whatever the most powerful role in the account can do.
  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = var.passable_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # Publish the built SPA to the CDN bucket, when the frontend is served from
  # CloudFront rather than from the container.
  dynamic "statement" {
    for_each = var.frontend_bucket_arn != "" ? [1] : []
    content {
      sid       = "PublishFrontend"
      actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      resources = [var.frontend_bucket_arn, "${var.frontend_bucket_arn}/*"]
    }
  }

  dynamic "statement" {
    for_each = var.cloudfront_distribution_arn != "" ? [1] : []
    content {
      sid       = "InvalidateCdn"
      actions   = ["cloudfront:CreateInvalidation"]
      resources = [var.cloudfront_distribution_arn]
    }
  }

  # Read the deploy log after a rollout, so the workflow can surface the posture
  # lines rather than making someone open the console.
  statement {
    sid       = "ReadDeployLogs"
    actions   = ["logs:GetLogEvents", "logs:DescribeLogStreams", "logs:FilterLogEvents"]
    resources = ["${var.log_group_arn}:*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "${var.name}-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
