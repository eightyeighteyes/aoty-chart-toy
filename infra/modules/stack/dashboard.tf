# One CloudWatch dashboard per env (AOTY-41): kamp-aoty-<env>-dashboard.
#
# The free tier covers 3 dashboards of up to 50 metrics each, so the precondition below fails
# the plan past 50. Every metric entry counts, hidden ones feeding an expression included
# (expressions themselves don't); a metric drawn twice (p50 and p95) counts twice. Logs
# Insights widgets count toward no metric limit (they cost per GB scanned, when viewed).
# Lambda Throttles for mb and chart are expected (reserved concurrency 1), so they're labelled,
# not alarmed. API Gateway metrics are per stage: per-route data needs detailed metrics, which
# are billed, and the routes are only GET, POST and $default.

locals {
  dashboard_name = "${local.prefix}-dashboard"

  env_dim = ["Environment", var.environment]
  fn      = { for k, f in aws_lambda_function.fn : k => f.function_name }
  hidden  = { visible = false }

  lastfm_label = var.environment == "sandbox" ? "Last.fm calls (includes the load-test fake)" : "Last.fm calls"

  dashboard_widgets = [
    { type = "text", width = 24, height = 1, properties = { markdown = "## MusicBrainz lane (mb)" } },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Albums answered per second", region = local.region, stat = "Sum", period = 3600, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "AlbumsResolved", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "res" })],
          ["kamp-aoty", "AlbumsMissed", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "mis" })],
          ["kamp-aoty", "AlbumsFailed", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "fail" })],
          ["kamp-aoty", "RunSeconds", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "secs" })],
          [{ expression = "(res + mis + fail) / secs", label = "albums/s (misses count)", id = "rate" }],
        ]
        annotations = { horizontal = [[{ label = "AOTY-12 baseline", value = 1.5 }, { value = 2.3 }]] }
        yAxis       = { left = { min = 0 } }
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "MusicBrainz 503 share", region = local.region, stat = "Sum", period = 3600, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "MbThrottled", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "thr" })],
          ["kamp-aoty", "MbRequests", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "req" })],
          [{ expression = "100 * thr / req", label = "% of requests refused", id = "pct" }],
        ]
        annotations = { horizontal = [{ label = "alarm (mb-throttled)", value = 85 }] }
        yAxis       = { left = { min = 0, max = 100 } }
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "mb queues", region = local.region, period = 300, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "HotQueueDepth", local.env_dim[0], local.env_dim[1], { stat = "Maximum", label = "mb-hot depth" }],
          ["kamp-aoty", "ColdQueueDepth", local.env_dim[0], local.env_dim[1], { stat = "Maximum", label = "mb-cold depth" }],
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.main["mb-hot"].name, { stat = "Maximum", label = "mb-hot oldest (s)", yAxis = "right" }],
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.main["mb-cold"].name, { stat = "Maximum", label = "mb-cold oldest (s)", yAxis = "right" }],
          ["kamp-aoty", "MbDeadlineStops", local.env_dim[0], local.env_dim[1], { stat = "Sum", label = "deadline stops" }],
        ]
      }
    },

    { type = "text", width = 24, height = 1, properties = { markdown = "## Release index" } },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Index age (hours, mb's view)", region = local.region, stat = "Maximum", period = 3600, view = "singleValue"
        metrics = [
          ["kamp-aoty", "IndexAgeHours", local.env_dim[0], local.env_dim[1], { label = "absent = no usable index" }],
        ]
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Index use in the mb lane", region = local.region, stat = "Sum", period = 3600, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "IndexHits", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "hit" })],
          ["kamp-aoty", "IndexUntrusted", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "unt" })],
          ["kamp-aoty", "IndexMisses", local.env_dim[0], local.env_dim[1], merge(local.hidden, { id = "mis" })],
          [{ expression = "100 * hit / (hit + unt + mis)", label = "% trusted hits", id = "rate" }],
          ["kamp-aoty", "IndexErrors", local.env_dim[0], local.env_dim[1], { label = "index errors", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Index build (daily 06:15 UTC)", region = local.region, period = 86400, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "IndexBuildSeconds", local.env_dim[0], local.env_dim[1], { stat = "Maximum", label = "build seconds" }],
          ["kamp-aoty", "IndexBuildSkipped", local.env_dim[0], local.env_dim[1], { stat = "Sum", label = "skipped (no new dump)", yAxis = "right" }],
          ["kamp-aoty", "RechecksQueued", local.env_dim[0], local.env_dim[1], { stat = "Sum", label = "re-checks queued", yAxis = "right" }],
        ]
      }
    },

    { type = "text", width = 24, height = 1, properties = { markdown = "## Charts" } },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Chart builds", region = local.region, stat = "Sum", period = 300, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "ChartsBuilt", local.env_dim[0], local.env_dim[1], { label = "built" }],
          ["kamp-aoty", "ChartFailures", local.env_dim[0], local.env_dim[1], { label = "failed" }],
        ]
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Chart build seconds", region = local.region, period = 300, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "ChartSeconds", local.env_dim[0], local.env_dim[1], { stat = "p50", label = "p50" }],
          ["kamp-aoty", "ChartSeconds", local.env_dim[0], local.env_dim[1], { stat = "p95", label = "p95" }],
        ]
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Chart backlog", region = local.region, period = 300, view = "timeSeries"
        metrics = [
          ["AWS/Lambda", "AsyncEventAge", "FunctionName", local.fn["chart"], { stat = "Maximum", label = "oldest waiting build (ms)" }],
          ["AWS/Lambda", "Throttles", "FunctionName", local.fn["chart"], { stat = "Sum", label = "throttles (expected while a build runs)", yAxis = "right" }],
        ]
        annotations = { horizontal = [{ label = "alarm (chart-event-age)", value = 1800000 }] }
      }
    },

    { type = "text", width = 24, height = 1, properties = { markdown = "## Syncs and the queue" } },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Sync queue", region = local.region, stat = "Maximum", period = 300, view = "timeSeries"
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.main["sync"].name, { label = "waiting" }],
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.main["sync"].name, { label = "oldest (s)", yAxis = "right" }],
        ]
        annotations = { horizontal = [{ label = "alarm (sync-queue-age)", value = 14400, yAxis = "right" }] }
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Sync worker", region = local.region, period = 300, view = "timeSeries"
        metrics = [
          ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", local.fn["sync"], { stat = "Maximum", label = "running (cap: sync_concurrency)" }],
          ["kamp-aoty", "SyncsDone", local.env_dim[0], local.env_dim[1], { stat = "Sum", label = "syncs done" }],
          ["kamp-aoty", "SyncFailures", local.env_dim[0], local.env_dim[1], { stat = "Sum", label = "sync failures" }],
          ["AWS/Lambda", "Duration", "FunctionName", local.fn["sync"], { stat = "p95", label = "p95 duration (ms)", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Dead-letter queues", region = local.region, stat = "Maximum", period = 300, view = "timeSeries"
        metrics = [
          for k, q in aws_sqs_queue.dlq : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", q.name, { label = k }]
        ]
      }
    },
    {
      type = "log", width = 12, height = 6
      properties = {
        title  = "Sync outcomes (last period)"
        region = local.region
        view   = "table"
        query  = "SOURCE '${aws_cloudwatch_log_group.fn["sync"].name}' | filter source = \"sync\" | stats count(*) as syncs, sum(first_sync) as first, avg(seconds) as avg_s, sum(LastfmCalls) as lastfm by outcome | sort syncs desc"
      }
    },
    {
      type = "log", width = 12, height = 6
      properties = {
        title  = "Sync requests by outcome (api)"
        region = local.region
        view   = "table"
        query  = "SOURCE '${aws_cloudwatch_log_group.fn["api"].name}' | filter source = \"api\" | stats count(*) as requests by outcome | sort requests desc"
      }
    },
    # Groups and sign-in (AOTY-53): EMF fields, not metrics, so these cost nothing. Group creates
    # and adds are the "created" and "added" outcomes; auth_failures is the alarmed metric.
    {
      type = "log", width = 12, height = 6
      properties = {
        title  = "Group writes and sign-ins (api)"
        region = local.region
        view   = "table"
        query = join(" ", [
          "SOURCE '${aws_cloudwatch_log_group.fn["api"].name}'",
          "| filter source = \"api\" and ispresent(action) and action != \"sync\"",
          "| fields coalesce(outcome, auth) as result",
          "| stats count(*) as requests, sum(AuthFailures) as auth_failures by action, result",
          "| sort requests desc",
        ])
      }
    },
    {
      type = "log", width = 12, height = 6
      properties = {
        title  = "Group builds per hour (chart)"
        region = local.region
        view   = "table"
        query = join(" ", [
          "SOURCE '${aws_cloudwatch_log_group.fn["chart"].name}'",
          "| filter ispresent(groups_built)",
          "| stats sum(groups_built) as built, sum(groups_coalesced) as coalesced,",
          "max(group_seconds_max) as slowest_s, max(group_members_max) as biggest by bin(1h) as hour",
          "| sort hour desc",
        ])
      }
    },

    # Cookie-free visit counts (AOTY-64): every user and group page load POSTs its sync, and the
    # API access log has the method, path and status, so ad blockers can't hide these the way they
    # hide gtag. The user query only sums: never `stats by` the path, which would put usernames
    # on the dashboard. Group slugs are fine (AOTY-38), but only 2xx ones, so a made-up slug
    # someone POSTs (a 404) never shows.
    {
      type = "text", width = 24, height = 2
      properties = {
        markdown = join(" ", [
          "## Visits (AOTY-64): page loads counted from the API access log.",
          "Set the dashboard to 1 week for daily rows (days are UTC; the first and last are partial).",
          "A new arrival counts twice (the intro's check, then the chart page); cooldown answers count;",
          "the intro page itself isn't counted; `serve_shell --proxy` previews count in the env they proxy.",
        ])
      }
    },
    {
      type = "log", width = 12, height = 6
      properties = {
        title  = "Visits per day (answered; refused = throttled, paused or bad names)"
        region = local.region
        view   = "table"
        query = join(" ", [
          "SOURCE '${aws_cloudwatch_log_group.api_access.name}'",
          "| filter method = \"POST\" and path like /\\/api\\/(users|groups)\\/[^\\/]+\\/sync$/",
          "and path not like /\\/api\\/users\\/(-smoke-|__smoke__)\\//",
          "| fields strcontains(path, \"/api/users/\") as user_page, strcontains(path, \"/api/groups/\") as group_page,",
          "status like /^2/ as answered",
          "| stats sum(user_page * answered) as user_visits, sum(group_page * answered) as group_visits,",
          "sum(1 - answered) as refused by bin(1d) as day",
          "| sort day desc",
        ])
      }
    },
    {
      type = "log", width = 12, height = 6
      properties = {
        title  = "Top groups by visits (answered)"
        region = local.region
        view   = "table"
        query = join(" ", [
          "SOURCE '${aws_cloudwatch_log_group.api_access.name}'",
          "| filter method = \"POST\" and status like /^2/ and path like /\\/api\\/groups\\/[^\\/]+\\/sync$/",
          "| parse path /\\/api\\/groups\\/(?<group>[^\\/]+)\\/sync$/",
          "| stats count(*) as visits by group",
          "| sort visits desc",
          "| limit 10",
        ])
      }
    },

    { type = "text", width = 24, height = 1, properties = { markdown = "## API and guardrails" } },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "HTTP API", region = local.region, stat = "Sum", period = 300, view = "timeSeries"
        metrics = [
          ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.http.id, "Stage", "$default", { label = "requests" }],
          ["AWS/ApiGateway", "4xx", "ApiId", aws_apigatewayv2_api.http.id, "Stage", "$default", { label = "4xx (incl. throttles)" }],
          ["AWS/ApiGateway", "5xx", "ApiId", aws_apigatewayv2_api.http.id, "Stage", "$default", { label = "5xx (incl. busy/paused 503s)" }],
          ["AWS/ApiGateway", "Latency", "ApiId", aws_apigatewayv2_api.http.id, "Stage", "$default", { stat = "p95", label = "p95 latency (ms)", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Sync requests, server errors, guardrails", region = local.region, stat = "Sum", period = 300, view = "timeSeries"
        metrics = concat(
          [["kamp-aoty", "SyncRequests", local.env_dim[0], local.env_dim[1], { label = "sync requests" }]],
          # Only prod has the filter behind it (api.tf), so only prod gets the line.
          var.alarms == "full" ? [["kamp-aoty", local.api_server_errors_metric, { label = "server errors (not deliberate 503s)" }]] : [],
          [["kamp-aoty", "GuardrailBadValue", local.env_dim[0], local.env_dim[1], { label = "bad guardrail values", yAxis = "right" }]],
        )
      }
    },
    {
      type = "metric", width = 8, height = 6
      properties = {
        title = "Last.fm", region = local.region, stat = "Sum", period = 60, view = "timeSeries"
        metrics = [
          ["kamp-aoty", "LastfmCalls", local.env_dim[0], local.env_dim[1], { label = local.lastfm_label }],
          ["kamp-aoty", "LastfmErrors", local.env_dim[0], local.env_dim[1], { label = "failed calls (after retries)", yAxis = "right" }],
        ]
      }
    },
  ]

  # Metric entries have a namespace and a name (2+ elements); expressions are one-element lists.
  dashboard_metric_count = sum([
    for w in local.dashboard_widgets : length([for m in try(w.properties.metrics, []) : m if length(m) >= 2])
  ])
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.dashboard_name
  dashboard_body = jsonencode({ widgets = local.dashboard_widgets })

  lifecycle {
    precondition {
      condition     = local.dashboard_metric_count <= 50
      error_message = "The dashboard has ${local.dashboard_metric_count} metrics: past 50 it leaves the free tier ($3/month)."
    }
  }
}
