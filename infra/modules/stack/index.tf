# The release index build (AOTY-20): a Fargate Spot task, run daily by EventBridge Scheduler.
# jobs/index/build.py downloads the MusicBrainz JSON dump, writes the sharded index to the store
# bucket (index/), and queues re-checks to mb-cold. Most days the index is already current and
# the task exits in seconds; daily rather than twice weekly so a run lost to a Spot interruption
# is made up the next day (Scheduler doesn't retry a task that started and then died).
#
# Why Fargate: the dump is 1.24 GB and the parse takes ~8 min of CPU; Lambda's 15 min can't fit
# both (AOTY-13). Why no ECR: the task runs AWS's public Lambda Python image (it already has
# boto3), pinned by digest, and fetches the code zip CD uploads to code/index.zip. Terraform
# owns the configuration and CD owns the code, as with the Lambdas.
#
# Networking: the default VPC's public subnets with a public IP, and the VPC's default security
# group (all egress, no ingress from outside it). No NAT gateway: it would cost ~$32/month,
# more than everything else here.
#
# X86_64, not ARM64: Fargate Spot doesn't run ARM64 tasks. lambdas/requirements.txt is
# standard library only today; if it gains packages, they're aarch64 wheels and this image
# would need its own.

locals {
  index_name     = "${local.prefix}-index"
  index_code_key = "code/index.zip"
  # public.ecr.aws/lambda/python:3.13-x86_64, as of 2026-11-22.
  index_image = "public.ecr.aws/lambda/python@sha256:74b4e7e6fccecee3a22b00dd97fa9464fb6f4f34c3b3f91bc11b7046ff825fb4"
  index_bootstrap = join("; ", [
    "import boto3, io, os, zipfile",
    "z = boto3.client('s3').get_object(Bucket=os.environ['STORE_BUCKET'], Key=os.environ['CODE_KEY'])['Body'].read()",
    "zipfile.ZipFile(io.BytesIO(z)).extractall('/tmp/code')",
  ])
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_security_group" "default" {
  vpc_id = data.aws_vpc.default.id
  name   = "default"
}

resource "aws_ecs_cluster" "index" {
  name = local.index_name
}

resource "aws_ecs_cluster_capacity_providers" "index" {
  cluster_name       = aws_ecs_cluster.index.name
  capacity_providers = ["FARGATE_SPOT"]
}

resource "aws_cloudwatch_log_group" "index" {
  name              = "/ecs/${local.index_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

# What ECS itself uses to start the task: only writing its logs (the image is public).
resource "aws_iam_role" "index_exec" {
  name                 = "${local.index_name}-exec"
  path                 = local.role_path
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "index_exec" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.index.arn}:*"]
  }
}

resource "aws_iam_role_policy" "index_exec" {
  name   = "logs"
  role   = aws_iam_role.index_exec.id
  policy = data.aws_iam_policy_document.index_exec.json
}

# What the build itself can do.
resource "aws_iam_role" "index_task" {
  name                 = "${local.index_name}-task"
  path                 = local.role_path
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "index_task" {
  statement {
    sid       = "Code"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.store.arn}/${local.index_code_key}"]
  }
  statement {
    sid       = "Index"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.store.arn}/index/*"]
  }
  statement {
    sid       = "ListIndex"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.store.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["index/*"]
    }
  }
  statement {
    sid       = "ScanAlbums"
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.albums.arn]
  }
  statement {
    sid       = "QueueRechecks"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.main["mb-cold"].arn]
  }
  statement {
    sid       = "Contact"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account}:parameter${local.ssm_params.mb_contact}"]
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
}

resource "aws_iam_role_policy" "index_task" {
  name   = "runtime"
  role   = aws_iam_role.index_task.id
  policy = data.aws_iam_policy_document.index_task.json
}

resource "aws_ecs_task_definition" "index" {
  family                   = local.index_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  # The build spills to disk, so memory stays near a sixteenth of the dump; 4 GB is headroom.
  cpu                = 1024
  memory             = 4096
  execution_role_arn = aws_iam_role.index_exec.arn
  task_role_arn      = aws_iam_role.index_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name       = "build"
    image      = local.index_image
    essential  = true
    entryPoint = ["/bin/sh", "-c"]
    command    = ["/var/lang/bin/python3 -c \"${local.index_bootstrap}\" && exec /var/lang/bin/python3 /tmp/code/build.py"]
    environment = [
      for k, v in {
        AOTY_ENV     = var.environment
        STORE_BUCKET = aws_s3_bucket.store.bucket
        CODE_KEY     = local.index_code_key
        ALBUMS_TABLE = aws_dynamodb_table.albums.name
        MB_COLD_URL  = aws_sqs_queue.main["mb-cold"].url
        SSM_PREFIX   = local.ssm_prefix
        WORK_DIR     = "/tmp/index-build"
      } : { name = k, value = v }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.index.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "build"
      }
    }
  }])

  depends_on = [aws_iam_role_policy.index_exec, aws_iam_role_policy.index_task]
}

# --- schedule ---------------------------------------------------------------------------------

resource "aws_iam_role" "index_schedule" {
  name                 = "${local.index_name}-schedule"
  path                 = local.role_path
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "index_schedule" {
  statement {
    actions   = ["ecs:RunTask"]
    resources = [aws_ecs_task_definition.index.arn]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.index.arn]
    }
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.index_exec.arn, aws_iam_role.index_task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "index_schedule" {
  name   = "run-index"
  role   = aws_iam_role.index_schedule.id
  policy = data.aws_iam_policy_document.index_schedule.json
}

resource "aws_scheduler_schedule" "index" {
  name       = "${local.index_name}-daily"
  group_name = "default"
  # The dump folder is dated ~00:10 UTC on dump days; its files are complete well before 06:15.
  schedule_expression = "cron(15 6 * * ? *)"
  state               = var.index_schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.index.arn
    role_arn = aws_iam_role.index_schedule.arn

    ecs_parameters {
      # The exact revision Terraform made, not the family's latest: no other role's
      # registration can change what this schedule runs.
      task_definition_arn = aws_ecs_task_definition.index.arn
      task_count          = 1

      capacity_provider_strategy {
        capacity_provider = "FARGATE_SPOT"
        weight            = 1
      }

      network_configuration {
        subnets          = data.aws_subnets.default.ids
        security_groups  = [data.aws_security_group.default.id]
        assign_public_ip = true
      }
    }

    # RunTask failing to start (no Spot capacity) is retried; a started task isn't.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }

  depends_on = [aws_iam_role_policy.index_schedule, aws_ecs_cluster_capacity_providers.index]
}
