variable "environment" {
  description = "staging, prod or sandbox; part of every resource name (kamp-aoty-<environment>-*)"
  type        = string
  validation {
    condition     = contains(["staging", "prod", "sandbox"], var.environment)
    error_message = "environment must be staging, prod or sandbox (the CI roles in infra/global only cover those)."
  }
}

variable "base_path" {
  description = "Public path on kamp.fm, e.g. /toys/aoty/. Site objects are stored under the same key prefix."
  type        = string
  validation {
    condition     = startswith(var.base_path, "/") && endswith(var.base_path, "/")
    error_message = "base_path must start and end with a slash."
  }
}

variable "cloudfront_distribution_arn" {
  description = <<-EOT
    kamp.fm's CloudFront distribution (kamp-site), the only reader of the site bucket. null for
    sandbox, which isn't served through kamp.fm: the site bucket then has no OAC grant at all.
  EOT
  type        = string
  default     = null
}

variable "albums_point_in_time_recovery" {
  description = "PITR on the albums table (the release-year cache). Costs ~$0.20/GB-month."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "api_throttle_burst" {
  type    = number
  default = 20
}

variable "api_throttle_rate" {
  description = "Requests per second across the whole HTTP API"
  type        = number
  default     = 10
}
