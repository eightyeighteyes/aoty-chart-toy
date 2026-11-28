"""A group's chart (AOTY-50, beta): members' stores + albums items -> one group chart.json.

No I/O here (handler.py does it). By Play and Weighted are M0's rules
(build_page.score_rankings), so a group of M0's members ranks as M0's page did there. Modes
(user decision 2026-11-25, AOTY-63: By Track is gone, and with it singles; one play makes a
listener):
  - Only albums whose albums item says year == the year are ranked; singles never are
    (common.mbmatch.is_single).
  - By Listener ("listener"): unique listeners, then plays / tracks.
  - By Play ("album"): plays / tracks, then listeners.
  - Weighted: plays / tracks / listeners, only with at least WEIGHTED_MIN_LISTENERS listeners.
  - Each ranking is the top n. A member's own view is ranked in the page (their plays /
    tracks, whatever the mode), so the chart also keeps every album in any member's top n by
    that (ties at the cutoff kept, as M0's page_data did).
  Ties after the score go to the key, so a build is deterministic.

Only members whose sync has finished are ranked ("ready"); the rest are counted, so the page
can say "3 of 8 still loading".

group chart.json (version 1):
  {"version", "kind": "group", "id", "name", "year", "generated_at", "top", "featured",
   "members": [usernames, ready ones, page order], "waiting": n, "unavailable": n,
   "pending": albums not yet dated, "pending_top": those in some member's top 150,
   "weighted_min_listeners",
   "albums": [{"artist", "album", "lastfm", "plays", "listeners", "by": {member index: plays},
               "tracks", "single", "art", "fallbacks": [...]}],
   "rankings": {"listener" | "album" | "weighted": [[album index, score]]},
   "theme"?: the owner's page theme, a common.themes id; absent for the default (AOTY-76),
   "background"?: the page's background, a common.backgrounds engine; absent for none (AOTY-96)}
"lastfm" is the Last.fm album name the page links to, as in chart_core (AOTY-43): the first
member's, in page order, whose store has one, so a build is deterministic.
Charts built before AOTY-63 have "track" instead of "listener"; the page falls back to "album".
"""

import time
from collections import Counter

import chart_core

from common.albumkey import base_title
from common.covers import cover
from common.mbmatch import is_single

GROUP_VERSION = 1
WEIGHTED_MIN_LISTENERS = 3  # as M0 (build_page.WEIGHTED_MIN_LISTENERS)
MODES = ("listener", "album", "weighted")


def aggregate(stores, items, year):
  """{key: {"plays", "by": {member: plays}}} over the members' dated albums. stores: {member: store}."""
  out = {}
  for member, store in stores.items():
    for k, n in chart_core.year_plays(store).items():
      if (items.get(k) or {}).get("year") != year:
        continue
      a = out.setdefault(k, {"plays": 0, "by": {}})
      a["plays"] += n
      a["by"][member] = n
  return out


def rank(stats, items, n=chart_core.TOP_N):
  """{mode: [(key, score)]}, each the top n."""

  def listeners(k):
    return len(stats[k]["by"])

  albums = [k for k in stats if not is_single(chart_core.track_info(items[k]))]
  per_track = {k: stats[k]["plays"] / items[k]["tracks"] for k in albums}
  ranked = {
    "listener": sorted(
      ((k, per_track[k]) for k in albums), key=lambda kv: (-listeners(kv[0]), -kv[1], kv[0])
    ),
    "album": sorted(((k, per_track[k]) for k in albums), key=lambda kv: (-kv[1], -listeners(kv[0]), kv[0])),
    "weighted": sorted(
      ((k, per_track[k] / listeners(k)) for k in albums if listeners(k) >= WEIGHTED_MIN_LISTENERS),
      key=lambda kv: (-kv[1], -listeners(kv[0]), kv[0]),
    ),
  }
  return {mode: pairs[:n] for mode, pairs in ranked.items()}


def member_keys(stats, items, members, n=chart_core.TOP_N):
  """Every album in some member's top n by their plays per track, ties kept."""
  albums = {k for k in stats if not is_single(chart_core.track_info(items[k]))}
  keep = set()
  for who in members:
    pool = [k for k in stats if who in stats[k]["by"] and k in albums]

    def score(k, who=who):
      return stats[k]["by"][who] / items[k]["tracks"]

    ordered = sorted(pool, key=score, reverse=True)
    cutoff = score(ordered[n - 1]) if len(ordered) > n else float("-inf")
    keep.update(k for k in ordered if score(k) >= cutoff)
  return keep


def build_group_chart(stores, items, *, group_id, name, year, waiting=0, unavailable=0, now,
                      n=chart_core.TOP_N, images=None, theme=None, background=None):  # fmt: skip
  """(chart dict, pending keys). stores: {member: store} for the ready members only.

  images: {key: cover URL} (the members' album.getInfo caches), used when no store has one.
  theme: the group meta's page theme (AOTY-76), published as "theme" unless it's the default.
  background: the meta's background (AOTY-96), published as "background" when an engine.
  """
  members = sorted(stores, key=str.casefold)  # the page's picklist order (M0, 2026-11-20)
  stats = aggregate(stores, items, year)
  rankings = rank(stats, items, n)
  shown = {k for pairs in rankings.values() for k, _ in pairs} | member_keys(stats, items, members, n)
  order = sorted(shown, key=lambda k: (-len(stats[k]["by"]), -stats[k]["plays"], k))  # listeners, then plays
  index = {k: i for i, k in enumerate(order)}
  member_index = {m: i for i, m in enumerate(members)}
  meta = {}
  for member in members:  # page order, so the names and covers chosen don't depend on dict order
    for k, m in (stores[member].get("albums") or {}).items():
      prev = meta.setdefault(k, {})
      for field in ("artist", "album", "image"):
        prev[field] = prev.get(field) or m.get(field)
  images = images or {}
  albums = []
  for k in order:
    item, m = items[k], meta.get(k, {})
    art, fallbacks = cover(m.get("image") or images.get(k), item.get("rg_mbid"))
    albums.append({
      "artist": m.get("artist") or "",
      "album": base_title(m.get("album") or ""),  # the root album: editions share its key
      "lastfm": m.get("album") or "",  # Last.fm's name, for the album's link (AOTY-43)
      "plays": stats[k]["plays"],
      "listeners": len(stats[k]["by"]),
      "by": {str(member_index[w]): p for w, p in sorted(stats[k]["by"].items(), key=lambda wp: member_index[wp[0]])},
      "tracks": item.get("tracks"),
      "single": is_single(chart_core.track_info(item)),
      "art": art,
      "fallbacks": fallbacks,
    })  # fmt: skip
  played = Counter()
  for store in stores.values():
    played.update(chart_core.year_plays(store))
  pending = chart_core.pending_keys(played, items)
  # Undated albums in any ready member's top 150 (what sync dates first): the page says "still
  # checking" while these remain, then "still counting deep cuts" for the cold tail, which can
  # still surface a lightly played album many members share (listeners rank first).
  top = set()
  for store in stores.values():
    top.update(chart_core.top_keys(chart_core.year_plays(store)))
  pending_top = sum(1 for k in top if k not in items)
  chart = {
    "version": GROUP_VERSION,
    "kind": "group",
    "id": group_id,
    "name": name,
    "year": year,
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    "top": n,
    "featured": chart_core.FEATURED_N,
    "members": members,
    "waiting": waiting,
    "unavailable": unavailable,
    "pending": len(pending),
    "pending_top": pending_top,
    "weighted_min_listeners": WEIGHTED_MIN_LISTENERS,
    "albums": albums,
    "rankings": {mode: [[index[k], round(s, 3)] for k, s in rankings[mode]] for mode in MODES},
  }
  return chart_core.with_background(chart_core.with_theme(chart, theme), background), pending
