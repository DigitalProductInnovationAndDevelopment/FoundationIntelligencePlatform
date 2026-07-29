data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

resource "aws_kms_key" "platform" {
  description             = "${local.name} application data encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = merge(local.common_tags, { Name = "${local.name}-data" })
}

resource "aws_kms_alias" "platform" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.platform.key_id
}

resource "aws_kms_key" "logs" {
  description             = "${local.name} CloudWatch log encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogsEncryption"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action = [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:ReEncrypt*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/foundation-intelligence/${var.environment}/*"
          }
        }
      },
    ]
  })

  tags = merge(local.common_tags, { Name = "${local.name}-logs" })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${local.name}-logs"
  target_key_id = aws_kms_key.logs.key_id
}
