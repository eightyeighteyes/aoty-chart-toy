"""Sync worker Lambda (AOTY-18): Last.fm weekly album charts -> per-user store in S3.

Triggered by the sync FIFO queue (message group = username, so one sync per user at a time),
batch size 1, with ReportBatchItemFailures. A message body is

  {"username": "alice", "year": 2026?, "full": false?}

and a direct invoke with that same object (no "Records") runs one sync, for manual testing.

One sync:
  1. Fetch the year's chart weeks that aren't settled yet (all on a first or monthly full
     sync) and overwrite them in s3://$STORE_BUCKET/users/<username>/<year>/weeks.json.gz,
     checkpointing every CHECKPOINT_EVERY weeks. A kill loses at most those weeks; the rerun
     refetches them, so totals come out the same.
  2. Harvest covers and the high-water mark from getRecentTracks (best effort).
  3. Queue albums with no albums-table item to mb-hot (the user's top 150) or mb-cold, and
     poke the mb Lambda if anything was queued. mb messages can repeat; AOTY-19 must treat
     them idempotently.
  4. Mark the users row done. The chart builder hook is a no-op until AOTY-21.

Error 6 (confirmed by user.getInfo) and 17 mark the user not_found / private and delete the
message; a new request always tries again. Other errors fail the message for an SQS retry
after retry_delay (60 s doubling, not the queue's 90-min visibility timeout), with the users
row set to "retrying"; on the last receive it is set to "error" before the DLQ.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import time

import sync_core as core
from common import lastfm
from common.albumkey import join_key, split_key
from common.config import TARGET_YEAR

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_RECEIVES = 5  # the sync queue's redrive maxReceiveCount (infra/modules/stack/queues.tf)
CHECKPOINT_EVERY = 8
TIME_MARGIN_MS = 60_000
FIRST_COVER_PAGES = 20
DELTA_COVER_PAGES = 5
BATCH_GET = 100  # DynamoDB BatchGetItem limit
SEND_BATCH = 10  # SQS SendMessageBatch limit
RETRIES = 4
RETRY_BASE_S = 60  # a failed sync is retried after 60 s, then 120, 240, 480
RETRY_MAX_S = 900
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class Deps:
  """Everything a sync touches, so tests can pass fakes. from_env() builds the real ones."""

  def __init__(self, *, s3, ddb, sqs, lambda_, lastfm_client, env, clock=time.time, sleep=time.sleep):
    self.s3, self.ddb, self.sqs, self.lambda_ = s3, ddb, sqs, lambda_
    self.lastfm, self.env, self.clock, self.sleep = lastfm_client, env, clock, sleep

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ
    key = lastfm.api_key_from_ssm(boto3.client("ssm"), f"{env['SSM_PREFIX']}lastfm_api_key")
    return cls(
      s3=boto3.client("s3"),
      ddb=boto3.client("dynamodb"),
      sqs=boto3.client("sqs"),
      lambda_=boto3.client("lambda"),
      lastfm_client=lastfm.Client(key),
      env=env,
    )


_deps = None


def handler(event, context, deps=None):
  global _deps
  if deps is None:
    _deps = _deps or Deps.from_env()
    deps = _deps
  if "Records" not in event:
    return {"result": sync_user(event, context, deps)}

  failures = []
  for rec in event["Records"]:
    msg = {}
    try:
      msg = json.loads(rec["body"])
      result = sync_user(msg, context, deps)
    except Exception as e:
      log.exception("sync failed for %s", _tag(msg.get("username")))
      receives = int((rec.get("attributes") or {}).get("ApproximateReceiveCount", 1))
      last = receives >= MAX_RECEIVES
      _mark_failed(deps, msg, e, "error" if last else "retrying")
      if not last:
        # Without this, a failed message waits out the queue's whole visibility timeout
        # (6 x the function timeout, 90 min) and blocks the user's FIFO group meanwhile.
        _set_visibility(deps, rec, retry_delay(receives))
      failures.append({"itemIdentifier": rec["messageId"]})
      continue
    if result == "partial":
      # Out of time with weeks left: checkpointed, so make it visible again right away.
      _set_visibility(deps, rec, 0)
      failures.append({"itemIdentifier": rec["messageId"]})
  return {"batchItemFailures": failures}


def sync_user(msg, context, deps):
  """One sync. Returns "done", "partial", "not_found", "private" or "invalid"."""
  username = str(msg.get("username") or "").strip().lower()
  if not USERNAME_RE.match(username):
    log.warning("invalid username in message; dropped")
    return "invalid"
  year = int(msg.get("year") or TARGET_YEAR)
  now = int(deps.clock())
  tag = _tag(username)

  user = _get_user(deps, username)
  store = _load_store(deps, username, year) or core.new_store(year)
  full = bool(msg.get("full")) or core.wants_full_resync(user.get("last_full_sync_at"), now)
  full_since = core.full_pass_start(store, now) if full else None
  todo = core.weeks_to_fetch(store, core.week_ranges(year, now), full_since)
  progress = {"weeks_done": 0, "weeks_total": len(todo), "plays": 0}
  _update_user(deps, username, {"state": "syncing", "progress": progress, "sync_started_at": now})
  log.info("sync %s year=%s full=%s weeks=%d", tag, year, full, len(todo))

  try:
    for i, (frm, to) in enumerate(todo, 1):
      if _low_on_time(context):
        _save_progress(deps, username, store, progress)
        log.info("sync %s out of time after %d/%d weeks", tag, i - 1, len(todo))
        return "partial"
      rows = core.fetch_range(lambda f, t: deps.lastfm.weekly_album_chart(username, f, t), frm, min(to, now))
      core.merge_week(store, (frm, to), *core.chart_plays(rows), now)
      progress["weeks_done"] = i
      if i % CHECKPOINT_EVERY == 0:
        _save_progress(deps, username, store, progress)
  except lastfm.LastfmError as e:
    terminal = _terminal_state(deps, username, e)
    if terminal is None:
      raise
    _update_user(deps, username, {"state": terminal, "error_code": str(e.code), "last_synced_at": now})
    log.info("sync %s stopped: %s (Last.fm %s)", tag, terminal, e.code)
    return terminal

  store.pop("full_started_at", None)  # the full pass is complete
  _harvest_covers(deps, username, store, year)
  _save_store(deps, username, store)
  queued = _queue_for_mb(deps, store, now)
  if queued:
    _save_store(deps, username, store)  # queued_at / resolved marks
    deps.lambda_.invoke(FunctionName=deps.env["MB_FUNCTION"], InvocationType="Event", Payload=b"{}")
  start_chart_build(deps, username, year)

  progress["plays"] = sum(core.year_plays(store).values())
  done = {"state": "done", "progress": progress, "last_synced_at": now}
  if full:
    done["last_full_sync_at"] = now
  if store.get("last_seen_uts"):
    done["last_seen_uts"] = store["last_seen_uts"]
  _update_user(deps, username, done, remove=("error_code",))
  log.info("sync %s done: %d weeks, %d plays, %d queued for mb, %d Last.fm calls",
           tag, len(todo), progress["plays"], queued, getattr(deps.lastfm, "calls", 0))  # fmt: skip
  return "done"


def start_chart_build(deps, username, year):
  """Hook for the chart builder (AOTY-21). A no-op until that story."""


# ---- Last.fm --------------------------------------------------------------------------


def _terminal_state(deps, username, err):
  """not_found / private when the error means "stop retrying this user", else None."""
  if err.code == lastfm.PRIVATE:
    return "private"
  # Last.fm has been known to return 6 for real users now and then; confirm before giving up.
  if err.code == lastfm.NOT_FOUND and not deps.lastfm.user_exists(username):
    return "not_found"
  return None


def _harvest_covers(deps, username, store, year):
  """Fill covers from getRecentTracks and advance last_seen_uts. Best effort: never fails a sync."""
  first = not store.get("last_seen_uts")
  frm = core.year_bounds(year)[0] if first else store["last_seen_uts"] + 1
  max_pages = FIRST_COVER_PAGES if first else DELTA_COVER_PAGES
  try:
    for page in range(1, max_pages + 1):
      tracks, total = deps.lastfm.recent_tracks(username, frm, page=page)
      newest = core.newest_uts(tracks)
      if newest and newest > (store.get("last_seen_uts") or 0):
        store["last_seen_uts"] = newest
      core.apply_covers(store, tracks)
      if page >= total or not tracks or core.missing_top_covers(store) == 0:
        break
  except lastfm.LastfmError as e:
    log.warning("cover harvest stopped early: Last.fm %s", e.code)


# ---- mb lane --------------------------------------------------------------------------


def _queue_for_mb(deps, store, now):
  candidates = core.mb_candidates(store, now)
  if not candidates:
    return 0
  resolved = _resolved_keys(deps, candidates)
  for k in resolved:
    store["albums"][k]["resolved"] = True
  hot, cold = core.split_hot_cold(store, [k for k in candidates if k not in resolved])
  return _send(deps, deps.env["MB_HOT_URL"], hot, store, now) + _send(
    deps, deps.env["MB_COLD_URL"], cold, store, now
  )


def _resolved_keys(deps, keys):
  """The keys that already have an albums-table item (a year or a confirmed miss)."""
  table, found = deps.env["ALBUMS_TABLE"], set()
  for i in range(0, len(keys), BATCH_GET):
    pending = {
      table: {
        "Keys": [
          {"artist_key": {"S": a}, "title_key": {"S": t}} for a, t in map(split_key, keys[i : i + BATCH_GET])
        ],
        "ProjectionExpression": "artist_key, title_key",
      }
    }
    for attempt in range(RETRIES + 1):
      out = deps.ddb.batch_get_item(RequestItems=pending)
      for item in out.get("Responses", {}).get(table, []):
        found.add(join_key((item["artist_key"]["S"], item["title_key"]["S"])))
      pending = out.get("UnprocessedKeys") or {}
      if not pending:
        break
      if attempt == RETRIES:
        raise RuntimeError("BatchGetItem left unprocessed keys after retries")
      deps.sleep(0.2 * 2**attempt)
  return found


def _send(deps, queue_url, keys, store, now):
  """SendMessageBatch in tens, retrying failed entries; stamps queued_at on each sent key."""
  sent = 0
  for i in range(0, len(keys), SEND_BATCH):
    batch = {str(j): k for j, k in enumerate(keys[i : i + SEND_BATCH])}
    for attempt in range(RETRIES + 1):
      entries = [{"Id": j, "MessageBody": json.dumps(_mb_message(store, k))} for j, k in batch.items()]
      out = deps.sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
      for ok in out.get("Successful", []):
        store["albums"][batch[ok["Id"]]]["queued_at"] = now
        sent += 1
      batch = {f["Id"]: batch[f["Id"]] for f in out.get("Failed", [])}
      if not batch:
        break
      if attempt == RETRIES:
        raise RuntimeError(f"SendMessageBatch failed for {len(batch)} entries after retries")
      deps.sleep(0.2 * 2**attempt)
  return sent


def _mb_message(store, key):
  artist_key, title_key = split_key(key)
  a = store["albums"][key]
  return {
    "artist_key": artist_key,
    "title_key": title_key,
    "artist": a.get("artist", ""),
    "album": a.get("album", ""),
    "mbid": a.get("mbid", ""),
  }


# ---- storage --------------------------------------------------------------------------


def store_key(username, year):
  return f"users/{username}/{year}/weeks.json.gz"


def _load_store(deps, username, year):
  try:
    obj = deps.s3.get_object(Bucket=deps.env["STORE_BUCKET"], Key=store_key(username, year))
  except Exception as e:
    if _error_code(e) in ("NoSuchKey", "404"):
      return None
    raise
  store = json.loads(gzip.decompress(obj["Body"].read()))
  if store.get("version") != core.STORE_VERSION:
    raise RuntimeError(f"unknown store version {store.get('version')!r}")
  return store


def _save_store(deps, username, store):
  body = gzip.compress(json.dumps(store, separators=(",", ":")).encode(), mtime=0)
  deps.s3.put_object(
    Bucket=deps.env["STORE_BUCKET"],
    Key=store_key(username, store["year"]),
    Body=body,
    ContentType="application/json",
    ContentEncoding="gzip",
  )


def _save_progress(deps, username, store, progress):
  _save_store(deps, username, store)
  _update_user(deps, username, {"progress": progress})


def _error_code(e):
  return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", ""))


# ---- users table ----------------------------------------------------------------------


def _av(v):
  """A Python value as a DynamoDB attribute value (the few types the users row uses)."""
  if isinstance(v, bool):
    return {"BOOL": v}
  if isinstance(v, int | float):
    return {"N": str(v)}
  if isinstance(v, dict):
    return {"M": {k: _av(x) for k, x in v.items()}}
  if v is None:
    return {"NULL": True}
  return {"S": str(v)}


def _get_user(deps, username):
  out = deps.ddb.get_item(TableName=deps.env["USERS_TABLE"], Key={"username": {"S": username}})
  item = out.get("Item") or {}
  return {k: int(v["N"]) if "N" in v else v.get("S") for k, v in item.items() if "N" in v or "S" in v}


def _update_user(deps, username, fields, remove=()):
  names, values, sets = {}, {}, []
  for i, (k, v) in enumerate(fields.items()):
    names[f"#s{i}"], values[f":s{i}"] = k, _av(v)
    sets.append(f"#s{i} = :s{i}")
  expr = "SET " + ", ".join(sets)
  if remove:
    for i, k in enumerate(remove):
      names[f"#r{i}"] = k
    expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(remove)))
  deps.ddb.update_item(
    TableName=deps.env["USERS_TABLE"],
    Key={"username": {"S": username}},
    UpdateExpression=expr,
    ExpressionAttributeNames=names,
    ExpressionAttributeValues=values,
  )


def _mark_failed(deps, msg, err, state):
  username = str(msg.get("username") or "").strip().lower()
  if not USERNAME_RE.match(username):
    return
  try:
    _update_user(deps, username, {"state": state, "error_code": type(err).__name__})
  except Exception:
    log.exception("could not mark %s as %s", _tag(username), state)


def retry_delay(receives):
  """Seconds before a failed message is retried: 60, 120, 240, 480 for receives 1-4."""
  return min(RETRY_BASE_S * 2 ** (max(receives, 1) - 1), RETRY_MAX_S)


def _set_visibility(deps, rec, seconds):
  try:
    deps.sqs.change_message_visibility(
      QueueUrl=deps.env["SYNC_QUEUE_URL"], ReceiptHandle=rec["receiptHandle"], VisibilityTimeout=seconds
    )
  except Exception:
    # The message still comes back, just after the queue's full visibility timeout.
    log.exception("could not change message visibility")


# ---- helpers --------------------------------------------------------------------------


def _low_on_time(context):
  return context is not None and context.get_remaining_time_in_millis() < TIME_MARGIN_MS


def _tag(username):
  """A short stable hash, so logs can follow one user without naming them."""
  if not username:
    return "-"
  return hashlib.sha256(str(username).lower().encode()).hexdigest()[:10]
