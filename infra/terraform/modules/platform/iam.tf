resource "aws_iam_role" "ecs_execution" {
  name_prefix = "${local.name}-ecs-execution-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "ecs_execution" {
  name = "runtime-image-logs-and-secrets"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid = "PullImages"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
      {
        Sid = "ReadImageLayers"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = [
          aws_ecr_repository.api.arn,
          aws_ecr_repository.worker.arn,
        ]
      },
      {
        Sid = "WriteTaskLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          "${aws_cloudwatch_log_group.api.arn}:*",
          "${aws_cloudwatch_log_group.worker.arn}:*",
        ]
      },
      {
        Sid      = "ReadDatabaseSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_db_instance.postgresql.master_user_secret[0].secret_arn]
      },
      {
        Sid      = "DecryptRuntimeSecrets"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.platform.arn]
      },
    ]
  })
}

resource "aws_iam_role" "api_task" {
  name_prefix = "${local.name}-api-task-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "api_task" {
  name = "curated-export-and-queue-access"
  role = aws_iam_role.api_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListCuratedAndExports"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.data["curated"].arn, aws_s3_bucket.data["exports"].arn]
      },
      {
        Sid      = "ReadCuratedObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.data["curated"].arn}/*"]
      },
      {
        Sid      = "ReadWriteExports"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.data["exports"].arn}/*"]
      },
      {
        Sid      = "EnqueuePipelineJobs"
        Effect   = "Allow"
        Action   = ["sqs:GetQueueAttributes", "sqs:SendMessage"]
        Resource = [aws_sqs_queue.pipeline.arn]
      },
      {
        Sid      = "UseDataKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.platform.arn]
      },
    ]
  })
}

resource "aws_iam_role" "worker_task" {
  name_prefix = "${local.name}-worker-task-"

  assume_role_policy = aws_iam_role.api_task.assume_role_policy
  tags              = local.common_tags
}

resource "aws_iam_role" "release_gate_task" {
  name_prefix = "${local.name}-release-gate-task-"

  assume_role_policy = aws_iam_role.api_task.assume_role_policy
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "worker_task" {
  name = "pipeline-storage-and-queue-access"
  role = aws_iam_role.worker_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListPipelineBuckets"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [for name, bucket in aws_s3_bucket.data : bucket.arn if contains(local.pipeline_storage_classes, name)]
      },
      {
        Sid    = "ReadWriteVersionedPipelineObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:PutObject",
        ]
        Resource = [for name, bucket in aws_s3_bucket.data : "${bucket.arn}/*" if contains(local.pipeline_storage_classes, name)]
      },
      {
        Sid    = "ConsumePipelineQueue"
        Effect = "Allow"
        Action = [
          "sqs:ChangeMessageVisibility",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ReceiveMessage",
          "sqs:SendMessage",
        ]
        Resource = [aws_sqs_queue.pipeline.arn, aws_sqs_queue.pipeline_dlq.arn]
      },
      {
        Sid      = "UseDataKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.platform.arn]
      },
    ]
  })
}
