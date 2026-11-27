"""Sync worker Lambda (AOTY-18): Last.fm weekly album charts -> per-user store in S3.

Triggered by the sync FIFO queue (message group = username, so one sync per user at a time),
batch size 1, with ReportBatchItemFailures. A message body is

  {"username": "alice", "year": 2026?, "full": false?, "continuation": n?}

(continuation: set by the worker itself when a run out of time hands on to a fresh message,
_continue) and a direct invoke with that same object (no "Records") runs one sync, for manual testing.

One sync:
  0. On a first sync, fetch one page of recent tracks and write the users row's `preview`
     (JSON: {"wall": [...]}, sync_core.wall_from_tracks), the cover wall a first visit shows
     (AOTY-24). The chart builder removes it once a chart ranks anything. `progress` (weeks,
     plays and sync_core.running_stats) is written after every week.
  1. Fetch the year's chart weeks that aren't settled yet (all on a first or monthly full
     sync) and overwrite them in s3://$STORE_BUCKET/users/<username>/<year>/weeks.json.gz,
     checkpointing every CHECKPOINT_EVERY weeks. A kill loses at most those weeks; the rerun
     refetches them, so totals come out the same.
  2. Harvest covers and the high-water mark from getRecentTracks (best effort).
  3. Queue albums with no albums-table item to mb-hot (the user's top 150) or mb-cold, and
     poke the mb Lambda if anything was queued. mb messages can repeat; AOTY-19 must treat
     them idempotently.
  4. Refresh the user's display name (Last.fm's casing) when it's missing or 30 days old, start
     the chart builder (AOTY-21) asynchronously, and mark the users row done.

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
import time

import sync_core as core
from common import emf, lastfm
from common.albumkey import join_key, split_key
from common.config import TARGET_YEAR
from common.username import USERNAME_RE

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_RECEIVES = 5  # the sync queue's redrive maxReceiveCount (infra/modules/stack/queues.tf)
# A full year is ~48 weeks; even at a week a minute a sync needs ~4 runs. Past this, something
# is wrong: fall back to re-receiving, which the DLQ bounds.
MAX_CONTINUATIONS = 20
CHECKPOINT_EVERY = 8
TIME_MARGIN_MS = 60_000
FIRST_COVER_PAGES = 20
DELTA_COVER_PAGES = 5
BATCH_GET = 100  # DynamoDB BatchGetItem limit
SEND_BATCH = 10  # SQS SendMessageBatch limit
RETRIES = 4
RETRY_BASE_S = 60  # a failed sync is retried after 60 s, then 120, 240, 480
RETRY_MAX_S = 900
DISPLAY_NAME_MAX_AGE = 30 * core.DAY


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
    msg, info, result = {}, {}, "failed"
    started, calls_before = deps.clock(), _lastfm_calls(deps)
    try:
      msg = json.loads(rec["body"])
      result = sync_user(msg, context, deps, info)
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
    finally:
      _emit(deps, result, info, deps.clock() - started, _lastfm_calls(deps) - calls_before)
    if result == "partial" and not _continue(deps, msg, info):
      # Out of time with weeks left, and no fresh message sent: checkpointed, so make this one
      # visible again right away. This receive counts toward the DLQ's maxReceiveCount.
      _set_visibility(deps, rec, 0)
      failures.append({"itemIdentifier": rec["messageId"]})
  return {"batchItemFailures": failures}


def _continue(deps, msg, info):
  """Carry on a checkpointed sync in a fresh message, so its receive count starts again (AOTY-46).

  Re-receiving the same message would count every continuation toward maxReceiveCount, and a
  long first sync would reach the DLQ without failing. The new message goes to the same FIFO
  group, behind this one, which is deleted when the handler reports success. Not when the run
  made no progress (re-receiving then bounds a stuck sync by the DLQ), past MAX_CONTINUATIONS,
  or when the send fails: then the caller re-receives this message as before.
  """
  if not info.get("weeks"):
    return False
  n = int(msg.get("continuation") or 0) + 1
  if n > MAX_CONTINUATIONS:
    log.warning("sync %s: %d continuations, re-receiving instead", _tag(msg.get("username")), n - 1)
    return False
  username = str(msg.get("username") or "").strip().lower()
  try:
    deps.sqs.send_message(
      QueueUrl=deps.env["SYNC_QUEUE_URL"],
      MessageBody=json.dumps({**msg, "continuation": n}),
      MessageGroupId=username,
      MessageDeduplicationId=f"{username}-cont-{n}-{int(deps.clock())}",
    )
  except Exception:
    log.exception("could not send the continuation for %s", _tag(username))
    return False
  return True


def _lastfm_calls(deps):
  """The client's running count of HTTP attempts. The client lives as long as the warm
  container, so callers take differences, never the total."""
  return getattr(deps.lastfm, "calls", 0)


def _emit(deps, outcome, info, seconds, calls):
  """One EMF line per record (AOTY-41). Metrics: SyncsDone, SyncFailures, LastfmCalls; the
  rest are plain fields (free) for the dashboard's Logs Insights table. No username."""
  emf.emit(
    deps.env.get("AOTY_ENV", ""),
    {
      "SyncsDone": (1 if outcome == "done" else 0, "Count"),
      "SyncFailures": (1 if outcome == "failed" else 0, "Count"),
      "LastfmCalls": (calls, "Count"),
    },
    clock=deps.clock,
    source="sync",
    outcome=outcome,
    first_sync=bool(info.get("first_sync")),
    weeks=info.get("weeks", 0),
    seconds=round(seconds, 1),
  )


def sync_user(msg, context, deps, info=None):
  """One sync. Returns "done", "partial", "not_found", "private" or "invalid".

  info, if given, gets "weeks" (weeks fetched) and "first_sync" (a clean first sync) for the
  run's EMF line.
  """
  info = {} if info is None else info
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
  # The status a page polls: weeks, plays and the running stats, written after every week (a
  # small users-row update; the store itself is still checkpointed every CHECKPOINT_EVERY).
  progress = {"weeks_done": 0, "weeks_total": len(todo), **_progress_stats(store)}
  started = {"state": "syncing", "progress": progress, "sync_started_at": now}
  first_page = None
  if not store.get("last_seen_uts") and not user.get("preview"):
    # A first sync: one page of recent tracks up front, so the page's cover wall (AOTY-24)
    # fills while the weeks are read. The cover harvest reuses it as its page 1.
    first_page = _first_recent_page(deps, username, year)
    if first_page is not None:
      started["preview"] = json.dumps({"wall": core.wall_from_tracks(first_page[0])}, separators=(",", ":"))
  # The queue ticket the api handed out (AOTY-26) is used up when the sync starts, so the users
  # still waiting move up a place. The FIFO group means no other worker has this user now.
  ticket = user.get("queue_ticket")
  _update_user(deps, username, started, remove=("queue_ticket",) if ticket is not None else ())
  if ticket is not None:
    _guard_add(deps, {"started": 1})
  # A clean first sync (no earlier attempt) measures the job time a queue's wait is estimated from.
  timed_first = not store.get("last_seen_uts") and not user.get("sync_started_at")
  info["first_sync"] = timed_first
  calls_before = getattr(deps.lastfm, "calls", 0)
  log.info("sync %s year=%s full=%s weeks=%d", tag, year, full, len(todo))

  try:
    for i, (frm, to) in enumerate(todo, 1):
      if _low_on_time(context):
        _save_progress(deps, username, store, progress)
        log.info("sync %s out of time after %d/%d weeks", tag, i - 1, len(todo))
        return "partial"
      rows = core.fetch_range(lambda f, t: deps.lastfm.weekly_album_chart(username, f, t), frm, min(to, now))
      core.merge_week(store, (frm, to), *core.chart_plays(rows), now)
      info["weeks"] = i
      progress.update(weeks_done=i, **_progress_stats(store))
      if i % CHECKPOINT_EVERY == 0:
        _save_progress(deps, username, store, progress)
      else:
        _update_user(deps, username, {"progress": progress})
  except lastfm.LastfmError as e:
    terminal = _terminal_state(deps, username, e)
    if terminal is None:
      raise
    _update_user(deps, username, {"state": terminal, "error_code": str(e.code), "last_synced_at": now,
                                  "sync_finished_at": int(deps.clock())})  # fmt: skip
    log.info("sync %s stopped: %s (Last.fm %s)", tag, terminal, e.code)
    return terminal

  store.pop("full_started_at", None)  # the full pass is complete
  _harvest_covers(deps, username, store, year, first_page)
  _save_store(deps, username, store)
  queued = _queue_for_mb(deps, store, now)
  if queued:
    _save_store(deps, username, store)  # queued_at / resolved marks
    deps.lambda_.invoke(FunctionName=deps.env["MB_FUNCTION"], InvocationType="Event", Payload=b"{}")
  progress.update(_progress_stats(store))
  # Before the chart build starts: it reads display_name, and the status API compares its
  # chart_built_at with sync_finished_at, so the finish must be recorded first.
  done = {"state": "done", "progress": progress, "last_synced_at": now, "sync_finished_at": int(deps.clock()),
          **_display_name_fields(deps, username, user, now)}  # fmt: skip
  if full:
    done["last_full_sync_at"] = now
  if store.get("last_seen_uts"):
    done["last_seen_uts"] = store["last_seen_uts"]
  _update_user(deps, username, done, remove=("error_code",))
  if timed_first:
    _guard_add(deps, {"first_syncs": 1, "first_sync_seconds": max(1, done["sync_finished_at"] - now)})
  start_chart_build(deps, username, year, now)
  log.info("sync %s done: %d weeks, %d plays, %d queued for mb, %d Last.fm calls",
           tag, len(todo), progress["plays"], queued, getattr(deps.lastfm, "calls", 0) - calls_before)  # fmt: skip
  return "done"


def start_chart_build(deps, username, year, now):
  """Ask the chart builder (AOTY-21) for this user's chart. Best effort: the sync is done.

  requested_at lets the builder skip a repeated or retried event once a newer chart exists.
  A lost poke leaves the chart stale until the user's next sync.
  """
  payload = {"username": username, "year": year, "requested_at": now}
  try:
    deps.lambda_.invoke(
      FunctionName=deps.env["CHART_FUNCTION"], InvocationType="Event", Payload=json.dumps(payload).encode()
    )
  except Exception:
    log.exception("could not start the chart build for %s", _tag(username))


def _display_name_fields(deps, username, user, now):
  """{display_name, display_name_at} when the stored casing is missing or stale, else {}.

  One user.getInfo call at most every DISPLAY_NAME_MAX_AGE. Best effort: a failure keeps
  whatever name is stored (the chart falls back to the username).
  """
  if user.get("display_name") and now - (user.get("display_name_at") or 0) < DISPLAY_NAME_MAX_AGE:
    return {}
  try:
    name = deps.lastfm.user_info(username)
  except lastfm.LastfmError as e:
    log.warning("display name lookup failed: Last.fm %s", e.code)
    return {}
  if not name or name.lower() != username:  # never store a name that isn't this user's
    return {}
  return {"display_name": name, "display_name_at": now}


# ---- Last.fm --------------------------------------------------------------------------


def _terminal_state(deps, username, err):
  """not_found / private when the error means "stop retrying this user", else None."""
  if err.code == lastfm.PRIVATE:
    return "private"
  # Last.fm has been known to return 6 for real users now and then; confirm before giving up.
  if err.code == lastfm.NOT_FOUND and not deps.lastfm.user_exists(username):
    return "not_found"
  return None


def _progress_stats(store):
  """plays (the store's year total) plus the running stats a first visit shows."""
  return {"plays": sum(core.year_plays(store).values()), **core.running_stats(store)}


def _first_recent_page(deps, username, year):
  """(tracks, total pages) of a first sync's newest scrobbles, or None. Best effort."""
  try:
    return deps.lastfm.recent_tracks(username, core.year_bounds(year)[0], page=1)
  except lastfm.LastfmError as e:
    log.warning("cover wall page failed: Last.fm %s", e.code)
    return None


def _harvest_covers(deps, username, store, year, first_page=None):
  """Fill covers from getRecentTracks and advance last_seen_uts. Best effort: never fails a sync.

  first_page: page 1 already fetched for a first sync's cover wall, so it isn't fetched twice.
  """
  first = not store.get("last_seen_uts")
  frm = core.year_bounds(year)[0] if first else store["last_seen_uts"] + 1
  max_pages = FIRST_COVER_PAGES if first else DELTA_COVER_PAGES
  try:
    for page in range(1, max_pages + 1):
      if page == 1 and first and first_page is not None:
        tracks, total = first_page
      else:
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


def _guard_add(deps, counts):
  """ADD counts to the guard table's queue item (AOTY-26). Best effort: a lost count only skews
  the queue position or the wait estimate a little, never a sync."""
  names = {f"#c{i}": k for i, k in enumerate(counts)}
  values = {f":c{i}": {"N": str(v)} for i, v in enumerate(counts.values())}
  try:
    deps.ddb.update_item(
      TableName=deps.env["GUARD_TABLE"],
      Key={"id": {"S": "queue"}},
      UpdateExpression="ADD " + ", ".join(f"#c{i} :c{i}" for i in range(len(counts))),
      ExpressionAttributeNames=names,
      ExpressionAttributeValues=values,
    )
  except Exception:
    log.exception("could not update the queue counters")


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
