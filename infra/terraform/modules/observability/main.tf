/**
 * observability — the alarms that page someone, and the dashboard they open next.
 *
 * WHAT IS ALARMED, AND WHAT IS DELIBERATELY NOT
 * ---------------------------------------------
 * An alarm that fires for something nobody acts on trains people to ignore
 * alarms, which is worse than having none. So this covers conditions that are
 * (a) user-visible or (b) about to become user-visible, and nothing else:
 *
 *   5xx rate            users are seeing errors right now
 *   unhealthy targets   capacity is gone or a deploy is failing
 *   p95 latency         the product is degrading before it breaks
 *   RDS CPU / storage   the store behind everything is running out
 *   RDS connections     the pool is exhausted; requests are about to queue
 *   Redis memory        eviction is imminent, which silently breaks the bus
 *
 * NOT alarmed: 4xx rate (users mistyping and unauthenticated probes are normal
 * traffic, and a 401 is the system working), task restarts (ECS replaces tasks
 * routinely), and the `/health` "degraded" state — that is a dashboard signal,
 * because the product is DESIGNED to keep serving through a Neo4j outage.
 */

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  alarm_actions = concat([aws_sns_topic.alerts.arn], var.extra_alarm_actions)
}

# ── Application ──────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "http_5xx" {
  alarm_name          = "${var.name}-5xx"
  alarm_description   = "The application is returning server errors to users."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_threshold
  evaluation_periods  = 2
  period              = 60
  statistic           = "Sum"

  namespace   = "AWS/ApplicationELB"
  metric_name = "HTTPCode_Target_5XX_Count"
  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  # A quiet period reports no datapoints, not zero. `notBreaching` stops the
  # alarm going INSUFFICIENT_DATA overnight and re-alerting at the first morning
  # request — a classic source of alarm fatigue.
  treat_missing_data = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  alarm_name          = "${var.name}-unhealthy-targets"
  alarm_description   = "Tasks are failing their readiness check — capacity is reduced or a deploy is failing."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 3
  period              = 60
  statistic           = "Maximum"

  namespace   = "AWS/ApplicationELB"
  metric_name = "UnHealthyHostCount"
  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  treat_missing_data = "notBreaching"
  alarm_actions      = local.alarm_actions
  ok_actions         = local.alarm_actions
  tags               = var.tags
}

resource "aws_cloudwatch_metric_alarm" "latency" {
  alarm_name          = "${var.name}-latency-p95"
  alarm_description   = "p95 response time is degrading."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.latency_threshold_seconds
  evaluation_periods  = 3
  period              = 300
  extended_statistic  = "p95"

  namespace   = "AWS/ApplicationELB"
  metric_name = "TargetResponseTime"
  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  treat_missing_data = "notBreaching"
  alarm_actions      = local.alarm_actions
  tags               = var.tags
}

# ── Database ─────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.name}-rds-cpu"
  alarm_description   = "RDS CPU is sustained high — queries are queueing behind it."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  evaluation_periods  = 3
  period              = 300
  statistic           = "Average"

  namespace   = "AWS/RDS"
  metric_name = "CPUUtilization"
  dimensions  = { DBInstanceIdentifier = var.database_identifier }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${var.name}-rds-storage"
  alarm_description   = "RDS free storage is low. Storage autoscaling has a ceiling; this is the warning before it."
  comparison_operator = "LessThanThreshold"
  threshold           = var.rds_free_storage_bytes
  evaluation_periods  = 1
  period              = 300
  statistic           = "Average"

  namespace   = "AWS/RDS"
  metric_name = "FreeStorageSpace"
  dimensions  = { DBInstanceIdentifier = var.database_identifier }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name          = "${var.name}-rds-connections"
  alarm_description   = "Connection count is near the instance limit — the per-task pools are exhausting it."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.rds_connection_threshold
  evaluation_periods  = 2
  period              = 300
  statistic           = "Maximum"

  namespace   = "AWS/RDS"
  metric_name = "DatabaseConnections"
  dimensions  = { DBInstanceIdentifier = var.database_identifier }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

# ── Cache ────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${var.name}-redis-memory"
  alarm_description   = "Redis is close to evicting. Evicted bus entries mean live updates silently stop for some users."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  evaluation_periods  = 2
  period              = 300
  statistic           = "Average"

  namespace   = "AWS/ElastiCache"
  metric_name = "DatabaseMemoryUsagePercentage"
  dimensions  = { ReplicationGroupId = var.redis_id }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

# ── Log-derived metrics ──────────────────────────────────────────────────
#
# The app logs JSON (server/observability.py), so a metric filter can read a
# FIELD rather than pattern-match text. This one counts unhandled exceptions —
# the thing a 5xx alarm tells you happened, whose CAUSE lives here.

resource "aws_cloudwatch_log_metric_filter" "errors" {
  name           = "${var.name}-errors"
  log_group_name = var.log_group_name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name      = "ApplicationErrors"
    namespace = "NextXR/${var.name}"
    value     = "1"
    # Without this the metric reports NO DATA when nothing is failing, and the
    # alarm sits in INSUFFICIENT_DATA rather than OK.
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "application_errors" {
  alarm_name          = "${var.name}-application-errors"
  alarm_description   = "Unhandled exceptions in the application log."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_threshold
  evaluation_periods  = 2
  period              = 300
  statistic           = "Sum"

  namespace   = "NextXR/${var.name}"
  metric_name = "ApplicationErrors"

  treat_missing_data = "notBreaching"
  alarm_actions      = local.alarm_actions
  tags               = var.tags
}

# Failed authentication, tracked separately. A spike is a credential-stuffing
# run, which is a security signal rather than a reliability one — and the app's
# rate limiter turning it away is exactly what should be visible here.
resource "aws_cloudwatch_log_metric_filter" "auth_denied" {
  name           = "${var.name}-auth-denied"
  log_group_name = var.log_group_name
  pattern        = "{ $.status = 401 || $.status = 403 }"

  metric_transformation {
    name          = "AuthDenied"
    namespace     = "NextXR/${var.name}"
    value         = "1"
    default_value = 0
  }
}

# ── Dashboard ────────────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Requests and errors"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { stat = "Sum", color = "#d62728" }],
            [".", "HTTPCode_Target_4XX_Count", ".", ".", { stat = "Sum", color = "#ff7f0e" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Latency"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p50" }],
            ["...", { stat = "p95" }],
            ["...", { stat = "p99" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Service capacity"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", var.cluster_name, "ServiceName", var.service_name],
            ["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", var.alb_arn_suffix, "TargetGroup", var.target_group_arn_suffix],
            [".", "UnHealthyHostCount", ".", ".", ".", ".", { color = "#d62728" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Task utilisation"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.cluster_name, "ServiceName", var.service_name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title  = "RDS"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.database_identifier],
            [".", "DatabaseConnections", ".", "."],
            [".", "ReadLatency", ".", "."],
            [".", "WriteLatency", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title  = "Authentication denials (credential stuffing shows here)"
          region = var.region
          view   = "timeSeries"
          metrics = [["NextXR/${var.name}", "AuthDenied", { stat = "Sum" }]]
        }
      },
      {
        type = "log", x = 0, y = 18, width = 24, height = 6
        properties = {
          title  = "Recent errors"
          region = var.region
          query  = "SOURCE '${var.log_group_name}' | fields @timestamp, level, message, request_id, path, status | filter level = 'ERROR' or status >= 500 | sort @timestamp desc | limit 50"
        }
      },
    ]
  })
}
