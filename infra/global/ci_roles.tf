# CI roles for GitHub Actions (OIDC, no long-lived keys).
#
# Layout that keeps CI from escalating:
#   /kamp-aoty-ci/          the CI roles and the boundary policies (managed only here, by the admin)
#   /kamp-aoty/<env>/       workload roles the deploy roles create (always under the boundary)
# A deploy role's IAM writes are scoped to its env's workload path, so it can't touch itself,
# the other env, or the boundary.

locals {
  account = data.aws_caller_identity.current.account_id
  region  = "us-east-1"
  ci_path = "/kamp-aoty-ci/"

  ci_arn_prefix = {
    role   = "arn:aws:iam::${local.account}:role${local.ci_path}"
    policy = "arn:aws:iam::${local.account}:policy${local.ci_path}"
  }

  oidc_sub = {
    plan    = "${var.github_sub_prefix}:pull_request"
    staging = "${var.github_sub_prefix}:environment:staging"
    # GitHub environment names; "production" matches the AOTY-17 release job.
    prod = "${var.github_sub_prefix}:environment:production"
    # AOTY-33. The `sandbox` GitHub environment has no branch rule (any branch may deploy) but
    # requires a reviewer, so this role is the one an unmerged branch can get.
    sandbox = "${var.github_sub_prefix}:environment:sandbox"
  }
}

# --- Trust ---------------------------------------------------------------------------------

data "aws_iam_policy_document" "trust" {
  for_each = local.oidc_sub

  statement {
    sid     = "GitHubOIDC"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }

  # Only while iterating on these policies from a laptop (see variables.tf).
  dynamic "statement" {
    for_each = var.admin_assume_principal == null ? [] : [var.admin_assume_principal]
    content {
      sid     = "AdminTesting"
      actions = ["sts:AssumeRole"]
      principals {
        type        = "AWS"
        identifiers = [statement.value]
      }
    }
  }
}

# --- Permissions boundary for workload roles -------------------------------------------------
# The most any Lambda role in an env can ever do, whatever the deploy role writes into it.

data "aws_iam_policy_document" "boundary" {
  for_each = var.environments

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/lambda/kamp-aoty-${each.key}-*:*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/ecs/kamp-aoty-${each.key}-*:*",
    ]
  }
  statement {
    sid     = "Params"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:${local.region}:${local.account}:parameter/kamp-aoty/${each.key}",
      "arn:aws:ssm:${local.region}:${local.account}:parameter/kamp-aoty/${each.key}/*",
    ]
  }
  statement {
    sid       = "DecryptParamsViaSSM"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }
  statement {
    sid = "Queues"
    actions = [
      "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes", "sqs:GetQueueUrl",
    ]
    resources = ["arn:aws:sqs:${local.region}:${local.account}:kamp-aoty-${each.key}-*"]
  }
  statement {
    sid = "Tables"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
      "dynamodb:Query", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem",
      "dynamodb:ConditionCheckItem", "dynamodb:DescribeTable",
      "dynamodb:Scan", # the index build's re-check pass over albums (AOTY-20)
    ]
    resources = [
      "arn:aws:dynamodb:${local.region}:${local.account}:table/kamp-aoty-${each.key}-*",
      "arn:aws:dynamodb:${local.region}:${local.account}:table/kamp-aoty-${each.key}-*/index/*",
    ]
  }
  statement {
    sid     = "Buckets"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::kamp-aoty-${each.key}-*",
      "arn:aws:s3:::kamp-aoty-${each.key}-*/*",
    ]
  }
  statement {
    sid       = "InvokeSiblings"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:function:kamp-aoty-${each.key}-*"]
  }
  # The index schedule's role starts the index task (AOTY-20), passing it this env's task and
  # execution roles, which carry this same boundary.
  statement {
    sid       = "RunOwnTasks"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${local.region}:${local.account}:task-definition/kamp-aoty-${each.key}-*:*"]
  }
  statement {
    sid       = "PassOwnTaskRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/${each.key}/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "boundary" {
  for_each    = var.environments
  name        = "kamp-aoty-${each.key}-boundary"
  path        = local.ci_path
  description = "Permissions boundary for kamp-aoty ${each.key} workload roles"
  policy      = data.aws_iam_policy_document.boundary[each.key].json
}

# --- Deploy roles (one per env) ---------------------------------------------------------------

data "aws_iam_policy_document" "deploy" {
  for_each = var.environments

  # State: own key only. ListBucket reveals key names, not contents.
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]
  }
  statement {
    sid       = "StateObject"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/aoty/${each.key}/*"]
  }
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = ["arn:aws:dynamodb:${local.region}:${local.account}:table/${var.lock_table}"]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      # Covers both the lock item and the "-md5" digest item.
      values = ["${var.state_bucket}/aoty/${each.key}/*"]
    }
  }

  statement {
    sid     = "Buckets"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::kamp-aoty-${each.key}-*",
      "arn:aws:s3:::kamp-aoty-${each.key}-*/*",
    ]
  }
  statement {
    sid     = "Tables"
    actions = ["dynamodb:*"]
    resources = [
      "arn:aws:dynamodb:${local.region}:${local.account}:table/kamp-aoty-${each.key}-*",
      "arn:aws:dynamodb:${local.region}:${local.account}:table/kamp-aoty-${each.key}-*/*",
    ]
  }
  statement {
    sid       = "Queues"
    actions   = ["sqs:*"]
    resources = ["arn:aws:sqs:${local.region}:${local.account}:kamp-aoty-${each.key}-*"]
  }
  statement {
    sid       = "Functions"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:function:kamp-aoty-${each.key}-*"]
  }
  # Event source mappings (the sync queue trigger, AOTY-18) have random UUID ARNs, so they're
  # scoped by the function they target. Create takes no resource type, only the condition.
  statement {
    sid       = "CreateEventSourceMappings"
    actions   = ["lambda:CreateEventSourceMapping"]
    resources = ["*"]
    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["arn:aws:lambda:${local.region}:${local.account}:function:kamp-aoty-${each.key}-*"]
    }
  }
  statement {
    sid = "EventSourceMappings"
    actions = [
      "lambda:GetEventSourceMapping", "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
    ]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*"]
    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["arn:aws:lambda:${local.region}:${local.account}:function:kamp-aoty-${each.key}-*"]
    }
  }
  # The provider reads mapping tags; ListTags has no lambda:FunctionArn key to scope by.
  statement {
    sid       = "EventSourceMappingTags"
    actions   = ["lambda:ListTags"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*"]
  }
  # The provider's default_tags go out with CreateEventSourceMapping, which AWS also authorizes
  # as lambda:TagResource on the new mapping (AOTY-18's first deploy failed on exactly this).
  # Tag actions have no lambda:FunctionArn key either, so they're scoped by the Environment tag.
  # Both keys are checked: RequestTag alone would let a role retag another env's mapping as its
  # own and then manage it (AOTY-33). IfExists: a mapping being created has no tags yet, and a
  # tag call that doesn't touch Environment carries none.
  statement {
    sid       = "TagOwnEventSourceMappings"
    actions   = ["lambda:TagResource"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*"]
    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:RequestTag/Environment"
      values   = [each.key]
    }
    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:ResourceTag/Environment"
      values   = [each.key]
    }
  }
  statement {
    sid       = "UntagOwnEventSourceMappings"
    actions   = ["lambda:UntagResource"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [each.key]
    }
  }
  # The mb lane's 5-minute schedule (AOTY-19, AOTY-58). Schedules carry no tags, so they're scoped by name.
  statement {
    sid = "Schedules"
    actions = [
      "scheduler:CreateSchedule", "scheduler:GetSchedule", "scheduler:UpdateSchedule",
      "scheduler:DeleteSchedule",
    ]
    resources = ["arn:aws:scheduler:${local.region}:${local.account}:schedule/default/kamp-aoty-${each.key}-*"]
  }
  statement {
    sid     = "LogGroups"
    actions = ["logs:*"]
    resources = [
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/lambda/kamp-aoty-${each.key}-*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/lambda/kamp-aoty-${each.key}-*:*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/apigateway/kamp-aoty-${each.key}-*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/apigateway/kamp-aoty-${each.key}-*:*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/ecs/kamp-aoty-${each.key}-*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/ecs/kamp-aoty-${each.key}-*:*",
    ]
  }
  # The index build (AOTY-20): its cluster (create, capacity providers, tags) by name.
  statement {
    sid       = "EcsClusters"
    actions   = ["ecs:*"]
    resources = ["arn:aws:ecs:${local.region}:${local.account}:cluster/kamp-aoty-${each.key}-*"]
  }
  # Task definitions: Register, Describe and Deregister take no resource ARN, so any env's role
  # could register or deregister a revision in another env's family. Registering can't hurt:
  # schedules pin the exact revision Terraform made, and PassRole limits a revision to the
  # caller's own roles. Deregistering another env's revision would stop its index build until
  # that env's next apply re-registers it: a stale index, not an escalation.
  statement {
    sid = "EcsTaskDefinitions"
    actions = [
      "ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition", "ecs:DeregisterTaskDefinition",
      "ecs:ListTaskDefinitions",
    ]
    resources = ["*"]
  }
  # Tags go out with RegisterTaskDefinition (default_tags) and are authorized on the new revision.
  statement {
    sid       = "EcsTaskDefinitionTags"
    actions   = ["ecs:TagResource", "ecs:UntagResource", "ecs:ListTagsForResource"]
    resources = ["arn:aws:ecs:${local.region}:${local.account}:task-definition/kamp-aoty-${each.key}-*:*"]
  }
  # Actions that only accept Resource "*": log-group listing, and the log-delivery calls
  # API Gateway makes on the caller's behalf when HTTP API access logging is configured.
  statement {
    sid = "AccountWideReads"
    actions = [
      "logs:DescribeLogGroups", "logs:DescribeResourcePolicies", "logs:PutResourcePolicy",
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries",
      "sqs:ListQueues", "lambda:ListFunctions", "lambda:ListEventSourceMappings",
      "dynamodb:ListTables",
      # The index task runs in the default VPC (AOTY-20); nothing in EC2 is written.
      "ec2:DescribeVpcs", "ec2:DescribeVpcAttribute", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
    ]
    resources = ["*"]
  }
  # HTTP API ids are random, so APIs are scoped by their Environment tag (AOTY-33; before that,
  # any env's role could edit every env's API). REST APIs (/restapis) stay out of reach.
  # Routes, integrations and stages are authorized against the parent API's tags.
  statement {
    sid       = "HttpApiList"
    actions   = ["apigateway:GET"]
    resources = ["arn:aws:apigateway:${local.region}::/apis"]
  }
  statement {
    sid       = "HttpApiCreate"
    actions   = ["apigateway:POST"]
    resources = ["arn:aws:apigateway:${local.region}::/apis"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Environment"
      values   = [each.key]
    }
  }
  statement {
    sid       = "HttpApiOwn"
    actions   = ["apigateway:*"]
    resources = ["arn:aws:apigateway:${local.region}::/apis/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [each.key]
    }
  }
  statement {
    sid       = "HttpApiTagsRead"
    actions   = ["apigateway:GET"]
    resources = ["arn:aws:apigateway:${local.region}::/tags/*"]
  }
  # Same two-key rule as the mapping tags above.
  statement {
    sid       = "HttpApiTagsWrite"
    actions   = ["apigateway:POST", "apigateway:PUT", "apigateway:DELETE"]
    resources = ["arn:aws:apigateway:${local.region}::/tags/*"]
    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:RequestTag/Environment"
      values   = [each.key]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [each.key]
    }
  }

  # Alarms (AOTY-27, modules/stack/alarms.tf), by name. The alarm actions point at the global
  # alerts topic, which needs no sns permission from the caller (CloudWatch publishes). Tags go
  # out with PutMetricAlarm (default_tags) and are authorized as TagResource on the alarm.
  # DescribeAlarms is account-wide: the provider's reads don't always name the alarm.
  # Not sandbox: any branch can deploy as that role, and an alarm could email the owner anything.
  dynamic "statement" {
    for_each = each.key == "sandbox" ? [] : [1]
    content {
      sid = "OwnAlarms"
      actions = [
        "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource",
        "cloudwatch:UntagResource", "cloudwatch:ListTagsForResource",
      ]
      resources = ["arn:aws:cloudwatch:${local.region}:${local.account}:alarm:kamp-aoty-${each.key}-*"]
    }
  }
  dynamic "statement" {
    for_each = each.key == "sandbox" ? [] : [1]
    content {
      sid       = "DescribeAlarms"
      actions   = ["cloudwatch:DescribeAlarms"]
      resources = ["*"]
    }
  }

  # The env's one dashboard (AOTY-41, modules/stack/dashboard.tf), by its exact name: every
  # dashboard past the account's 3 free ones costs $3/month, and any branch can deploy as the
  # sandbox role. Dashboard ARNs have no region. Dashboards take no tags.
  statement {
    sid       = "OwnDashboard"
    actions   = ["cloudwatch:PutDashboard", "cloudwatch:GetDashboard", "cloudwatch:DeleteDashboards"]
    resources = ["arn:aws:cloudwatch::${local.account}:dashboard/kamp-aoty-${each.key}-dashboard"]
  }
  statement {
    sid       = "ListDashboards"
    actions   = ["cloudwatch:ListDashboards"]
    resources = ["*"]
  }

  # CD invalidates its env's base path after a shell upload (AOTY-17). IAM can't scope an
  # invalidation by path, so either env's role could invalidate the other's (or kamp's) paths;
  # the worst case is a cache refill, and the workflow only ever sends its own <base>*.
  # Sandbox isn't served through kamp.fm, so it gets no CloudFront access at all.
  dynamic "statement" {
    for_each = each.key == "sandbox" ? [] : [var.kamp_distribution_arn]
    content {
      sid       = "InvalidateShell"
      actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
      resources = [statement.value]
    }
  }

  # Sandbox only (AOTY-33): any branch can run as this role, so it may not make anything public
  # or rewrite account-wide log policies. The bucket policies, public access blocks and the log
  # resource policy are created once by the admin's first apply; a branch that changes them
  # fails its deploy. (Account-wide S3 Block Public Access isn't an option: another site in the
  # account serves a public bucket.)
  dynamic "statement" {
    for_each = each.key == "sandbox" ? [1] : []
    content {
      sid    = "SandboxNeverPublic"
      effect = "Deny"
      actions = [
        "lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig",
        "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketAcl", "s3:PutObjectAcl", "s3:PutBucketOwnershipControls",
        "dynamodb:PutResourcePolicy", "sqs:AddPermission",
        "logs:PutResourcePolicy", "logs:DeleteResourcePolicy",
        "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
      ]
      resources = ["*"]
    }
  }
  dynamic "statement" {
    for_each = each.key == "sandbox" ? [1] : []
    content {
      sid       = "SandboxInvokeOnlyFromApiGateway"
      effect    = "Deny"
      actions   = ["lambda:AddPermission"]
      resources = ["*"]
      condition {
        test     = "StringNotEquals"
        variable = "lambda:Principal"
        values   = ["apigateway.amazonaws.com"]
      }
    }
  }

  # Workload roles: only under this env's path, only with this env's boundary attached.
  statement {
    sid = "WorkloadRolesBounded"
    actions = [
      "iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePermissionsBoundary",
    ]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/${each.key}/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary[each.key].arn]
    }
  }
  statement {
    sid = "WorkloadRolesManage"
    actions = [
      "iam:GetRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy",
      "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags", "iam:GetRolePolicy",
      "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/${each.key}/*"]
  }
  statement {
    sid       = "PassWorkloadRolesToLambda"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/${each.key}/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }
  # The schedule's invoke role (AOTY-19); still a bounded role under this env's path.
  statement {
    sid       = "PassWorkloadRolesToScheduler"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/${each.key}/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["scheduler.amazonaws.com"]
    }
  }
  # The index task's execution and task roles, named in its task definition (AOTY-20).
  statement {
    sid       = "PassWorkloadRolesToEcsTasks"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/${each.key}/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid     = "NeverTouchCI"
    effect  = "Deny"
    actions = ["iam:*"]
    resources = [
      "${local.ci_arn_prefix.role}*",
      "${local.ci_arn_prefix.policy}*",
    ]
  }
  statement {
    sid       = "NeverDropBoundaries"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "deploy" {
  for_each             = var.environments
  name                 = "kamp-aoty-${each.key}-deploy"
  path                 = local.ci_path
  description          = "GitHub Actions deploy role for kamp-aoty ${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.trust[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "deploy" {
  for_each = var.environments
  name     = "deploy"
  role     = aws_iam_role.deploy[each.key].id
  policy   = data.aws_iam_policy_document.deploy[each.key].json
}

# --- Plan role (PRs; read-only) ---------------------------------------------------------------
# Any same-repo PR can change the workflow and use this role, so it gets metadata reads only:
# no decrypted parameters, no table items, no objects, no log events, nothing on kamp-site state.

data "aws_iam_policy_document" "plan" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]
  }
  statement {
    sid     = "StateRead"
    actions = ["s3:GetObject"]
    resources = [
      for env in var.environments : "arn:aws:s3:::${var.state_bucket}/aoty/${env}/*"
    ]
  }
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = ["arn:aws:dynamodb:${local.region}:${local.account}:table/${var.lock_table}"]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = [for env in var.environments : "${var.state_bucket}/aoty/${env}/*"]
    }
  }

  # Bucket ARNs only (no "/*"), so s3:Get* covers configuration but never GetObject.
  statement {
    sid       = "BucketConfig"
    actions   = ["s3:Get*", "s3:List*"]
    resources = ["arn:aws:s3:::kamp-aoty-*"]
  }
  statement {
    sid = "TableMetadata"
    actions = [
      "dynamodb:DescribeTable", "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTimeToLive", "dynamodb:ListTagsOfResource",
      "dynamodb:DescribeKinesisStreamingDestination",
      "dynamodb:GetResourcePolicy",
    ]
    resources = ["arn:aws:dynamodb:${local.region}:${local.account}:table/kamp-aoty-*"]
  }
  statement {
    sid = "QueueMetadata"
    actions = [
      "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ListQueueTags",
      "sqs:ListDeadLetterSourceQueues",
    ]
    resources = ["arn:aws:sqs:${local.region}:${local.account}:kamp-aoty-*"]
  }
  statement {
    sid       = "FunctionMetadata"
    actions   = ["lambda:Get*", "lambda:List*"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:function:kamp-aoty-*"]
  }
  statement {
    sid       = "EventSourceMappingMetadata"
    actions   = ["lambda:GetEventSourceMapping"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*"]
    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["arn:aws:lambda:${local.region}:${local.account}:function:kamp-aoty-*"]
    }
  }
  statement {
    sid       = "EventSourceMappingTags"
    actions   = ["lambda:ListTags"]
    resources = ["arn:aws:lambda:${local.region}:${local.account}:event-source-mapping:*"]
  }
  statement {
    sid     = "LogGroupMetadata"
    actions = ["logs:ListTagsForResource", "logs:ListTagsLogGroup", "logs:DescribeLogGroups", "logs:DescribeMetricFilters"]
    resources = [
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/lambda/kamp-aoty-*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/apigateway/kamp-aoty-*",
      "arn:aws:logs:${local.region}:${local.account}:log-group:/ecs/kamp-aoty-*",
    ]
  }
  statement {
    sid     = "EcsMetadata"
    actions = ["ecs:DescribeClusters", "ecs:ListTagsForResource"]
    resources = [
      "arn:aws:ecs:${local.region}:${local.account}:cluster/kamp-aoty-*",
      "arn:aws:ecs:${local.region}:${local.account}:task-definition/kamp-aoty-*:*",
    ]
  }
  # DescribeTaskDefinition takes no resource ARN. Task definitions hold no secrets by design
  # (the same rule as Lambda environment variables).
  statement {
    sid = "AccountWideReads"
    actions = [
      "logs:DescribeLogGroups", "sqs:ListQueues", "lambda:ListFunctions",
      "lambda:ListEventSourceMappings", "dynamodb:ListTables", "ecs:DescribeTaskDefinition",
      "ec2:DescribeVpcs", "ec2:DescribeVpcAttribute", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
    ]
    resources = ["*"]
  }
  statement {
    sid     = "HttpApis"
    actions = ["apigateway:GET"]
    resources = [
      "arn:aws:apigateway:${local.region}::/apis",
      "arn:aws:apigateway:${local.region}::/apis/*",
      "arn:aws:apigateway:${local.region}::/tags/*",
    ]
  }
  statement {
    sid       = "AlarmMetadata"
    actions   = ["cloudwatch:ListTagsForResource"]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account}:alarm:kamp-aoty-*"]
  }
  statement {
    sid       = "DescribeAlarms"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["*"]
  }
  statement {
    sid       = "DashboardMetadata"
    actions   = ["cloudwatch:GetDashboard"]
    resources = ["arn:aws:cloudwatch::${local.account}:dashboard/kamp-aoty-*"]
  }
  statement {
    sid       = "ScheduleMetadata"
    actions   = ["scheduler:GetSchedule"]
    resources = ["arn:aws:scheduler:${local.region}:${local.account}:schedule/default/kamp-aoty-*"]
  }
  statement {
    sid = "WorkloadRoleMetadata"
    actions = [
      "iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies", "iam:ListRoleTags",
    ]
    resources = ["arn:aws:iam::${local.account}:role/kamp-aoty/*"]
  }

  # Belt and braces: the allow-list already excludes these.
  statement {
    sid       = "NeverKampSiteState"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/site/*"]
  }
  statement {
    sid       = "NeverSecrets"
    effect    = "Deny"
    actions   = ["ssm:GetParameter*", "secretsmanager:GetSecretValue"]
    resources = ["*"]
  }
  # Only decryption through SSM. A blanket kms:Decrypt deny also hides Lambda environment
  # variables (GetFunction returns them only to callers that can decrypt them), and that shows up
  # as a permanent diff. Lambda env vars hold no secrets by design.
  statement {
    sid       = "NeverDecryptParams"
    effect    = "Deny"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "kamp-aoty-plan"
  path                 = local.ci_path
  description          = "GitHub Actions read-only terraform plan role for kamp-aoty PRs"
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan" {
  name   = "plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}
