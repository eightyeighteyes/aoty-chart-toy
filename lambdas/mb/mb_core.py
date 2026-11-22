"""The mb lane's pure core (AOTY-19): albums in, albums-table items out. No AWS, no HTTP.

The handler passes in the I/O as callables:

  search(entity, query, limit) -> list | None   one MusicBrainz search; None = failed
  lastfm_tracks(artist, album) -> int | None    Last.fm tracklist length; raises on failure
  year_from_index(album) -> Hit | None          the AOTY-20 release index (common.releaseindex);
                                                may raise, which counts as an index error

and gets back `Result(items, failed, stats)`. An album is either resolved (an item, written
and never looked up again) or failed (nothing written, so a later run retries it). A
confirmed miss is resolved, with year None, exactly like M0's null cache entries.

Lookups follow M0 (aoty.lookup_years, build_page.lookup_releases), so the M0 cold-run set
gets the same answers:
  0. The release index, when there is one. Only a trusted hit is used: an untrusted one, a
     miss or an error goes on to the live search, because the dump lags (releaseindex).
  1. Year: one release-group search per MB_BATCH_SIZE albums, then one search per miss.
  2. Track count, only for albums of the target year (as M0): one release search per
     MB_TRACK_BATCH_SIZE albums, one per miss, then Last.fm's tracklist. A re-check
     (`previous` holds the stored item) keeps the stored count when the release group is
     the same, so correcting a year costs no track lookup.
An album whose year was found but whose track lookup failed is failed as a whole; the
`year_wasted` stat counts how often that costs a repeated year lookup.

Queries use the Last.fm display names; items are keyed by the message's own keys, never
recomputed, because the sync worker looks them up by those exact keys.
"""

from dataclasses import dataclass, field

from common.mbmatch import pick_release, pick_release_group, release_clause, rg_clause

MB_BATCH_SIZE = 20  # release groups per search (AOTY-10: fastest of 10/20/40)
MB_TRACK_BATCH_SIZE = 10  # releases per search: each album has many editions
RG_LIMIT, RG_RETRY_LIMIT = 100, 25
REL_LIMIT, REL_RETRY_LIMIT = 100, 50


@dataclass
class Result:
  items: dict = field(default_factory=dict)  # key -> albums-table item
  failed: set = field(default_factory=set)  # keys to leave on the queue
  stats: dict = field(default_factory=dict)


def key_of(album):
  return album["artist_key"], album["title_key"]


def resolve(albums, *, search, lastfm_tracks, target_year, now, year_from_index=None, previous=None):
  """Resolve a batch of queue messages (dicts with artist_key, title_key, artist, album, mbid).

  previous: key -> the stored albums-table item (plain values), for re-checked albums.
  """
  unique = {}
  for a in albums:
    unique.setdefault(key_of(a), a)  # the first message for a key wins; order is kept
  albums = list(unique.values())
  previous = previous or {}
  stats = {"albums": len(albums), "index_hits": 0, "index_untrusted": 0, "index_misses": 0,
           "index_errors": 0, "year_wasted": 0, "tracks_reused": 0}  # fmt: skip
  years, failed = {}, set()

  todo = []
  for a in albums:
    hit = None
    if year_from_index:
      try:
        hit = year_from_index(a)
      except Exception:
        stats["index_errors"] += 1
      else:
        stats["index_misses" if hit is None else "index_hits" if hit.trusted else "index_untrusted"] += 1
    if hit and hit.trusted:
      years[key_of(a)] = hit.as_match()
    else:
      todo.append(a)
  _lookup_years(todo, search, years, failed)

  tracks = {}
  want_tracks = [a for a in albums if (years.get(key_of(a)) or {}).get("year") == str(target_year)]
  for a in want_tracks:
    prev = previous.get(key_of(a))
    if prev and "tracks" in prev and prev.get("rg_mbid") and prev["rg_mbid"] == years[key_of(a)].get("id"):
      tracks[key_of(a)] = {"tracks": prev["tracks"], "type": prev.get("tracks_type"),
                           "source": prev.get("tracks_source")}  # fmt: skip
      stats["tracks_reused"] += 1
  need_tracks = [a for a in want_tracks if key_of(a) not in tracks]
  _lookup_tracks(need_tracks, search, lastfm_tracks, tracks, failed)
  stats["year_wasted"] = sum(1 for a in need_tracks if key_of(a) in failed)

  result = Result(failed=failed, stats=stats)
  for a in albums:
    k = key_of(a)
    if k in failed or k not in years:
      continue
    result.items[k] = build_item(a, years[k], tracks.get(k), now)
  stats.update(
    resolved=len(result.items),
    missed=sum(1 for i in result.items.values() if i["year"] is None),
    failed=len(failed),
  )
  return result


def _lookup_years(albums, search, years, failed):
  """years[key] = pick_release_group's match, or {} for a confirmed miss; failures -> failed."""
  misses = []
  for i in range(0, len(albums), MB_BATCH_SIZE):
    batch = albums[i : i + MB_BATCH_SIZE]
    rgs = search("release-group", " OR ".join(rg_clause(a["artist"], a["album"]) for a in batch), RG_LIMIT)
    for a in batch:
      match = pick_release_group(a["artist"], a["album"], rgs or [])
      if match:
        years[key_of(a)] = match
      else:
        misses.append(a)  # a failed batch retries every album on its own, as in M0

  # A batch can miss an album whose near-misses crowded it out of the results, so give each
  # miss its own search before concluding there's no match.
  for a in misses:
    rgs = search("release-group", rg_clause(a["artist"], a["album"]), RG_RETRY_LIMIT)
    if rgs is None:
      failed.add(key_of(a))
    else:
      years[key_of(a)] = pick_release_group(a["artist"], a["album"], rgs) or {}


def _lookup_tracks(albums, search, lastfm_tracks, tracks, failed):
  """tracks[key] = {"tracks", "type", "source"}; failures -> failed."""
  misses = []
  for i in range(0, len(albums), MB_TRACK_BATCH_SIZE):
    batch = albums[i : i + MB_TRACK_BATCH_SIZE]
    rels = search("release", " OR ".join(release_clause(a["artist"], a["album"]) for a in batch), REL_LIMIT)
    for a in batch:
      info = pick_release(a["artist"], a["album"], rels or [])
      if info:
        tracks[key_of(a)] = {**info, "source": "musicbrainz"}
      else:
        misses.append(a)

  for a in misses:
    rels = search("release", release_clause(a["artist"], a["album"]), REL_RETRY_LIMIT)
    if rels is None:
      failed.add(key_of(a))
      continue
    info = pick_release(a["artist"], a["album"], rels)
    if info:
      tracks[key_of(a)] = {**info, "source": "musicbrainz"}
      continue
    try:
      count = lastfm_tracks(a["artist"], a["album"])
    except Exception:
      failed.add(key_of(a))
      continue
    tracks[key_of(a)] = {"tracks": count, "type": None, "source": "lastfm" if count else None}


def build_item(album, rg, tracks, now):
  """The albums-table item, as plain Python (the handler converts it to DynamoDB types).

  year is an int, or None for a confirmed miss. tracks / tracks_type / tracks_source exist
  only for target-year albums: tracks_type is the matched release's group type, which is
  what the singles rule reads (common.mbmatch.is_single).
  """
  item = {
    "artist_key": album["artist_key"],
    "title_key": album["title_key"],
    "artist": album.get("artist", ""),
    "album": album.get("album", ""),
    "year": int(rg["year"]) if rg.get("year") else None,
    "rg_mbid": rg.get("id") or None,
    "primary_type": rg.get("type"),
    "lastfm_mbid": album.get("mbid") or None,
    "looked_up_at": int(now),
  }
  if tracks is not None:
    item.update(tracks=tracks["tracks"], tracks_type=tracks["type"], tracks_source=tracks["source"])
  return item
