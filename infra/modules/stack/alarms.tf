# CloudWatch alarms (AOTY-27). Every one emails the global kamp-aoty-alerts topic (infra/global/
# alerts.tf; the subscription is made by hand, so no address is in this public repo), on ALARM and
# on OK. var.alarms picks the set: "basic" (staging) is the DLQs and the uptime check, "full"
# (prod) everything. Each description links to its runbook entry in infra/README.md, "Alarms".
#
# Thresholds and why (the README has more):
# - DLQs: any message. Missing data keeps the state: SQS stops publishing for a queue nobody
#   calls (about 6 h), even with messages in it, and that mustn't read as "resolved".
# - Sync queue age 4 h: max_queue_for_new (500) at the load test's ~2.3 syncs/min is a ~3.6 h
#   line by design (AOTY-26). Poison messages are the DLQ alarm's job.
# - mb: the 503 rate is normally 45-61% (AOTY-10/12), and throughput counts misses too, or the
#   daily index re-check pass (mostly misses) would look slow.
# - The api's own 503s (busy, paused) are deliberate, so API Gateway's 5xx metric isn't used: a
#   metric filter on the access log counts 5xx that aren't the api's 503 (api.tf).
# - Lambda Throttles aren't alarmed: mb and chart hold one slot, so throttles are normal. The
#   chart builder's async event age and dropped events are (AOTY-26: a backlog drops builds after
#   the 1 h max event age). mb's drops are by design; its 5-minute schedule covers them.
# - Recursive loop drops: any. Lambda stops a request chain after about 16 hops; mb's old
#   self-invoke hit that on a big backlog (AOTY-58). sync's continuations, and the pokes they
#   carry to mb and chart, are the other chain that could.

locals {
  alerts_topic_arn = "arn:aws:sns:${local.region}:${local.account}:kamp-aoty-alerts"
  runbook          = "https://github.com/eightyeighteyes/aoty-chart-toy/blob/main/infra/README.md"

  dlq_alarms = {
    for k, q in aws_sqs_queue.dlq : "dlq-${k}" => {
      summary   = "A ${k} message failed 5 times and is in its dead-letter queue."
      anchor    = "dlq-messages"
      namespace = "AWS/SQS"
      metric    = "ApproximateNumberOfMessagesVisible"
      dims      = tomap({ QueueName = q.name })
      stat      = "Maximum"
      period    = 300
      evals     = 1
      op        = "GreaterThanThreshold"
      threshold = 0
      missing   = "missing"
    }
  }

  basic_alarms = merge(local.dlq_alarms, {
    uptime-failing = {
      summary   = "kamp.fm's AOTY page, health or a chart failed the uptime check two hours running."
      anchor    = "uptime-failing"
      namespace = "kamp-aoty"
      metric    = "UptimeFailures"
      dims      = tomap({ Environment = var.environment })
      stat      = "Sum"
      period    = 3600
      evals     = 2
      op        = "GreaterThanOrEqualToThreshold"
      threshold = 1
      missing   = "notBreaching"
    }
    uptime-silent = {
      summary   = "The uptime check hasn't reported for 3 hours: its schedule or function stopped."
      anchor    = "uptime-silent"
      namespace = "kamp-aoty"
      metric    = "UptimeChecks"
      dims      = tomap({ Environment = var.environment })
      stat      = "Sum"
      period    = 3600
      evals     = 3
      op        = "LessThanThreshold"
      threshold = 1
      missing   = "breaching"
    }
  })

  full_alarms = merge(
    {
      for fn in ["sync", "mb", "chart", "card"] : "${fn}-errors" => {
        summary   = "The ${fn} Lambda failed at least twice in an hour."
        anchor    = "lambda-errors"
        namespace = "AWS/Lambda"
        metric    = "Errors"
        dims      = tomap({ FunctionName = aws_lambda_function.fn[fn].function_name })
        stat      = "Sum"
        period    = 3600
        evals     = 1
        op        = "GreaterThanOrEqualToThreshold"
        threshold = 2
        missing   = "notBreaching"
      }
    },
    {
      for fn in ["sync", "mb", "chart"] : "${fn}-recursion-dropped" => {
        summary   = "Lambda's recursive loop detection stopped a ${fn} invocation chain."
        anchor    = "recursion-dropped"
        namespace = "AWS/Lambda"
        metric    = "RecursiveInvocationsDropped"
        dims      = tomap({ FunctionName = aws_lambda_function.fn[fn].function_name })
        stat      = "Sum"
        period    = 3600
        evals     = 1
        op        = "GreaterThanThreshold"
        threshold = 0
        missing   = "notBreaching"
      }
    },
    {
      sync-queue-age = {
        summary   = "The oldest sync has waited over 4 hours: the queue is longer than the guardrails allow for."
        anchor    = "sync-queue-age"
        namespace = "AWS/SQS"
        metric    = "ApproximateAgeOfOldestMessage"
        dims      = tomap({ QueueName = aws_sqs_queue.main["sync"].name })
        stat      = "Maximum"
        period    = 300
        evals     = 3
        op        = "GreaterThanThreshold"
        threshold = 14400
        missing   = "notBreaching"
      }
      mb-hot-age = {
        summary   = "An album users are waiting on has been in mb-hot over 30 minutes."
        anchor    = "mb-hot-age"
        namespace = "AWS/SQS"
        metric    = "ApproximateAgeOfOldestMessage"
        dims      = tomap({ QueueName = aws_sqs_queue.main["mb-hot"].name })
        stat      = "Maximum"
        period    = 300
        evals     = 3
        op        = "GreaterThanThreshold"
        threshold = 1800
        missing   = "notBreaching"
      }
      lastfm-errors = {
        summary   = "20 or more Last.fm calls failed (after retries) in 15 minutes."
        anchor    = "lastfm-errors"
        namespace = "kamp-aoty"
        metric    = "LastfmErrors"
        dims      = tomap({ Environment = var.environment })
        stat      = "Sum"
        period    = 900
        evals     = 1
        op        = "GreaterThanOrEqualToThreshold"
        threshold = 20
        missing   = "notBreaching"
      }
      api-5xx = {
        summary   = "The API answered 5 or more server errors (not its deliberate busy/paused 503s) in each of two 5-minute windows."
        anchor    = "api-5xx"
        namespace = "kamp-aoty"
        metric    = local.api_server_errors_metric # the env is in the name (api.tf)
        dims      = tomap({})
        stat      = "Sum"
        period    = 300
        evals     = 2
        op        = "GreaterThanOrEqualToThreshold"
        threshold = 5
        missing   = "notBreaching"
      }
      guardrail-bad-value = {
        summary   = "A guardrail SSM parameter is malformed; the api is ignoring it and keeping the last good value."
        anchor    = "guardrail-bad-value"
        namespace = "kamp-aoty"
        metric    = "GuardrailBadValue"
        dims      = tomap({ Environment = var.environment })
        stat      = "Sum"
        period    = 300
        evals     = 1
        op        = "GreaterThanThreshold"
        threshold = 0
        missing   = "notBreaching"
      }
      chart-event-age = {
        summary   = "Chart builds are waiting over 30 minutes to start; past an hour they're dropped."
        anchor    = "chart-backlog"
        namespace = "AWS/Lambda"
        metric    = "AsyncEventAge"
        dims      = tomap({ FunctionName = aws_lambda_function.fn["chart"].function_name })
        stat      = "Maximum"
        period    = 900
        evals     = 1
        op        = "GreaterThanThreshold"
        threshold = 1800000 # AsyncEventAge is in milliseconds: 30 min
        missing   = "notBreaching"
      }
      chart-events-dropped = {
        summary   = "Chart build requests were dropped after waiting an hour."
        anchor    = "chart-backlog"
        namespace = "AWS/Lambda"
        metric    = "AsyncEventsDropped"
        dims      = tomap({ FunctionName = aws_lambda_function.fn["chart"].function_name })
        stat      = "Sum"
        period    = 900
        evals     = 1
        op        = "GreaterThanThreshold"
        threshold = 0
        missing   = "notBreaching"
      }
    },
  )

  # Metric-math alarms: "value" is the expression compared with the threshold. The IF guards keep
  # quiet hours (a few requests, a handful of albums) from tripping a ratio.
  math_alarms = {
    api-errors = {
      summary   = "Over 5% of API invocations failed (at least 5 errors) in 15 minutes."
      anchor    = "lambda-errors"
      namespace = "AWS/Lambda"
      dims      = tomap({ FunctionName = aws_lambda_function.fn["api"].function_name })
      metrics   = tomap({ errors = "Errors", calls = "Invocations" })
      value     = "IF(errors >= 5, 100 * errors / calls, 0)"
      period    = 900
      evals     = 1
      op        = "GreaterThanThreshold"
      threshold = 5
    }
    mb-throttled = {
      summary   = "MusicBrainz refused over 85% of the mb lane's requests (normal is about half), two hours running."
      anchor    = "mb-throttled"
      namespace = "kamp-aoty"
      dims      = tomap({ Environment = var.environment })
      metrics   = tomap({ thr = "MbThrottled", req = "MbRequests" })
      value     = "IF(req >= 30, thr / req, 0)"
      period    = 3600
      evals     = 2
      op        = "GreaterThanThreshold"
      threshold = 0.85
    }
    mb-slow = {
      summary   = "The mb lane answered under 0.75 albums/s (baseline 1.5-2.3) two hours running."
      anchor    = "mb-slow"
      namespace = "kamp-aoty"
      dims      = tomap({ Environment = var.environment })
      metrics   = tomap({ res = "AlbumsResolved", mis = "AlbumsMissed", fail = "AlbumsFailed", secs = "RunSeconds" })
      value     = "IF(res + mis + fail >= 50, (res + mis + fail) / secs, 1)"
      period    = 3600
      evals     = 2
      op        = "LessThanThreshold"
      threshold = 0.75
    }
  }

  # for-filters rather than conditionals, so every branch has the same (map) type.
  simple_alarms = {
    for k, v in merge(local.basic_alarms, local.full_alarms) : k => v
    if var.alarms == "full" || (var.alarms == "basic" && contains(keys(local.basic_alarms), k))
  }
  metric_math_alarms = { for k, v in local.math_alarms : k => v if var.alarms == "full" }
}

resource "aws_cloudwatch_metric_alarm" "simple" {
  for_each = local.simple_alarms

  alarm_name          = "${local.prefix}-${each.key}"
  alarm_description   = "${each.value.summary} Runbook: ${local.runbook}#${each.value.anchor}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  dimensions          = each.value.dims
  statistic           = each.value.stat
  period              = each.value.period
  evaluation_periods  = each.value.evals
  datapoints_to_alarm = each.value.evals
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  treat_missing_data  = each.value.missing
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "math" {
  for_each = local.metric_math_alarms

  alarm_name          = "${local.prefix}-${each.key}"
  alarm_description   = "${each.value.summary} Runbook: ${local.runbook}#${each.value.anchor}"
  evaluation_periods  = each.value.evals
  datapoints_to_alarm = each.value.evals
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]

  metric_query {
    id          = "value"
    expression  = each.value.value
    label       = each.key
    return_data = true
  }

  dynamic "metric_query" {
    for_each = each.value.metrics
    content {
      id = metric_query.key
      metric {
        namespace   = each.value.namespace
        metric_name = metric_query.value
        dimensions  = each.value.dims
        stat        = "Sum"
        period      = each.value.period
      }
    }
  }
}
