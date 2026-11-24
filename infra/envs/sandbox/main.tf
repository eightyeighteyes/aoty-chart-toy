# Sandbox (AOTY-33): where an unmerged branch is deployed before its PR merges. One shared
# stack, last deploy wins. Not served through kamp.fm: the API is reached at its execute-api
# URL, and nothing reads the site bucket. See infra/README.md, "Testing a branch in sandbox".

terraform {
  required_version = ">= 1.7"

  backend "s3" {
    bucket         = "kamp-site-tfstate"
    key            = "aoty/sandbox/terraform.tfstate"
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
      Environment = "sandbox"
      ManagedBy   = "terraform"
      Repo        = "aoty-chart-toy"
    }
  }
}

module "stack" {
  source = "../../modules/stack"

  environment        = "sandbox"
  base_path          = "/toys/aoty-sandbox/"
  log_retention_days = 3
  # Run by hand (infra/README.md, "Release index"): a branch shouldn't download the dump daily.
  index_schedule_enabled = false
  # Not on kamp.fm, so nothing to check; no alarms (the default), which its role may not write.
  uptime_enabled = false
  group_writes   = true # AOTY-48
}

# Same shape as staging and prod, so deploy.yml and scripts/sandbox_reset.py read one object.
output "stack" {
  value = module.stack
}
