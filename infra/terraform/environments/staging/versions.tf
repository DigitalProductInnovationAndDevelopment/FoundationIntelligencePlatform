terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  backend "s3" {
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.90"
    }
  }
}
