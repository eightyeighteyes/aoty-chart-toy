"""The release index format and lookup (lambdas/common/releaseindex.py)."""

import pytest

from common import releaseindex as ri
from common.albumkey import norm


def rg(title, date, type_="Album", artist="Artist", id_=None):
  return ri.record(
    {
      "id": id_ or f"{title}-{date}-{type_}",
      "title": title,
      "first-release-date": date,
      "primary-type": type_,
      "secondary-types": ["Live"],  # dropped: pick_release_group doesn't read it
      "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": "x"}}],
    }
  )


def shards_of(*records):
  """The shards a builder would write for these records."""
  shards = {}
  for r in records:
    key = norm(r["title"])
    shards.setdefault(ri.shard_of(key), {}).setdefault(key, []).append(r)
  return shards


def loader(*records):
  shards = shards_of(*records)
  loaded = []

  def load(shard):
    loaded.append(shard)
    return shards.get(shard, {})

  load.loaded = loaded
  return load


def test_record_keeps_only_what_matching_reads():
  r = rg("X", "2026-01-02")
  assert set(r) == {"id", "title", "first-release-date", "primary-type", "artist-credit"}
  assert r["artist-credit"] == [{"name": "Artist", "joinphrase": ""}]


def test_undated_release_groups_are_not_indexed():
  assert ri.is_dated({"first-release-date": "2026"})
  assert not ri.is_dated({"first-release-date": ""})
  assert not ri.is_dated({})


def test_shard_names_are_stable_hex():
  assert ri.shard_of("weezer") == ri.shard_of("weezer")
  assert len(ri.shard_of("weezer")) == ri.SHARD_HEX
  int(ri.shard_of("weezer"), 16)
  assert ri.shard_key("20261121-001001", "abc") == "index/20261121-001001/abc.json.gz"


def test_an_album_hit_with_an_exact_title_is_trusted():
  hit = ri.lookup("Artist", "Old", loader(rg("Old", "1994-05-10")))
  assert (hit.year, hit.type, hit.trusted) == ("1994", "Album", True)
  assert hit.as_match() == {"year": "1994", "id": "Old-1994-05-10-Album", "type": "Album"}


def test_a_stale_dump_single_is_not_trusted():
  # The dump has last year's lead single "X" but not this year's album "X" yet: a live search
  # would find the album and prefer it, so the single must not be taken as the answer.
  hit = ri.lookup("Artist", "X", loader(rg("X", "2025-09-01", "Single")))
  assert hit.year == "2025" and not hit.trusted


@pytest.mark.parametrize("type_", ["EP", "Other", None])
def test_non_album_types_are_not_trusted(type_):
  # An EP can be beaten by a same-named album the dump doesn't have yet.
  assert not ri.lookup("Artist", "X", loader(rg("X", "2020", type_))).trusted


def test_an_edition_stripped_match_is_not_trusted():
  # "1989 (Taylor's Version)" missing from the dump would otherwise take 2014's "1989".
  load = loader(rg("1989", "2014-10-27", artist="Taylor Swift"))
  hit = ri.lookup("Taylor Swift", "1989 (Taylor's Version)", load)
  assert hit.year == "2014" and not hit.trusted


def test_the_exact_title_wins_when_both_are_indexed():
  load = loader(
    rg("1989", "2014-10-27", artist="Taylor Swift"),
    rg("1989 (Taylor's Version)", "2023-10-27", artist="Taylor Swift"),
  )
  hit = ri.lookup("Taylor Swift", "1989 (Taylor's Version)", load)
  assert (hit.year, hit.trusted) == ("2023", True)
  assert len(set(load.loaded)) == 2  # both title shards were read


def test_all_years_decide_the_self_titled_album():
  # A 2026 release group named "Weezer" exists; the 1994 album must still win (AOTY-13).
  load = loader(
    rg("Weezer", "2026-02-01", "Single", artist="Weezer"),
    rg("Weezer", "1994-05-10", "Album", artist="Weezer"),
  )
  hit = ri.lookup("Weezer", "Weezer", load)
  assert (hit.year, hit.trusted) == ("1994", True)


def test_a_different_artist_is_no_match():
  assert ri.lookup("Someone Else", "Old", loader(rg("Old", "1994"))) is None


def test_no_candidates_is_unknown_not_a_miss():
  assert ri.lookup("Artist", "Brand New", loader()) is None


def test_an_all_punctuation_title_has_no_index_key():
  load = loader()
  assert ri.title_keys("!!!") == []
  assert ri.lookup("!!!", "!!!", load) is None and load.loaded == []


def test_loader_errors_propagate():
  def boom(shard):
    raise OSError("S3 down")

  with pytest.raises(OSError):
    ri.lookup("Artist", "X", boom)


def test_manifest_round_trip_and_fingerprint_guard():
  m = ri.manifest("20261121-001001", built_at=1.5, records=10, titles=8, shards=4096)
  assert ri.usable(m) and m["built_at"] == 1
  assert not ri.usable({**m, "fingerprint": "0" * 16})
  assert not ri.usable({**m, "format": ri.FORMAT + 1})
  assert not ri.usable(None)


def test_fingerprint_tracks_norm(monkeypatch):
  before = ri.fingerprint()
  monkeypatch.setattr(ri, "norm", lambda s: s.lower())
  assert ri.fingerprint() != before
