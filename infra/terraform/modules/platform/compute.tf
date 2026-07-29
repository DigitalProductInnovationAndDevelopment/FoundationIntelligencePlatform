resource "aws_ecr_repository" "api" {
  name                 = "${local.name}/api"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.platform.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.name}/worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.platform.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the most recent 30 immutable images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy     = aws_ecr_lifecycle_policy.api.policy
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/foundation-intelligence/${var.environment}/api"
  retention_in_days = var.environment == "staging" ? 90 : 30
  kms_key_id        = aws_kms_key.logs.arn

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/foundation-intelligence/${var.environment}/worker"
  retention_in_days = var.environment == "staging" ? 90 : 30
  kms_key_id        = aws_kms_key.logs.arn

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.api_cpu)
  memory                   = tostring(var.api_memory)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.api_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name                   = "api"
    image                  = var.api_image
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    stopTimeout            = 60
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
    portMappings = [{
      name          = "http"
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
      appProtocol   = "http"
    }]
    environment = [
      { name = "APP_ENV", value = var.environment },
      { name = "DATABASE_HOST", value = aws_db_instance.postgresql.address },
      { name = "DATABASE_PORT", value = tostring(aws_db_instance.postgresql.port) },
      { name = "DATABASE_NAME", value = aws_db_instance.postgresql.db_name },
      { name = "DATABASE_USER", value = aws_db_instance.postgresql.username },
      { name = "PIPELINE_QUEUE_URL", value = aws_sqs_queue.pipeline.url },
      { name = "RAW_BUCKET", value = aws_s3_bucket.data["raw"].id },
      { name = "VALIDATED_BUCKET", value = aws_s3_bucket.data["validated"].id },
      { name = "CURATED_BUCKET", value = aws_s3_bucket.data["curated"].id },
      { name = "EXPORTS_BUCKET", value = aws_s3_bucket.data["exports"].id },
    ]
    secrets = [{
      name      = "DATABASE_PASSWORD"
      valueFrom = "${aws_db_instance.postgresql.master_user_secret[0].secret_arn}:password::"
    }]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\", timeout=3)' || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])

  ephemeral_storage {
    size_in_gib = 21
  }

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.worker_cpu)
  memory                   = tostring(var.worker_memory)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = var.worker_image
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    stopTimeout            = 120
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
    environment = [
      { name = "APP_ENV", value = var.environment },
      { name = "DATABASE_HOST", value = aws_db_instance.postgresql.address },
      { name = "DATABASE_PORT", value = tostring(aws_db_instance.postgresql.port) },
      { name = "DATABASE_NAME", value = aws_db_instance.postgresql.db_name },
      { name = "DATABASE_USER", value = aws_db_instance.postgresql.username },
      { name = "PIPELINE_QUEUE_URL", value = aws_sqs_queue.pipeline.url },
      { name = "PIPELINE_DLQ_URL", value = aws_sqs_queue.pipeline_dlq.url },
      { name = "RAW_BUCKET", value = aws_s3_bucket.data["raw"].id },
      { name = "VALIDATED_BUCKET", value = aws_s3_bucket.data["validated"].id },
      { name = "CURATED_BUCKET", value = aws_s3_bucket.data["curated"].id },
    ]
    secrets = [{
      name      = "DATABASE_PASSWORD"
      valueFrom = "${aws_db_instance.postgresql.master_user_secret[0].secret_arn}:password::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }])

  ephemeral_storage {
    size_in_gib = 21
  }

  tags = local.common_tags
}

resource "aws_lb" "api" {
  name                       = substr("${local.name}-api", 0, 32)
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = values(aws_subnet.public)[*].id
  drop_invalid_header_fields = true
  enable_deletion_protection = true

  tags = local.common_tags
}

resource "aws_lb_target_group" "api" {
  name        = substr("${local.name}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  deregistration_delay = 60

  health_check {
    enabled             = true
    path                = "/health/ready"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = local.common_tags
}

resource "aws_lb_listener" "http" {
  count = var.manage_dns && var.domain_name != null ? 1 : 0

  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_ecs_service" "api" {
  name                               = "api"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = var.api_desired_count
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 120
  enable_execute_command             = false
  wait_for_steady_state              = false

  network_configuration {
    assign_public_ip = false
    subnets          = values(aws_subnet.application)[*].id
    security_groups  = [aws_security_group.ecs.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.common_tags

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name                  = "worker"
  cluster               = aws_ecs_cluster.this.id
  task_definition       = aws_ecs_task_definition.worker.arn
  desired_count         = var.worker_desired_count
  launch_type           = "FARGATE"
  platform_version      = "1.4.0"
  enable_execute_command = false

  network_configuration {
    assign_public_ip = false
    subnets          = values(aws_subnet.application)[*].id
    security_groups  = [aws_security_group.ecs.id]
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.common_tags
}

resource "aws_appautoscaling_target" "api" {
  max_capacity       = var.environment == "staging" ? 6 : 2
  min_capacity       = var.api_desired_count
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_target" "worker" {
  max_capacity       = var.environment == "staging" ? 8 : 2
  min_capacity       = var.worker_desired_count
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "worker_queue" {
  name               = "${local.name}-worker-queue"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace

  target_tracking_scaling_policy_configuration {
    customized_metric_specification {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      statistic   = "Average"
      unit        = "Count"
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.pipeline.name
      }
    }
    target_value       = 5
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
