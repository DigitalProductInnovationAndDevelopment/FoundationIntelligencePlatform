module "platform" {
  source = "../../modules/platform"

  environment = "dev"
  aws_region  = var.aws_region

  availability_zones       = ["${var.aws_region}a", "${var.aws_region}b"]
  vpc_cidr                  = "10.40.0.0/16"
  public_subnet_cidrs       = ["10.40.0.0/24", "10.40.1.0/24"]
  application_subnet_cidrs  = ["10.40.10.0/24", "10.40.11.0/24"]
  database_subnet_cidrs     = ["10.40.20.0/24", "10.40.21.0/24"]
  single_nat_gateway        = true
  enable_interface_endpoints = false

  api_image                    = var.api_image
  worker_image                 = var.worker_image
  frontend_origin_image_digest = var.frontend_origin_image_digest
  api_desired_count             = 1
  worker_desired_count          = 1

  rds_instance_class        = "db.t4g.small"
  rds_allocated_storage_gib = 30
  rds_max_storage_gib       = 100
  rds_backup_retention_days = 7
  rds_multi_az              = false

  github_repository        = var.github_repository
  github_oidc_provider_arn = var.github_oidc_provider_arn
  domain_name              = var.domain_name
  hosted_zone_id           = var.hosted_zone_id
  manage_dns               = var.manage_dns
  enable_schedules         = false
  monthly_budget_usd       = 300

  tags = {
    CostCentre = "unapproved-development"
    Owner      = "unassigned"
  }
}
