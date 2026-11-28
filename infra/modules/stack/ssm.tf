# Parameter VALUES are deliberately not managed by Terraform: refreshing a SecureString
# decrypts it into state, and the PR plan role (any same-repo PR can use it) would need
# decrypt rights. Terraform only knows the names and grants the Lambdas read access.
# Set them once per env with `aws ssm put-parameter` (see infra/README.md).

locals {
  ssm_prefix = "/kamp-aoty/${var.environment}/"

  # Runtime guardrails (AOTY-26, lambdas/common/guardrails.py): plain String parameters, set by
  # hand when needed. A missing one means its default, so none has to exist.
  guardrail_names = [
    "pause_new_users", "pause_syncs", "sync_cooldown_seconds", "new_users_per_ip_hour",
    "max_queue_for_new", "group_sync_cooldown_seconds",
    "max_groups_per_owner", "max_group_member_adds_per_hour", "pause_groups", # AOTY-53
  ]

  ssm_params = merge(
    {
      lastfm_api_key = "${local.ssm_prefix}lastfm_api_key"
      mb_contact     = "${local.ssm_prefix}mb_contact"
      # The uptime check's user with a chart (AOTY-27): a String, kept out of the repo.
      uptime_user = "${local.ssm_prefix}uptime_user"
    },
    { for n in local.guardrail_names : n => "${local.ssm_prefix}${n}" },
  )

  # kamp.fm's CloudFront origin secret (AOTY-26). kamp-site creates and owns these SecureStrings
  # (only kamp-site's state holds the value); named here for the api's read grant alone.
  origin_secret_arns = [
    for n in ["origin_secret", "origin_secret_previous"] :
    "arn:aws:ssm:${local.region}:${local.account}:parameter${local.ssm_prefix}${n}"
  ]

  # Sign-in (AOTY-47, lambdas/api/handler.py AUTH_SECRETS): SecureStrings set by hand. Kept out of
  # ssm_params on purpose: function_params grants ssm_params to sync, mb and chart as well, and
  # these are the api's alone.
  auth_param_arns = [
    for n in ["session_key", "session_key_previous", "lastfm_api_secret"] :
    "arn:aws:ssm:${local.region}:${local.account}:parameter${local.ssm_prefix}${n}"
  ]
}
