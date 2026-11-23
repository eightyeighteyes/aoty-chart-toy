"""The sync worker's pure core: week ranges, what to fetch, merging, mb queue selection, covers.

No I/O here (handler.py does it), so tests and tools/sync_check.py drive it directly.

Data shape (AOTY-11 spike): per-(album, chart week) play totals from user.getWeeklyAlbumChart,
which groups by album artist like M0's getTopAlbums. One store object per user and year:

  {"version": 1, "year": 2026, "last_seen_uts": int | None, "full_started_at"?: uts,
   "weeks":  {"<from_uts>": {"to": uts, "fetched_at": uts, "plays": {key: n}}},
   "albums": {key: {"artist", "album", "mbid", "image", "queued_at"?, "resolved"?}}}

key is common.albumkey.join_key((artist_key, title_key)), the albums table's identity.
"""

import calendar
from collections import Counter

from common.albumkey import album_key, is_complete, join_key, split_key
from common.covers import PLACEHOLDER, cover, image_url  # noqa: F401  (PLACEHOLDER: the chart builder's)

STORE_VERSION = 1
DAY = 86400
WEEK = 7 * DAY
# Last.fm chart weeks run Sunday 12:00 UTC to Sunday 12:00 UTC (getWeeklyChartList).
_BOUNDARY_ANCHOR = calendar.timegm((2026, 1, 4, 12, 0, 0))  # a Sunday, 12:00 UTC

# A week is final once fetched at least this long after it closed. Until then every sync
# re-fetches and overwrites it, which picks up late and offline scrobbles. With syncs
# running often, that's the current week plus the last 3 closed ones.
SETTLE = 3 * WEEK
FULL_RESYNC_AFTER = 30 * DAY
CAP_ROWS = 950  # responses are capped at ~1,000 rows; at or above this, split the range
MIN_SPLIT = DAY
HOT_N = 150  # the user's top albums by plays go to mb-hot; the rest to mb-cold
REQUEUE_AFTER = 7 * DAY


class CapError(Exception):
  """A range of MIN_SPLIT or less still hit the row cap."""


def year_bounds(year):
  return calendar.timegm((year, 1, 1, 0, 0, 0)), calendar.timegm((year + 1, 1, 1, 0, 0, 0))


def week_ranges(year, now):
  """The year's chart ranges up to now: [(from, to)], to exclusive.

  The first runs from Jan 1 00:00 UTC to the first Sunday 12:00 boundary and the last ends
  at the year end, so the ranges tile the year exactly. A range's `to` is its full end even
  for the current partial week (clip to now when fetching): the bucket's identity is `from`.
  """
  start, end = year_bounds(year)
  b = _BOUNDARY_ANCHOR + ((start - _BOUNDARY_ANCHOR) // WEEK + 1) * WEEK  # first boundary > start
  stop = min(now, end)
  ranges, frm = [], start
  while frm < stop:
    to = min(b, end)
    ranges.append((frm, to))
    frm, b = to, b + WEEK
  return ranges


def new_store(year):
  return {"version": STORE_VERSION, "year": year, "last_seen_uts": None, "weeks": {}, "albums": {}}


def weeks_to_fetch(store, ranges, full_since=None):
  """Ranges never fetched or not yet settled (fetched less than SETTLE after closing).

  full_since: a full re-sync's start time. Every week fetched before it is refetched too, so a
  full pass that was killed resumes where it stopped instead of starting over.
  """
  out = []
  for frm, to in ranges:
    week = store["weeks"].get(str(frm))
    if (
      week is None
      or week["fetched_at"] < to + SETTLE
      or (full_since is not None and week["fetched_at"] < full_since)
    ):
      out.append((frm, to))
  return out


def wants_full_resync(last_full_sync_at, now):
  return last_full_sync_at is None or now - last_full_sync_at > FULL_RESYNC_AFTER


def full_pass_start(store, now):
  """When the current full pass began: kept across retries, restarted if a day stale."""
  started = store.get("full_started_at")
  if not started or now - started > DAY:
    started = now
  store["full_started_at"] = started
  return started


def fetch_range(fetch, frm, to):
  """fetch(frm, to) -> rows, splitting in halves while a response looks capped."""
  rows = fetch(frm, to)
  if len(rows) < CAP_ROWS:
    return rows
  if to - frm <= MIN_SPLIT:
    raise CapError(f"{len(rows)} rows for a {to - frm} s range")
  mid = frm + (to - frm) // 2
  return fetch_range(fetch, frm, mid) + fetch_range(fetch, mid, to)


def _text(v):
  if isinstance(v, dict):
    return v.get("#text") or v.get("name") or ""
  return v or ""


def chart_plays(rows):
  """Chart rows -> ({key: plays}, {key: meta}). Editions that share a key are summed."""
  plays, meta = Counter(), {}
  for row in rows:
    artist, album = _text(row.get("artist")), row.get("name") or ""
    n = int(row.get("playcount") or 0)
    pair = album_key(artist, album)
    if not is_complete(pair) or n <= 0:  # a blank artist or album name can't be keyed
      continue
    key = join_key(pair)
    plays[key] += n
    m = meta.setdefault(key, {"artist": artist, "album": album, "mbid": ""})
    m["mbid"] = m["mbid"] or row.get("mbid") or ""
  return dict(plays), meta


def merge_week(store, rng, plays, meta, now):
  """Overwrite one week's bucket and fill album metadata (never clearing what's known)."""
  frm, to = rng
  store["weeks"][str(frm)] = {"to": to, "fetched_at": now, "plays": plays}
  for key, m in meta.items():
    known = store["albums"].setdefault(key, {"artist": "", "album": "", "mbid": "", "image": ""})
    known["artist"] = known["artist"] or m["artist"]
    known["album"] = known["album"] or m["album"]
    known["mbid"] = known["mbid"] or m["mbid"]


def year_plays(store):
  total = Counter()
  for week in store["weeks"].values():
    total.update(week["plays"])
  return total


def ranked_keys(store):
  """Album keys by the user's year plays, most first; ties by key so the order is stable."""
  return [k for k, _ in sorted(year_plays(store).items(), key=lambda kv: (-kv[1], kv[0]))]


def mb_candidates(store, now):
  """Keys in play order that may still need the mb lane.

  Skips albums already in the albums table ("resolved", permanent: a confirmed miss is an
  item too) and those queued within REQUEUE_AFTER, so a sync doesn't re-check thousands of
  keys or flood mb-hot with duplicates while AOTY-19 catches up.

  Also skips keys with an empty half, which the albums table can't hold. Since album_key's
  punctuation fallback they aren't created, but a store written before it may still have them.
  """
  albums = store["albums"]

  def wanted(k):
    a = albums.get(k, {})
    if a.get("resolved") or not is_complete(split_key(k)):
      return False
    return now - (a.get("queued_at") or 0) >= REQUEUE_AFTER

  return [k for k in ranked_keys(store) if wanted(k)]


def split_hot_cold(store, unresolved):
  """(hot, cold): unresolved keys in the user's top HOT_N go hot, the rest cold, in play order."""
  ranked, wanted = ranked_keys(store), set(unresolved)
  top = set(ranked[:HOT_N])
  order = [k for k in ranked if k in wanted]
  return [k for k in order if k in top], [k for k in order if k not in top]


# ---- covers from getRecentTracks ---------------------------------------------------------


def dated(tracks):
  """Drop the now-playing row: it has no date and ignores from/to."""
  return [t for t in tracks if (t.get("date") or {}).get("uts")]


def newest_uts(tracks):
  return max((int(t["date"]["uts"]) for t in dated(tracks)), default=None)


def apply_covers(store, tracks):
  """Fill missing album covers from recent tracks: album MBID first, then the album key.

  Scrobbles carry the track artist, not the album artist, so compilations and featured-artist
  tracks rarely match by key; the chart builder's album.getInfo fallback covers those for
  albums a chart shows (AOTY-21). Returns the
  number of albums given a cover.
  """
  albums = store["albums"]
  by_mbid = {a["mbid"]: k for k, a in albums.items() if a.get("mbid")}
  filled = 0
  for t in dated(tracks):
    image = image_url(t.get("image"))
    if not image:
      continue
    album = t.get("album") or {}
    mbid = album.get("mbid") if isinstance(album, dict) else ""
    key = by_mbid.get(mbid) if mbid else None
    if key is None:
      key = join_key(album_key(_text(t.get("artist")), _text(album)))
    entry = albums.get(key)
    if entry is not None and not entry.get("image"):
      entry["image"] = image
      filled += 1
  return filled


def missing_top_covers(store, n=HOT_N):
  albums = store["albums"]
  return sum(1 for k in ranked_keys(store)[:n] if not albums.get(k, {}).get("image"))


# ---- first-visit preview (AOTY-24) ------------------------------------------------------

WALL_N = 60


def wall_from_tracks(tracks, n=WALL_N):
  """The cover wall a first visit shows while weeks are read: distinct albums, newest first.

  [{"artist", "album", "art", "fallbacks"}], at most n. Keyed by the track artist (all a
  scrobble carries), so a compilation can appear more than once; that's fine for a wall.
  Albums with no cover are kept: the page shows the artist-name tile for them.
  """
  out, seen = [], set()
  for t in dated(tracks):
    artist, album = _text(t.get("artist")), _text(t.get("album"))
    pair = album_key(artist, album)
    if not is_complete(pair) or pair in seen:
      continue
    seen.add(pair)
    art, fallbacks = cover(image_url(t.get("image")), "")
    out.append({"artist": artist, "album": album, "art": art, "fallbacks": fallbacks})
    if len(out) >= n:
      break
  return out


def running_stats(store):
  """{albums, top_artist, top_album, top_plays} over every play read so far, any release year."""
  plays = year_plays(store)
  stats = {
    "albums": sum(1 for n in plays.values() if n > 0),
    "top_artist": "",
    "top_album": "",
    "top_plays": 0,
  }
  ranked = ranked_keys(store)
  if ranked:
    a = store["albums"].get(ranked[0], {})
    stats.update(top_artist=a.get("artist", ""), top_album=a.get("album", ""), top_plays=plays[ranked[0]])
  return stats
