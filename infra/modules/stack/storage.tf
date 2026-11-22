# Two buckets: "site" is what kamp.fm's CloudFront serves (shell, data/ charts and status);
# "store" is private Lambda state (per-user weekly album totals, the release index).
# Keeping them apart means no CloudFront behaviour can ever expose private data.
# Both use the S3 default encryption (SSE-S3); no KMS keys, which would cost $1/mo each.

resource "aws_s3_bucket" "site" {
  bucket = "${local.prefix}-site"
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

locals {
  # No distribution (sandbox): no OAC statements, only TLSOnly.
  oac_distribution = var.cloudfront_distribution_arn == null ? [] : [var.cloudfront_distribution_arn]
}

data "aws_iam_policy_document" "site" {
  dynamic "statement" {
    for_each = local.oac_distribution
    content {
      sid       = "CloudFrontOACRead"
      actions   = ["s3:GetObject"]
      resources = ["${aws_s3_bucket.site.arn}/${local.s3_key_prefix}*"]
      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [statement.value]
      }
    }
  }
  # With ListBucket, a missing key is a 404 rather than a 403, so the browser can tell
  # "no chart yet" from "forbidden" (AOTY-15). CloudFront only ever sends GETs for paths under
  # the base path, never a bucket listing.
  dynamic "statement" {
    for_each = local.oac_distribution
    content {
      sid       = "CloudFrontOACMissingAs404"
      actions   = ["s3:ListBucket"]
      resources = [aws_s3_bucket.site.arn]
      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [statement.value]
      }
    }
  }
  statement {
    sid       = "TLSOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
  # The public access block must exist first or S3 may evaluate the policy without it.
  depends_on = [aws_s3_bucket_public_access_block.site]
}

resource "aws_s3_bucket" "store" {
  bucket = "${local.prefix}-store"
}

resource "aws_s3_bucket_public_access_block" "store" {
  bucket                  = aws_s3_bucket.store.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning is a cheap undo for a bad sync write; old versions go after a week.
resource "aws_s3_bucket_versioning" "store" {
  bucket = aws_s3_bucket.store.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "store" {
  bucket = aws_s3_bucket.store.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.store]
}

data "aws_iam_policy_document" "store" {
  statement {
    sid       = "TLSOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.store.arn, "${aws_s3_bucket.store.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "store" {
  bucket     = aws_s3_bucket.store.id
  policy     = data.aws_iam_policy_document.store.json
  depends_on = [aws_s3_bucket_public_access_block.store]
}
