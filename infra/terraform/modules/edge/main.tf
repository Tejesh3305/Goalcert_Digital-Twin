/**
 * edge — the ALB, its TLS certificate, and the target group.
 *
 * THE TARGET GROUP HEALTH CHECK POINTS AT /health/ready, NOT /health.
 *
 * That is the single most important line in this file. `/api/v1/health` NEVER
 * returns non-200 — it is a diagnostic that reports "degraded" in its body, by
 * design, so a Neo4j blip does not roll the fleet. As a target-group check it is
 * therefore useless: a task with no database stays in rotation and serves errors,
 * and a rolling deploy shifts all traffic to new tasks before they can answer.
 *
 * `/api/v1/health/ready` is allowed to 503, which is what lets the ALB drain a
 * task that cannot serve without ECS killing it. See `server/query_api.py`.
 */

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_lb" "main" {
  name               = var.name
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [var.alb_security_group_id]

  # Survives a `terraform destroy` of everything else. The ALB owns the DNS name
  # customers have bookmarked and any Route 53 alias pointing at it.
  enable_deletion_protection = var.deletion_protection

  # Longer than the default 60s because the SSE stream (/api/v1/bus/stream) sends
  # a keepalive every ~15s but a quiet twin can still go a while between events.
  # Too short and the browser sees the live dashboard disconnect every minute.
  idle_timeout = 180

  # HTTP desync protection. `defensive` is the AWS default and the right one:
  # it drops requests with ambiguous Content-Length/Transfer-Encoding rather than
  # forwarding them, which is the class of bug behind request smuggling.
  desync_mitigation_mode = "defensive"

  drop_invalid_header_fields = true

  dynamic "access_logs" {
    for_each = var.access_logs_bucket != "" ? [1] : []
    content {
      bucket  = var.access_logs_bucket
      prefix  = var.name
      enabled = true
    }
  }

  tags = var.tags
}

resource "aws_lb_target_group" "app" {
  name        = "${var.name}-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # awsvpc network mode

  health_check {
    enabled             = true
    path                = "/api/v1/health/ready"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    # Five failures at 15s intervals before a task is pulled. Deliberately not
    # tighter: the readiness probe checks RDS, and one slow query should not
    # remove a task that is otherwise serving.
    unhealthy_threshold = 5
  }

  # How long the ALB waits for in-flight requests before killing a draining
  # target. Must exceed the longest ordinary request; the copilot endpoints call
  # an LLM and can legitimately take 30s+.
  deregistration_delay = 60

  # Sticky sessions are OFF and must stay off. The tasks are stateless — that is
  # the property RDS/S3/Redis were introduced to achieve — and stickiness would
  # both hide a regression of it and unbalance the fleet.
  stickiness {
    type    = "lb_cookie"
    enabled = false
  }

  tags = var.tags
}

# ── TLS ──────────────────────────────────────────────────────────────────

resource "aws_acm_certificate" "main" {
  count = var.certificate_arn == "" ? 1 : 0

  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = "DNS"

  lifecycle {
    # Replacing a certificate in place briefly leaves the listener without one.
    create_before_destroy = true
  }

  tags = var.tags
}

locals {
  certificate_arn = var.certificate_arn != "" ? var.certificate_arn : aws_acm_certificate.main[0].arn
}

# ── Listeners ────────────────────────────────────────────────────────────

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"

  # TLS 1.2 minimum, forward secrecy only. TLS 1.3 is included in this policy;
  # the older `ELBSecurityPolicy-2016-08` default still permits TLS 1.0/1.1,
  # which fails a PCI scan and any serious security questionnaire.
  ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = local.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = var.tags
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # 301, not 302: permanent, so browsers and intermediaries stop asking. Paired
  # with the HSTS header the app sends (NXR_HSTS=1), a returning browser never
  # makes the plaintext request at all.
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = var.tags
}

# ── WAF ──────────────────────────────────────────────────────────────────
#
# Optional, and worth turning on for anything public. The app has its own rate
# limiter (server/ratelimit.py), which is per-credential and understands the
# routes; this is the blunt layer in front of it that absorbs volumetric abuse
# before it costs us a Fargate task.

resource "aws_wafv2_web_acl" "main" {
  count = var.enable_waf ? 1 : 0

  name  = var.name
  scope = "REGIONAL"

  default_action { allow {} }

  rule {
    name     = "AWSManagedCommonRuleSet"
    priority = 1

    override_action { none {} }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"

        # The 3-D endpoints accept base64 image payloads well over the 8KB body
        # limit this rule enforces, and the plan-image upload is legitimately
        # megabytes. Left on, every scan upload is blocked as an attack.
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use { allow {} }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedKnownBadInputs"
    priority = 2

    override_action { none {} }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Volumetric ceiling per IP. Far above what the app's own limiter allows, on
  # purpose — this catches the flood, the application limiter catches the abuse.
  rule {
    name     = "RateLimit"
    priority = 3

    action { block {} }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = true
  }

  tags = var.tags
}

resource "aws_wafv2_web_acl_association" "main" {
  count = var.enable_waf ? 1 : 0

  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main[0].arn
}
