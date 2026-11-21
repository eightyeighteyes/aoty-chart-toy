# On-demand billing: $0 when idle. Encryption is DynamoDB's default AWS-owned key (free).

resource "aws_dynamodb_table" "users" {
  name         = "${local.prefix}-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "username" # lowercased Last.fm username

  attribute {
    name = "username"
    type = "S"
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

  # Slow to rebuild at MusicBrainz's 1 req/s, so guarded in both environments.
  deletion_protection_enabled = true

  point_in_time_recovery {
    enabled = var.albums_point_in_time_recovery
  }

  lifecycle {
    prevent_destroy = true
  }
}
