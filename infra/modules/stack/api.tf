# HTTP API in front of the api Lambda. Every route goes to the one integration: the Lambda sees
# the full public path (e.g. /toys/aoty/api/health) and strips BASE_PATH itself, because kamp.fm's
# CloudFront forwards /toys/aoty[-staging]/api/* unchanged (kamp-site, AOTY-15). The GET and POST
# routes exist only to throttle separately (AOTY-26): status polls from a queued crowd mustn't
# use up the sync requests' allowance, or the other way round.
# The execute-api URL also works directly, bypassing CloudFront: the api Lambda refuses user
# routes without kamp.fm's origin secret once var.enforce_origin is on (AOTY-26).

resource "aws_apigatewayv2_api" "http" {
  name          = "${local.prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["api"].invoke_arn
  payload_format_version = "2.0"
}

locals {
  api_routes = {
    default = { key = "$default", throttle = { burst = var.api_throttle_burst, rate = var.api_throttle_rate } }
    get     = { key = "GET /{proxy+}", throttle = var.api_get_throttle }
    post    = { key = "POST /{proxy+}", throttle = var.api_post_throttle }
  }
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "method" {
  for_each  = { for k, r in local.api_routes : k => r if k != "default" }
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value.key
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.prefix}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # Throttles per route (AOTY-26). An over-limit request gets API Gateway's own 429
  # ({"message":"Too Many Requests"}), which the page tells apart from the api's too_many_new.
  default_route_settings {
    throttling_burst_limit = var.api_throttle_burst
    throttling_rate_limit  = var.api_throttle_rate
  }

  dynamic "route_settings" {
    for_each = { for k, r in local.api_routes : k => r if k != "default" }
    content {
      route_key              = route_settings.value.key
      throttling_burst_limit = route_settings.value.throttle.burst
      throttling_rate_limit  = route_settings.value.throttle.rate
    }
  }

  depends_on = [aws_apigatewayv2_route.method]

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      time      = "$context.requestTime"
      method    = "$context.httpMethod"
      path      = "$context.path"
      status    = "$context.status"
      latencyMs = "$context.responseLatency"
      error     = "$context.integrationErrorMessage"
    })
  }
}

# Server errors for the api-5xx alarm (AOTY-27, alarms.tf). API Gateway's own 5xx metric would
# count the api's deliberate 503s (busy, paused, Last.fm or SQS down), so this counts 5xx from
# the access log instead, minus 503s the api answered itself. An integration timeout or crash
# is still a 503 (or 500/502/504) but carries an integration error message; an empty one is "-".
# status is a string because the format is jsonencoded. Only where the alarm exists: a metric
# that's published costs money. A filter's dimensions can only come from log fields (and rule out
# a default value), so the environment is in the metric's name instead of a dimension.
locals {
  api_server_errors_metric = "ApiServerErrors-${var.environment}"
}

resource "aws_cloudwatch_log_metric_filter" "api_server_errors" {
  count = var.alarms == "full" ? 1 : 0

  name           = "${local.prefix}-api-server-errors"
  log_group_name = aws_cloudwatch_log_group.api_access.name
  pattern        = "{ ($.status = \"5*\") && (($.status != \"503\") || ($.error != \"-\")) }"

  metric_transformation {
    name          = local.api_server_errors_metric
    namespace     = "kamp-aoty"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
