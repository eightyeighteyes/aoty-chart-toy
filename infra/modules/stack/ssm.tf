# Parameter VALUES are deliberately not managed by Terraform: refreshing a SecureString
# decrypts it into state, and the PR plan role (any same-repo PR can use it) would need
# decrypt rights. Terraform only knows the names and grants the Lambdas read access.
# Set them once per env with `aws ssm put-parameter` (see infra/README.md).

locals {
  ssm_prefix = "/kamp-aoty/${var.environment}/"
  ssm_params = {
    lastfm_api_key = "${local.ssm_prefix}lastfm_api_key"
    mb_contact     = "${local.ssm_prefix}mb_contact"
  }
}
