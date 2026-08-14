variable "aws_region" {
  type = string
}

variable "api_image" {
  type = string
}

variable "worker_image" {
  type = string
}

variable "frontend_origin_image_digest" {
  type = string
}

variable "github_repository" {
  type = string
}

variable "github_oidc_provider_arn" {
  type     = string
  default  = null
  nullable = true
}

variable "domain_name" {
  type     = string
  default  = null
  nullable = true
}

variable "hosted_zone_id" {
  type     = string
  default  = null
  nullable = true
}

variable "manage_dns" {
  type    = bool
  default = false
}
