"""API Lambda (HTTP API, payload v2): health, sync requests and status (AOTY-22).

The function sees the full public path (/toys/aoty/api/... or /toys/aoty-staging/api/...)
because kamp.fm's CloudFront forwards it unchanged; BASE_PATH comes from Terraform. Every
answer, errors included, is Cache-Control: no-store.

  GET  <base>api/health               200 {ok, source, env, sha} (CD's smoke test)
  POST <base>api/users/<name>/sync    202 {queued: true, status} | 200 {queued: false, reason, status}
                                      400 bad name, 404 no such Last.fm user, 415 not JSON,
                                      503 Last.fm or SQS unavailable, or too busy for a new user
  GET  <base>api/users/<name>/status  200 status (api_core.status) | 404 unknown user

POST sync, in order:
  1. A user with no row (or an expired not_found one) is checked with user.getInfo (3 s, one
     attempt). Not found: a not_found row with a one-day TTL (expires_at), then 404.
  2. api_core.decide_sync: a running sync or the per-user cooldown (SYNC_COOLDOWN) answers 200
     without queueing.
  3. A new user is refused (503 busy) while the sync queue is over MAX_QUEUE_FOR_NEW: random
     real names cost ~50 Last.fm calls each and would starve users already here.
  4. A conditional UpdateItem marks the row queued and stamps sync_requested_at. Losing the
     condition means another request just queued it: 200 in_progress.
  5. SendMessage to the sync FIFO (group = username). If it fails, step 4 is undone and the
     answer is 503, so the cooldown never starts for a sync that wasn't sent. A crash between 4
     and 5 leaves the row queued until api_core.STUCK_AFTER, then a POST queues again.
The update comes before the send because the sync worker may start before a late update would
land; its "syncing" would then fail the condition and the cooldown would never be recorded.

POST requires content-type application/json (415 otherwise) and no CORS headers are sent, so a
plain cross-site form can't trigger syncs. Logs name users by a hash, never by username.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import api_core as core

from common import dynamo, lastfm

log = logging.getLogger()
log.setLevel(logging.INFO)

DEFAULT_COOLDOWN_S = 600
MAX_QUEUE_FOR_NEW = 50
NOT_FOUND_TTL_S = 86400
LASTFM_TIMEOUT_S = 3


def _build_sha(path):
  """The commit scripts/package_lambdas.py stamped into the zip's build.json (AOTY-35).

  "unknown" when there's no readable stamp: run from a checkout, or a zip built without one.
  """
  try:
    sha = json.loads(Path(path).read_text())["sha"]
  except (OSError, ValueError, KeyError, TypeError):
    return "unknown"
  return sha if isinstance(sha, str) and sha else "unknown"


BUILD_SHA = _build_sha(Path(__file__).with_name("build.json"))


class Deps:
  """What the user routes touch, so tests can pass fakes. The Last.fm client is made on first
  use (it reads the key from SSM), so health checks never wait for SSM."""

  def __init__(self, *, ddb, sqs, env, lastfm_client=None, lastfm_factory=None, clock=time.time):
    self.ddb, self.sqs, self.env, self.clock = ddb, sqs, env, clock
    self._lastfm, self._lastfm_factory = lastfm_client, lastfm_factory

  @property
  def lastfm(self):
    if self._lastfm is None:
      self._lastfm = self._lastfm_factory()
    return self._lastfm

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ

    def make_lastfm():
      key = lastfm.api_key_from_ssm(boto3.client("ssm"), f"{env['SSM_PREFIX']}lastfm_api_key")
      return lastfm.Client(key, max_attempts=1, timeout=LASTFM_TIMEOUT_S)

    return cls(ddb=boto3.client("dynamodb"), sqs=boto3.client("sqs"), env=env, lastfm_factory=make_lastfm)


_deps = None


def _response(status, body):
  return {
    "statusCode": status,
    # no-store: no cache between here and the browser may keep an API answer, errors included.
    "headers": {"content-type": "application/json", "cache-control": "no-store"},
    "body": json.dumps(body),
  }


def handler(event, context, deps=None):
  global _deps
  event = event or {}
  base = os.environ.get("BASE_PATH", "/")
  method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET").upper()
  action, name = core.route(method, event.get("rawPath", ""), base)
  if action == "health":
    # "source" tells CD's smoke test this is deployed code, not Terraform's placeholder, and
    # "sha" which commit: the smoke test fails unless it's the one just deployed.
    env = os.environ.get("AOTY_ENV", "")
    return _response(200, {"ok": True, "source": "lambdas/api", "env": env, "sha": BUILD_SHA})
  if action == "method":
    return _response(405, {"error": "method not allowed"})
  if action == "not_found":
    return _response(404, {"error": "not found"})
  if not core.USERNAME_RE.match(name):
    return _response(400, {"error": "invalid username"})
  if deps is None:
    _deps = _deps or Deps.from_env()
    deps = _deps
  try:
    if action == "status":
      return get_status(deps, name)
    return post_sync(deps, name, event.get("headers") or {})
  except Exception:
    log.exception("%s failed for %s", action, _tag(name))
    return _response(503, {"error": "unavailable"})


def _cooldown(deps):
  return int(deps.env.get("SYNC_COOLDOWN") or DEFAULT_COOLDOWN_S)


def get_status(deps, name):
  row = _get_user(deps, name)
  if not row or row.get("state") == "not_found":
    return _response(404, {"error": "unknown user"})
  now = int(deps.clock())
  depth = _queue_depth(deps) if row.get("state") == "queued" else None
  return _response(200, core.status(row, now, _cooldown(deps), depth))


def post_sync(deps, name, headers):
  ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
  if not ctype.lower().startswith("application/json"):
    return _response(415, {"error": "send content-type: application/json"})
  now, cooldown = int(deps.clock()), _cooldown(deps)
  row = _get_user(deps, name)
  display = None
  if not row or (row.get("state") == "not_found" and (row.get("expires_at") or 0) <= now):
    try:
      canonical = deps.lastfm.user_info(name)
    except Exception as e:  # LastfmError, a timeout, SSM: say "try later", never "no such user"
      log.warning("user.getInfo failed for %s: %s", _tag(name), getattr(e, "code", type(e).__name__))
      return _response(503, {"error": "lastfm_unavailable"})
    if canonical is None:
      _update(deps, name, {"state": "not_found", "expires_at": now + NOT_FOUND_TTL_S, "not_found_at": now})
      return _response(404, {"error": "unknown user"})
    display = canonical if canonical.lower() == name else None
    row = {"username": name}  # a new user (or one Last.fm knows again)
  elif row.get("state") == "not_found":
    return _response(404, {"error": "unknown user"})

  decision = core.decide_sync(row, now, cooldown)
  if decision != "queue":
    return _response(200, {"queued": False, "reason": decision, "status": core.status(row, now, cooldown)})
  if not row.get("last_synced_at") and _queue_depth(deps) > MAX_QUEUE_FOR_NEW:
    return _response(503, {"error": "busy"})

  previous = {k: row.get(k) for k in ("state", "sync_requested_at")}
  if not _mark_queued(deps, name, now, cooldown, display):
    row = _get_user(deps, name)
    return _response(
      200, {"queued": False, "reason": "in_progress", "status": core.status(row, now, cooldown)}
    )
  try:
    deps.sqs.send_message(
      QueueUrl=deps.env["SYNC_QUEUE_URL"],
      MessageBody=json.dumps({"username": name}),
      MessageGroupId=name,
      MessageDeduplicationId=f"{name}-{now}",
    )
  except Exception:
    log.exception("could not queue a sync for %s", _tag(name))
    _restore(deps, name, previous)
    return _response(503, {"error": "queue_unavailable"})
  log.info("sync queued for %s", _tag(name))
  row = _get_user(deps, name)
  return _response(202, {"queued": True, "status": core.status(row, now, cooldown)})


# ---- users table --------------------------------------------------------------------------


def _get_user(deps, name):
  out = deps.ddb.get_item(
    TableName=deps.env["USERS_TABLE"], Key={"username": {"S": name}}, ConsistentRead=True
  )
  return _from_item(out.get("Item") or {})


def _from_item(item):
  """dynamo.from_item, plus the progress map the sync worker writes."""
  row = dynamo.from_item(item)
  if "M" in (item.get("progress") or {}):
    row["progress"] = dynamo.from_item(item["progress"]["M"])
  return row


def _update(deps, name, fields, remove=()):
  names = {f"#f{i}": k for i, k in enumerate(fields)}
  values = dynamo.to_item({f":f{i}": v for i, v in enumerate(fields.values())})
  expr = "SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(fields)))
  if remove:
    names.update({f"#r{i}": k for i, k in enumerate(remove)})
    expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(remove)))
  deps.ddb.update_item(TableName=deps.env["USERS_TABLE"], Key={"username": {"S": name}},
                       UpdateExpression=expr, ExpressionAttributeNames=names,
                       ExpressionAttributeValues=values)  # fmt: skip


def _mark_queued(deps, name, now, cooldown, display):
  """Set state=queued and sync_requested_at, unless another request beat us to it. True if set.

  The condition repeats decide_sync atomically: no recent request, and no live sync (one idle
  past STUCK_AFTER counts as dead).
  """
  fields = {"state": "queued", "sync_requested_at": now}
  if display:
    fields["display_name"] = display
  names = {"#s": "state", "#rq": "sync_requested_at", "#st": "sync_started_at",
           **{f"#f{i}": k for i, k in enumerate(fields)}}  # fmt: skip
  values = dynamo.to_item({
    ":cool": now - cooldown, ":stuck": now - core.STUCK_AFTER,
    ":q": "queued", ":sy": "syncing", ":rt": "retrying",
    **{f":f{i}": v for i, v in enumerate(fields.values())},
  })  # fmt: skip
  condition = (
    "(attribute_not_exists(#rq) OR #rq < :cool)"
    " AND (attribute_not_exists(#s) OR NOT (#s IN (:q, :sy, :rt))"
    " OR (#rq < :stuck AND (attribute_not_exists(#st) OR #st < :stuck)))"
  )
  try:
    deps.ddb.update_item(
      TableName=deps.env["USERS_TABLE"],
      Key={"username": {"S": name}},
      UpdateExpression="SET "
      + ", ".join(f"#f{i} = :f{i}" for i in range(len(fields)))
      + " REMOVE expires_at",
      ConditionExpression=condition,
      ExpressionAttributeNames=names,
      ExpressionAttributeValues=values,
    )
  except Exception as e:
    if _error_code(e) == "ConditionalCheckFailedException":
      return False
    raise
  return True


def _restore(deps, name, previous):
  """Undo _mark_queued after a failed send, so the cooldown doesn't start for nothing."""
  try:
    keep = {k: v for k, v in previous.items() if v is not None}
    gone = [k for k, v in previous.items() if v is None]
    if keep:
      _update(deps, name, keep, remove=gone)
    else:
      deps.ddb.update_item(TableName=deps.env["USERS_TABLE"], Key={"username": {"S": name}},
                           UpdateExpression="REMOVE #a, #b",
                           ExpressionAttributeNames={"#a": "state", "#b": "sync_requested_at"})  # fmt: skip
  except Exception:
    log.exception("could not undo the queued mark for %s", _tag(name))


def _queue_depth(deps):
  try:
    out = deps.sqs.get_queue_attributes(QueueUrl=deps.env["SYNC_QUEUE_URL"],
                                        AttributeNames=["ApproximateNumberOfMessages"])  # fmt: skip
    return int(out["Attributes"]["ApproximateNumberOfMessages"])
  except Exception:
    log.exception("could not read the sync queue depth")
    return 0


def _error_code(e):
  return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", ""))


def _tag(username):
  """A short stable hash, so logs can follow one user without naming them (as sync's)."""
  if not username:
    return "-"
  return hashlib.sha256(str(username).lower().encode()).hexdigest()[:10]
