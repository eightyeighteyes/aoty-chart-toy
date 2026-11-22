# sync: FIFO, message group = username, so one user never has two syncs running at once.
# mb-hot / mb-cold: albums users are waiting on vs the long tail; the mb Lambda polls them
# itself (hot first), so they have no event source mapping. The sync queue's mapping is in
# functions.tf (AOTY-18). Their visibility is the mb function's timeout: a received batch is
# finished or released within one run, and a run that dies returns it after that.

locals {
  queues = {
    sync = {
      fifo = true
      # AWS guidance for an SQS event source: 6 x the function timeout.
      visibility = 6 * local.functions.sync.timeout
    }
    mb-hot = {
      fifo       = false
      visibility = local.functions.mb.timeout
    }
    mb-cold = {
      fifo       = false
      visibility = local.functions.mb.timeout
    }
  }
}

resource "aws_sqs_queue" "dlq" {
  for_each = local.queues

  name                        = each.value.fifo ? "${local.prefix}-${each.key}-dlq.fifo" : "${local.prefix}-${each.key}-dlq"
  fifo_queue                  = each.value.fifo ? true : null
  content_based_deduplication = each.value.fifo ? true : null
  message_retention_seconds   = 1209600 # 14 days: time to notice and redrive
  sqs_managed_sse_enabled     = true
}

resource "aws_sqs_queue" "main" {
  for_each = local.queues

  name                        = each.value.fifo ? "${local.prefix}-${each.key}.fifo" : "${local.prefix}-${each.key}"
  fifo_queue                  = each.value.fifo ? true : null
  content_based_deduplication = each.value.fifo ? true : null
  visibility_timeout_seconds  = each.value.visibility
  receive_wait_time_seconds   = 20 # long polling: fewer empty receives
  sqs_managed_sse_enabled     = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 5
  })
}

# Separate resource: putting this on the DLQ itself would be a dependency cycle.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  for_each  = local.queues
  queue_url = aws_sqs_queue.dlq[each.key].id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.main[each.key].arn]
  })
}
