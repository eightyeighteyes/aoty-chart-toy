output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}

output "plan_role_arn" {
  description = "AWS_PLAN_ROLE_ARN for the CI workflow (AOTY-16)"
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arns" {
  description = "Per-env deploy role ARNs for the CD workflow (AOTY-17)"
  value       = { for env, role in aws_iam_role.deploy : env => role.arn }
}

output "alerts_topic_arn" {
  description = "Every env's alarms and the budget publish here (AOTY-27; modules/stack/alarms.tf builds the same ARN)"
  value       = aws_sns_topic.alerts.arn
}

output "boundary_policy_arns" {
  description = "Permissions boundary each env's workload roles must carry"
  value       = { for env, p in aws_iam_policy.boundary : env => p.arn }
}
