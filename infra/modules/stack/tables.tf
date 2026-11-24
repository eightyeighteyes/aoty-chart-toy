# On-demand billing: $0 when idle. Encryption is DynamoDB's default AWS-owned key (free).

resource "aws_dynamodb_table" "users" {
  name         = "${local.prefix}-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "username" # lowercased Last.fm username

  attribute {
    name = "username"
    type = "S"
  }

  # Only rows that set expires_at expire: the api's not_found rows for names Last.fm doesn't
  # know (AOTY-22), so typos and junk names don't pile up. Real users never set it.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  deletion_protection_enabled = var.environment == "prod"
}

# Guardrail counters (AOTY-26), apart from users because the chart builder Scans that table:
#   ip#<hash>#<hour>  {names: string set of hashed usernames, expires_at}  the per-IP new-name limit
#   queue             {joined, started}  queue tickets handed out and used up (queue position), and
#                     {first_syncs, first_sync_seconds}  measured first-sync time (estimated wait)
# Only the ip# items set expires_at. Nothing here is precious: an empty table just restarts counts.
resource "aws_dynamodb_table" "guard" {
  name         = "${local.prefix}-guard"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Owned groups (AOTY-48). Layout and rules: lambdas/common/groups.py. One partition per group:
#   pk group#<slug>, sk meta               {name, owner, member_count, created_at, updated_at,
#                                           chart_requested_at, chart_built_at}
#   pk group#<slug>, sk member#<username>  {username, added_at}
#   pk session#<username>, sk meta         {revoked_at}  sign out everywhere's cutoff (AOTY-57):
#                                          never owner or username, so it stays out of the GSIs
# Members are items, not a list, so by_member can answer "which groups is alice in?" (the chart
# builder after a sync; AOTY-49's opt-out). by_owner is sparse: only meta items carry owner.
resource "aws_dynamodb_table" "groups" {
  name         = "${local.prefix}-groups"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "username"
    type = "S"
  }
  attribute {
    name = "owner"
    type = "S"
  }

  global_secondary_index {
    name            = "by_member"
    projection_type = "KEYS_ONLY"
    key_schema {
      attribute_name = "username"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "pk"
      key_type       = "RANGE"
    }
  }

  global_secondary_index {
    name               = "by_owner"
    projection_type    = "INCLUDE"
    non_key_attributes = ["name", "member_count", "updated_at"]
    key_schema {
      attribute_name = "owner"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "pk"
      key_type       = "RANGE"
    }
  }

  deletion_protection_enabled = var.environment == "prod"
}

# The shared release-year cache. Keyed like M0's identity (aoty.py): a match needs title AND
# artist credit, so "1989" by two artists can't collide, and one Query returns an artist's
# albums. artist_key = norm(album artist); title_key = norm(base_title(album)), so edition
# suffixes like "(Deluxe Edition)" share an item.
resource "aws_dynamodb_table" "albums" {
  name         = "${local.prefix}-albums"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "artist_key"
  range_key    = "title_key"

  attribute {
    name = "artist_key"
    type = "S"
  }
  attribute {
    name = "title_key"
    type = "S"
  }

  # Slow to rebuild at MusicBrainz's 1 req/s, so guarded in staging and prod. Sandbox's copy
  # is disposable (AOTY-33). prevent_destroy can't depend on a variable, so destroying sandbox
  # still needs `terraform state rm` first (infra/README.md, "Testing a branch in sandbox").
  deletion_protection_enabled = var.environment != "sandbox"

  point_in_time_recovery {
    enabled = var.albums_point_in_time_recovery
  }

  lifecycle {
    prevent_destroy = true
  }
}
