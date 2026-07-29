output "vpc_id" {
  value = aws_vpc.this.id
}

output "api_load_balancer_dns_name" {
  value = aws_lb.api.dns_name
}

output "frontend_distribution_domain_name" {
  value = aws_cloudfront_distribution.frontend.domain_name
}

output "database_endpoint" {
  value     = aws_db_instance.postgresql.endpoint
  sensitive = true
}

output "database_secret_arn" {
  value     = aws_db_instance.postgresql.master_user_secret[0].secret_arn
  sensitive = true
}

output "storage_bucket_arns" {
  value = { for name, bucket in aws_s3_bucket.data : name => bucket.arn }
}

output "api_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "worker_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "deployment_role_arn" {
  value = aws_iam_role.github_deployment.arn
}

output "schedules_enabled" {
  value = var.enable_schedules
}

output "release_gate" {
  value = {
    cluster_name         = aws_ecs_cluster.this.name
    task_definition_arn  = aws_ecs_task_definition.release_gate.arn
    application_subnets  = values(aws_subnet.application)[*].id
    security_group_id    = aws_security_group.ecs.id
    frontend_bucket      = aws_s3_bucket.data["frontend"].id
    cloudfront_id        = aws_cloudfront_distribution.frontend.id
    api_task_definition  = aws_ecs_task_definition.api.arn
    worker_task_definition = aws_ecs_task_definition.worker.arn
  }
}
