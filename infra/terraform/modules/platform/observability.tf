locals {
  custom_alarms = {
    readiness-failure = {
      metric     = "readiness_success"
      comparison = "LessThanThreshold"
      threshold  = 1
      periods    = 2
      period     = 60
      statistic  = "Minimum"
      runbook    = "readiness-failure"
    }
    api-5xx-budget = {
      metric     = "api_errors_total"
      comparison = "GreaterThanThreshold"
      threshold  = 5
      periods    = 5
      period     = 60
      statistic  = "Sum"
      runbook    = "bad-deployment"
    }
    api-latency-budget = {
      metric     = "api_request_duration_ms"
      comparison = "GreaterThanThreshold"
      threshold  = 1000
      periods    = 5
      period     = 60
      statistic  = "p95"
      runbook    = "bad-deployment"
    }
    stale-data = {
      metric     = "dataset_age_seconds"
      comparison = "GreaterThanThreshold"
      threshold  = 691200
      periods    = 2
      period     = 3600
      statistic  = "Maximum"
      runbook    = "stale-dataset"
    }
    pipeline-failure = {
      metric     = "pipeline_failures_total"
      comparison = "GreaterThanThreshold"
      threshold  = 0
      periods    = 1
      period     = 300
      statistic  = "Sum"
      runbook    = "ingestion-failure"
    }
    reconciliation-mismatch = {
      metric     = "reconciliation_mismatch_count"
      comparison = "GreaterThanThreshold"
      threshold  = 0
      periods    = 1
      period     = 300
      statistic  = "Maximum"
      runbook    = "migration-failure"
    }
    conversion-gap-increase = {
      metric     = "conversion_gap_count"
      comparison = "GreaterThanThreshold"
      threshold  = 432
      periods    = 1
      period     = 3600
      statistic  = "Maximum"
      runbook    = "stale-dataset"
    }
    programme-coverage-decrease = {
      metric     = "programme_coverage_ratio"
      comparison = "LessThanThreshold"
      threshold  = 0.44
      periods    = 1
      period     = 3600
      statistic  = "Minimum"
      runbook    = "stale-dataset"
    }
    geography-coverage-decrease = {
      metric     = "geography_coverage_ratio"
      comparison = "LessThanThreshold"
      threshold  = 0.34
      periods    = 1
      period     = 3600
      statistic  = "Minimum"
      runbook    = "stale-dataset"
    }
    cost-threshold = {
      metric     = "estimated_cost_usd"
      comparison = "GreaterThanThreshold"
      threshold  = 500
      periods    = 1
      period     = 86400
      statistic  = "Maximum"
      runbook    = "cost-spike"
    }
  }

  rds_alarms = {
    rds-cpu = {
      metric     = "CPUUtilization"
      comparison = "GreaterThanThreshold"
      threshold  = 80
      periods    = 5
      period     = 60
      statistic  = "Average"
    }
    rds-connections = {
      metric     = "DatabaseConnections"
      comparison = "GreaterThanThreshold"
      threshold  = 80
      periods    = 5
      period     = 60
      statistic  = "Average"
    }
    rds-storage = {
      metric     = "FreeStorageSpace"
      comparison = "LessThanThreshold"
      threshold  = 10737418240
      periods    = 3
      period     = 300
      statistic  = "Minimum"
    }
  }
}

resource "aws_sns_topic" "operations" {
  name              = "${local.name}-operations"
  kms_master_key_id = aws_kms_key.platform.id

  tags = local.common_tags

  depends_on = [aws_kms_key_policy.platform]
}

resource "aws_sns_topic_subscription" "email" {
  count = var.notification_email == null ? 0 : 1

  topic_arn = aws_sns_topic.operations.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_cloudwatch_metric_alarm" "custom" {
  for_each = local.custom_alarms

  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = "Runbook: docs/remediation/observability-runbooks.md#${each.value.runbook}"
  namespace           = "FoundationIntelligence"
  metric_name         = each.value.metric
  comparison_operator = each.value.comparison
  threshold           = each.value.threshold
  evaluation_periods  = each.value.periods
  period              = each.value.period
  statistic           = contains(["p90", "p95", "p99"], each.value.statistic) ? null : each.value.statistic
  extended_statistic  = contains(["p90", "p95", "p99"], each.value.statistic) ? each.value.statistic : null
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]

  dimensions = {
    Service     = "foundation-intelligence-api"
    Environment = var.environment
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "queue_backlog" {
  alarm_name          = "${local.name}-queue-backlog"
  alarm_description   = "Runbook: docs/remediation/observability-runbooks.md#queue-backlog"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 900
  evaluation_periods  = 3
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.operations.arn]

  dimensions = { QueueName = aws_sqs_queue.pipeline.name }
  tags       = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${local.name}-dlq-messages"
  alarm_description   = "Runbook: docs/remediation/observability-runbooks.md#dlq-replay"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.operations.arn]

  dimensions = { QueueName = aws_sqs_queue.pipeline_dlq.name }
  tags       = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "rds" {
  for_each = local.rds_alarms

  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = "Runbook: docs/remediation/observability-runbooks.md#rds-outage"
  namespace           = "AWS/RDS"
  metric_name         = each.value.metric
  comparison_operator = each.value.comparison
  threshold           = each.value.threshold
  evaluation_periods  = each.value.periods
  period              = each.value.period
  statistic           = each.value.statistic
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.operations.arn]

  dimensions = { DBInstanceIdentifier = aws_db_instance.postgresql.id }
  tags       = local.common_tags
}

resource "aws_cloudwatch_dashboard" "platform" {
  dashboard_name = local.name

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "API readiness, errors and latency"
          region = var.aws_region
          metrics = [
            ["FoundationIntelligence", "readiness_success", "Environment", var.environment],
            [".", "api_errors_total", ".", "."],
            [".", "api_request_duration_ms", ".", ".", { stat = "p95" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "RDS health"
          region = var.aws_region
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.postgresql.id],
            [".", "DatabaseConnections", ".", "."],
            [".", "FreeStorageSpace", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 24
        height = 6
        properties = {
          title  = "Pipeline queue and DLQ"
          region = var.aws_region
          metrics = [
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.pipeline.name],
            [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.pipeline.name],
            [".", ".", ".", aws_sqs_queue.pipeline_dlq.name],
          ]
        }
      },
    ]
  })
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Environment$%s", var.environment)]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.operations.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.operations.arn]
  }
}
