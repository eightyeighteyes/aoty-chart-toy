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
  {"group": "<slug>", "requested_at": uts?}
      From the api after a group change (AOTY-48). Skipped when the group's chart_built_at is at
      or after requested_at. A group is also rebuilt after each member's own build, unless a group
      build since that member's sync finished already read them (chart_read_at, AOTY-55).
  {"group": "<slug>", "deleted": true}
      From the api after a group was deleted or moved to a new slug: removes its chart, unless a
      group with that slug exists again by now (then it's rebuilt). Coming through here orders the
      delete after any build that was in flight.

A build:
  1. Loads s3://$STORE_BUCKET/users/<u>/<year>/weeks.json.gz (no store: nothing to build).
  2. BatchGets the albums items for every key the user played this year.
  3. Looks up album.getInfo covers for shown albums whose scrobbles carried none (cached in
     users/<u>/<year>/chart_covers.json; at most COVER_LOOKUPS per build).
  4. Writes s3://$SITE_BUCKET/$SITE_PREFIX/data/users/<u>/chart.json with Cache-Control no-cache,
     and users/<u>/<year>/chart_pending.json (the keys still pending) in the store bucket.
  5. Sets chart_built_at and chart_pending on the users row.

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
import group_core

from common import dynamo, lastfm
from common import groups as grp
from common.albumkey import join_key, split_key
from common.config import TARGET_YEAR

log = logging.getLogger()
log.setLevel(logging.INFO)

NAMESPACE = "kamp-aoty"
BATCH_GET = 100  # DynamoDB BatchGetItem limit
RETRIES = 4
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")  # as the sync worker's
# album.getInfo cover lookups per build: at Last.fm's 0.5 s pacing, ~60 s at most. A heavy
# user's first chart needed ~50; the next build continues where this one stopped.
COVER_LOOKUPS = 120
COVER_BUDGET_S = 90


class Deps:
  """Everything a build touches, so tests can pass fakes. from_env() builds the real ones.

  lastfm may be None: covers then come only from the scrobbles and the Cover Art Archive.
  """

  def __init__(self, *, s3, ddb, env, lastfm_client=None, clock=time.time, mono=time.monotonic,
               sleep=time.sleep):  # fmt: skip
    self.s3, self.ddb, self.env, self.lastfm = s3, ddb, env, lastfm_client
    self.clock, self.mono, self.sleep = clock, mono, sleep

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ
    key = lastfm.api_key_from_ssm(boto3.client("ssm"), f"{env['SSM_PREFIX']}lastfm_api_key")
    return cls(s3=boto3.client("s3"), ddb=boto3.client("dynamodb"), env=env, lastfm_client=lastfm.Client(key))


_deps = None


def handler(event, context, deps=None):
  global _deps
  if deps is None:
    _deps = _deps or Deps.from_env()
    deps = _deps
  event = event or {}
  started = deps.mono()
  # The client lives as long as the warm container: count this run's calls as a difference.
  calls_before = _lastfm_calls(deps)
  totals = {"built": 0, "skipped": 0, "failed": 0, "checked": 0}
  if event.get("group"):
    group_id = str(event["group"])
    if event.get("deleted"):
      _guarded(deps, group_id, totals, lambda: _count(totals, delete_group_chart(deps, group_id)))
    else:
      requested_at = int(event.get("requested_at") or 0)
      _guarded(
        deps, group_id, totals, lambda: _count(totals, build_group(deps, group_id, requested_at=requested_at))
      )
  elif event.get("pending"):
    rebuilt = []
    for row in _pending_users(deps):
      totals["checked"] += 1
      _guarded(deps, row["username"], totals, lambda r=row: _rebuild_if_resolved(deps, r, totals, rebuilt))
    # Albums the mb lane just dated reach the groups too, each group once per run (AOTY-50).
    for group_id in _groups_with(deps, *rebuilt):
      _guarded(deps, group_id, totals, lambda g=group_id: _count(totals, build_group(deps, g)))
  else:
    username = str(event.get("username") or "").strip().lower()
    if not USERNAME_RE.match(username):
      log.warning("invalid username in event; dropped")
      totals["skipped"] += 1
    else:
      requested_at = int(event.get("requested_at") or 0)
      year = int(event.get("year") or TARGET_YEAR)
      _guarded(deps, username, totals, lambda: _build_requested(deps, username, year, requested_at, totals))
  _emit(deps, totals, deps.mono() - started, _lastfm_calls(deps) - calls_before)
  return totals


def _lastfm_calls(deps):
  """lastfm.Client's running count of HTTP attempts (0 without a client)."""
  n = getattr(deps.lastfm, "calls", 0)
  return n if isinstance(n, int) else 0


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
  _count(totals, build(deps, username, row, year, totals))
  # A sync's build: the groups this user is in get their new plays too (AOTY-50), unless a group
  # build since their sync finished already read them (AOTY-55).
  finished = int(row.get("sync_finished_at") or 0)
  for group_id in _groups_with(deps, username):
    _guarded(deps, group_id, totals,
             lambda g=group_id: _count(totals, build_group(deps, g, member_finished_at=finished)))  # fmt: skip


def _rebuild_if_resolved(deps, row, totals, rebuilt=None):
  username, year = row["username"], TARGET_YEAR
  keys = _load_pending(deps, username, year)
  if keys and not _get_items(deps, keys, keys_only=True):
    totals["skipped"] += 1  # nothing new since the last build
    return
  built = build(deps, username, _get_user(deps, username), year, totals)
  _count(totals, built)
  if built and rebuilt is not None:
    rebuilt.append(username)


def _count(totals, built):
  totals["built" if built else "skipped"] += 1


def _cover_images(deps, username, year, store, items, now, totals=None):
  """{key: album.getInfo cover} for shown albums whose scrobbles carried none.

  Looks up at most COVER_LOOKUPS per build and for at most COVER_BUDGET_S, in chart order, so
  the top of the chart is covered first; the next build continues. Answers ("none" included)
  are cached in the store's chart_covers.json; a failed request isn't, so it's retried.
  """
  cache = _load_covers(deps, username, year)
  todo = core.cover_lookups(core.shown_keys(store, items), store, cache, now)[:COVER_LOOKUPS]
  if todo and deps.lastfm is not None:
    meta, stop_at, changed = store.get("albums", {}), deps.mono() + COVER_BUDGET_S, False
    for k in todo:
      if deps.mono() >= stop_at:
        break
      m = meta.get(k, {})
      try:
        url = deps.lastfm.album_image(m.get("artist", ""), m.get("album", ""))
      except Exception as e:  # LastfmError or the network: not cached, so the next build retries
        log.warning("cover lookup failed: %s", getattr(e, "code", type(e).__name__))
        continue
      cache[k] = {"url": url, "at": now}
      changed = True
      if totals is not None:
        totals["covers"] = totals.get("covers", 0) + 1
    if changed:
      _put_json(deps, deps.env["STORE_BUCKET"], covers_key(username, year), {"version": 1, "covers": cache})
  return {k: v["url"] for k, v in cache.items() if v.get("url")}


def build(deps, username, row, year=TARGET_YEAR, totals=None):
  """One user's chart. False when they have no store yet (never synced)."""
  store = _load_store(deps, username, year)
  if store is None:
    return False
  now = int(deps.clock())
  plays = core.year_plays(store)
  items = _get_items(deps, sorted(plays))
  images = _cover_images(deps, username, year, store, items, now, totals)
  chart, pending = core.build_chart(
    store, items, username=username, display_name=row.get("display_name"), now=now, images=images
  )
  _put_json(
    deps, deps.env["SITE_BUCKET"], chart_key(deps.env.get("SITE_PREFIX", ""), username), chart,
    CacheControl="no-cache",
  )  # fmt: skip
  _put_json(deps, deps.env["STORE_BUCKET"], pending_key(username, year), {"keys": pending})
  # The status API (AOTY-22) reads these: new plays = sync's total - chart_plays (the same sum),
  # albums checked = chart_albums - chart_pending, hits = this year's albums found so far.
  # Once the chart ranks anything, the first-visit cover wall (sync's `preview`, AOTY-24) is
  # done with, so status polls stop carrying it.
  _update_user(deps, username, {
    "chart_built_at": now, "chart_pending": len(pending), "chart_plays": sum(plays.values()),
    "chart_albums": len(plays), "chart_hits": core.year_hits(plays, items, year),
  }, remove=("preview",) if chart["rankings"]["track"] else ())  # fmt: skip
  log.info("chart %s: %d albums shown, %d pending", _tag(username), len(chart["albums"]), len(pending))
  return True


def build_group(deps, group_id, year=TARGET_YEAR, requested_at=0, member_finished_at=0):
  """One group's chart from its ready members' stores (group_core). False for no such group, or
  when its chart already covers requested_at, or (for a member's build) member_finished_at.

  member_finished_at (AOTY-55): the sync_finished_at of the member whose build asked for this one.
  The meta's chart_read_at is when the last build started reading stores, in ms; the sync worker
  saves the store before it writes sync_finished_at, so a build that started reading in a later
  second already has the member's plays. A visit that syncs 25 members then costs a few group
  builds, not 25. A build that started in the same second may have read the old store: it rebuilds.

  The group is the groups table's partition (common/groups.py, AOTY-48). Members whose sync
  hasn't finished are counted as waiting; not_found or private ones as unavailable
  (groups.member_state, which the api's ready count shares).

  chart_built_at is stamped with the chart_requested_at read before the build, not the time it
  ends: a member change made while a 25-member build runs must not count as built.
  """
  if not grp.SLUG_RE.match(group_id):
    log.warning("invalid group id in event; dropped")
    return False
  meta, members = _load_group(deps, group_id)
  if meta is None:
    return False
  if requested_at and (meta.get("chart_built_at") or 0) >= requested_at:
    return False
  # sync_finished_at is whole seconds: the finish was somewhere in that second, so only a read from
  # the next second on is sure to be after it.
  if member_finished_at and (meta.get("chart_read_at") or 0) >= (member_finished_at + 1) * 1000:
    log.info("group %s: already has this member's sync", group_id)
    return False
  read_at = int(deps.clock() * 1000)
  stores, waiting, unavailable, images = {}, 0, 0, {}
  for member in members:
    if not USERNAME_RE.match(member):
      continue
    row = _get_user(deps, member)
    state = grp.member_state(row)
    store = _load_store(deps, member, year) if state == "ready" else None
    if state == "unavailable":
      unavailable += 1
    elif store is None:
      waiting += 1  # a first sync still running; a re-sync of a ready member keeps them ranked
    else:
      stores[member] = store
      images.update({k: v["url"] for k, v in _load_covers(deps, member, year).items() if v.get("url")})
  keys = sorted({k for s in stores.values() for k in core.year_plays(s)})
  items = _get_items(deps, keys)
  chart, _ = group_core.build_group_chart(
    stores, items, group_id=group_id, name=str(meta.get("name") or ""), year=year,
    waiting=waiting, unavailable=unavailable, now=int(deps.clock()), images=images,
  )  # fmt: skip
  _put_json(
    deps, deps.env["SITE_BUCKET"], group_chart_key(deps.env.get("SITE_PREFIX", ""), group_id), chart,
    CacheControl="no-cache",
  )  # fmt: skip
  _stamp_group(deps, group_id, meta.get("chart_requested_at"), read_at)
  log.info(
    "group %s: %d ready, %d waiting, %d albums shown", group_id, len(stores), waiting, len(chart["albums"])
  )
  return True


def delete_group_chart(deps, group_id):
  """A deleted or moved group's chart goes. If the slug is a group again by now, it's rebuilt
  instead, so a quick delete-and-recreate doesn't lose the new group's chart."""
  if not grp.SLUG_RE.match(group_id):
    log.warning("invalid group id in event; dropped")
    return False
  meta, _ = _load_group(deps, group_id)
  if meta is not None:
    return build_group(deps, group_id)
  deps.s3.delete_object(
    Bucket=deps.env["SITE_BUCKET"], Key=group_chart_key(deps.env.get("SITE_PREFIX", ""), group_id)
  )
  log.info("group %s: chart deleted", group_id)
  return True


def _groups_with(deps, *usernames):
  """Slugs of the groups any of these users is in (the by_member index), in a stable order. A
  lookup that fails is logged and skipped: the user's own chart is already built."""
  out = set()
  for username in dict.fromkeys(usernames):
    try:
      out.update(_member_groups(deps, username))
    except Exception:
      log.exception("could not look up the groups of %s", _tag(username))
  return sorted(out)


# ---- storage --------------------------------------------------------------------------


def chart_key(site_prefix, username):
  return f"{site_prefix}data/users/{username}/chart.json"


def group_chart_key(site_prefix, group_id):
  return f"{site_prefix}data/groups/{group_id}/chart.json"


def store_key(username, year):
  return f"users/{username}/{year}/weeks.json.gz"  # the sync worker's (lambdas/sync/handler.py)


def pending_key(username, year):
  return f"users/{username}/{year}/chart_pending.json"


def covers_key(username, year):
  return f"users/{username}/{year}/chart_covers.json"


def _load_covers(deps, username, year):
  body = _get_object(deps, deps.env["STORE_BUCKET"], covers_key(username, year))
  return json.loads(body).get("covers", {}) if body else {}


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
      "Keys": [
        {"artist_key": {"S": a}, "title_key": {"S": t}} for a, t in map(split_key, keys[i : i + BATCH_GET])
      ]
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


# ---- groups table (AOTY-48) -------------------------------------------------------------


def _query_all(deps, **kwargs):
  items = []
  while True:
    out = deps.ddb.query(TableName=deps.env["GROUPS_TABLE"], **kwargs)
    items += [dynamo.from_item(it) for it in out.get("Items", [])]
    if not out.get("LastEvaluatedKey"):
      return items
    kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]


def _load_group(deps, group_id):
  """(meta, [members]) from the group's partition, consistent; (None, []) for no such group."""
  items = _query_all(deps, KeyConditionExpression="pk = :pk", ConsistentRead=True,
                     ExpressionAttributeValues={":pk": {"S": grp.pk(group_id)}})  # fmt: skip
  meta, members = grp.split_items(items)
  return (meta, members) if meta else (None, [])


def _member_groups(deps, username):
  items = _query_all(deps, IndexName=grp.BY_MEMBER, KeyConditionExpression="username = :u",
                     ExpressionAttributeValues={":u": {"S": username}})  # fmt: skip
  return [s for s in (grp.slug_of(it.get("pk")) for it in items) if s]


def _stamp_group(deps, group_id, built, read_at):
  """chart_built_at (when the meta had a chart_requested_at) and chart_read_at on the meta, only
  while the group still exists: an update would otherwise create a bare meta item for a group
  deleted mid-build."""
  sets, values = ["chart_read_at = :r"], {":r": {"N": str(read_at)}}
  if built:
    sets.append("chart_built_at = :t")
    values[":t"] = {"N": str(built)}
  try:
    deps.ddb.update_item(
      TableName=deps.env["GROUPS_TABLE"], Key={"pk": {"S": grp.pk(group_id)}, "sk": {"S": grp.META}},
      UpdateExpression="SET " + ", ".join(sets), ConditionExpression="attribute_exists(pk)",
      ExpressionAttributeValues=values,
    )  # fmt: skip
  except Exception as e:
    if _error_code(e) != "ConditionalCheckFailedException":
      raise


# ---- users table ------------------------------------------------------------------------


def _get_user(deps, username):
  out = deps.ddb.get_item(TableName=deps.env["USERS_TABLE"], Key={"username": {"S": username}})
  return dynamo.from_item(out.get("Item") or {})


def _update_user(deps, username, fields, remove=()):
  names = {f"#f{i}": k for i, k in enumerate(fields)}
  values = {f":f{i}": v for i, v in enumerate(fields.values())}
  expr = "SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(fields)))
  if remove:
    names.update({f"#r{i}": k for i, k in enumerate(remove)})
    expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(remove)))
  deps.ddb.update_item(
    TableName=deps.env["USERS_TABLE"],
    Key={"username": {"S": username}},
    UpdateExpression=expr,
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


def _emit(deps, totals, elapsed, lastfm_calls=0):
  values = {
    "ChartsBuilt": (totals["built"], "Count"),
    "ChartsSkipped": (totals["skipped"], "Count"),
    "ChartFailures": (totals["failed"], "Count"),
    "ChartSeconds": (round(elapsed, 2), "Seconds"),
    "CoverLookups": (totals.get("covers", 0), "Count"),
    "LastfmCalls": (lastfm_calls, "Count"),  # one metric shared by every function
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
