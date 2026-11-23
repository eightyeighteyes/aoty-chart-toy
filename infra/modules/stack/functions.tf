# Placeholder Lambdas. Terraform owns configuration (role, env vars, timeouts); CD owns the
# code (AOTY-17 deploys with `aws lambda update-function-code`). The placeholder zip is only
# used when a function is first created, hence ignore_changes on the code attributes.
# reserved = reserved concurrency (null = none). mb has exactly 1: it is the one MusicBrainz
# client per environment (AOTY-19), so a second start waits instead of running alongside. The
# account quota is 1,000 since 2026-11-22; at 10, any reservation failed.

locals {
  functions = {
    api  = { timeout = 30, memory = 256, reserved = null }
    sync = { timeout = 900, memory = 512, reserved = null }
    # 512: the release index's shard cache (AOTY-20) holds up to 128 parsed shards.
    mb = { timeout = 900, memory = 512, reserved = 1 }
    # Chart builder (AOTY-21): reserved 1 runs builds one at a time, so a build that read older
    # data can't overwrite a newer chart. 512: a heavy user's store is a few MB parsed.
    chart = { timeout = 300, memory = 512, reserved = 1 }
  }

  function_env = {
    AOTY_ENV       = var.environment
    BASE_PATH      = var.base_path
    SSM_PREFIX     = local.ssm_prefix
    USERS_TABLE    = aws_dynamodb_table.users.name
    ALBUMS_TABLE   = aws_dynamodb_table.albums.name
    GUARD_TABLE    = aws_dynamodb_table.guard.name
    SITE_BUCKET    = aws_s3_bucket.site.bucket
    STORE_BUCKET   = aws_s3_bucket.store.bucket
    SYNC_QUEUE_URL = aws_sqs_queue.main["sync"].url
    MB_HOT_URL     = aws_sqs_queue.main["mb-hot"].url
    MB_COLD_URL    = aws_sqs_queue.main["mb-cold"].url
    MB_FUNCTION    = "${local.prefix}-mb"
    CHART_FUNCTION = "${local.prefix}-chart"
    SITE_PREFIX    = local.s3_key_prefix
    ENFORCE_ORIGIN = tostring(var.enforce_origin)
  }

  queue_consume = [
    "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility",
    "sqs:GetQueueAttributes",
  ]

  # What each function's flow needs (AOTY-18/19/22), on top of logs + SSM.
  function_statements = {
    api = [
      # Queue syncs (AOTY-22), and read the queue's depth for the status and the busy guard.
      {
        actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
        resources = [aws_sqs_queue.main["sync"].arn]
      },
      {
        actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        resources = [aws_dynamodb_table.users.arn]
      },
      {
        # The per-IP new-name limit, queue tickets and the measured wait (AOTY-26).
        actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        resources = [aws_dynamodb_table.guard.arn]
      },
      {
        # kamp.fm's origin secret (AOTY-26); decrypting it is DecryptParams below.
        actions   = ["ssm:GetParameters"]
        resources = local.origin_secret_arns
      },
    ]
    sync = [
      {
        # Using up queue tickets and recording first-sync times (AOTY-26).
        actions   = ["dynamodb:UpdateItem"]
        resources = [aws_dynamodb_table.guard.arn]
      },
      { actions = local.queue_consume, resources = [aws_sqs_queue.main["sync"].arn] },
      {
        actions   = ["sqs:SendMessage"]
        resources = [aws_sqs_queue.main["mb-hot"].arn, aws_sqs_queue.main["mb-cold"].arn]
      },
      {
        actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        resources = [aws_dynamodb_table.users.arn]
      },
      {
        actions   = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query"]
        resources = [aws_dynamodb_table.albums.arn]
      },
      {
        actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        resources = [aws_s3_bucket.store.arn, "${aws_s3_bucket.store.arn}/*"]
      },
      {
        actions = ["lambda:InvokeFunction"]
        resources = [
          "arn:aws:lambda:${local.region}:${local.account}:function:${local.prefix}-mb",
          "arn:aws:lambda:${local.region}:${local.account}:function:${local.prefix}-chart",
        ]
      },
    ]
    mb = [
      {
        actions   = local.queue_consume
        resources = [aws_sqs_queue.main["mb-hot"].arn, aws_sqs_queue.main["mb-cold"].arn]
      },
      {
        actions = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem", "dynamodb:Query",
        ]
        resources = [aws_dynamodb_table.albums.arn]
      },
      {
        # The release index (AOTY-20): the manifest and shards, read only.
        actions   = ["s3:GetObject"]
        resources = ["${aws_s3_bucket.store.arn}/index/*"]
      },
      {
        # Out of time with work left, it starts its own next run (it queues behind this one).
        # It also pokes the chart builder as albums land (AOTY-21).
        actions = ["lambda:InvokeFunction"]
        resources = [
          "arn:aws:lambda:${local.region}:${local.account}:function:${local.prefix}-mb",
          "arn:aws:lambda:${local.region}:${local.account}:function:${local.prefix}-chart",
        ]
      },
    ]
    chart = [
      {
        # The sync worker's stores, and the builder's own files beside them.
        actions   = ["s3:GetObject"]
        resources = ["${aws_s3_bucket.store.arn}/users/*"]
      },
      {
        # Its own files beside them: pending keys, and the album.getInfo cover cache.
        actions = ["s3:PutObject"]
        resources = [
          "${aws_s3_bucket.store.arn}/users/*/chart_pending.json",
          "${aws_s3_bucket.store.arn}/users/*/chart_covers.json",
        ]
      },
      {
        # ListBucket makes a missing store a 404 (NoSuchKey) instead of a 403.
        actions   = ["s3:ListBucket"]
        resources = [aws_s3_bucket.store.arn]
      },
      {
        actions   = ["s3:PutObject"]
        resources = ["${aws_s3_bucket.site.arn}/${local.s3_key_prefix}data/users/*"]
      },
      {
        actions   = ["dynamodb:BatchGetItem"]
        resources = [aws_dynamodb_table.albums.arn]
      },
      {
        actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
        resources = [aws_dynamodb_table.users.arn]
      },
    ]
  }
}

data "archive_file" "placeholder" {
  type        = "zip"
  source_file = "${path.module}/placeholder/handler.py"
  output_path = "${path.module}/build/placeholder.zip"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each = local.functions

  name                 = "${local.prefix}-${each.key}"
  path                 = local.role_path
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "fn" {
  for_each = local.functions

  statement {
    sid       = "OwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn[each.key].arn}:*"]
  }
  statement {
    sid       = "Params"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [for name in values(local.ssm_params) : "arn:aws:ssm:${local.region}:${local.account}:parameter${name}"]
  }
  statement {
    sid       = "DecryptParams"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }

  dynamic "statement" {
    for_each = local.function_statements[each.key]
    content {
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "runtime"
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn[each.key].json
}

# Explicit so retention is set before the first invocation creates the group.
resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "fn" {
  for_each = local.functions

  function_name = "${local.prefix}-${each.key}"
  role          = aws_iam_role.fn[each.key].arn
  runtime       = "python3.13"
  architectures = ["arm64"] # ~20% cheaper; packaging must use aarch64 wheels
  handler       = "handler.handler"
  timeout       = each.value.timeout
  memory_size   = each.value.memory

  reserved_concurrent_executions = each.value.reserved == null ? -1 : each.value.reserved
  filename                       = data.archive_file.placeholder.output_path
  source_code_hash               = data.archive_file.placeholder.output_base64sha256

  environment {
    variables = local.function_env
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

# mb is started by async invokes (sync's poke, the hourly schedule, itself). While a run is
# going, every other start is throttled, and Lambda retries throttled events by itself until
# maximum_event_age, whatever maximum_retry_attempts says. Each retry finds the queues empty or
# drains them, so that's harmless; an hour bounds the pile-up, and the schedule covers any
# event that ages out. A run that errors isn't retried: its messages come back on their own.
resource "aws_lambda_function_event_invoke_config" "mb" {
  function_name                = aws_lambda_function.fn["mb"].function_name
  maximum_event_age_in_seconds = 3600
  maximum_retry_attempts       = 0
}

# The chart builder works the same way (AOTY-21): sync and mb start it with async invokes,
# throttled starts wait behind the running build, and repeats are skipped by requested_at.
resource "aws_lambda_function_event_invoke_config" "chart" {
  function_name                = aws_lambda_function.fn["chart"].function_name
  maximum_event_age_in_seconds = 3600
  maximum_retry_attempts       = 0
}

# The sync queue drives the sync worker (AOTY-18). One message per invocation; the handler
# reports failures per item. maximum_concurrency (2 is the floor AWS allows) caps how many
# syncs run at once, and with them the aggregate Last.fm rate (0.5 s/call each).
# Until CD deploys real code, the placeholder raises on SQS events, so no message is lost.
resource "aws_lambda_event_source_mapping" "sync" {
  event_source_arn        = aws_sqs_queue.main["sync"].arn
  function_name           = aws_lambda_function.fn["sync"].arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}
