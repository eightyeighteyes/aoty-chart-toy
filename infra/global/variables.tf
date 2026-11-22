# The repo uses GitHub's immutable OIDC subject claims, which embed the owner and repo IDs:
# `repo:<owner>@<owner_id>/<repo>@<repo_id>:...`. Trusting that form, not the bare names, means a
# deleted-and-recreated or renamed repo can't inherit the trust. Check the current prefix with:
#   gh api repos/eightyeighteyes/aoty-chart-toy/actions/oidc/customization/sub
variable "github_sub_prefix" {
  description = "OIDC `sub` prefix for this repo's workflows (immutable form)"
  type        = string
  default     = "repo:eightyeighteyes@6600722/aoty-chart-toy@1466126436"
}

variable "environments" {
  description = "Environments that get a deploy role and a permissions boundary"
  type        = set(string)
  default     = ["staging", "prod"]
}

variable "admin_assume_principal" {
  description = <<-EOT
    IAM principal ARN allowed to assume the CI roles from a laptop, so their policies can be
    tested with real plans and applies. Leave null (the default) outside of that work.
  EOT
  type        = string
  default     = null
}

variable "kamp_distribution_arn" {
  description = "kamp.fm's CloudFront distribution (kamp-site repo), which CD invalidates after a shell upload"
  type        = string
  default     = "arn:aws:cloudfront::424416271636:distribution/EU1A3HSUCEQS9"
}

variable "state_bucket" {
  type    = string
  default = "kamp-site-tfstate"
}

variable "lock_table" {
  type    = string
  default = "kamp-site-tfstate-lock"
}
