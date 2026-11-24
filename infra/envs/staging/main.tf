terraform {
  required_version = ">= 1.7"

  backend "s3" {
    bucket         = "kamp-site-tfstate"
    key            = "aoty/staging/terraform.tfstate"
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
      Environment = "staging"
      ManagedBy   = "terraform"
      Repo        = "aoty-chart-toy"
    }
  }
}

module "stack" {
  source = "../../modules/stack"

  environment                 = "staging"
  base_path                   = "/toys/aoty-staging/"
  cloudfront_distribution_arn = "arn:aws:cloudfront::424416271636:distribution/EU1A3HSUCEQS9" # kamp.fm
  # DLQs and the uptime check only (AOTY-27): the full set is prod's, and alarms past 10 cost.
  alarms = "basic"
  # The execute-api lock (AOTY-26): user routes need kamp.fm's origin secret. Turned on 2026-11-23
  # after the api logged origin_ok=True for requests through kamp.fm (infra/README.md).
  enforce_origin = true
}

# One object so staging and prod expose identical outputs; kamp-site reads
# data.terraform_remote_state.<x>.outputs.stack.<name>.
output "stack" {
  value = module.stack
}
