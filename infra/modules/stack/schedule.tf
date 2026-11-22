# Hourly backstop for the mb lane (AOTY-19): if a sync's async poke was lost, this starts the
# drain anyway. With both queues empty a run exits without contacting MusicBrainz, so it costs
# one short invocation an hour.
#
# EventBridge Scheduler rather than an EventBridge rule: a rule needs a lambda:AddPermission
# for events.amazonaws.com, which the sandbox deploy role is denied (AOTY-33). Scheduler invokes
# through a role of ours instead, under this env's path and boundary like every workload role.

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "mb_schedule" {
  name                 = "${local.prefix}-mb-schedule"
  path                 = local.role_path
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "mb_schedule" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fn["mb"].arn]
  }
}

resource "aws_iam_role_policy" "mb_schedule" {
  name   = "invoke-mb"
  role   = aws_iam_role.mb_schedule.id
  policy = data.aws_iam_policy_document.mb_schedule.json
}

resource "aws_scheduler_schedule" "mb" {
  name                = "${local.prefix}-mb-backstop"
  group_name          = "default"
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fn["mb"].arn
    role_arn = aws_iam_role.mb_schedule.arn
    input    = "{}"

    # A missed hour is covered by the next one.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }

  depends_on = [aws_iam_role_policy.mb_schedule]
}
