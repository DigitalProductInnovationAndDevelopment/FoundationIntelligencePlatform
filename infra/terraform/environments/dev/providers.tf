provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Application = "foundation-intelligence"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}
