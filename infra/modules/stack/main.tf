terraform {
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

data "aws_caller_identity" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = "us-east-1"
  prefix  = "kamp-aoty-${var.environment}"

  # Objects mirror the public path (/toys/aoty/x -> key toys/aoty/x), because CloudFront path
  # behaviours forward the full path to the origin.
  s3_key_prefix = trimprefix(var.base_path, "/")

  # Created in infra/global. The deploy role can only create roles that carry it.
  boundary_arn = "arn:aws:iam::${local.account}:policy/kamp-aoty-ci/${local.prefix}-boundary"
  role_path    = "/kamp-aoty/${var.environment}/"
}
