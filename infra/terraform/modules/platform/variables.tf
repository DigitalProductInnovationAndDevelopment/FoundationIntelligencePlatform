variable "project" {
  description = "Lowercase project identifier used in resource names and tags."
  type        = string
  default     = "foundation-intelligence"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.project))
    error_message = "project must be a short lowercase identifier."
  }
}

variable "environment" {
  description = "Isolated deployment environment."
  type        = string

  validation {
    condition     = contains(["dev", "staging"], var.environment)
    error_message = "Only dev and staging are defined by this remediation."
  }
}

variable "aws_region" {
  description = "AWS region selected by the approved deployment operator."
  type        = string
}

variable "availability_zones" {
  description = "Two or more approved availability zones."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required."
  }
}

variable "vpc_cidr" {
  type = string
}

variable "public_subnet_cidrs" {
  type = list(string)
}

variable "application_subnet_cidrs" {
  type = list(string)
}

variable "database_subnet_cidrs" {
  type = list(string)
}

variable "single_nat_gateway" {
  description = "Cost-aware dev mode; staging should use one NAT per AZ."
  type        = bool
  default     = false
}

variable "enable_interface_endpoints" {
  description = "Create private endpoints for ECR, logs, secrets and SQS."
  type        = bool
  default     = true
}

variable "api_image" {
  description = "Immutable ECR image reference including an sha256 digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "api_image must be immutable and end with @sha256:<64 hex>."
  }
}

variable "worker_image" {
  description = "Immutable ECR image reference including an sha256 digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.worker_image))
    error_message = "worker_image must be immutable and end with @sha256:<64 hex>."
  }
}

variable "frontend_origin_image_digest" {
  description = "Audit metadata for the immutable frontend build image."
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.frontend_origin_image_digest))
    error_message = "frontend_origin_image_digest must be sha256:<64 hex>."
  }
}

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "worker_cpu" {
  type    = number
  default = 512
}

variable "worker_memory" {
  type    = number
  default = 1024
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "rds_instance_class" {
  type = string
}

variable "rds_allocated_storage_gib" {
  type = number
}

variable "rds_max_storage_gib" {
  type = number
}

variable "rds_backup_retention_days" {
  type = number

  validation {
    condition     = var.rds_backup_retention_days >= 7
    error_message = "At least seven days of PITR backups are required."
  }
}

variable "rds_multi_az" {
  type = bool
}

variable "domain_name" {
  description = "Optional approved application DNS name. Null avoids DNS/certificate resources."
  type        = string
  default     = null
  nullable    = true
}

variable "hosted_zone_id" {
  description = "Approved Route53 zone ID. Required only when manage_dns is true."
  type        = string
  default     = null
  nullable    = true
}

variable "manage_dns" {
  description = "Explicit opt-in for certificate validation and DNS records."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub owner/repository allowed to assume deployment roles."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be owner/repository."
  }
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN; null creates one in this account."
  type        = string
  default     = null
  nullable    = true
}

variable "enable_schedules" {
  description = "Fail-closed schedule switch; keep false until source governance approval."
  type        = bool
  default     = false
}

variable "notification_email" {
  description = "Optional approved alarm/budget destination. Null creates no subscription."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "monthly_budget_usd" {
  description = "Proposed environment budget; must be approved before deployment."
  type        = number
}

variable "tags" {
  type    = map(string)
  default = {}
}
