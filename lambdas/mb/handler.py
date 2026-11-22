"""MusicBrainz lane Lambda (AOTY-19): the one MusicBrainz client, draining mb-hot then mb-cold.

Started by the sync worker (async invoke, payload {}) whenever it queues albums, by an hourly
schedule as a backstop, and by itself when it runs out of time with work left. Reserved
concurrency 1 (infra/modules/stack/functions.tf) makes it the only MusicBrainz client in its
environment: a second start is throttled and Lambda retries it after this one exits.

A message is one album, as the sync worker sends it (AOTY-18):

  {"artist_key", "title_key", "artist", "album", "mbid"}

Messages can repeat (several users, or a crash in sync), so a run is idempotent: an album
with an albums-table item is resolved and its messages are just deleted.

One run, until both queues are empty or time runs short:
  1. Receive up to BATCH messages, from mb-hot if it has any, else mb-cold.
  2. Delete messages whose album already has an item.
  3. Resolve the rest (mb_core.resolve: year, then track count for target-year albums).
  4. Write an item per resolved album (a confirmed miss included, with year null), then
     delete all of that album's messages, from both queues.
  5. Failed albums are neither written nor deleted. They come back after the queue's
     visibility timeout (sooner when the run stopped for time), and after 5 receives go to the
     dead-letter queue.
With both queues empty, a run makes no MusicBrainz request. Each run logs one EMF line.
"""

import json
import logging
import os
import time

import mb_core as core
from common import lastfm, musicbrainz
from common.config import TARGET_YEAR

log = logging.getLogger()
log.setLevel(logging.INFO)

UA_VERSION = "0.1.0"
RECEIVE_MAX = 10  # SQS ReceiveMessage limit
BATCH = 20  # albums per resolve: one release-group search (mb_core.MB_BATCH_SIZE)
WAIT_S = 1  # long poll: an empty answer means the queue is empty, not an unlucky sample
DEADLINE_MARGIN_S = 20  # stop MusicBrainz work this long before the Lambda timeout
MIN_BATCH_S = 30  # don't start a batch with less than this left
BATCH_GET, BATCH_WRITE, DELETE_BATCH = 100, 25, 10  # DynamoDB / SQS limits
RETRIES = 4
NAMESPACE = "kamp-aoty"


class Deps:
  """Everything a run touches, so tests can pass fakes. from_env() builds the real ones."""

  def __init__(self, *, sqs, ddb, lambda_, mb, lastfm_client, env, clock=time.time, mono=time.monotonic,
               sleep=time.sleep):  # fmt: skip
    self.sqs, self.ddb, self.lambda_, self.mb, self.lastfm = sqs, ddb, lambda_, mb, lastfm_client
    self.env, self.clock, self.mono, self.sleep = env, clock, mono, sleep

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ
    ssm = boto3.client("ssm")
    key = lastfm.api_key_from_ssm(ssm, f"{env['SSM_PREFIX']}lastfm_api_key")
    contact = _ssm_value(ssm, f"{env['SSM_PREFIX']}mb_contact")
    return cls(
      sqs=boto3.client("sqs"),
      ddb=boto3.client("dynamodb"),
      lambda_=boto3.client("lambda"),
      mb=musicbrainz.Client(musicbrainz.user_agent(UA_VERSION, contact)),
      lastfm_client=lastfm.Client(key),
      env=env,
    )


_contact = {}


def _ssm_value(ssm, name):
  if name not in _contact:
    _contact[name] = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
  return _contact[name]


def handler(event, context, deps=None):
  # A fresh MusicBrainz client per run: its stats are this run's metrics. Pacing still holds,
  # because a run never overlaps another (reserved concurrency 1).
  deps = deps or Deps.from_env()
  return run(deps, context)


def run(deps, context):
  started = deps.mono()
  remaining = context.get_remaining_time_in_millis() / 1000 if context else 900
  deadline = started + remaining - DEADLINE_MARGIN_S
  totals = {"messages": 0, "already_resolved": 0, "resolved": 0, "missed": 0, "failed": 0,
            "year_wasted": 0, "invalid": 0, "batches": 0}  # fmt: skip
  queues = [("hot", deps.env["MB_HOT_URL"]), ("cold", deps.env["MB_COLD_URL"])]
  out_of_time = False

  while True:
    if deps.mono() + MIN_BATCH_S > deadline:
      out_of_time = True
      break
    msgs = _receive(deps, queues)
    if not msgs:
      break
    totals["batches"] += 1
    _process(deps, msgs, deadline, totals)
    if deps.mono() + MIN_BATCH_S > deadline:
      out_of_time = True
      break

  depths = _depths(deps, queues)
  poked = False
  if out_of_time and sum(depths.values()):
    # The next run queues behind this one (reserved concurrency 1) and starts when it exits.
    deps.lambda_.invoke(FunctionName=deps.env["MB_FUNCTION"], InvocationType="Event", Payload=b"{}")
    poked = True
  elapsed = deps.mono() - started
  _emit(deps, totals, depths, elapsed, out_of_time, poked)
  return {**totals, "out_of_time": out_of_time, "self_invoked": poked}


# ---- queues -----------------------------------------------------------------------------


def _receive(deps, queues):
  """Up to BATCH messages from the first queue that has any: [(album, queue_url, receipt)]."""
  for _, url in queues:
    got = []
    while len(got) < BATCH:
      out = deps.sqs.receive_message(
        QueueUrl=url,
        MaxNumberOfMessages=min(RECEIVE_MAX, BATCH - len(got)),
        WaitTimeSeconds=WAIT_S if not got else 0,
      )
      page = out.get("Messages", [])
      got += [(m.get("Body", ""), url, m["ReceiptHandle"]) for m in page]
      if len(page) < RECEIVE_MAX:
        break
    if got:
      return got
  return []


def _parse(body):
  try:
    msg = json.loads(body)
  except ValueError:
    return None
  if not isinstance(msg, dict):
    return None
  if not all(isinstance(msg.get(k), str) and msg[k] for k in ("artist_key", "title_key", "artist", "album")):
    return None
  msg.setdefault("mbid", "")
  return msg


def _delete(deps, handles):
  """Delete (queue_url, receipt) pairs, in tens per queue. Best effort: a miss just repeats."""
  by_queue = {}
  for url, receipt in handles:
    by_queue.setdefault(url, []).append(receipt)
  for url, receipts in by_queue.items():
    for i in range(0, len(receipts), DELETE_BATCH):
      entries = [{"Id": str(j), "ReceiptHandle": r} for j, r in enumerate(receipts[i : i + DELETE_BATCH])]
      out = deps.sqs.delete_message_batch(QueueUrl=url, Entries=entries)
      if out.get("Failed"):
        log.warning("could not delete %d messages; they will be skipped as resolved", len(out["Failed"]))


def _release(deps, handles):
  """Make messages visible again now, so the next run (not one 15 min later) retries them."""
  for url, receipt in handles:
    try:
      deps.sqs.change_message_visibility(QueueUrl=url, ReceiptHandle=receipt, VisibilityTimeout=0)
    except Exception:
      log.exception("could not release a message; it returns after the visibility timeout")


def _depths(deps, queues):
  depths = {}
  for name, url in queues:
    try:
      attrs = deps.sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
      depths[name] = int(attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", 0))
    except Exception:
      log.exception("could not read the %s queue depth", name)
      depths[name] = 0
  return depths


# ---- one batch --------------------------------------------------------------------------


def _process(deps, msgs, deadline, totals):
  handles, albums = {}, []
  for body, url, receipt in msgs:
    album = _parse(body)
    if album is None:
      log.warning("invalid mb message; dropped")
      totals["invalid"] += 1
      _delete(deps, [(url, receipt)])
      continue
    key = core.key_of(album)
    if key not in handles:
      albums.append(album)
    handles.setdefault(key, []).append((url, receipt))
  totals["messages"] += len(msgs)

  done = _existing(deps, list(handles))
  totals["already_resolved"] += len(done)
  _delete(deps, [h for k in done for h in handles[k]])
  albums = [a for a in albums if core.key_of(a) not in done]
  if not albums:
    return

  result = core.resolve(
    albums,
    search=lambda entity, query, limit: deps.mb.search(entity, query, limit, deadline=deadline),
    lastfm_tracks=deps.lastfm.album_track_count,
    target_year=TARGET_YEAR,
    now=deps.clock(),
  )
  _write(deps, list(result.items.values()))
  _delete(deps, [h for k in result.items for h in handles[k]])
  if deps.mono() + MIN_BATCH_S > deadline:
    _release(deps, [h for k in result.failed for h in handles[k]])
  for name in ("resolved", "missed", "failed", "year_wasted"):
    totals[name] += result.stats.get(name, 0)


# ---- albums table -----------------------------------------------------------------------


def _existing(deps, keys):
  """The keys that already have an albums-table item."""
  table, found = deps.env["ALBUMS_TABLE"], set()
  for i in range(0, len(keys), BATCH_GET):
    pending = {
      table: {
        "Keys": [{"artist_key": {"S": a}, "title_key": {"S": t}} for a, t in keys[i : i + BATCH_GET]],
        "ProjectionExpression": "artist_key, title_key",
      }
    }
    for attempt in range(RETRIES + 1):
      out = deps.ddb.batch_get_item(RequestItems=pending)
      for item in out.get("Responses", {}).get(table, []):
        found.add((item["artist_key"]["S"], item["title_key"]["S"]))
      pending = out.get("UnprocessedKeys") or {}
      if not pending:
        break
      if attempt == RETRIES:
        raise RuntimeError("BatchGetItem left unprocessed keys after retries")
      deps.sleep(0.2 * 2**attempt)
  return found


def _write(deps, items):
  """BatchWriteItem in 25s, retrying unprocessed items; raises if any are left.

  Raising leaves every message of the batch on the queue, so nothing is lost: the albums
  that did get written are skipped as resolved next time.
  """
  table = deps.env["ALBUMS_TABLE"]
  for i in range(0, len(items), BATCH_WRITE):
    pending = {table: [{"PutRequest": {"Item": to_dynamo(it)}} for it in items[i : i + BATCH_WRITE]]}
    for attempt in range(RETRIES + 1):
      out = deps.ddb.batch_write_item(RequestItems=pending)
      pending = out.get("UnprocessedItems") or {}
      if not pending:
        break
      if attempt == RETRIES:
        raise RuntimeError("BatchWriteItem left unprocessed items after retries")
      deps.sleep(0.2 * 2**attempt)


def to_dynamo(item):
  """A plain item (str, int, None values) as DynamoDB attribute values."""
  out = {}
  for k, v in item.items():
    if v is None:
      out[k] = {"NULL": True}
    elif isinstance(v, bool):
      out[k] = {"BOOL": v}
    elif isinstance(v, int | float):
      out[k] = {"N": str(v)}
    else:
      out[k] = {"S": str(v)}
  return out


# ---- metrics ----------------------------------------------------------------------------


def _emit(deps, totals, depths, elapsed, out_of_time, poked):
  """One CloudWatch Embedded Metric Format line: the run's metrics, by Environment."""
  mb = deps.mb.summary()
  values = {
    "MbRequests": (mb["requests"], "Count"),
    "MbThrottled": (mb["throttled"], "Count"),
    "MbErrors": (mb["errors"], "Count"),
    "MbRetryAfterSeconds": (mb["retry_after_s"], "Seconds"),
    "MbDeadlineStops": (mb["deadline_stops"], "Count"),
    "AlbumsResolved": (totals["resolved"], "Count"),
    "AlbumsMissed": (totals["missed"], "Count"),
    "AlbumsFailed": (totals["failed"], "Count"),
    "AlbumsAlreadyResolved": (totals["already_resolved"], "Count"),
    "YearWasted": (totals["year_wasted"], "Count"),
    "AlbumsPerSecond": (round(totals["resolved"] / elapsed, 3) if elapsed > 0 else 0, "Count/Second"),
    "HotQueueDepth": (depths.get("hot", 0), "Count"),
    "ColdQueueDepth": (depths.get("cold", 0), "Count"),
    "RunSeconds": (round(elapsed, 1), "Seconds"),
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
    "messages": totals["messages"],
    "invalid": totals["invalid"],
    "batches": totals["batches"],
    "out_of_time": out_of_time,
    "self_invoked": poked,
    "slept_s": round(mb["slept_s"], 1),
    "server_s": round(mb["server_s"], 1),
  }
  print(json.dumps(line, separators=(",", ":")))
  if not totals["messages"]:
    log.info("queues empty: no MusicBrainz requests")
