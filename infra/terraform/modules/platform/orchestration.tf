resource "aws_sqs_queue" "pipeline_dlq" {
  name                              = "${local.name}-pipeline-dlq.fifo"
  fifo_queue                        = true
  content_based_deduplication       = false
  kms_master_key_id                 = aws_kms_key.platform.arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600
  visibility_timeout_seconds        = 900

  tags = local.common_tags
}

resource "aws_sqs_queue" "pipeline" {
  name                              = "${local.name}-pipeline.fifo"
  fifo_queue                        = true
  content_based_deduplication       = false
  deduplication_scope               = "messageGroup"
  fifo_throughput_limit             = "perMessageGroupId"
  kms_master_key_id                 = aws_kms_key.platform.arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 345600
  visibility_timeout_seconds        = 900
  receive_wait_time_seconds         = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.pipeline_dlq.arn
    maxReceiveCount     = 5
  })

  tags = local.common_tags
}

resource "aws_sqs_queue_redrive_allow_policy" "pipeline_dlq" {
  queue_url = aws_sqs_queue.pipeline_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.pipeline.arn]
  })
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/foundation-intelligence/${var.environment}/step-functions"
  retention_in_days = var.environment == "staging" ? 90 : 30
  kms_key_id        = aws_kms_key.logs.arn

  tags = local.common_tags
}

resource "aws_iam_role" "step_functions" {
  name_prefix = "${local.name}-step-functions-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "step_functions" {
  name = "run-bounded-worker-task"
  role = aws_iam_role.step_functions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunWorkerTask"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [aws_ecs_task_definition.worker.arn]
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.this.arn
          }
        }
      },
      {
        Sid      = "PassWorkerRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.worker_task.arn]
      },
      {
        Sid = "ManageSynchronousTaskEvents"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:PutTargets",
          "events:DescribeRule",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
        ]
      },
      {
        Sid      = "ManageSynchronousWorkerTask"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks", "ecs:StopTask"]
        Resource = "*"
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.this.arn
          }
        }
      },
      {
        Sid = "WriteExecutionLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.name}-pipeline"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Run one immutable worker task; durable job state remains PostgreSQL/SQS owned."
    StartAt = "RunWorker"
    States = {
      RunWorker = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::ecs:runTask.sync"
        Parameters = {
          LaunchType         = "FARGATE"
          Cluster            = aws_ecs_cluster.this.arn
          TaskDefinition     = aws_ecs_task_definition.worker.arn
          PlatformVersion    = "1.4.0"
          EnableExecuteCommand = false
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              AssignPublicIp = "DISABLED"
              Subnets        = values(aws_subnet.application)[*].id
              SecurityGroups = [aws_security_group.ecs.id]
            }
          }
        }
        TimeoutSeconds = 7200
        Retry = [{
          ErrorEquals     = ["ECS.AmazonECSException", "ECS.ThrottlingException", "States.Timeout"]
          IntervalSeconds = 30
          BackoffRate     = 2
          MaxAttempts     = 3
        }]
        End = true
      }
    }
  })

  logging_configuration {
    include_execution_data = false
    level                  = "ERROR"
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
  }

  tags = local.common_tags
}

resource "aws_iam_role" "scheduler" {
  name_prefix = "${local.name}-scheduler-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "start-pipeline-state-machine"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = aws_sfn_state_machine.pipeline.arn
    }]
  })
}

resource "aws_scheduler_schedule_group" "pipeline" {
  name = "${local.name}-pipeline"
  tags = local.common_tags
}

resource "aws_scheduler_schedule" "weekly_pipeline" {
  name       = "weekly-curation"
  group_name = aws_scheduler_schedule_group.pipeline.name
  state      = local.schedule_state

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 2 ? * SUN *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      job_type = "scheduled_curation"
      source   = "governance-gated"
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

  }
}
