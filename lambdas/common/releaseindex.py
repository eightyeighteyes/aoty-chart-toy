"""The MusicBrainz release index (AOTY-20): format, sharding and lookup. Pure, standard library.

The index holds every dated release group of any year, from the MusicBrainz JSON dump, so
years are data: the same index serves any TARGET_YEAR (and M4's albums of any year).

Layout in the store bucket (written by jobs/index/build.py, read by the mb lane):

  index/current.json                 the manifest, written last: a build is live only once
                                     every shard it names is uploaded
  index/<dump>/<shard>.json.gz       {norm(title): [record, ...]}, SHARD_HEX hex chars of
                                     sha1(norm(title)) per shard name

A record is what common.mbmatch.pick_release_group reads (see `record`), so a lookup runs
the same matching rule as a live search.

Two safety rules:

  - The manifest carries a fingerprint of `norm`. Shards are keyed by norm(title), so if norm
    changes, every lookup would silently miss and send everything to live MusicBrainz searches.
    A reader that sees a different fingerprint must not use the index (`usable`).
  - The dump lags days behind MusicBrainz, so an answer can be stale, not just missing: a dump
    with last year's single "X" but not this year's album "X" returns the single. `lookup`
    marks a hit trusted only when nothing newer could beat it under pick_release_group's
    order: an exact title (tier 0) of type Album. Anything else goes to a live search.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from common.albumkey import base_title, norm
from common.mbmatch import pick_release_group, title_tier

FORMAT = 1
SHARD_HEX = 3  # 4096 shards: ~60 KB gzipped each for ~4.7M records
PREFIX = "index/"
MANIFEST_KEY = f"{PREFIX}current.json"

# Strings that exercise every part of norm (NFKC, casefold, quotes, punctuation, spacing). If
# norm's output for any of them changes, the fingerprint changes and old shards are unusable.
_PROBES = (
  "Taylor’s Version",
  "ＦＵＬＬ－ＷＩＤＴＨ",
  "Straße  “Quoted”",
  "AC/DC — Back in Black!!",
  "( )",
  "Sigur Rós: ágætis byrjun",
  "  spaced\tout\n",
  "don't stop",
)


def fingerprint():
  """A short hash of norm's behaviour plus the format version."""
  h = hashlib.sha256(f"format={FORMAT};shard_hex={SHARD_HEX}".encode())
  for p in _PROBES:
    h.update(b"\0" + norm(p).encode())
  return h.hexdigest()[:16]


def shard_of(title_key):
  """The shard name for a norm(title)."""
  return hashlib.sha1(title_key.encode()).hexdigest()[:SHARD_HEX]


def shard_key(dump, shard):
  return f"{PREFIX}{dump}/{shard}.json.gz"


def record(rg):
  """The fields pick_release_group reads, from a dump release-group object."""
  return {
    "id": rg["id"],
    "title": rg.get("title", ""),
    "first-release-date": rg.get("first-release-date") or "",
    "primary-type": rg.get("primary-type"),
    "artist-credit": [
      {"name": c.get("name", ""), "joinphrase": c.get("joinphrase", "")} for c in rg.get("artist-credit", [])
    ],
  }


def is_dated(rg):
  """pick_release_group skips release groups without at least a year, so the index does too."""
  return len(rg.get("first-release-date") or "") >= 4


def manifest(dump, *, built_at, records, titles, shards):
  return {
    "format": FORMAT,
    "fingerprint": fingerprint(),
    "dump": dump,
    "built_at": int(built_at),
    "records": records,
    "titles": titles,
    "shards": shards,
  }


def dump_age_hours(dump, now):
  """Hours since a dump was made, from its folder name (20261121-001001, UTC); None if unparsable."""
  try:
    made = datetime.strptime(dump, "%Y%m%d-%H%M%S").replace(tzinfo=UTC).timestamp()
  except (TypeError, ValueError):
    return None
  return round((now - made) / 3600, 1)


def usable(m):
  """True if a manifest was written by a compatible builder (same format and norm)."""
  return isinstance(m, dict) and m.get("format") == FORMAT and m.get("fingerprint") == fingerprint()


@dataclass(frozen=True)
class Hit:
  year: str
  id: str
  type: str | None
  trusted: bool
  candidate_ids: frozenset = frozenset()  # every release group the index had for these titles

  def as_match(self):
    """The shape mb_core keeps for a year: pick_release_group's."""
    return {"year": self.year, "id": self.id, "type": self.type}


def title_keys(album):
  """The norm(title)s a Last.fm title can match: exact, then edition-stripped."""
  keys = [norm(album)]
  stripped = norm(base_title(album))
  if stripped != keys[0]:
    keys.append(stripped)
  return [k for k in keys if k]


def lookup(artist, album, load_shard):
  """The index's answer for an album: a Hit, or None when it has no match.

  load_shard(shard) -> {norm(title): [records]}; it may raise, and so may this.
  None means "unknown", never "not this year": the dump lags, so the album may be too new.
  """
  candidates = []
  for key in title_keys(album):
    candidates += load_shard(shard_of(key)).get(key, [])
  match = pick_release_group(artist, album, candidates)
  if not match:
    return None
  chosen = next(rg for rg in candidates if rg["id"] == match["id"])
  trusted = title_tier(album, chosen["title"]) == 0 and match["type"] == "Album"
  ids = frozenset(rg["id"] for rg in candidates)
  return Hit(year=match["year"], id=match["id"], type=match["type"], trusted=trusted, candidate_ids=ids)
