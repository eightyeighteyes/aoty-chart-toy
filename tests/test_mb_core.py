"""The mb lane's pure core (lambdas/mb/mb_core.py): lookups in M0's order, items, failures."""

import pytest

import mb_core as core

NOW = 1_795_000_000


def msg(artist, album, artist_key=None, title_key=None, mbid=""):
  return {
    "artist_key": artist_key or artist.lower(),
    "title_key": title_key or album.lower(),
    "artist": artist,
    "album": album,
    "mbid": mbid,
  }


def rg(title, date, type_="Album", artist="Artist", id_="rg"):
  return {
    "id": id_,
    "title": title,
    "first-release-date": date,
    "primary-type": type_,
    "artist-credit": [{"name": artist, "joinphrase": ""}],
  }


def rel(title, tracks, type_="Album", artist="Artist", date="2026-01-01"):
  return {
    "title": title,
    "status": "Official",
    "date": date,
    "track-count": tracks,
    "release-group": {"primary-type": type_},
    "artist-credit": [{"name": artist, "joinphrase": ""}],
  }


class FakeMB:
  """Answers searches from a list of (entity, predicate on the query, results or None)."""

  def __init__(self, *rules):
    self.rules, self.calls = list(rules), []

  def __call__(self, entity, query, limit):
    self.calls.append((entity, query, limit))
    for ent, match, results in self.rules:
      if ent == entity and match(query):
        return results
    return []


def has(text):
  return lambda q: text in q


def single(text):
  """Only an individual search (one clause) for this title."""
  return lambda q: text in q and " OR " not in q


def resolve(albums, mb, lastfm=lambda a, b: None, **kw):
  return core.resolve(albums, search=mb, lastfm_tracks=lastfm, target_year=2026, now=NOW, **kw)


def test_target_year_album_gets_year_ids_and_tracks_in_two_batched_searches():
  mb = FakeMB(
    ("release-group", has("X"), [rg("X", "2026-03-01", id_="rg-x")]),
    ("release", has("X"), [rel("X", 9)]),
  )
  out = resolve([msg("Artist", "X")], mb)
  assert out.failed == set()
  assert out.items[("artist", "x")] == {
    "artist_key": "artist",
    "title_key": "x",
    "artist": "Artist",
    "album": "X",
    "year": 2026,
    "rg_mbid": "rg-x",
    "primary_type": "Album",
    "lastfm_mbid": None,
    "looked_up_at": NOW,
    "tracks": 9,
    "tracks_type": "Album",
    "tracks_source": "musicbrainz",
  }
  assert [c[0] for c in mb.calls] == ["release-group", "release"]


def test_other_years_get_no_track_lookup():
  mb = FakeMB(("release-group", has("Old"), [rg("Old", "1994-05-10")]))
  out = resolve([msg("Artist", "Old")], mb)
  assert out.items[("artist", "old")]["year"] == 1994
  assert "tracks" not in out.items[("artist", "old")]
  assert [c[0] for c in mb.calls] == ["release-group"]


def test_twenty_albums_share_one_search_and_the_21st_gets_the_next():
  albums = [msg("Artist", f"T{i:02}") for i in range(21)]
  mb = FakeMB(("release-group", lambda q: True, [rg(f"T{i:02}", "1990") for i in range(21)]))
  resolve(albums, mb)
  rg_calls = [c for c in mb.calls if c[0] == "release-group"]
  assert len(rg_calls) == 2 and rg_calls[0][1].count(" OR ") == 19 and rg_calls[0][2] == 100


def test_a_batch_miss_gets_its_own_search_before_it_is_a_confirmed_miss():
  mb = FakeMB(("release-group", single("Gone"), []), ("release-group", has("Here"), [rg("Here", "1999")]))
  out = resolve([msg("Artist", "Here"), msg("Artist", "Gone")], mb)
  assert out.items[("artist", "gone")]["year"] is None  # confirmed miss: written, year null
  assert out.items[("artist", "gone")]["rg_mbid"] is None
  assert ("release-group", '(artist:"Artist" AND releasegroup:"Gone")', 25) in mb.calls
  assert out.stats["missed"] == 1


def test_a_failed_individual_search_is_failed_not_a_miss():
  mb = FakeMB(("release-group", single("Gone"), None), ("release-group", has("Here"), [rg("Here", "1999")]))
  out = resolve([msg("Artist", "Here"), msg("Artist", "Gone")], mb)
  assert out.failed == {("artist", "gone")}
  assert ("artist", "gone") not in out.items
  assert ("artist", "here") in out.items


def test_a_failed_batch_retries_every_album_on_its_own():
  mb = FakeMB(
    ("release-group", has(" OR "), None),
    ("release-group", single("A1"), [rg("A1", "1990")]),
    ("release-group", single("A2"), [rg("A2", "1991")]),
  )
  out = resolve([msg("Artist", "A1"), msg("Artist", "A2")], mb)
  assert {k: i["year"] for k, i in out.items.items()} == {("artist", "a1"): 1990, ("artist", "a2"): 1991}


def test_track_miss_falls_back_to_lastfm():
  mb = FakeMB(("release-group", has("X"), [rg("X", "2026")]), ("release", has("X"), []))
  out = resolve([msg("Artist", "X")], mb, lastfm=lambda a, b: 7)
  item = out.items[("artist", "x")]
  assert (item["tracks"], item["tracks_type"], item["tracks_source"]) == (7, None, "lastfm")


def test_no_count_anywhere_is_resolved_with_tracks_none():
  mb = FakeMB(("release-group", has("X"), [rg("X", "2026")]), ("release", has("X"), []))
  item = resolve([msg("Artist", "X")], mb).items[("artist", "x")]
  assert (item["tracks"], item["tracks_source"]) == (None, None)


def test_track_lookup_failure_fails_the_whole_album_and_counts_the_wasted_year():
  def boom(a, b):
    raise RuntimeError("Last.fm down")

  mb = FakeMB(("release-group", has("X"), [rg("X", "2026")]), ("release", has("X"), []))
  out = resolve([msg("Artist", "X")], mb, lastfm=boom)
  assert out.failed == {("artist", "x")} and out.items == {}
  assert out.stats["year_wasted"] == 1

  mb = FakeMB(("release-group", has("X"), [rg("X", "2026")]), ("release", single("X"), None))
  assert resolve([msg("Artist", "X")], mb).failed == {("artist", "x")}


def test_tracks_type_is_the_matched_release_groups_type_for_the_singles_rule(mb_fixture):
  mb = FakeMB(
    ("release-group", has("Dopesmoker"), [rg("Dopesmoker", "2026", "Album", artist="Sleep")]),
    ("release", has("Dopesmoker"), mb_fixture("rel_sleep_dopesmoker")),
  )
  item = resolve([msg("Sleep", "Dopesmoker")], mb).items[("sleep", "dopesmoker")]
  assert (item["tracks"], item["tracks_type"]) == (2, "Album")


def test_items_use_the_messages_keys_never_recomputed():
  # Sync's album_key falls back to the casefolded name for all-punctuation titles; recomputing
  # with norm() would give "" and a key the sync worker never asks for.
  mb = FakeMB(("release-group", has("!!!"), [rg("!!!", "1999", artist="!!!")]))
  out = resolve([msg("!!!", "!!!", artist_key="!!!", title_key="!!!")], mb)
  assert list(out.items) == [("!!!", "!!!")]


def test_duplicate_messages_are_looked_up_once():
  mb = FakeMB(("release-group", has("X"), [rg("X", "1999")]))
  out = resolve([msg("Artist", "X"), msg("Artist", "X")], mb)
  assert len(out.items) == 1 and mb.calls[0][1].count("releasegroup:") == 1


def test_index_hit_skips_the_year_search_but_not_the_track_lookup():
  mb = FakeMB(("release", has("X"), [rel("X", 11)]))
  out = resolve(
    [msg("Artist", "X")], mb, year_from_index=lambda a: {"year": "2026", "id": "rg-i", "type": "Album"}
  )
  assert [c[0] for c in mb.calls] == ["release"]
  assert out.items[("artist", "x")]["tracks"] == 11
  assert out.stats["index_hits"] == 1


@pytest.mark.parametrize("mbid", ["", "abc-123"])
def test_lastfm_mbid_is_stored_not_used(mbid):
  mb = FakeMB(("release-group", has("X"), [rg("X", "1999")]))
  item = resolve([msg("Artist", "X", mbid=mbid)], mb).items[("artist", "x")]
  assert item["lastfm_mbid"] == (mbid or None)
