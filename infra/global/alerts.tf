# Where problems go (AOTY-27): one SNS topic for every environment's alarms (modules/stack/
# alarms.tf names it by ARN), the AWS budget, and later the stale-index alarm (AOTY-37). An
# alarm's name carries its env (kamp-aoty-<env>-...), so one topic is enough.
#
# The email subscription is NOT in Terraform: this repo is public, and the address would be in
# it. The admin subscribes once by hand (infra/README.md, "Alerts"), and confirms with
# --authenticate-on-unsubscribe so a forwarded email's unsubscribe link can't remove it.
#
# No SSE: with the AWS-managed aws/sns key neither CloudWatch nor Budgets can publish, and a
# customer-managed key costs $1/month for alerts that hold nothing secret.

resource "aws_sns_topic" "alerts" {
  name = "kamp-aoty-alerts"
}

# This replaces the default topic policy, so the account's own access is restated. Budgets
# needs its statement; CloudWatch's is belt and braces (same-account alarms usually publish
# under the default policy, which this one replaces).
data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "OwnerManages"
    actions   = ["sns:Publish", "sns:Subscribe", "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account}:root"]
    }
  }
  statement {
    sid       = "CloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
  statement {
    sid       = "Budgets"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

# One monthly cost budget, account-wide: kamp-site shares the account, and a cost allocation tag
# only counts spend from the day it's activated, so a Project-tag filter could read $0 while real
# money goes out. Emails at $10 / $20 / $40 actual and at a $40 forecast. One budget is free
# (the first two are); four notifications of the five allowed.
resource "aws_budgets_budget" "monthly" {
  name         = "kamp-aoty-monthly"
  budget_type  = "COST"
  limit_amount = "40"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = { actual-10 = ["ACTUAL", 25], actual-20 = ["ACTUAL", 50], actual-40 = ["ACTUAL", 100], forecast-40 = ["FORECASTED", 100] }
    content {
      comparison_operator       = "GREATER_THAN"
      notification_type         = notification.value[0]
      threshold                 = notification.value[1]
      threshold_type            = "PERCENTAGE"
      subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
    }
  }

  depends_on = [aws_sns_topic_policy.alerts]
}
