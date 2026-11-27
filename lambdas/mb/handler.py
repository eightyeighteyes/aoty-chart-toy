"""MusicBrainz lane Lambda (AOTY-19): the one MusicBrainz client, draining mb-hot then mb-cold.

Started by the sync worker (async invoke, payload {}) whenever it queues albums, and by a
schedule every 5 minutes (infra/modules/stack/schedule.tf), which is also how a run that ran
out of time with work left gets its successor. Reserved concurrency 1
(infra/modules/stack/functions.tf) makes it the only MusicBrainz client in its environment: a
second start is throttled and Lambda retries it after this one exits.

It never invokes itself (AOTY-58). Each self-invoke was one more hop in the same request chain,
and Lambda's recursive loop detection stops a chain after about 16 hops, which a large backlog
reached. A Scheduler start is outside any chain.

A message is one album, as the sync worker sends it (AOTY-18):

  {"artist_key", "title_key", "artist", "album", "mbid"}

plus "recheck": true when the index build (jobs/index/build.py, AOTY-20) asks for an album
to be looked up again. Messages can repeat (several users, or a crash in sync), so a run is
idempotent: an album with an albums-table item is resolved and its messages are just deleted,
unless any of its messages is a re-check, which overwrites the item.

Years come from the release index first (common.releaseindex, in the store bucket): the
manifest is re-read at most hourly, shards are kept in an LRU while the container is warm. An
unusable manifest (missing, other format, other norm) turns the index off for the run and
counts as an index error, so every album goes to the live search as before AOTY-20.

One run, until both queues are empty or time runs short:
  1. Receive up to BATCH messages, from mb-hot if it has any, else mb-cold.
  2. Delete messages whose album already has an item (re-checks excepted).
  3. Resolve the rest (mb_core.resolve: index, year, then track count for target-year albums).
  4. Write an item per resolved album (a confirmed miss included, with year null), then
     delete all of that album's messages, from both queues.
  5. Failed albums are neither written nor deleted. They come back after the queue's
     visibility timeout (sooner when the run stopped for time), and after 5 receives go to the
     dead-letter queue.
  6. Once items are written, poke the chart builder (AOTY-21) with {"pending": true}: after the
     first such batch, then at most once a minute, and at the end of the run (ChartPoke).
With both queues empty, a run makes no MusicBrainz request. Each run logs one EMF line; a run
that received no messages leaves out RunSeconds and AlbumsPerSecond, so the empty scheduled
runs don't dilute throughput (the mb-slow alarm divides by RunSeconds).
"""

import gzip
import json
import logging
import os
import time
from collections import OrderedDict

import mb_core as core
from common import dynamo, lastfm, musicbrainz
from common import releaseindex as ri
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
MANIFEST_TTL_S = 3600
CHART_POKE_S = 60  # at most one chart-builder poke a minute (ChartPoke)
SHARD_CACHE = 128  # a batch of 20 albums reads at most 40 shards (~250 KB parsed each)


class Index:
  """The release index in the store bucket, cached across warm invocations."""

  def __init__(self, s3, bucket, clock=time.time):
    self.s3, self.bucket, self.clock = s3, bucket, clock
    self.manifest, self.fetched_at, self.state = None, None, "missing"
    self.shards = OrderedDict()  # (dump, shard) -> {norm(title): [records]}

  def current(self):
    """The usable manifest, or None. Raises if it can't be read."""
    now = self.clock()
    if self.fetched_at is None or now - self.fetched_at > MANIFEST_TTL_S:
      try:
        body = self.s3.get_object(Bucket=self.bucket, Key=ri.MANIFEST_KEY)["Body"].read()
        m = json.loads(body)
      except Exception as e:
        if _error_code(e) not in ("NoSuchKey", "404"):
          raise
        m = None
      self.manifest = m if ri.usable(m) else None
      self.fetched_at = now
      self.state = "missing" if m is None else "ok" if self.manifest else "unusable"
      if self.state == "unusable":
        log.error("release index manifest is unusable (format or norm changed); index off")
    return self.manifest

  def shard(self, dump, shard):
    key = (dump, shard)
    if key in self.shards:
      self.shards.move_to_end(key)
      return self.shards[key]
    body = self.s3.get_object(Bucket=self.bucket, Key=ri.shard_key(dump, shard))["Body"].read()
    data = json.loads(gzip.decompress(body))
    self.shards[key] = data
    while len(self.shards) > SHARD_CACHE:
      self.shards.popitem(last=False)
    return data

  def lookup_fn(self, manifest):
    dump = manifest["dump"]

    def year_from_index(album):
      return ri.lookup(album["artist"], album["album"], lambda shard: self.shard(dump, shard))

    return year_from_index


def _error_code(e):
  return getattr(e, "response", {}).get("Error", {}).get("Code")


class Deps:
  """Everything a run touches, so tests can pass fakes. from_env() builds the real ones."""

  def __init__(self, *, sqs, ddb, lambda_, mb, lastfm_client, env, index=None, clock=time.time,
               mono=time.monotonic, sleep=time.sleep):  # fmt: skip
    self.sqs, self.ddb, self.lambda_, self.mb, self.lastfm = sqs, ddb, lambda_, mb, lastfm_client
    self.env, self.index, self.clock, self.mono, self.sleep = env, index, clock, mono, sleep

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ
    ssm = boto3.client("ssm")
    key = lastfm.api_key_from_ssm(ssm, f"{env['SSM_PREFIX']}lastfm_api_key")
    contact = _ssm_value(ssm, f"{env['SSM_PREFIX']}mb_contact")
    global _index
    if _index is None:
      _index = Index(boto3.client("s3"), env["STORE_BUCKET"])
    return cls(
      sqs=boto3.client("sqs"),
      ddb=boto3.client("dynamodb"),
      lambda_=boto3.client("lambda"),
      mb=musicbrainz.Client(musicbrainz.user_agent(UA_VERSION, contact)),
      lastfm_client=lastfm.Client(key),
      env=env,
      index=_index,
    )


_contact = {}
_index = None  # one Index per container, so its caches outlive an invocation


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
  calls_before = getattr(deps.lastfm, "calls", 0)  # track-count fallback; a difference, as sync's
  remaining = context.get_remaining_time_in_millis() / 1000 if context else 900
  deadline = started + remaining - DEADLINE_MARGIN_S
  totals = {"messages": 0, "already_resolved": 0, "resolved": 0, "missed": 0, "failed": 0,
            "year_wasted": 0, "invalid": 0, "batches": 0, "rechecks": 0, "index_hits": 0,
            "index_untrusted": 0, "index_misses": 0, "index_errors": 0, "tracks_reused": 0,
            "written": 0, "chart_pokes": 0}  # fmt: skip
  queues = [("hot", deps.env["MB_HOT_URL"]), ("cold", deps.env["MB_COLD_URL"])]
  out_of_time = False
  year_from_index, index_age = _open_index(deps, totals)
  charts = ChartPoke(deps, totals)

  while True:
    if deps.mono() + MIN_BATCH_S > deadline:
      out_of_time = True
      break
    msgs = _receive(deps, queues)
    if not msgs:
      break
    totals["batches"] += 1
    _process(deps, msgs, deadline, totals, year_from_index)
    charts.after_batch()
    if deps.mono() + MIN_BATCH_S > deadline:
      out_of_time = True
      break

  charts.at_end()
  depths = _depths(deps, queues)
  # No self-invoke (AOTY-58): the schedule, or a start throttled behind this run, picks it up.
  work_left = out_of_time and sum(depths.values()) > 0
  elapsed = deps.mono() - started
  totals["lastfm_calls"] = getattr(deps.lastfm, "calls", 0) - calls_before
  _emit(deps, totals, depths, elapsed, out_of_time, work_left, index_age)
  return {**totals, "out_of_time": out_of_time, "work_left": work_left}


class ChartPoke:
  """Ask the chart builder (AOTY-21) to rebuild charts with pending albums, as items land.

  Right after the first batch that writes items (so a new user's chart fills in quickly), then
  at most every CHART_POKE_S, and once at the end for anything written since. The builder only
  rebuilds users whose pending albums now have items. Best effort: a failed invoke is logged
  and never fails the batch; the next poke covers it.
  """

  def __init__(self, deps, totals):
    self.deps, self.totals = deps, totals
    self.fn = deps.env.get("CHART_FUNCTION")
    self.last, self.seen = None, 0

  def _due(self):
    return self.fn and self.totals["written"] > self.seen

  def _poke(self):
    self.seen = self.totals["written"]
    self.last = self.deps.mono()
    try:
      self.deps.lambda_.invoke(FunctionName=self.fn, InvocationType="Event", Payload=b'{"pending":true}')
      self.totals["chart_pokes"] += 1
    except Exception:
      log.exception("could not start the chart builder")

  def after_batch(self):
    if self._due() and (self.last is None or self.deps.mono() - self.last >= CHART_POKE_S):
      self._poke()

  def at_end(self):
    if self._due():
      self._poke()


def _open_index(deps, totals):
  """(year_from_index or None, the dump's age in hours or None) for this run."""
  if deps.index is None:
    return None, None
  try:
    m = deps.index.current()
  except Exception:
    log.exception("could not read the release index manifest; index off for this run")
    totals["index_errors"] += 1
    return None, None
  if m is None:
    if deps.index.state == "unusable":
      totals["index_errors"] += 1
    return None, None
  return deps.index.lookup_fn(m), ri.dump_age_hours(m["dump"], deps.clock())


# ---- queues -----------------------------------------------------------------------------


def _receive(deps, queues):
  """Up to BATCH messages from the first queue that has any: [(album, queue_url, receipt)].

  SQS often answers with fewer than asked even when more are waiting (sandbox, 2026-11-22: 51
  messages came in 12 batches), so keep receiving until BATCH or an empty answer. Small batches
  waste MusicBrainz requests: one search covers 20 albums as cheaply as 4.
  """
  for _, url in queues:
    got = []
    while len(got) < BATCH:
      out = deps.sqs.receive_message(
        QueueUrl=url,
        MaxNumberOfMessages=min(RECEIVE_MAX, BATCH - len(got)),
        WaitTimeSeconds=WAIT_S,
      )
      page = out.get("Messages", [])
      if not page:
        break
      got += [(m.get("Body", ""), url, m["ReceiptHandle"]) for m in page]
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
  msg["recheck"] = msg.get("recheck") is True
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


def _process(deps, msgs, deadline, totals, year_from_index=None):
  handles, albums, recheck = {}, [], set()
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
    if album["recheck"]:
      recheck.add(key)  # any message for the key decides, not just the first
  totals["messages"] += len(msgs)
  totals["rechecks"] += len(recheck)

  done = set(_get_items(deps, [k for k in handles if k not in recheck], keys_only=True))
  totals["already_resolved"] += len(done)
  _delete(deps, [h for k in done for h in handles[k]])
  albums = [a for a in albums if core.key_of(a) not in done]
  if not albums:
    return
  previous = _get_items(deps, sorted(recheck)) if recheck else {}

  result = core.resolve(
    albums,
    search=lambda entity, query, limit: deps.mb.search(entity, query, limit, deadline=deadline),
    lastfm_tracks=deps.lastfm.album_track_count,
    target_year=TARGET_YEAR,
    now=deps.clock(),
    year_from_index=year_from_index,
    previous=previous,
  )
  if result.stats.get("index_errors"):
    log.warning("%d release index lookups failed; those albums went to the live search",
                result.stats["index_errors"])  # fmt: skip
  _write(deps, list(result.items.values()))
  totals["written"] += len(result.items)
  _delete(deps, [h for k in result.items for h in handles[k]])
  if deps.mono() + MIN_BATCH_S > deadline:
    _release(deps, [h for k in result.failed for h in handles[k]])
  for name in ("resolved", "missed", "failed", "year_wasted", "index_hits", "index_untrusted", "index_misses",
               "index_errors", "tracks_reused"):  # fmt: skip
    totals[name] += result.stats.get(name, 0)


# ---- albums table -----------------------------------------------------------------------


def _get_items(deps, keys, keys_only=False):
  """key -> the albums-table item as plain values, for the keys that have one."""
  table, found = deps.env["ALBUMS_TABLE"], {}
  for i in range(0, len(keys), BATCH_GET):
    request = {"Keys": [{"artist_key": {"S": a}, "title_key": {"S": t}} for a, t in keys[i : i + BATCH_GET]]}
    if keys_only:
      request["ProjectionExpression"] = "artist_key, title_key"
    pending = {table: request}
    for attempt in range(RETRIES + 1):
      out = deps.ddb.batch_get_item(RequestItems=pending)
      for item in out.get("Responses", {}).get(table, []):
        found[(item["artist_key"]["S"], item["title_key"]["S"])] = dynamo.from_item(item)
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


to_dynamo = dynamo.to_item


# ---- metrics ----------------------------------------------------------------------------


def _emit(deps, totals, depths, elapsed, out_of_time, work_left, index_age=None):
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
    "IndexHits": (totals["index_hits"], "Count"),
    "IndexUntrusted": (totals["index_untrusted"], "Count"),
    "IndexMisses": (totals["index_misses"], "Count"),
    "IndexErrors": (totals["index_errors"], "Count"),
    "Rechecks": (totals["rechecks"], "Count"),
    "TracksReused": (totals["tracks_reused"], "Count"),
    "HotQueueDepth": (depths.get("hot", 0), "Count"),
    "ColdQueueDepth": (depths.get("cold", 0), "Count"),
    "RunSeconds": (round(elapsed, 1), "Seconds"),
    "LastfmCalls": (totals.get("lastfm_calls", 0), "Count"),  # one metric shared by every function
  }
  if index_age is not None:  # absent rather than 0 when there's no usable index
    values["IndexAgeHours"] = (index_age, "None")
  if not totals["messages"]:
    # An empty run (most scheduled starts) did no lane work: absent rather than its few seconds
    # of polling, which would drag down albums per second and trip mb-slow in a light hour.
    del values["RunSeconds"], values["AlbumsPerSecond"]
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
    "work_left": work_left,
    "chart_pokes": totals["chart_pokes"],
    "slept_s": round(mb["slept_s"], 1),
    "server_s": round(mb["server_s"], 1),
  }
  print(json.dumps(line, separators=(",", ":")))
  if not totals["messages"]:
    log.info("queues empty: no MusicBrainz requests")
