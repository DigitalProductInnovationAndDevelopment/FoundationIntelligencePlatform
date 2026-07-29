locals {
  name = "${var.project}-${var.environment}"

  common_tags = merge(var.tags, {
    Application = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    DataClass   = "foundation-intelligence"
  })

  public_subnets = {
    for index, cidr in var.public_subnet_cidrs : tostring(index) => {
      cidr = cidr
      az   = var.availability_zones[index]
    }
  }
  application_subnets = {
    for index, cidr in var.application_subnet_cidrs : tostring(index) => {
      cidr = cidr
      az   = var.availability_zones[index]
    }
  }
  database_subnets = {
    for index, cidr in var.database_subnet_cidrs : tostring(index) => {
      cidr = cidr
      az   = var.availability_zones[index]
    }
  }

  nat_gateway_keys         = var.single_nat_gateway ? ["0"] : sort(keys(local.public_subnets))
  storage_classes          = toset(["raw", "validated", "curated", "exports", "frontend"])
  pipeline_storage_classes = toset(["raw", "validated", "curated", "exports"])
  versioned_storage_classes = toset([
    "raw",
    "validated",
    "curated",
    "exports",
  ])

  repository_environment_subject = "repo:${var.github_repository}:environment:${var.environment}"
  schedule_state                 = var.enable_schedules ? "ENABLED" : "DISABLED"
}
