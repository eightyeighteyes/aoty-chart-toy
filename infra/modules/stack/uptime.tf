# The hourly uptime check (AOTY-27): lambdas/uptime fetches kamp.fm<base>, the api's health and a
# known user's chart.json, and emits UptimeChecks / UptimeFailures (alarms.tf). Scheduler rather
# than an EventBridge rule, as for the mb backstop (schedule.tf, scheduler_trust). The function
# exists everywhere so CD can deploy one list; the schedule is off where there's no kamp.fm path
# (sandbox). Cheaper than CloudWatch Synthetics: one short invocation an hour.

resource "aws_iam_role" "uptime_schedule" {
  count = var.uptime_enabled ? 1 : 0

  name                 = "${local.prefix}-uptime-schedule"
  path                 = local.role_path
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "uptime_schedule" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fn["uptime"].arn]
  }
}

resource "aws_iam_role_policy" "uptime_schedule" {
  count = var.uptime_enabled ? 1 : 0

  name   = "invoke-uptime"
  role   = aws_iam_role.uptime_schedule[0].id
  policy = data.aws_iam_policy_document.uptime_schedule.json
}

resource "aws_scheduler_schedule" "uptime" {
  count = var.uptime_enabled ? 1 : 0

  name                = "${local.prefix}-uptime"
  group_name          = "default"
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fn["uptime"].arn
    role_arn = aws_iam_role.uptime_schedule[0].arn
    input    = "{}"

    # A missed run shows as a missing UptimeChecks; three in a row is the "silent" alarm.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }

  depends_on = [aws_iam_role_policy.uptime_schedule]
}
