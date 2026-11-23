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
"""

import json
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


def status(row, now, cooldown, queue_depth=None):
  """The status a page polls: stage, progress and timestamps. No usernames beyond the user's own."""
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
  if out["stage"] == "queued" and queue_depth is not None:
    out["queue_ahead_approx"] = queue_depth  # the whole sync queue's depth, not this user's place
  return out
