# Account-level resources: the GitHub OIDC provider (one per URL per account) and the CI
# roles. Applied by the admin from a laptop only — CI can never change its own permissions.
terraform {
  backend "s3" {
    bucket         = "kamp-site-tfstate"
    key            = "aoty/global/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "kamp-site-tfstate-lock"
    encrypt        = true
  }
}
