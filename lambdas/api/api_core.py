"""The API's pure core (AOTY-22): routing, whether to queue a sync, and the status a page polls.

No I/O here (handler.py does it), so the rules are tested directly.

The users row (written by api, sync and the chart builder) carries:
  state              queued (api) | syncing | retrying | done | error | not_found | private (sync)
  sync_requested_at  when the api last queued a sync: drives the cooldown
  sync_started_at    when the sync worker started (sync); last_synced_at is the same instant
  sync_finished_at   when it finished, done or terminal (sync)
  progress           {weeks_done, weeks_total, plays, albums, top_artist, top_album, top_plays}
                     (sync, after every week; albums and top_* count every play, any release year)
  preview            JSON {"wall": [{artist, album, art, fallbacks}]}: a first sync's newest albums
                     for the first-visit cover wall (sync, AOTY-24); removed by the chart builder once
                     a chart ranks anything. It's the user's public recent listening, the same as
                     their Last.fm profile shows, served to anyone who asks for their status.
  chart_built_at, chart_pending, chart_plays, chart_albums, chart_hits   (chart builder)
  display_name       Last.fm's casing (sync, or api on a new user)
  queue_ticket       the place in line handed out when the api queued the sync (AOTY-26); the
                     sync worker removes it when the sync starts (queue_place)
  theme, theme_set_at  the owner's page theme, a common.themes id, and when they set it (api,
                     AOTY-65); the chart builder copies theme into chart.json
"""

import hashlib
import ipaddress
import json
import math
import re
from urllib.parse import parse_qsl, unquote, urlencode

from common import themes

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")  # as the sync worker's
ACTIVE = frozenset({"queued", "syncing", "retrying"})
FAILED = frozenset({"error", "not_found", "private"})
STUCK_AFTER = 30 * 60  # a sync that hasn't moved this long is dead (OOM, timeout, lost message)
BUILDING_MAX = 5 * 60  # the chart builder runs within seconds of a sync; past this, give up waiting
DATING_FRESH = 10 * 60  # charts rebuild about once a minute while albums resolve
_USER_ROUTE = re.compile(r"api/users/([^/]+)/(sync|status|theme)/?")
_CALLBACK_ROUTE = re.compile(r"api/auth/callback/([A-Za-z0-9_-]{1,64})/?")
USER_ACTIONS = frozenset({"sync", "status", "theme"})
_USER_METHODS = {"sync": "POST", "status": "GET", "theme": "POST"}  # theme: the owner's, AOTY-65
# Groups (AOTY-48): path -> {method: action}. <id> is the group's slug; <name> a member.
_GROUP_ROUTE = re.compile(r"api/groups/([^/]+)/?")
_MEMBERS_ROUTE = re.compile(r"api/groups/([^/]+)/members/?")
_MEMBER_ROUTE = re.compile(r"api/groups/([^/]+)/members/([^/]+)/?")
_GROUP_SYNC_ROUTE = re.compile(r"api/groups/([^/]+)/sync/?")
_GROUP_METHODS = {"GET": "group_get", "PATCH": "group_rename", "DELETE": "group_delete"}
GROUP_WRITES = frozenset({"group_create", "group_rename", "group_delete", "member_add", "member_remove"})
# group_sync (AOTY-55) is public like group_get: a visit to a group page syncs its members.
GROUP_ACTIONS = GROUP_WRITES | {"group_get", "my_groups", "group_sync"}
# Sign-in (AOTY-47): path -> (action, method). Only the callback changes anything on a GET, and
# only after its state check; signout is a JSON-only POST like sync.
_AUTH_ROUTES = {
  "api/auth/start": ("auth_start", "GET"),
  "api/me": ("me", "GET"),
  "api/auth/signout": ("auth_signout", "POST"),
}


def route(method, path, base):
  """(action, arg) for a request. action is health, sync, status, theme, auth_start,
  auth_callback, me, auth_signout, one of GROUP_ACTIONS, method or not_found.

  For sync, status and theme, arg is the username, lowercased but not yet validated (the caller answers
  400 for a bad one). For auth_callback it's the state nonce from the path. For a group route
  it's the group id (lowercased, unvalidated: the caller answers 404 for a bad one), and for
  member_remove (id, username). Otherwise None.
  """
  if not path.startswith(base):
    return "not_found", None
  rest = path[len(base) :]
  if rest == "api/health":
    return ("health", None) if method in ("GET", "HEAD") else ("method", None)
  auth = _AUTH_ROUTES.get(rest.rstrip("/"))
  if auth:
    return (auth[0], None) if method == auth[1] else ("method", None)
  m = _CALLBACK_ROUTE.fullmatch(rest)
  if m:
    return ("auth_callback", m.group(1)) if method == "GET" else ("method", None)
  if rest.startswith("api/groups") or rest.rstrip("/") == "api/me/groups":
    return _group_route(method, rest)
  m = _USER_ROUTE.fullmatch(rest)
  if not m:
    return "not_found", None
  action = m.group(2)
  if method != _USER_METHODS[action]:
    return "method", None
  return action, unquote(m.group(1)).strip().lower()


def _part(raw):
  return unquote(raw).strip().lower()


def _group_route(method, rest):
  bare = rest.rstrip("/")
  if bare == "api/me/groups":
    return ("my_groups", None) if method == "GET" else ("method", None)
  if bare == "api/groups":
    return ("group_create", None) if method == "POST" else ("method", None)
  m = _MEMBER_ROUTE.fullmatch(rest)
  if m:
    return (
      ("member_remove", (_part(m.group(1)), _part(m.group(2)))) if method == "DELETE" else ("method", None)
    )
  m = _GROUP_SYNC_ROUTE.fullmatch(rest)
  if m:
    return ("group_sync", _part(m.group(1))) if method == "POST" else ("method", None)
  m = _MEMBERS_ROUTE.fullmatch(rest)
  if m:
    return ("member_add", _part(m.group(1))) if method == "POST" else ("method", None)
  m = _GROUP_ROUTE.fullmatch(rest)
  if m:
    action = _GROUP_METHODS.get(method)
    return (action, _part(m.group(1))) if action else ("method", None)
  return "not_found", None


def sync_word(status, body):
  """What a member add's sync request did, for its answer (AOTY-48): queued, cooldown,
  in_progress, paused, busy, not_queued (SQS failed) or unavailable."""
  if status == 202:
    return "queued"
  body = body if isinstance(body, dict) else {}
  if body.get("error") == "queue_unavailable":
    return "not_queued"
  return str(body.get("reason") or body.get("error") or "unavailable")


def tally(words):
  """{word: count} of a group sync's per-member sync_words (AOTY-55), in a stable order."""
  out = {}
  for w in sorted(words):
    out[w] = out.get(w, 0) + 1
  return out


# ---- sign-in (AOTY-47) ----------------------------------------------------------------------

LASTFM_AUTH_URL = "https://www.last.fm/api/auth/"
_NEXT_CHARS = re.compile(r"[A-Za-z0-9/_.~%?=&+-]{1,512}")


def safe_next(raw, base):
  """Where a sign-in may land: a path under base, else base itself.

  raw is the already-decoded ?next= value. It must start with base (which starts with "/"), and
  may hold no "//" or backslash (either could make a browser read it as another host), no control
  characters or spaces (header splitting), only URL-safe characters, and at most 512 of them.
  """
  if not isinstance(raw, str) or not raw.startswith(base) or "//" in raw or "\\" in raw:
    return base
  return raw if _NEXT_CHARS.fullmatch(raw) else base


def with_query(path, **params):
  """path with params added to (or replacing in) its query string."""
  head, _, query = path.partition("?")
  pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in params]
  return f"{head}?{urlencode(pairs + list(params.items()))}"


def lastfm_auth_url(api_key, public_origin, base, nonce):
  """Last.fm's web-auth page, sending the visitor back to our callback with the nonce in its path.

  The nonce goes in the path, not the query: Last.fm appends ?token=... to cb itself.
  """
  cb = f"{public_origin.rstrip('/')}{base}api/auth/callback/{nonce}"
  return f"{LASTFM_AUTH_URL}?{urlencode({'api_key': api_key, 'cb': cb})}"


def session_username(claims):
  """The username a verified session names, or None if it isn't one the api would accept."""
  u = (claims or {}).get("u")
  return u if isinstance(u, str) and USERNAME_RE.match(u) else None


# Answers for the sync request's metric line (AOTY-41), by status where the body can't tell them
# apart. 503 errors and 200 reasons are already short names.
_STATUS_OUTCOMES = {400: "bad_name", 403: "origin_refused", 404: "unknown_user", 415: "not_json"}


def sync_outcome(status, body, new=False):
  """One word for how a POST sync ended, for the api's EMF line: queued / new_user, a 200
  reason (cooldown, in_progress, paused), a 503 or 429 error (paused, busy, too_many_new,
  lastfm_unavailable, queue_unavailable, unavailable), or one of _STATUS_OUTCOMES."""
  if status == 202:
    return "new_user" if new else "queued"
  if status in _STATUS_OUTCOMES:
    return _STATUS_OUTCOMES[status]
  body = body if isinstance(body, dict) else {}
  return str(body.get("reason") or body.get("error") or status)


def client_ip(headers, source_ip, trusted):
  """The viewer's IP for the per-IP limit.

  Only a request that carried kamp.fm's origin secret came through CloudFront (trusted): then the
  viewer is CloudFront-Viewer-Address (ip:port, set by CloudFront, if forwarded), else the last
  X-Forwarded-For entry that isn't the edge itself (requestContext's sourceIp). Anything earlier
  in X-Forwarded-For is whatever the client sent. Untrusted requests (sandbox, or execute-api
  before the lock is enforced) use sourceIp. headers has lowercase names (payload v2).
  """
  if trusted:
    viewer = (headers.get("cloudfront-viewer-address") or "").strip()
    if viewer:
      return viewer.rsplit(":", 1)[0].strip("[]")
    parts = [p.strip() for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if parts and parts[-1] == source_ip:
      parts.pop()
    if parts:
      return parts[-1]
  return source_ip or ""


def ip_limit_key(ip, hour):
  """The guard table key counting one IP's new names in one hour. IPv6 is grouped by /64, since
  one subscriber usually holds a whole /64. Hashed: the table never stores an address."""
  try:
    addr = ipaddress.ip_address(ip)
    bucket = str(ipaddress.ip_network(f"{ip}/64", strict=False)) if addr.version == 6 else str(addr)
  except ValueError:
    bucket = ip
  return f"ip#{hashlib.sha256(bucket.encode()).hexdigest()[:16]}#{hour}"


def name_tag(name):
  """The short hash of a username that the per-IP set holds."""
  return hashlib.sha256(name.encode()).hexdigest()[:12]


def _last_activity(row):
  return max(row.get("sync_requested_at") or 0, row.get("sync_started_at") or 0)


def _running(row, now, queue_depth=None):
  """A sync is queued or running, and not stuck: a dead one mustn't block the user for good.

  A queued row also counts as live while the sync queue holds messages (queue_depth > 0): in a
  long queue (AOTY-26) a real wait outlasts STUCK_AFTER, and treating it as dead would show the
  page an idle stage and let the next POST queue the user a second time. A row left queued by a
  crash between the update and the send is then freed once the queue drains.
  """
  state = row.get("state")
  if state not in ACTIVE:
    return False
  if now - _last_activity(row) < STUCK_AFTER:
    return True
  return state == "queued" and (queue_depth or 0) > 0


def decide_sync(row, now, cooldown, queue_depth=None):
  """queue, in_progress or cooldown, for a POST sync."""
  if _running(row, now, queue_depth):
    return "in_progress"
  requested = row.get("sync_requested_at") or 0
  if requested and now - requested < cooldown:
    return "cooldown"
  return "queue"


def stage(row, now, queue_depth=None):
  """Where the user's update is: queued, reading, error, building, dating or idle."""
  state = row.get("state")
  if _running(row, now, queue_depth):
    return "queued" if state == "queued" else "reading"
  if state in FAILED:
    return "error"
  finished, built = row.get("sync_finished_at"), row.get("chart_built_at")
  if finished and (not built or built < finished) and now - finished < BUILDING_MAX:
    return "building"
  if (row.get("chart_pending") or 0) > 0 and built and finished and built >= finished:
    if now - built < DATING_FRESH:
      return "dating"
  return "idle"


def new_plays(row):
  """Plays the last sync found that the current chart doesn't have yet; 0 if none or unknown."""
  if row.get("state") != "done" or row.get("chart_plays") is None:
    return 0
  plays = (row.get("progress") or {}).get("plays") or 0
  return max(0, plays - row["chart_plays"])


_WALL_FIELDS = ("artist", "album", "art")


def preview_wall(raw):
  """The cover wall from the users row's preview JSON; [] when missing or malformed."""
  try:
    wall = json.loads(raw).get("wall") if raw else None
  except (ValueError, AttributeError):
    return []
  if not isinstance(wall, list):
    return []
  out = []
  for w in wall:
    if isinstance(w, dict) and all(isinstance(w.get(k), str) for k in _WALL_FIELDS):
      fallbacks = [u for u in w.get("fallbacks") or [] if isinstance(u, str)]
      out.append({**{k: w[k] for k in _WALL_FIELDS}, "fallbacks": fallbacks})
  return out


WAIT_MIN_SAMPLES = 20  # first syncs timed before a wait is estimated: a guess is worse than none


def queue_place(ticket, queue_depth, counters, concurrency):
  """{position, wait_s?} for a queued user (AOTY-26).

  ticket: the users row's queue_ticket, from the guard table's `queue` item's joined counter.
  counters: that item: started (tickets used up by syncs starting, or burnt by the api), and
  first_syncs / first_sync_seconds (clean first syncs, timed by the sync worker).

  position = tickets still ahead, this one included. SQS FIFO doesn't order across users, so it's
  approximate: it's clamped to 1..queue depth, and the page only shows it going down. wait_s is
  how long until this chart is pressed: whole rounds of `concurrency` syncs at the measured mean.
  """
  depth = max(queue_depth or 0, 1)
  position = max(1, min(ticket - (counters.get("started") or 0), depth))
  out = {"position": position}
  n = counters.get("first_syncs") or 0
  if n >= WAIT_MIN_SAMPLES:
    mean = (counters.get("first_sync_seconds") or 0) / n
    out["wait_s"] = math.ceil(math.ceil(position / max(1, concurrency)) * mean)
  return out


def status(row, now, cooldown, queue_depth=None, queue=None, concurrency=2):
  """The status a page polls: stage, progress and timestamps. No usernames beyond the user's own.

  queue: the guard table's queue counters (queue_place), read only while the user is queued.
  """
  progress = row.get("progress") or {}
  total = row.get("chart_albums")
  pending = row.get("chart_pending") or 0
  requested = row.get("sync_requested_at") or 0
  out = {
    "stage": stage(row, now, queue_depth),
    "display_name": row.get("display_name") or row.get("username"),
    "weeks": {"done": progress.get("weeks_done", 0), "total": progress.get("weeks_total", 0)},
    "scrobbles": progress.get("plays", 0),
    "albums": {
      "checked": max(0, total - pending) if total is not None else None,
      "total": total,
      "hits": row.get("chart_hits"),
    },
    "new_plays": new_plays(row),
    "last_synced_at": row.get("last_synced_at"),
    "chart_built_at": row.get("chart_built_at"),
    "cooldown_until": requested + cooldown if requested and now - requested < cooldown else None,
  }
  if progress.get("top_plays"):
    out["so_far"] = {
      "albums": progress.get("albums", 0),
      "top": {"artist": progress.get("top_artist", ""), "album": progress.get("top_album", ""),
              "plays": progress["top_plays"]},
    }  # fmt: skip
  wall = preview_wall(row.get("preview"))
  if wall:
    out["wall"] = wall
  if out["stage"] == "error":
    out["reason"] = row.get("state")
  # The owner's page theme (AOTY-65), so a page with no chart to carry it yet (a first visit)
  # still shows it. Left out for the default or anything that isn't an allowlisted id.
  theme = row.get("theme")
  if themes.valid(theme) and theme != themes.DEFAULT:
    out["theme"] = theme
  ticket = row.get("queue_ticket")
  if out["stage"] == "queued" and ticket is not None and queue is not None:
    out["queue"] = queue_place(ticket, queue_depth, queue, concurrency)
  return out
