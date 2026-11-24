"""API Lambda (HTTP API, payload v2): health, sync requests and status (AOTY-22).

The function sees the full public path (/toys/aoty/api/... or /toys/aoty-staging/api/...)
because kamp.fm's CloudFront forwards it unchanged; BASE_PATH comes from Terraform. Every
answer, errors included, is Cache-Control: no-store.

  GET  <base>api/health               200 {ok, source, env, sha} (CD's smoke test)
  POST <base>api/users/<name>/sync    202 {queued: true, status} | 200 {queued: false, reason, status}
                                      (reason: in_progress, cooldown or paused)
                                      400 bad name, 404 no such Last.fm user, 415 not JSON,
                                      503 Last.fm or SQS unavailable, too busy for a new user
                                      (busy), or new users paused (paused)
  GET  <base>api/users/<name>/status  200 status (api_core.status) | 404 unknown user
  GET  <base>api/auth/start, GET <base>api/auth/callback/<nonce>, GET <base>api/me,
  POST <base>api/auth/signout         sign in with Last.fm (AOTY-47; see "sign-in" below)
  <base>api/groups..., GET <base>api/me/groups
                                      owned groups (AOTY-48; see "groups" below)

POST sync, in order:
  1. A user with no row (or an expired not_found one) is new. Unless new users are paused, the
     name counts against the caller's IP (new_users_per_ip_hour distinct names, 429 over it),
     then is checked with user.getInfo (3 s, one attempt). Not found: a not_found row with a
     one-day TTL (expires_at), then 404.
  2. api_core.decide_sync: a running sync or the per-user cooldown (sync_cooldown_seconds) answers
     200 without queueing.
  3. A new user is refused (503 busy) while the sync queue is over max_queue_for_new: random
     real names cost ~50 Last.fm calls each and would starve users already here.
  4. A conditional UpdateItem marks the row queued and stamps sync_requested_at. Losing the
     condition means another request just queued it: 200 in_progress.
  5. SendMessage to the sync FIFO (group = username). If it fails, step 4 is undone and the
     answer is 503, so the cooldown never starts for a sync that wasn't sent. A crash between 4
     and 5 leaves the row queued until api_core.STUCK_AFTER, then a POST queues again.
The update comes before the send because the sync worker may start before a late update would
land; its "syncing" would then fail the condition and the cooldown would never be recorded.

The execute-api URL bypasses CloudFront. kamp.fm's CloudFront adds x-kamp-origin with a secret
that kamp-site keeps in SSM (origin_secret, plus origin_secret_previous during a rotation). With
ENFORCE_ORIGIN=true (Terraform, per env), user routes without it are 403; health stays open.
Only a request with the secret has its X-Forwarded-For / CloudFront-Viewer-Address believed.

POST requires content-type application/json (415 otherwise) and no CORS headers are sent, so a
plain cross-site form can't trigger syncs. Logs name users by a hash, never by username.

The cooldown, the new-user queue limit and the other knobs are runtime settings in SSM
(common/guardrails.py, AOTY-26), so they change without a deploy. The kill switch:
  pause_new_users  a user with no finished sync gets 503 paused, before any Last.fm call
  pause_syncs      nothing is queued: existing users get 200 paused (their chart still serves),
                   new users 503 paused. Messages already queued still drain; the hard stop is
                   disabling the sync event source mapping (infra/README.md).
"""

import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path

import api_core as core
import groups_store

from common import dynamo, emf, guardrails, lastfm, session
from common import groups as grp

log = logging.getLogger()
log.setLevel(logging.INFO)

ORIGIN_HEADER = "x-kamp-origin"  # added by kamp.fm's CloudFront on the api origins (kamp-site)
NOT_FOUND_TTL_S = 86400
LASTFM_TIMEOUT_S = 3
# SecureStrings set by hand (infra/README.md, "Sign-in"); readable by the api alone.
AUTH_SECRETS = (*session.KEY_NAMES, "lastfm_api_secret")
AUTH_ACTIONS = frozenset({"auth_start", "auth_callback", "me", "auth_signout"})


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
  use (it reads the key from SSM), so health checks never wait for SSM. settings() returns the
  current guardrails.Settings (cached by guardrails.Reader in the real thing)."""

  def __init__(self, *, ddb, sqs, env, lastfm_client=None, lastfm_factory=None, clock=time.time,
               settings=None, origin_secrets=None, auth_secrets=None, lam=None,
               sleep=time.sleep):  # fmt: skip
    self.ddb, self.sqs, self.env, self.clock, self.sleep = ddb, sqs, env, clock, sleep
    self.lam = lam  # the Lambda client: group changes poke the chart builder (AOTY-48)
    self._lastfm, self._lastfm_factory = lastfm_client, lastfm_factory
    self.settings = settings or (lambda: guardrails.DEFAULTS)
    self.origin_secrets = origin_secrets or (lambda: ())
    # {name: value} of AUTH_SECRETS that exist (sign-in, AOTY-47); none means sign-in is down (503).
    self.auth_secrets = auth_secrets or (lambda: {})
    self.depth_cache = self.counters_cache = None  # (expires, value): _queue_depth, _queue_counters

  @property
  def lastfm(self):
    if self._lastfm is None:
      self._lastfm = self._lastfm_factory()
    return self._lastfm

  def lastfm_calls(self):
    """The client's running count of HTTP attempts, without creating it (that reads SSM)."""
    return getattr(self._lastfm, "calls", 0) if self._lastfm is not None else 0

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ

    def make_lastfm():
      key = lastfm.api_key_from_ssm(boto3.client("ssm"), f"{env['SSM_PREFIX']}lastfm_api_key")
      return lastfm.Client(key, max_attempts=1, timeout=LASTFM_TIMEOUT_S)

    ssm = boto3.client("ssm")
    reader = guardrails.Reader(ssm, env["SSM_PREFIX"], env_name=env.get("AOTY_ENV", ""))
    secrets = guardrails.OriginSecrets(ssm, env["SSM_PREFIX"])
    auth = guardrails.Secrets(ssm, env["SSM_PREFIX"], AUTH_SECRETS)
    return cls(ddb=boto3.client("dynamodb"), sqs=boto3.client("sqs"), env=env, lastfm_factory=make_lastfm,
               settings=reader.get, origin_secrets=secrets.get, auth_secrets=auth.get,
               lam=boto3.client("lambda"))  # fmt: skip


_deps = None


def _response(status, body, cookies=None):
  """body None: an empty body (204)."""
  out = {
    "statusCode": status,
    # no-store: no cache between here and the browser may keep an API answer, errors included.
    "headers": {"content-type": "application/json", "cache-control": "no-store"},
    "body": "" if body is None else json.dumps(body),
  }
  if cookies:
    out["cookies"] = list(cookies)  # payload v2: Set-Cookie goes here, not in headers
  return out


def _redirect(location, cookies=()):
  """A 302 to Last.fm's auth page or to a path on this site (relative, from core.safe_next), with
  no Referer to carry the callback's token onward."""
  out = {
    "statusCode": 302,
    "headers": {"location": location, "cache-control": "no-store", "referrer-policy": "no-referrer"},
    "body": "",
  }
  if cookies:
    out["cookies"] = list(cookies)
  return out


def handler(event, context, deps=None):
  """Dispatch; a POST sync's outcome is counted once here, whichever return it took (AOTY-41).
  Sign-in requests get one line too, with the outcome as a plain field (no new metric names)."""
  info = {}
  started = time.time()
  out = _dispatch(event or {}, context, deps, info)
  if info.get("action") == "sync":
    _emit(info, out, time.time() - started)
  elif info.get("action") in core.GROUP_WRITES or info.get("action") == "group_sync":
    # One line per group write (AOTY-48) or visit sync (AOTY-55): outcome as a plain field, no new
    # metric names (AOTY-53). A visit sync's member count queued is a plain field too.
    extra = {"queued": info.get("queued", 0)} if info["action"] == "group_sync" else {}
    emf.emit(
      os.environ.get("AOTY_ENV", ""),
      {"LastfmCalls": (info.get("lastfm_calls", 0), "Count")},
      source="api",
      outcome=info.get("group") or str(out.get("statusCode")),
      action=info["action"],
      status=out.get("statusCode"),
      seconds=round(time.time() - started, 3),
      **extra,
    )
  elif info.get("action") in AUTH_ACTIONS:
    emf.emit(
      os.environ.get("AOTY_ENV", ""),
      {"LastfmCalls": (info.get("lastfm_calls", 0), "Count")},
      source="api",
      auth=info.get("auth") or str(out.get("statusCode")),
      action=info["action"],
      status=out.get("statusCode"),
      seconds=round(time.time() - started, 3),
    )
  return out


def _emit(info, out, seconds):
  """One EMF line per POST sync: SyncRequests and LastfmCalls (this request's user.getInfo, if
  any); the outcome is a plain field for the dashboard's Logs Insights table. Status polls
  aren't counted: a queued crowd's polling would drown everything else. No username."""
  try:
    body = json.loads(out.get("body") or "{}")
  except ValueError:
    body = {}
  emf.emit(
    os.environ.get("AOTY_ENV", ""),
    {"SyncRequests": (1, "Count"), "LastfmCalls": (info.get("lastfm_calls", 0), "Count")},
    source="api",
    outcome=core.sync_outcome(out.get("statusCode"), body, info.get("new", False)),
    status=out.get("statusCode"),
    seconds=round(seconds, 3),
  )


def _dispatch(event, context, deps, info):
  global _deps
  base = os.environ.get("BASE_PATH", "/")
  method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET").upper()
  action, arg = core.route(method, event.get("rawPath", ""), base)
  info["action"] = action
  if action == "health":
    # "source" tells CD's smoke test this is deployed code, not Terraform's placeholder, and
    # "sha" which commit: the smoke test fails unless it's the one just deployed.
    env = os.environ.get("AOTY_ENV", "")
    return _response(200, {"ok": True, "source": "lambdas/api", "env": env, "sha": BUILD_SHA})
  if action == "method":
    return _response(405, {"error": "method not allowed"})
  if action == "not_found":
    return _response(404, {"error": "not found"})
  user_route = action in core.USER_ACTIONS
  if user_route and not core.USERNAME_RE.match(arg):
    return _response(400, {"error": "invalid username"})
  if deps is None:
    _deps = _deps or Deps.from_env()
    deps = _deps
  headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
  calls_before = deps.lastfm_calls()  # the client outlives the request: count the difference
  try:
    # Through kamp.fm's CloudFront, the request carries the origin secret. Health above is exempt,
    # so a mis-set secret can't fail CD's smoke test. Sign-in routes are behind it like the rest.
    trusted = guardrails.origin_ok(headers.get(ORIGIN_HEADER), deps.origin_secrets())
    if not trusted and deps.env.get("ENFORCE_ORIGIN") == "true":
      info["auth"] = "origin_refused"
      return _response(403, {"error": "forbidden"})
    if action == "status":
      return get_status(deps, arg)
    if action in AUTH_ACTIONS:
      return _AUTH_HANDLERS[action](deps, event, headers, arg, base, info)
    source_ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")
    ip = core.client_ip(headers, source_ip, trusted)
    if action in core.GROUP_ACTIONS:
      return _group_dispatch(deps, event, headers, action, arg, ip, trusted, info)
    return post_sync(deps, arg, headers, ip, trusted, info)
  except groups_store.Changed:
    info["group"] = "changed"
    return _response(409, {"error": "changed"})
  except Exception:
    # Never log a sign-in route's arg: it's the state nonce.
    log.exception("%s failed for %s", action, _tag(arg) if user_route else "-")
    info["auth"] = "error"
    return _response(503, {"error": "unavailable"})
  finally:
    info["lastfm_calls"] = deps.lastfm_calls() - calls_before


def _cooldown(deps):
  return deps.settings().sync_cooldown_seconds


def _status(deps, row, now, cooldown):
  """core.status, with the queue's depth and counters read only for a queued row."""
  if row.get("state") != "queued":
    return core.status(row, now, cooldown)
  counters = _queue_counters(deps) if row.get("queue_ticket") is not None else None
  concurrency = int(deps.env.get("SYNC_CONCURRENCY") or 2)
  return core.status(row, now, cooldown, _queue_depth(deps), counters, concurrency)


def get_status(deps, name):
  row = _get_user(deps, name)
  if not row or row.get("state") == "not_found":
    return _response(404, {"error": "unknown user"})
  return _response(200, _status(deps, row, int(deps.clock()), _cooldown(deps)))


def post_sync(deps, name, headers, ip, trusted=False, info=None):
  """headers: lowercase names. ip: the viewer, for the per-IP limit on new names. trusted: the
  origin secret matched (logged with the header shape). info, if given, gets "new" (a user's
  first sync) for the metric line."""
  ctype = headers.get("content-type", "")
  if not ctype.lower().startswith("application/json"):
    return _response(415, {"error": "send content-type: application/json"})
  now, settings = int(deps.clock()), deps.settings()
  row, display, refused = check_new_user(
    deps, name, _get_user(deps, name), headers, ip, trusted, now, settings
  )
  if refused:
    return _response(*refused)
  return _response(*request_sync(deps, name, row, display, now, info))


def _is_new_name(row, now):
  """No row, or an expired not_found one: the name hasn't been checked with Last.fm lately."""
  return not row or (row.get("state") == "not_found" and (row.get("expires_at") or 0) <= now)


def check_new_user(deps, name, row, headers, ip, trusted, now, settings):
  """Step 1 of POST sync, shared with a group's member add (AOTY-48): (row, display, None) for a
  name that may be synced, or (None, None, (status, body)) refusing it.

  row: the users row as read (maybe {}). A name new to the app is checked with user.getInfo,
  after the new-user pause and the per-IP limit; one Last.fm doesn't know gets a not_found row.
  display is Last.fm's casing for a new name, else None.
  """
  if not _is_new_name(row, now):
    if row.get("state") == "not_found":
      return None, None, (404, {"error": "unknown user"})
    return row, None, None
  if settings.new_users_paused:  # before user.getInfo: a pause spends nothing on Last.fm
    return None, None, (503, {"error": "paused"})
  _log_header_shape(headers, trusted)
  if not _allow_new_name(deps, name, ip, now, settings.new_users_per_ip_hour):
    log.info("new-name limit reached for %s", _tag(name))
    return None, None, (429, {"error": "too_many_new"})
  try:
    canonical = deps.lastfm.user_info(name)
  except Exception as e:  # LastfmError, a timeout, SSM: say "try later", never "no such user"
    log.warning("user.getInfo failed for %s: %s", _tag(name), getattr(e, "code", type(e).__name__))
    return None, None, (503, {"error": "lastfm_unavailable"})
  if canonical is None:
    _update(deps, name, {"state": "not_found", "expires_at": now + NOT_FOUND_TTL_S, "not_found_at": now})
    return None, None, (404, {"error": "unknown user"})
  display = canonical if canonical.lower() == name else None
  return {"username": name}, display, None  # a new user (or one Last.fm knows again)


def request_sync(deps, name, row, display, now, info=None, lean=False):
  """Steps 2-5 of POST sync (the module docstring), for a name check_new_user accepted, shared
  with a group's member add (AOTY-48) and a group visit (AOTY-55). (status, body). info gets
  "new" (a first sync). lean: no "status" in the body (it costs a re-read and the queue's
  counters), for a group visit that asks for up to 25 members in one request."""
  info = {} if info is None else info
  cooldown, settings = _cooldown(deps), deps.settings()

  def status_of(r):
    return None if lean else _status(deps, r, now, cooldown)

  # A queued row needs the queue's depth to tell a long wait from a dead mark (api_core._running).
  depth = _queue_depth(deps) if row.get("state") == "queued" else None
  decision = core.decide_sync(row, now, cooldown, depth)
  if decision != "queue":
    return 200, {"queued": False, "reason": decision, "status": status_of(row)}
  new = not row.get("last_synced_at")
  info["new"] = new
  if new and settings.new_users_paused:
    return 503, {"error": "paused"}
  if settings.pause_syncs:  # an existing user: their chart still serves; the banner stays quiet
    return 200, {"queued": False, "reason": "paused", "status": status_of(row)}
  if new and _queue_depth(deps) > settings.max_queue_for_new:
    return 503, {"error": "busy"}

  previous = {k: row.get(k) for k in ("state", "sync_requested_at", "queue_ticket")}
  ticket = _take_ticket(deps)
  if not _mark_queued(deps, name, now, cooldown, display, ticket):
    _burn_tickets(deps, ticket)
    if lean:
      return 200, {"queued": False, "reason": "in_progress", "status": None}
    row = _get_user(deps, name)
    return 200, {"queued": False, "reason": "in_progress", "status": _status(deps, row, now, cooldown)}
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
    _burn_tickets(deps, ticket)
    return 503, {"error": "queue_unavailable"}
  log.info("sync queued for %s", _tag(name))
  # A stale mark being replaced held a ticket no sync will use up: burn it, or everyone behind
  # it would read one place further back for good.
  _burn_tickets(deps, previous["queue_ticket"])
  if lean:
    return 202, {"queued": True, "status": None}
  row = _get_user(deps, name)
  return 202, {"queued": True, "status": _status(deps, row, now, cooldown)}


# ---- sign-in (AOTY-47) --------------------------------------------------------------------
#
# GET  auth/start?next=<path>   302 to Last.fm, with a signed state cookie (10 min) holding a nonce
#                               and the landing path; 503 when the keys or secret can't be read
# GET  auth/callback/<nonce>    Last.fm sends the visitor back here with ?token=. The nonce must
#                               match the state cookie's (else no Last.fm call at all), then
#                               auth.getSession names the user: session cookie set, 302 to next.
#                               Every failure is a 302 to the landing path with ?signin=failed.
# GET  me                       200 {username} | 401 signed out or revoked | 503 keys or the
#                               revocation cutoff unreadable
# POST auth/signout             JSON only (415 otherwise): clears the session cookie
# POST auth/signout?everywhere=1  also revokes every session of the signed-in user (AOTY-57):
#                               401 without a valid, unrevoked session (so a revoked copy can't keep
#                               pushing the cutoff), 503 keeping the cookie when it can't be stored
#
# The session cookie is scoped to <base> and is sent with every request to it, from any kamp.fm
# page: the whole kamp.fm origin is the trust boundary. A plain sign-out clears this browser's
# cookie only; "everywhere" stores a cutoff (groups table, session#<name>) that session_user checks
# on every use, so copied cookies stop working too.


def _state_path(base):
  return f"{base}api/auth/"


def _auth_keys(deps):
  return session.usable_keys(deps.auth_secrets())


class SessionUnavailable(Exception):
  """The revocation cutoff couldn't be read: answer 503, never 401 (a DynamoDB blip mustn't sign
  anyone out)."""


def session_user(deps, event, keys, consistent=True):
  """(username, "ok"), or (None, "signed_out" | "revoked"). keys: _auth_keys(deps), non-empty (the
  caller has already answered 503 when it's empty).

  Every caller gets the revocation check (AOTY-57), so a new route can't forget it. consistent=False
  is for api/me alone, which only decides what the page offers; writes read the cutoff strongly.
  Raises SessionUnavailable when the cutoff can't be read.
  """
  token = session.parse_cookies(event.get("cookies")).get(session.SESSION_COOKIE)
  claims = session.verify(session.SESSION, token, keys, int(deps.clock()))
  user = core.session_username(claims)
  if not user:
    return None, "signed_out"
  try:
    cutoff = groups_store.revoked_at(deps, user, consistent)
  except Exception as e:
    log.warning("revocation cutoff unreadable: %s", _error_code(e) or type(e).__name__)
    raise SessionUnavailable() from e
  if session.is_revoked(claims, cutoff):
    log.info("revoked session for %s", _tag(user))
    return None, "revoked"
  return user, "ok"


def auth_start(deps, event, headers, _arg, base, info):
  keys, secrets = _auth_keys(deps), deps.auth_secrets()
  origin = deps.env.get("PUBLIC_ORIGIN")
  if not keys or not secrets.get("lastfm_api_secret") or not origin:
    info["auth"] = "unavailable"
    return _response(503, {"error": "signin_unavailable"})
  landing = core.safe_next((event.get("queryStringParameters") or {}).get("next"), base)
  nonce = session.new_nonce()
  state = session.new_state(nonce, landing, keys[0], int(deps.clock()))
  info["auth"] = "started"
  return _redirect(
    core.lastfm_auth_url(deps.lastfm.api_key, origin, base, nonce),
    [session.set_cookie(session.STATE_COOKIE, state, _state_path(base), session.STATE_TTL_S)],
  )


def auth_callback(deps, event, headers, nonce, base, info):
  clear_state = session.clear_cookie(session.STATE_COOKIE, _state_path(base))
  keys, secret = _auth_keys(deps), deps.auth_secrets().get("lastfm_api_secret")
  if not keys or not secret:
    info["auth"] = "unavailable"
    return _redirect(core.with_query(base, signin="failed"), [clear_state])
  cookie = session.parse_cookies(event.get("cookies")).get(session.STATE_COOKIE)
  claims = session.verify(session.STATE, cookie, keys, int(deps.clock()))
  if not session.nonce_matches(claims, nonce):
    # A forged or replayed callback, an expired sign-in, or a second tab's newer state cookie.
    info["auth"] = "bad_state" if cookie else "no_state"
    return _redirect(core.with_query(base, signin="failed"), [clear_state])
  landing = core.safe_next(claims.get("next"), base)  # checked at start too; signed, but cheap
  failed = core.with_query(landing, signin="failed")
  token = (event.get("queryStringParameters") or {}).get("token") or ""
  if not token or len(token) > 128:
    info["auth"] = "no_token"
    return _redirect(failed, [clear_state])
  try:
    name = deps.lastfm.auth_get_session(token, secret)
  except Exception as e:  # LastfmError (a used or expired token), a timeout, SSM for the API key
    log.warning("auth.getSession failed: %s", getattr(e, "code", type(e).__name__))
    info["auth"] = "lastfm_failed"
    return _redirect(failed, [clear_state])
  if not name or not core.USERNAME_RE.match(name):
    info["auth"] = "bad_name"
    return _redirect(failed, [clear_state])
  log.info("signed in %s", _tag(name))
  info["auth"] = "signed_in"
  signed = session.new_session(name, keys[0], int(deps.clock()))
  return _redirect(landing, [clear_state, session.set_cookie(session.SESSION_COOKIE, signed, base,
                                                             session.SESSION_TTL_S)])  # fmt: skip


def get_me(deps, event, headers, _arg, base, info):
  keys = _auth_keys(deps)
  if not keys:  # not 401: an SSM outage mustn't tell the page the visitor signed out
    info["auth"] = "unavailable"
    return _response(503, {"error": "signin_unavailable"})
  # group_writes tells the page whether to offer sign-in, create and edit at all (off in prod until
  # AOTY-57), so it hides them rather than showing controls that answer 503.
  writes = deps.env.get("GROUP_WRITES") == "true"
  try:
    user, word = session_user(deps, event, keys, consistent=False)
  except SessionUnavailable:
    info["auth"] = "unavailable"
    return _response(503, {"error": "signin_unavailable"})
  if not user:
    had_cookie = session.SESSION_COOKIE in session.parse_cookies(event.get("cookies"))
    info["auth"] = "revoked" if word == "revoked" else "bad_session" if had_cookie else "anonymous"
    clear = [session.clear_cookie(session.SESSION_COOKIE, base)] if had_cookie else None
    return _response(401, {"error": "signed_out", "group_writes": writes}, clear)
  info["auth"] = "me"
  return _response(200, {"username": user, "group_writes": writes})


def post_signout(deps, event, headers, _arg, base, info):
  if not headers.get("content-type", "").lower().startswith("application/json"):
    info["auth"] = "not_json"
    return _response(415, {"error": "send content-type: application/json"})
  clear = [session.clear_cookie(session.SESSION_COOKIE, base)]
  if (event.get("queryStringParameters") or {}).get("everywhere") != "1":
    info["auth"] = "signed_out"
    return _response(200, {"signed_out": True}, clear)
  # Everywhere: the cookie stays when this fails, so the visitor can try again.
  keys = _auth_keys(deps)
  if not keys:
    info["auth"] = "unavailable"
    return _response(503, {"error": "signin_unavailable"})
  try:
    user, word = session_user(deps, event, keys)
  except SessionUnavailable:
    info["auth"] = "unavailable"
    return _response(503, {"error": "signin_unavailable"})
  if not user:
    info["auth"] = word
    return _response(401, {"error": "signed_out"}, clear)
  try:
    groups_store.revoke(deps, user, int(deps.clock()))
  except Exception as e:
    log.warning("could not revoke sessions: %s", _error_code(e) or type(e).__name__)
    info["auth"] = "unavailable"
    return _response(503, {"error": "signin_unavailable"})
  log.info("revoked every session of %s", _tag(user))
  info["auth"] = "signed_out_everywhere"
  return _response(200, {"signed_out": True, "everywhere": True}, clear)


_AUTH_HANDLERS = {"auth_start": auth_start, "auth_callback": auth_callback, "me": get_me,
                  "auth_signout": post_signout}  # fmt: skip


# ---- groups (AOTY-48) -----------------------------------------------------------------------
#
# GET    groups/<id>                    public {id, name, owner, members, ready, total} | 404
# GET    me/groups                      the caller's own groups {groups: [{id, name, member_count,
#                                       updated_at}]} | 401
# POST   groups {name, slug?}           201 {id, sync} | 400 bad_name, needs_slug | 409 taken
# PATCH  groups/<id> {name, slug?}      200 {id}: the slug follows the name (the group moves) | 409 taken
# DELETE groups/<id>                    204
# POST   groups/<id>/members {username} 201 {member, added: true, sync, status} | 200 {added: false}
#                                       | 404 unknown user | 409 full, cannot_add | 429 | 503
# DELETE groups/<id>/members/<name>     204, also when they weren't a member
# POST   groups/<id>/sync               200 {queued: n, members: {word: count}} | 200 {queued: 0,
#                                       reason: cooldown | paused} | 404 | 415. Public, on even where
#                                       writes are off: a visit to the group page (AOTY-55, group_sync)
#
# GET groups/<id> also counts members syncing now (queued or running, not stuck), for the page.
#
# Writes: 503 groups_unavailable unless GROUP_WRITES=true (Terraform; off in prod until AOTY-57),
# 415 unless JSON, 503 signin_unavailable when the session keys can't be read, 401 without a
# session, 404 for no such group, 403 for someone else's. A changed membership stamps the meta's
# chart_requested_at and pokes the chart builder; a delete or a move asks it to delete the old
# chart, so its single concurrency orders that after any build in flight.
#
# Adding a member new to the app goes through POST sync's checks (check_new_user: the new-user
# pause, the per-IP limit, user.getInfo), and is refused while the queue is too busy for new
# users, before anything is written. Then their sync is requested as POST sync does; its outcome
# (queued, cooldown, in_progress, paused, not_queued) is in the answer, and the member stays
# either way: their next visit or sync picks it up. An IP slot spent on an add that then loses a
# race (full, or a duplicate) isn't given back.


def _json_body(event):
  """The request's JSON object, {} for none, or None if it isn't a JSON object."""
  raw = event.get("body") or ""
  if event.get("isBase64Encoded"):
    try:
      raw = base64.b64decode(raw).decode()
    except ValueError:
      return None
  if not raw.strip():
    return {}
  try:
    body = json.loads(raw)
  except ValueError:
    return None
  return body if isinstance(body, dict) else None


def _group_dispatch(deps, event, headers, action, arg, ip, trusted, info):
  if action == "group_get":
    return group_get(deps, arg) if grp.SLUG_RE.match(arg) else _response(404, {"error": "no such group"})
  if action == "group_sync":  # public, and on where group writes are off: it's syncs, not edits
    if not headers.get("content-type", "").lower().startswith("application/json"):
      info["group"] = "not_json"
      return _response(415, {"error": "send content-type: application/json"})
    if not grp.SLUG_RE.match(arg):
      info["group"] = "missing"
      return _response(404, {"error": "no such group"})
    return group_sync(deps, arg, info)
  if action in core.GROUP_WRITES:
    if deps.env.get("GROUP_WRITES") != "true":
      info["group"] = "disabled"
      return _response(503, {"error": "groups_unavailable"})
    if not headers.get("content-type", "").lower().startswith("application/json"):
      info["group"] = "not_json"
      return _response(415, {"error": "send content-type: application/json"})
  keys = _auth_keys(deps)
  if not keys:  # not 401: an SSM outage mustn't read as signed out
    info["group"] = "signin_unavailable"
    return _response(503, {"error": "signin_unavailable"})
  try:
    user, word = session_user(deps, event, keys)
  except SessionUnavailable:
    info["group"] = "signin_unavailable"
    return _response(503, {"error": "signin_unavailable"})
  if not user:
    info["group"] = word
    return _response(401, {"error": "signed_out"})
  if action == "my_groups":
    return _response(200, {"groups": groups_store.owned_by(deps, user)})
  slug = arg[0] if action == "member_remove" else arg
  if action != "group_create" and not grp.SLUG_RE.match(slug or ""):
    info["group"] = "missing"
    return _response(404, {"error": "no such group"})
  body = _json_body(event) if action in ("group_create", "group_rename", "member_add") else {}
  if body is None:
    info["group"] = "bad_json"
    return _response(400, {"error": "send a JSON object"})
  if action == "group_create":
    return group_create(deps, user, body, info)
  if action == "group_rename":
    return group_rename(deps, user, slug, body, info)
  if action == "group_delete":
    return group_delete(deps, user, slug, info)
  if action == "member_add":
    return member_add(deps, user, slug, body, headers, ip, trusted, info)
  return member_remove(deps, user, slug, arg[1], info)


# Store words -> answers, for the ones every write shares.
_REFUSALS = {
  "missing": (404, {"error": "no such group"}),
  "forbidden": (403, {"error": "not your group"}),
  "taken": (409, {"error": "taken"}),
  "full": (409, {"error": "full", "max": grp.MAX_MEMBERS}),
}


def _refused(word, info):
  info["group"] = word
  return _response(*_REFUSALS[word])


def group_get(deps, slug):
  meta, members = groups_store.load(deps, slug)
  if meta is None:
    return _response(404, {"error": "no such group"})
  rows, now = _get_users(deps, members), int(deps.clock())
  ready = sum(1 for m in members if grp.member_state(rows.get(m, {})) == "ready")
  # syncing (AOTY-55): members with a sync queued or running, so the page knows to look again.
  syncing = sum(1 for m in members if core.decide_sync(rows.get(m, {}), now, 0) == "in_progress")
  return _response(200, {"id": slug, "name": meta.get("name", ""), "owner": meta.get("owner"),
                         "members": members, "ready": ready, "total": len(members),
                         "syncing": syncing})  # fmt: skip


def group_sync(deps, slug, info):
  """POST groups/<id>/sync (AOTY-55): a visit to the group's page queues its members' syncs.

  At most once per group_sync_cooldown_seconds (the meta's sync_requested_at, claimed atomically),
  and through request_sync for each member, so their own cooldown, pause_syncs, the new-user pause
  and the queue limit all hold. This is also how members who never visit stay fresh (AOTY-60).
  Members who are not_found, private or blocking group adds are skipped. Nothing is written while
  pause_syncs is on. If nobody was queued because every send failed, the claim is given back.
  Anyone may call it: the per-user cooldown is what bounds the Last.fm calls it can cause.
  """
  now, settings = int(deps.clock()), deps.settings()
  if settings.pause_syncs:
    info["group"] = "paused"
    return _response(200, {"queued": 0, "reason": "paused"})
  meta, members = groups_store.load(deps, slug)
  if meta is None:
    return _refused("missing", info)
  claimed, previous = groups_store.claim_sync(deps, slug, now, settings.group_sync_cooldown_seconds)
  if not claimed:
    if previous == "missing":  # deleted since the read
      return _refused("missing", info)
    info["group"] = "cooldown"
    return _response(200, {"queued": 0, "reason": "cooldown"})
  rows = _get_users(deps, members, full=True)
  words = []
  for name in members:
    row = rows.get(name) or {"username": name}
    # Left alone (AOTY-60): Last.fm doesn't know them, their listening is hidden, or they opted out
    # of group adds (AOTY-49). Their own page visit still syncs them.
    skip = row.get("state") if row.get("state") in ("not_found", "private") else None
    skip = skip or ("blocked" if row.get("block_group_adds") else None)
    if skip:
      words.append(skip)
      continue
    try:
      words.append(core.sync_word(*request_sync(deps, name, row, None, now, lean=True)))
    except Exception:  # one member's failure mustn't keep the rest from syncing
      log.exception("group %s visit: sync request failed for %s", slug, _tag(name))
      words.append("unavailable")
  counts = core.tally(words)
  queued = counts.get("queued", 0)
  if not queued and counts.get("not_queued"):
    groups_store.release_sync(deps, slug, now, previous)
  info["group"] = "synced"
  info["queued"] = queued
  log.info("group %s visit: %s", slug, counts)
  return _response(200, {"queued": queued, "members": counts})


def _name_and_slug(body, info):
  """(name, slug) from a create or rename body, or (None, an answer)."""
  name = grp.clean_name(body.get("name"))
  if name is None:
    info["group"] = "bad_name"
    return None, _response(400, {"error": "bad_name", "max": grp.NAME_MAX})
  slug = grp.slug_for(name, body.get("slug"))
  if slug is None:
    info["group"] = "needs_slug"  # a name with no usable letters, or a bad slug asked for
    return None, _response(400, {"error": "needs_slug"})
  return (name, slug), None


def group_create(deps, user, body, info):
  parsed, answer = _name_and_slug(body, info)
  if answer:
    return answer
  name, slug = parsed
  now = int(deps.clock())
  if groups_store.create(deps, slug, name, user, now) == "taken":
    return _refused("taken", info)
  log.info("group %s created by %s", slug, _tag(user))
  info["group"] = "created"
  _request_chart(deps, slug, now)
  # The owner is the first member: a signed-in name is a real Last.fm user, so no user.getInfo.
  status, sync_body = request_sync(deps, user, _get_user(deps, user) or {"username": user}, None, now)
  return _response(201, {"id": slug, "sync": core.sync_word(status, sync_body)})


def group_rename(deps, user, slug, body, info):
  parsed, answer = _name_and_slug(body, info)
  if answer:
    return answer
  name, new_slug = parsed
  now = int(deps.clock())
  word, slug_after = groups_store.rename(deps, slug, new_slug, name, user, now)
  if word != "renamed":
    return _refused(word, info)
  info["group"] = "renamed" if slug_after == slug else "moved"
  if slug_after != slug:
    log.info("group %s moved to %s", slug, slug_after)
    _request_chart(deps, slug, deleted=True)
  _request_chart(deps, slug_after, now)
  return _response(200, {"id": slug_after})


def group_delete(deps, user, slug, info):
  word = groups_store.delete(deps, slug, user)
  if word != "deleted":
    return _refused(word, info)
  log.info("group %s deleted", slug)
  info["group"] = "deleted"
  _request_chart(deps, slug, deleted=True)
  return _response(204, None)


def member_add(deps, user, slug, body, headers, ip, trusted, info):
  name = str(body.get("username") or "").strip().lower()
  if not core.USERNAME_RE.match(name):
    info["group"] = "bad_name"
    return _response(400, {"error": "invalid username"})
  # Cheap refusals first, so a full group or a stranger's spends no Last.fm call or IP slot.
  meta, members = groups_store.load(deps, slug)
  if meta is None:
    return _refused("missing", info)
  if meta.get("owner") != user:
    return _refused("forbidden", info)
  if name in members:
    info["group"] = "member"
    return _response(200, {"member": name, "added": False})
  if len(members) >= grp.MAX_MEMBERS:
    return _refused("full", info)
  now, settings = int(deps.clock()), deps.settings()
  row = _get_user(deps, name)
  # Opted out of being added (AOTY-49 sets it). Neutral: an owner never learns why.
  if name != user and row.get("block_group_adds"):
    info["group"] = "cannot_add"
    return _response(409, {"error": "cannot_add"})
  row, display, refused = check_new_user(deps, name, row, headers, ip, trusted, now, settings)
  if refused:
    info["group"] = core.sync_outcome(*refused)
    return _response(*refused)
  if not row.get("last_synced_at"):  # a member who'd never sync would wait on the page for good
    if settings.new_users_paused:
      info["group"] = "paused"
      return _response(503, {"error": "paused"})
    if _queue_depth(deps) > settings.max_queue_for_new:
      info["group"] = "busy"
      return _response(503, {"error": "busy"})
  word = groups_store.add_member(deps, slug, user, name, now)
  if word == "member":
    info["group"] = "member"
    return _response(200, {"member": name, "added": False})
  if word != "added":
    return _refused(word, info)
  log.info("group %s: added %s", slug, _tag(name))
  _request_chart(deps, slug, now)
  status, sync_body = request_sync(deps, name, row, display, now)
  info["group"] = "added"
  return _response(201, {"member": name, "added": True, "sync": core.sync_word(status, sync_body),
                         "status": sync_body.get("status")})  # fmt: skip


def member_remove(deps, user, slug, name, info):
  if not core.USERNAME_RE.match(name or ""):
    info["group"] = "bad_name"
    return _response(400, {"error": "invalid username"})
  word = groups_store.remove_member(deps, slug, user, name, int(deps.clock()))
  if word in _REFUSALS:
    return _refused(word, info)
  info["group"] = word  # removed or absent: either way they aren't a member now
  if word == "removed":
    log.info("group %s: removed %s", slug, _tag(name))
    _request_chart(deps, slug, int(deps.clock()))
  return _response(204, None)


def _request_chart(deps, slug, requested_at=None, deleted=False):
  """Ask the chart builder to rebuild a group (or delete its chart). Async; a failure is logged and
  leaves the chart stale until the next change, never failing the write."""
  payload = {"group": slug, "deleted": True} if deleted else {"group": slug, "requested_at": requested_at}
  try:
    deps.lam.invoke(FunctionName=deps.env["CHART_FUNCTION"], InvocationType="Event",
                    Payload=json.dumps(payload).encode())  # fmt: skip
  except Exception:
    log.exception("could not ask the chart builder about group %s", slug)


# ---- users table --------------------------------------------------------------------------


def _get_user(deps, name):
  out = deps.ddb.get_item(
    TableName=deps.env["USERS_TABLE"], Key={"username": {"S": name}}, ConsistentRead=True
  )
  return _from_item(out.get("Item") or {})


USERS_BATCH_FIELDS = "username, #s, sync_finished_at, chart_built_at, sync_requested_at, sync_started_at"
RETRIES = 4


def _get_users(deps, names, full=False):
  """{name: row} for the users rows that exist, with the fields groups.member_state reads (whole
  rows when full, for request_sync). BatchGetItem (at most 25 names, under its 100), retrying
  unprocessed keys. Eventually consistent: a group visit's rows may be a moment old, and
  _mark_queued's condition is what stops a double queue."""
  table, found = deps.env["USERS_TABLE"], {}
  if not names:
    return found
  request = {"Keys": [{"username": {"S": n}} for n in names]}
  if not full:
    request |= {"ProjectionExpression": USERS_BATCH_FIELDS, "ExpressionAttributeNames": {"#s": "state"}}
  pending = {table: request}
  for attempt in range(RETRIES + 1):
    out = deps.ddb.batch_get_item(RequestItems=pending)
    for raw in out.get("Responses", {}).get(table, []):
      row = _from_item(raw)
      found[row["username"]] = row
    pending = out.get("UnprocessedKeys") or {}
    if not pending:
      return found
    if attempt == RETRIES:
      raise RuntimeError("BatchGetItem left unprocessed keys after retries")
    deps.sleep(0.2 * 2**attempt)
  return found


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


def _mark_queued(deps, name, now, cooldown, display, ticket=None):
  """Set state=queued and sync_requested_at (and queue_ticket), unless another request beat us
  to it. True if set.

  The condition repeats decide_sync atomically: no recent request, and no live sync (one idle
  past STUCK_AFTER counts as dead; decide_sync has already kept a queued row in a long queue).
  """
  fields = {"state": "queued", "sync_requested_at": now}
  if display:
    fields["display_name"] = display
  if ticket is not None:
    fields["queue_ticket"] = ticket
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
      names = {f"#r{i}": k for i, k in enumerate(gone)}
      deps.ddb.update_item(TableName=deps.env["USERS_TABLE"], Key={"username": {"S": name}},
                           UpdateExpression="REMOVE " + ", ".join(names),
                           ExpressionAttributeNames=names)  # fmt: skip
  except Exception:
    log.exception("could not undo the queued mark for %s", _tag(name))


# ---- queue tickets (AOTY-26; the counters are api_core.queue_place's) ----------------------

QUEUE_KEY = {"id": {"S": "queue"}}


def _take_ticket(deps):
  """The next place in line (the queue item's joined counter), or None if it can't be had: the
  sync is queued anyway, just without a position to show."""
  try:
    out = deps.ddb.update_item(
      TableName=deps.env["GUARD_TABLE"], Key=QUEUE_KEY, UpdateExpression="ADD #j :one",
      ExpressionAttributeNames={"#j": "joined"}, ExpressionAttributeValues={":one": {"N": "1"}},
      ReturnValues="UPDATED_NEW",
    )  # fmt: skip
    return int(out["Attributes"]["joined"]["N"])
  except Exception:
    log.exception("could not take a queue ticket")
    return None


def _burn_tickets(deps, ticket):
  """Count a ticket no sync will start with as used up, so the places behind it move up."""
  if ticket is None:
    return
  try:
    deps.ddb.update_item(
      TableName=deps.env["GUARD_TABLE"], Key=QUEUE_KEY, UpdateExpression="ADD #s :one",
      ExpressionAttributeNames={"#s": "started"}, ExpressionAttributeValues={":one": {"N": "1"}},
    )  # fmt: skip
  except Exception:
    log.exception("could not burn a queue ticket")


def _queue_counters(deps):
  """The queue item's counters, cached like the depth; None when unreadable (no position shown)."""
  now = deps.clock()
  if deps.counters_cache and now < deps.counters_cache[0]:
    return deps.counters_cache[1]
  try:
    item = deps.ddb.get_item(TableName=deps.env["GUARD_TABLE"], Key=QUEUE_KEY).get("Item") or {}
    counters = {k: int(v["N"]) for k, v in item.items() if "N" in v}
  except Exception:
    log.exception("could not read the queue counters")
    return None
  deps.counters_cache = (now + DEPTH_TTL_S, counters)
  return counters


IP_WINDOW_S = 3600


def _allow_new_name(deps, name, ip, now, limit):
  """Count name against ip's distinct new names this hour; False once over limit.

  The guard table item ip#<hash>#<hour> holds a string set of hashed names. Adding a name is
  conditional: room left, or the name is already there, so retrying the same name (say after
  lastfm_unavailable) never uses up the allowance. Items expire an hour after their window.
  """
  hour = now // IP_WINDOW_S
  tag = core.name_tag(name)
  try:
    deps.ddb.update_item(
      TableName=deps.env["GUARD_TABLE"],
      Key={"id": {"S": core.ip_limit_key(ip, hour)}},
      UpdateExpression="ADD #n :h SET #e = if_not_exists(#e, :exp)",
      ConditionExpression="attribute_not_exists(#n) OR size(#n) < :lim OR contains(#n, :t)",
      ExpressionAttributeNames={"#n": "names", "#e": "expires_at"},
      ExpressionAttributeValues={
        ":h": {"SS": [tag]}, ":t": {"S": tag}, ":lim": {"N": str(limit)},
        ":exp": {"N": str((hour + 2) * IP_WINDOW_S)},
      },
    )  # fmt: skip
  except Exception as e:
    if _error_code(e) == "ConditionalCheckFailedException":
      return False
    raise
  return True


def _log_header_shape(headers, trusted):
  """Which client-IP headers arrive, without any address or secret (AOTY-26: confirm on staging
  that CloudFront's header arrives before enforcing, and which IP header to rely on)."""
  xff = [p for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
  log.info(
    "ip headers: viewer_address=%s xff_entries=%d origin_header=%s origin_ok=%s",
    "cloudfront-viewer-address" in headers, len(xff), ORIGIN_HEADER in headers, trusted,
  )  # fmt: skip


DEPTH_TTL_S = 10  # status polls from a crowd of queued pages would otherwise each call SQS


def _queue_depth(deps):
  now = deps.clock()
  if deps.depth_cache and now < deps.depth_cache[0]:
    return deps.depth_cache[1]
  try:
    out = deps.sqs.get_queue_attributes(QueueUrl=deps.env["SYNC_QUEUE_URL"],
                                        AttributeNames=["ApproximateNumberOfMessages"])  # fmt: skip
    depth = int(out["Attributes"]["ApproximateNumberOfMessages"])
  except Exception:
    log.exception("could not read the sync queue depth")
    return 0
  deps.depth_cache = (now + DEPTH_TTL_S, depth)
  return depth


def _error_code(e):
  return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", ""))


def _tag(username):
  """A short stable hash, so logs can follow one user without naming them (as sync's)."""
  if not username:
    return "-"
  return hashlib.sha256(str(username).lower().encode()).hexdigest()[:10]
