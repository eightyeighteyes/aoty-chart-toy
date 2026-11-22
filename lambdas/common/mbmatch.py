"""MusicBrainz matching rules shared by M0 (aoty.py, build_page.py) and the mb Lambda (AOTY-19).

Pure functions over MusicBrainz search results: no I/O, standard library only. M0 re-exports
these under their old names, so the two can't drift. The lessons behind each rule are in
CLAUDE.md ("MusicBrainz — hard-won lessons").
"""

from common.albumkey import base_title, norm

# Singles are left out of the per-album rankings (user decision 2026-11-20). MusicBrainz's
# release-group type decides, whatever the length; only an untyped release (a Last.fm fallback
# count) falls back to "under MIN_ALBUM_TRACKS".
MIN_ALBUM_TRACKS = 4

TYPE_RANK = {"Album": 0, "EP": 1}
STATUS_RANK = {"Official": 0}


def lucene_escape(s):
  """Escape chars that would break a quoted Lucene phrase (e.g. an album with a " in it)."""
  return s.replace("\\", "\\\\").replace('"', '\\"')


def rg_clause(artist, album):
  """One album's clause in a release-group search (query with the edition-stripped title)."""
  e = lucene_escape
  return f'(artist:"{e(artist)}" AND releasegroup:"{e(base_title(album))}")'


def release_clause(artist, album):
  """One album's clause in a release search (for track counts)."""
  e = lucene_escape
  return f'(artist:"{e(artist)}" AND release:"{e(base_title(album))}")'


def credit_names(entity):
  """Every way the entity's artist credit can be read: the joined credit and each name."""
  credits = entity.get("artist-credit", [])
  joined = "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits)
  return {norm(joined)} | {norm(c.get("name", "")) for c in credits}


def title_tier(album, title):
  """How a MusicBrainz title matches a Last.fm one: 0 exactly, 1 edition-stripped, None not at all.

  Callers sort on this first, so an exact match always beats a stripped one. Without that,
  "1989 (Taylor's Version)" (stripped to "1989") took the 2014 "1989" release group's year
  over its own 2023 one. Deluxe editions still resolve to the original: MusicBrainz keeps them
  in the original release group, under the original title.
  """
  have = norm(title)
  if have == norm(album):
    return 0
  if have == norm(base_title(album)):
    return 1
  return None


def pick_release_group(artist, album, release_groups):
  """{"year", "id", "type"} of the release group matching this artist + title, or None.

  A search hit is not a match: the phrase "1989" also hits "1989 [nightcore]", so compare
  the titles ourselves. An exact title beats an edition-stripped one (title_tier); then, when
  several match (an album and a same-named single, say), prefer Album, then EP, then the
  earliest date.
  """
  want_artist = norm(artist)
  matches = []
  for i, rg in enumerate(release_groups):
    date = rg.get("first-release-date", "")
    if len(date) < 4:
      continue
    tier = title_tier(album, rg.get("title", ""))
    if tier is None:
      continue
    if want_artist not in credit_names(rg):
      continue
    # i breaks exact ties in search order, so the dicts below never get compared.
    matches.append((tier, TYPE_RANK.get(rg.get("primary-type"), 2), date, i, rg))
  if not matches:
    return None
  *_, date, _, rg = min(matches)
  return {"year": date[:4], "id": rg.get("id", ""), "type": rg.get("primary-type") or None}


def pick_year(artist, album, release_groups):
  """Year (a string) of the release group matching this artist + title, or None."""
  match = pick_release_group(artist, album, release_groups)
  return match["year"] if match else None


def pick_release(artist, album, releases):
  """{"tracks", "type"} of the release matching this artist + title, or None.

  Same matching rule as pick_release_group. Among matches, prefer Official, then a release
  group typed Album > EP > other, then the earliest date, which is usually the standard
  edition rather than a later deluxe.
  """
  want_artist = norm(artist)
  matches = []
  for rel in releases:
    count = rel.get("track-count")
    if not count:
      continue
    tier = title_tier(album, rel.get("title", ""))
    if tier is None:
      continue
    if want_artist not in credit_names(rel):
      continue
    rg_type = rel.get("release-group", {}).get("primary-type")
    matches.append(
      (
        tier,
        STATUS_RANK.get(rel.get("status"), 1),
        TYPE_RANK.get(rg_type, 2),
        rel.get("date") or "9999",
        count,
        rg_type or "",
      )
    )
  if not matches:
    return None
  *_, tracks, rg_type = min(matches)
  return {"tracks": tracks, "type": rg_type or None}


def is_single(info):
  """MusicBrainz's type decides: a 1-track Album (The Necks, Dopesmoker) is an album.
  The track floor applies only when there's no type, i.e. a Last.fm fallback count."""
  if not info["tracks"]:
    return True  # no count: can't be scored per album
  if info["type"]:
    return info["type"] == "Single"
  return info["tracks"] < MIN_ALBUM_TRACKS
