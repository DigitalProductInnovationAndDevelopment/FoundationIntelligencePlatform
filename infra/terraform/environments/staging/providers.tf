provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Application = "foundation-intelligence"
      Environment = "staging"
      ManagedBy   = "terraform"
    }
  }
}
