variable "github_repo" {
  description = "owner/name of the repo whose workflows may assume the CI roles"
  type        = string
  default     = "eightyeighteyes/aoty-chart-toy"
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

variable "state_bucket" {
  type    = string
  default = "kamp-site-tfstate"
}

variable "lock_table" {
  type    = string
  default = "kamp-site-tfstate-lock"
}
