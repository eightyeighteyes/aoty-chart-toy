terraform {
  required_version = ">= 1.7"

  backend "s3" {
    bucket         = "kamp-site-tfstate"
    key            = "aoty/prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "kamp-site-tfstate-lock"
    encrypt        = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "kamp-aoty"
      Environment = "prod"
      ManagedBy   = "terraform"
      Repo        = "aoty-chart-toy"
    }
  }
}

module "stack" {
  source = "../../modules/stack"

  environment                   = "prod"
  base_path                     = "/toys/aoty/"
  cloudfront_distribution_arn   = "arn:aws:cloudfront::424416271636:distribution/EU1A3HSUCEQS9" # kamp.fm
  albums_point_in_time_recovery = true
}

# Same shape as staging; kamp-site reads data.terraform_remote_state.<x>.outputs.stack.<name>.
output "stack" {
  value = module.stack
}
