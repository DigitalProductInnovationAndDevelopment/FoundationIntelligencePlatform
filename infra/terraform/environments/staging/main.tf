module "platform" {
  source = "../../modules/platform"

  environment = "staging"
  aws_region  = var.aws_region

  availability_zones        = ["${var.aws_region}a", "${var.aws_region}b"]
  vpc_cidr                   = "10.50.0.0/16"
  public_subnet_cidrs        = ["10.50.0.0/24", "10.50.1.0/24"]
  application_subnet_cidrs   = ["10.50.10.0/24", "10.50.11.0/24"]
  database_subnet_cidrs      = ["10.50.20.0/24", "10.50.21.0/24"]
  single_nat_gateway         = false
  enable_interface_endpoints = true

  api_image                     = var.api_image
  worker_image                  = var.worker_image
  frontend_origin_image_digest = var.frontend_origin_image_digest
  api_desired_count             = 2
  worker_desired_count          = 2

  rds_instance_class        = "db.t4g.medium"
  rds_allocated_storage_gib = 100
  rds_max_storage_gib       = 500
  rds_backup_retention_days = 14
  rds_multi_az              = true

  github_repository        = var.github_repository
  github_oidc_provider_arn = var.github_oidc_provider_arn
  domain_name              = var.domain_name
  hosted_zone_id           = var.hosted_zone_id
  manage_dns               = var.manage_dns
  enable_schedules         = false
  monthly_budget_usd       = 750

  tags = {
    CostCentre = "unapproved-staging"
    Owner      = "unassigned"
  }
}
