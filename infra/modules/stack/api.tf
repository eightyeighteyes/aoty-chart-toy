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

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
