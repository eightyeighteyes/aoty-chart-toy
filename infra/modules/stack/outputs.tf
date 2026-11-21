# kamp-site (AOTY-15) reads these through terraform_remote_state; CD (AOTY-17) uses the
# names. Nothing here is secret.

output "environment" {
  value = var.environment
}

output "base_path" {
  description = "Public path on kamp.fm, e.g. /toys/aoty/"
  value       = var.base_path
}

output "s3_key_prefix" {
  description = "Key prefix in the site bucket that mirrors base_path (no leading slash)"
  value       = local.s3_key_prefix
}

output "site_bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "site_bucket_arn" {
  description = "Already readable by kamp.fm's distribution through OAC"
  value       = aws_s3_bucket.site.arn
}

output "site_bucket_regional_domain_name" {
  description = "CloudFront S3 origin domain_name"
  value       = aws_s3_bucket.site.bucket_regional_domain_name
}

output "store_bucket_name" {
  value = aws_s3_bucket.store.bucket
}

output "api_id" {
  value = aws_apigatewayv2_api.http.id
}

output "api_endpoint" {
  description = "https://<id>.execute-api.us-east-1.amazonaws.com (CloudFront custom origin)"
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "api_domain_name" {
  description = "api_endpoint without the scheme, for a CloudFront origin domain_name"
  value       = trimprefix(aws_apigatewayv2_api.http.api_endpoint, "https://")
}

output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}

output "queue_urls" {
  value = { for k, q in aws_sqs_queue.main : k => q.url }
}

output "dlq_urls" {
  value = { for k, q in aws_sqs_queue.dlq : k => q.url }
}

output "table_names" {
  value = {
    users  = aws_dynamodb_table.users.name
    albums = aws_dynamodb_table.albums.name
  }
}

output "ssm_parameter_names" {
  description = "Set these out of band (infra/README.md); Terraform never holds the values"
  value       = local.ssm_params
}
