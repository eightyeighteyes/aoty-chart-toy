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
"""

import hashlib
import ipaddress
import json
import math
import re
from urllib.parse import unquote

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")  # as the sync worker's
ACTIVE = frozenset({"queued", "syncing", "retrying"})
FAILED = frozenset({"error", "not_found", "private"})
STUCK_AFTER = 30 * 60  # a sync that hasn't moved this long is dead (OOM, timeout, lost message)
BUILDING_MAX = 5 * 60  # the chart builder runs within seconds of a sync; past this, give up waiting
DATING_FRESH = 10 * 60  # charts rebuild about once a minute while albums resolve
_USER_ROUTE = re.compile(r"api/users/([^/]+)/(sync|status)/?")


def route(method, path, base):
  """(action, username) for a request: action is health, sync, status, method or not_found.

  username is lowercased but not yet validated (the caller answers 400 for a bad one).
  """
  if not path.startswith(base):
    return "not_found", None
  rest = path[len(base) :]
  if rest == "api/health":
    return ("health", None) if method in ("GET", "HEAD") else ("method", None)
  m = _USER_ROUTE.fullmatch(rest)
  if not m:
    return "not_found", None
  action = m.group(2)
  if method != ("POST" if action == "sync" else "GET"):
    return "method", None
  return action, unquote(m.group(1)).strip().lower()


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
  ticket = row.get("queue_ticket")
  if out["stage"] == "queued" and ticket is not None and queue is not None:
    out["queue"] = queue_place(ticket, queue_depth, queue, concurrency)
  return out
