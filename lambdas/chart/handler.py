"""Chart builder Lambda (AOTY-21): one user's store + the albums table -> chart.json on the site.

Started by async invokes only. Reserved concurrency 1 runs builds one at a time, so a build that
read older data can never overwrite a newer chart. Two events:

  {"username": "alice", "requested_at": uts, "year": 2026?}
      From the sync worker after each sync. Skipped when the user's chart was built at or after
      requested_at: async events can repeat, and throttled ones are retried for up to an hour.
  {"pending": true}
      From the mb lane after it writes albums (at most once a minute). Rebuilds every user whose
      last chart had pending albums, if at least one of those now has an albums item. An album
      the lane can never answer doesn't cause a rebuild loop. Corrections to an album that was
      already on a chart (index re-checks) show up at the user's next sync.

A build:
  1. Loads s3://$STORE_BUCKET/users/<u>/<year>/weeks.json.gz (no store: nothing to build).
  2. BatchGets the albums items for every key the user played this year.
  3. Writes s3://$SITE_BUCKET/$SITE_PREFIX/data/users/<u>/chart.json with Cache-Control no-cache,
     and users/<u>/<year>/chart_pending.json (the keys still pending) in the store bucket.
  4. Sets chart_built_at and chart_pending on the users row.

The pending pass Scans the users table for chart_pending > 0. That's fine at M1's scale (a
few hundred users); past that, a sparse GSI on chart_pending replaces the Scan.

Logs name users by a hash (_tag), never by username. One EMF line per run.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import time

import chart_core as core
from common import dynamo
from common.albumkey import join_key, split_key
from common.config import TARGET_YEAR

log = logging.getLogger()
log.setLevel(logging.INFO)

NAMESPACE = "kamp-aoty"
BATCH_GET = 100  # DynamoDB BatchGetItem limit
RETRIES = 4
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")  # as the sync worker's


class Deps:
  """Everything a build touches, so tests can pass fakes. from_env() builds the real ones."""

  def __init__(self, *, s3, ddb, env, clock=time.time, mono=time.monotonic, sleep=time.sleep):
    self.s3, self.ddb, self.env = s3, ddb, env
    self.clock, self.mono, self.sleep = clock, mono, sleep

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    return cls(s3=boto3.client("s3"), ddb=boto3.client("dynamodb"), env=os.environ)


_deps = None


def handler(event, context, deps=None):
  global _deps
  if deps is None:
    _deps = _deps or Deps.from_env()
    deps = _deps
  event = event or {}
  started = deps.mono()
  totals = {"built": 0, "skipped": 0, "failed": 0, "checked": 0}
  if event.get("pending"):
    for row in _pending_users(deps):
      totals["checked"] += 1
      _guarded(deps, row["username"], totals, lambda r=row: _rebuild_if_resolved(deps, r, totals))
  else:
    username = str(event.get("username") or "").strip().lower()
    if not USERNAME_RE.match(username):
      log.warning("invalid username in event; dropped")
      totals["skipped"] += 1
    else:
      requested_at = int(event.get("requested_at") or 0)
      year = int(event.get("year") or TARGET_YEAR)
      _guarded(deps, username, totals, lambda: _build_requested(deps, username, year, requested_at, totals))
  _emit(deps, totals, deps.mono() - started)
  return totals


def _guarded(deps, username, totals, fn):
  """Run one user's build; a failure is logged and counted, never raised past the others."""
  try:
    fn()
  except Exception:
    log.exception("chart build failed for %s", _tag(username))
    totals["failed"] += 1


def _build_requested(deps, username, year, requested_at, totals):
  row = _get_user(deps, username)
  if requested_at and row.get("chart_built_at", 0) >= requested_at:
    totals["skipped"] += 1
    return
  _count(totals, build(deps, username, row, year))


def _rebuild_if_resolved(deps, row, totals):
  username, year = row["username"], TARGET_YEAR
  keys = _load_pending(deps, username, year)
  if keys and not _get_items(deps, keys, keys_only=True):
    totals["skipped"] += 1  # nothing new since the last build
    return
  _count(totals, build(deps, username, _get_user(deps, username), year))


def _count(totals, built):
  totals["built" if built else "skipped"] += 1


def build(deps, username, row, year=TARGET_YEAR):
  """One user's chart. False when they have no store yet (never synced)."""
  store = _load_store(deps, username, year)
  if store is None:
    return False
  now = int(deps.clock())
  plays = core.year_plays(store)
  items = _get_items(deps, sorted(plays))
  chart, pending = core.build_chart(
    store, items, username=username, display_name=row.get("display_name"), now=now
  )
  _put_json(
    deps, deps.env["SITE_BUCKET"], chart_key(deps.env.get("SITE_PREFIX", ""), username), chart,
    CacheControl="no-cache",
  )  # fmt: skip
  _put_json(deps, deps.env["STORE_BUCKET"], pending_key(username, year), {"keys": pending})
  _update_user(deps, username, {"chart_built_at": now, "chart_pending": len(pending)})
  log.info("chart %s: %d albums shown, %d pending", _tag(username), len(chart["albums"]), len(pending))
  return True


# ---- storage --------------------------------------------------------------------------


def chart_key(site_prefix, username):
  return f"{site_prefix}data/users/{username}/chart.json"


def store_key(username, year):
  return f"users/{username}/{year}/weeks.json.gz"  # the sync worker's (lambdas/sync/handler.py)


def pending_key(username, year):
  return f"users/{username}/{year}/chart_pending.json"


def _get_object(deps, bucket, key):
  try:
    return deps.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
  except Exception as e:
    if _error_code(e) in ("NoSuchKey", "404"):
      return None
    raise


def _load_store(deps, username, year):
  body = _get_object(deps, deps.env["STORE_BUCKET"], store_key(username, year))
  if body is None:
    return None
  store = json.loads(gzip.decompress(body))
  if store.get("version") != 1:
    raise RuntimeError(f"unknown store version {store.get('version')!r}")
  return store


def _load_pending(deps, username, year):
  body = _get_object(deps, deps.env["STORE_BUCKET"], pending_key(username, year))
  return json.loads(body)["keys"] if body else []


def _put_json(deps, bucket, key, obj, **extra):
  deps.s3.put_object(
    Bucket=bucket,
    Key=key,
    Body=json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(),
    ContentType="application/json",
    **extra,
  )


def _error_code(e):
  return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", ""))


# ---- albums table -----------------------------------------------------------------------


def _get_items(deps, keys, keys_only=False):
  """{key: item} for the keys that have an albums item; BatchGetItem in 100s, with retries."""
  table, found = deps.env["ALBUMS_TABLE"], {}
  for i in range(0, len(keys), BATCH_GET):
    request = {
      "Keys": [{"artist_key": {"S": a}, "title_key": {"S": t}} for a, t in map(split_key, keys[i : i + BATCH_GET])]
    }
    if keys_only:
      request["ProjectionExpression"] = "artist_key, title_key"
    pending = {table: request}
    for attempt in range(RETRIES + 1):
      out = deps.ddb.batch_get_item(RequestItems=pending)
      for raw in out.get("Responses", {}).get(table, []):
        item = dynamo.from_item(raw)
        found[join_key((item["artist_key"], item["title_key"]))] = item
      pending = out.get("UnprocessedKeys") or {}
      if not pending:
        break
      if attempt == RETRIES:
        raise RuntimeError("BatchGetItem left unprocessed keys after retries")
      deps.sleep(0.2 * 2**attempt)
  return found


# ---- users table ------------------------------------------------------------------------


def _get_user(deps, username):
  out = deps.ddb.get_item(TableName=deps.env["USERS_TABLE"], Key={"username": {"S": username}})
  return dynamo.from_item(out.get("Item") or {})


def _update_user(deps, username, fields):
  names = {f"#f{i}": k for i, k in enumerate(fields)}
  values = {f":f{i}": v for i, v in enumerate(fields.values())}
  deps.ddb.update_item(
    TableName=deps.env["USERS_TABLE"],
    Key={"username": {"S": username}},
    UpdateExpression="SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(fields))),
    ExpressionAttributeNames=names,
    ExpressionAttributeValues=dynamo.to_item(values),
  )


def _pending_users(deps):
  """Users rows whose last chart had pending albums: [{"username", ...}]."""
  kwargs = {
    "TableName": deps.env["USERS_TABLE"],
    "FilterExpression": "chart_pending > :zero",
    "ProjectionExpression": "username, chart_built_at",
    "ExpressionAttributeValues": {":zero": {"N": "0"}},
  }
  rows = []
  while True:
    out = deps.ddb.scan(**kwargs)
    rows += [dynamo.from_item(it) for it in out.get("Items", [])]
    if not out.get("LastEvaluatedKey"):
      return rows
    kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]


# ---- metrics ----------------------------------------------------------------------------


def _emit(deps, totals, elapsed):
  values = {
    "ChartsBuilt": (totals["built"], "Count"),
    "ChartsSkipped": (totals["skipped"], "Count"),
    "ChartFailures": (totals["failed"], "Count"),
    "ChartSeconds": (round(elapsed, 2), "Seconds"),
  }
  line = {
    "_aws": {
      "Timestamp": int(deps.clock() * 1000),
      "CloudWatchMetrics": [
        {
          "Namespace": NAMESPACE,
          "Dimensions": [["Environment"]],
          "Metrics": [{"Name": n, "Unit": u} for n, (_, u) in values.items()],
        }
      ],
    },
    "Environment": deps.env.get("AOTY_ENV", ""),
    **{n: v for n, (v, _) in values.items()},
    "pending_checked": totals["checked"],
  }
  print(json.dumps(line, separators=(",", ":")))


def _tag(username):
  """A short stable hash, so logs can follow one user without naming them (as sync's)."""
  if not username:
    return "-"
  return hashlib.sha256(str(username).lower().encode()).hexdigest()[:10]
