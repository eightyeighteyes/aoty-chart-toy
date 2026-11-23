"""The chart builder's pure core (AOTY-21): one user's store + albums items -> chart.json.

No I/O here (handler.py does it), so tests and tools/chart_check.py drive it directly.

Inputs:
  store: the sync worker's weeks.json.gz object (lambdas/sync/sync_core.py has the shape).
  items: {key: albums-table item as plain values} for the store's keys that have one, where
    key is common.albumkey.join_key((artist_key, title_key)), the same key the store uses.

Rules (M0's, CLAUDE.md; user decisions 2026-11-22):
  - Only albums whose item says year == the store's year are ranked. The year comes from the
    albums table at build time, never copied into the store, so mb lane corrections show up.
  - By Track: the user's plays, most first; ties by key (sync_core.ranked_keys' order).
  - By Album: plays / track count, singles left out (common.mbmatch.is_single: the release
    group's type decides; no count at all counts as a single). Ties: plays, then key.
  - Weighted equals By Album for one listener, so single-user charts leave it out (M2 adds it).
  - Each ranking is a strict top TOP_N: albums tied with the last one shown are dropped.
  - Covers: the scrobbles' Last.fm image, else album.getInfo's (the handler looks those up for
    shown albums only, cover_lookups), then that image's other sizes, then the Cover Art
    Archive. CAA has no art for many small (e.g. Bandcamp-only) releases, which Last.fm has.

chart.json (CHART_VERSION 1):
  {"version", "username", "display_name", "year", "generated_at", "pending", "top", "featured",
   "albums": [{"artist", "album", "plays", "tracks", "single", "art", "fallbacks": [...]}],
   "rankings": {"track": [[album index, plays]], "album": [[album index, album plays]]}}
albums holds each album once, only if a ranking shows it, in By Track order.
"""

import time
from collections import Counter

from common.albumkey import base_title
from common.covers import cover
from common.mbmatch import is_single

CHART_VERSION = 1
TOP_N = 100
FEATURED_N = 10
MODES = ("track", "album")
COVER_RETRY_AFTER = 30 * 86400  # re-ask Last.fm about an album it had no cover for


def year_plays(store):
  """{key: plays} over every week in the store (as sync_core.year_plays)."""
  total = Counter()
  for week in store["weeks"].values():
    total.update(week["plays"])
  return {k: n for k, n in total.items() if n > 0}


def pending_keys(plays, items):
  """Keys the user played that have no albums item yet: the mb lane hasn't answered."""
  return sorted(k for k in plays if k not in items)


def year_hits(plays, items, year):
  """How many of the user's played albums are known to be from `year`."""
  return sum(1 for k in plays if (items.get(k) or {}).get("year") == year)


def track_info(item):
  """What is_single reads: the matched release's track count and its group's type."""
  return {"tracks": item.get("tracks"), "type": item.get("tracks_type")}


def rank(plays, items, year, n=TOP_N):
  """{"track": [(key, plays)], "album": [(key, plays / tracks)]}, each a strict top n."""
  dated = [k for k in plays if (items.get(k) or {}).get("year") == year]
  by_track = sorted(((k, plays[k]) for k in dated), key=lambda kv: (-kv[1], kv[0]))
  albums = [k for k in dated if not is_single(track_info(items[k]))]
  by_album = sorted(
    ((k, plays[k] / items[k]["tracks"]) for k in albums), key=lambda kv: (-kv[1], -plays[kv[0]], kv[0])
  )
  return {"track": by_track[:n], "album": by_album[:n]}


def shown_keys(store, items, n=TOP_N):
  """The albums a chart shows (in any ranking), in By Track order."""
  plays = year_plays(store)
  rankings = rank(plays, items, store["year"], n)
  shown = list(dict.fromkeys(k for mode in MODES for k, _ in rankings[mode]))
  return sorted(shown, key=lambda k: (-plays[k], k))


def cover_lookups(shown, store, cache, now):
  """Shown albums that need an album.getInfo cover lookup, in chart order.

  Only albums whose scrobbles carried no cover (the store's "image"), and that the cache
  hasn't answered: a found URL is kept for good, "none" is retried after COVER_RETRY_AFTER.
  cache is {key: {"url": str, "at": uts}}.
  """
  meta = store.get("albums", {})
  out = []
  for k in shown:
    if (meta.get(k) or {}).get("image"):
      continue
    hit = cache.get(k)
    if hit is None or (not hit.get("url") and now - hit.get("at", 0) >= COVER_RETRY_AFTER):
      out.append(k)
  return out


def build_chart(store, items, *, username, display_name, now, n=TOP_N, images=None):
  """(chart dict, pending keys) for one user. display_name falls back to the username.

  images: {key: cover URL} from album.getInfo, used when the store has no image for a key.
  """
  year = store["year"]
  plays = year_plays(store)
  rankings = rank(plays, items, year, n)
  shown = shown_keys(store, items, n)
  index = {k: i for i, k in enumerate(shown)}
  meta = store.get("albums", {})
  images = images or {}
  albums = []
  for k in shown:
    item, m = items[k], meta.get(k, {})
    art, fallbacks = cover(m.get("image") or images.get(k), item.get("rg_mbid"))
    albums.append({
      "artist": m.get("artist") or "",
      "album": base_title(m.get("album") or ""),  # the root album: editions share its key
      "plays": plays[k],
      "tracks": item.get("tracks"),
      "single": is_single(track_info(item)),
      "art": art,
      "fallbacks": fallbacks,
    })  # fmt: skip
  pending = pending_keys(plays, items)
  chart = {
    "version": CHART_VERSION,
    "username": username,
    "display_name": display_name or username,
    "year": year,
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    "pending": len(pending),
    "top": n,
    "featured": FEATURED_N,
    "albums": albums,
    "rankings": {mode: [[index[k], round(s, 3)] for k, s in rankings[mode]] for mode in MODES},
  }
  return chart, pending
