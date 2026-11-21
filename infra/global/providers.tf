terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "kamp-aoty"
      Environment = "global"
      ManagedBy   = "terraform"
      Repo        = "aoty-chart-toy"
    }
  }
}

data "aws_caller_identity" "current" {}
