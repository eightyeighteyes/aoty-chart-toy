# Starts the mb lane every 5 minutes (AOTY-19, AOTY-58). It's how a run that ran out of time
# with work left gets its successor: the lane never invokes itself, because each self-invoke was
# another hop in one request chain and Lambda's recursive loop detection stopped the chain after
# about 16 (a big backlog). A Scheduler start is outside any chain. It also covers a lost sync
# poke. A start during a run is throttled (reserved concurrency 1) and retried by Lambda, so runs
# follow each other within about 5 minutes. An empty run makes no MusicBrainz request: two short
# long polls and the depth reads, about 288 a day.
#
# The resource keeps its "backstop" name, so the change didn't recreate it.
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
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fn["mb"].arn
    role_arn = aws_iam_role.mb_schedule.arn
    input    = "{}"

    # A missed tick is covered by the next one.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }

  depends_on = [aws_iam_role_policy.mb_schedule]
}
