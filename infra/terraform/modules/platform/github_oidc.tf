resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_arn == null ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = local.common_tags
}

locals {
  github_oidc_provider_arn = coalesce(
    var.github_oidc_provider_arn,
    try(aws_iam_openid_connect_provider.github[0].arn, null),
  )
}

data "aws_iam_policy_document" "github_deployment_trust" {
  statement {
    sid     = "GitHubProtectedEnvironmentOnly"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.repository_environment_subject]
    }
  }
}

resource "aws_iam_role" "github_deployment" {
  name                 = "${local.name}-github-deployment"
  assume_role_policy   = data.aws_iam_policy_document.github_deployment_trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

resource "aws_iam_role_policy" "github_deployment" {
  name = "immutable-artifact-and-service-deployment"
  role = aws_iam_role.github_deployment.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AuthenticateToECR"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid = "PublishImmutableImages"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = [aws_ecr_repository.api.arn, aws_ecr_repository.worker.arn]
      },
      {
        Sid = "InspectAndDeployServices"
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:UpdateService",
        ]
        Resource = [
          aws_ecs_cluster.this.arn,
          aws_ecs_service.api.id,
          aws_ecs_service.worker.id,
          aws_ecs_task_definition.api.arn,
          aws_ecs_task_definition.worker.arn,
          aws_ecs_task_definition.release_gate.arn,
        ]
      },
      {
        Sid      = "RegisterTaggedTaskDefinitions"
        Effect   = "Allow"
        Action   = ["ecs:RegisterTaskDefinition"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/Environment" = var.environment
            "aws:RequestTag/Application" = var.project
          }
        }
      },
      {
        Sid      = "PassExactTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_execution.arn,
          aws_iam_role.api_task.arn,
          aws_iam_role.worker_task.arn,
          aws_iam_role.release_gate_task.arn,
        ]
      },
      {
        Sid      = "RunReleaseGate"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [aws_ecs_task_definition.release_gate.arn]
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.this.arn
          }
        }
      },
      {
        Sid      = "ObserveReleaseGate"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks"]
        Resource = "*"
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.this.arn
          }
        }
      },
      {
        Sid      = "PublishFrontend"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject", "s3:GetObject", "s3:ListBucket", "s3:PutObject"]
        Resource = [
          aws_s3_bucket.data["frontend"].arn,
          "${aws_s3_bucket.data["frontend"].arn}/*",
        ]
      },
      {
        Sid      = "InvalidateFrontendCache"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetDistribution"]
        Resource = [aws_cloudfront_distribution.frontend.arn]
      },
    ]
  })
}
