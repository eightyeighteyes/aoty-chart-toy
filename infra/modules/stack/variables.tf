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

variable "index_schedule_enabled" {
  description = "Run the release index build daily (AOTY-20). Off in sandbox, where it's run by hand."
  type        = bool
  default     = true
}

variable "alarms" {
  description = <<-EOT
    Which CloudWatch alarms to create (AOTY-27, alarms.tf), all emailing the global kamp-aoty-alerts
    topic: "full" (prod), "basic" (DLQs and the uptime check; staging) or "none" (sandbox, whose
    deploy role may not write alarms at all). The first 10 alarms in the account are free.
  EOT
  type        = string
  default     = "none"
  validation {
    condition     = contains(["full", "basic", "none"], var.alarms)
    error_message = "alarms must be full, basic or none."
  }
}

variable "uptime_enabled" {
  description = "Run the hourly uptime check of kamp.fm<base_path> (AOTY-27). Off in sandbox, which isn't on kamp.fm."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "enforce_origin" {
  description = <<-EOT
    Refuse user API routes (403) that don't carry kamp.fm's CloudFront origin secret, which kamp-site
    adds and keeps in SSM (AOTY-26). Turn on only after the header is seen arriving (infra/README.md).
    Never in sandbox: it isn't behind CloudFront.
  EOT
  type        = bool
  default     = false
}

variable "sync_concurrency" {
  description = <<-EOT
    Syncs that run at once (AOTY-26): the sync mapping's maximum_concurrency. It caps the sync
    worker's spend and its aggregate Last.fm rate: each worker paces 0.5 s/call, so 2 workers make
    about 4 req/s against Last.fm's ~5 per IP (AOTY-11). AWS's floor is 2.
  EOT
  type        = number
  default     = 2
  validation {
    condition     = var.sync_concurrency >= 2 && var.sync_concurrency <= 10
    error_message = "sync_concurrency must be 2-10 (2 is the SQS mapping's minimum; past ~2 Last.fm may rate-limit)."
  }
}

variable "api_throttle_burst" {
  description = "Burst across the HTTP API's default route (health, and anything not below)"
  type        = number
  default     = 20
}

variable "api_throttle_rate" {
  description = "Requests per second across the HTTP API's default route"
  type        = number
  default     = 10
}

variable "api_post_throttle" {
  description = "POST routes (sync requests): {burst, rate}. Each new user may cost Last.fm calls."
  type        = object({ burst = number, rate = number })
  default     = { burst = 5, rate = 2 }
}

variable "api_get_throttle" {
  description = "GET routes (status polling, AOTY-26): {burst, rate}. A queued crowd polls every ~20 s."
  type        = object({ burst = number, rate = number })
  default     = { burst = 50, rate = 25 }
}
