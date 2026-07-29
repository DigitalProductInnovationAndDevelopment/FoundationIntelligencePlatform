resource "aws_s3_bucket" "data" {
  for_each = local.storage_classes

  bucket_prefix = "${local.name}-${each.key}-"
  force_destroy = false

  tags = merge(local.common_tags, {
    Name             = "${local.name}-${each.key}"
    DataStage        = each.key
    DestructiveRules = "disabled"
  })
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each = aws_s3_bucket.data

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.platform.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  versioning_configuration {
    status = contains(local.versioned_storage_classes, each.key) ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  for_each = toset(["raw", "validated", "curated", "exports"])

  bucket = aws_s3_bucket.data[each.key].id

  rule {
    id     = "non-destructive-storage-transition"
    status = "Enabled"

    filter {}

    transition {
      days          = each.key == "exports" ? 30 : 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = each.key == "exports" ? 90 : 180
      storage_class = "GLACIER_IR"
    }
  }
}

data "aws_iam_policy_document" "bucket_transport" {
  for_each = aws_s3_bucket.data

  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "frontend" ? [1] : []
    content {
      sid       = "AllowCloudFrontOriginAccess"
      effect    = "Allow"
      actions   = ["s3:GetObject"]
      resources = ["${each.value.arn}/*"]

      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }

      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [aws_cloudfront_distribution.frontend.arn]
      }
    }
  }

}

resource "aws_s3_bucket_policy" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  policy = data.aws_iam_policy_document.bucket_transport[each.key].json
}
