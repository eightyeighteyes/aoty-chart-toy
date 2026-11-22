"""The chart builder's pure core (lambdas/chart/chart_core.py)."""

import calendar

import chart_core as core

import sync_core
from common.albumkey import album_key, join_key
from common.covers import art_fallbacks, caa_url

NOW = calendar.timegm((2026, 11, 22, 12, 0, 0))
IMG = "https://lastfm.freetls.fastly.net/i/u/300x300/{}.jpg"


def key(artist, album):
  return join_key(album_key(artist, album))


def make_store(plays_by_album, weeks=2, images=None):
  """A store whose albums get these year plays, split across `weeks` weeks."""
  store = sync_core.new_store(2026)
  for w in range(weeks):
    week = {}
    for (artist, album), n in plays_by_album.items():
      share = n // weeks + (1 if w < n % weeks else 0)
      if share:
        week[key(artist, album)] = share
    store["weeks"][str(1767225600 + w * 604800)] = {"to": 0, "fetched_at": NOW, "plays": week}
  for artist, album in plays_by_album:
    img = (images or {}).get((artist, album), "")
    store["albums"][key(artist, album)] = {"artist": artist, "album": album, "mbid": "", "image": img}
  return store


def item(year=2026, tracks=10, ttype="Album", rg="rg-1"):
  return {"year": year, "tracks": tracks, "tracks_type": ttype, "rg_mbid": rg, "looked_up_at": NOW - 60}


def build(store, items, **kw):
  return core.build_chart(store, items, username="alice", display_name="Alice", now=NOW, **kw)


def ranked_names(chart, mode):
  return [chart["albums"][i]["album"] for i, _ in chart["rankings"][mode]]


def test_year_plays_matches_the_sync_worker():
  store = make_store({("A", "One"): 7, ("B", "Two"): 3}, weeks=3)
  assert core.year_plays(store) == dict(sync_core.year_plays(store))


def test_only_target_year_albums_rank():
  store = make_store({("A", "New"): 5, ("B", "Old"): 50, ("C", "Missed"): 9})
  items = {key("A", "New"): item(), key("B", "Old"): item(year=1994), key("C", "Missed"): item(year=None)}
  chart, pending = build(store, items)
  assert ranked_names(chart, "track") == ["New"]
  assert pending == []
  assert chart["pending"] == 0


def test_by_track_is_plays_then_key():
  store = make_store({("B", "Bee"): 5, ("A", "Ay"): 5, ("C", "Sea"): 9})
  items = {k: item() for k in core.year_plays(store)}
  chart, _ = build(store, items)
  assert ranked_names(chart, "track") == ["Sea", "Ay", "Bee"]
  assert [s for _, s in chart["rankings"]["track"]] == [9, 5, 5]


def test_by_album_divides_by_tracks_and_leaves_singles_out():
  store = make_store({
    ("A", "Long"): 40,  # 40 / 20 = 2
    ("B", "Short"): 30,  # 30 / 5 = 6
    ("C", "Single"): 90,  # MusicBrainz type Single: out, whatever its length
    ("D", "Necks"): 3,  # a 1-track Album is an album: 3 / 1 = 3
    ("E", "Untyped3"): 60,  # no type, under MIN_ALBUM_TRACKS (Last.fm count): out
    ("F", "Untyped9"): 18,  # no type, 9 tracks: in, 18 / 9 = 2
    ("G", "NoCount"): 80,  # no track count at all: out
  })  # fmt: skip
  items = {
    key("A", "Long"): item(tracks=20),
    key("B", "Short"): item(tracks=5),
    key("C", "Single"): item(tracks=4, ttype="Single"),
    key("D", "Necks"): item(tracks=1),
    key("E", "Untyped3"): item(tracks=3, ttype=None),
    key("F", "Untyped9"): item(tracks=9, ttype=None),
    key("G", "NoCount"): {"year": 2026, "tracks": None, "tracks_type": None, "rg_mbid": None},
  }
  chart, _ = build(store, items)
  # Long and Untyped9 tie on 2.0: more plays first.
  assert ranked_names(chart, "album") == ["Short", "Necks", "Long", "Untyped9"]
  assert [s for _, s in chart["rankings"]["album"]] == [6.0, 3.0, 2.0, 2.0]
  # By Track keeps singles and uncounted albums.
  assert "Single" in ranked_names(chart, "track") and "NoCount" in ranked_names(chart, "track")
  single = {a["album"]: a["single"] for a in chart["albums"]}
  assert single["Single"] and single["NoCount"] and single["Untyped3"] and not single["Necks"]


def test_album_scores_are_rounded_like_m0():
  store = make_store({("A", "Third"): 10})
  chart, _ = build(store, {key("A", "Third"): item(tracks=3)})
  assert chart["rankings"]["album"] == [[0, 3.333]]


def test_each_ranking_is_a_strict_top_n():
  store = make_store({("A", f"Album {i:02d}"): 50 - i for i in range(12)})
  items = {k: item() for k in core.year_plays(store)}
  chart, _ = build(store, items, n=5)
  assert len(chart["rankings"]["track"]) == 5
  assert len(chart["rankings"]["album"]) == 5
  assert chart["top"] == 5


def test_ties_at_the_cutoff_are_dropped_by_key():
  store = make_store({("A", "X"): 9, ("B", "Y"): 4, ("C", "Z"): 4})
  items = {k: item() for k in core.year_plays(store)}
  chart, _ = build(store, items, n=2)
  assert ranked_names(chart, "track") == ["X", "Y"]  # Y and Z tie; Y's key sorts first


def test_albums_appear_once_and_only_if_shown():
  store = make_store({("A", f"Album {i:02d}"): 50 - i for i in range(8)})
  items = {k: item() for k in core.year_plays(store)}
  items[key("A", "Album 00")] = item(ttype="Single")  # track-only
  chart, _ = build(store, items, n=3)
  names = [a["album"] for a in chart["albums"]]
  assert len(names) == len(set(names))
  shown = set(ranked_names(chart, "track")) | set(ranked_names(chart, "album"))
  assert set(names) == shown
  assert names == sorted(names)  # By Track order (plays desc here = name order)


def test_pending_counts_played_albums_without_an_item():
  store = make_store({("A", "Known"): 5, ("B", "Waiting"): 3, ("C", "Also"): 1})
  chart, pending = build(store, {key("A", "Known"): item()})
  assert pending == sorted([key("B", "Waiting"), key("C", "Also")])
  assert chart["pending"] == 2


def test_albums_with_no_plays_this_year_arent_pending():
  store = make_store({("A", "Known"): 5})
  store["albums"][key("Z", "Zero")] = {"artist": "Z", "album": "Zero", "mbid": "", "image": ""}
  _, pending = build(store, {key("A", "Known"): item()})
  assert pending == []


def test_covers_chain_lastfm_sizes_then_caa():
  img = IMG.format("abc")
  store = make_store({("A", "Pic"): 5, ("B", "NoPic"): 4, ("C", "Nothing"): 3}, images={("A", "Pic"): img})
  items = {
    key("A", "Pic"): item(rg="rg-a"),
    key("B", "NoPic"): item(rg="rg-b"),
    key("C", "Nothing"): item(rg=None),
  }
  chart, _ = build(store, items)
  by = {a["album"]: a for a in chart["albums"]}
  assert by["Pic"]["art"] == img
  assert by["Pic"]["fallbacks"] == [*art_fallbacks(img), caa_url("rg-a")]
  assert (by["NoPic"]["art"], by["NoPic"]["fallbacks"]) == (caa_url("rg-b"), [])
  assert (by["Nothing"]["art"], by["Nothing"]["fallbacks"]) == ("", [])


def test_empty_store_and_no_target_year_albums_still_make_a_chart():
  for store, items in (
    (sync_core.new_store(2026), {}),
    (make_store({("B", "Old"): 9}), {key("B", "Old"): item(year=2001)}),
  ):
    chart, pending = build(store, items)
    assert chart["albums"] == []
    assert chart["rankings"] == {"track": [], "album": []}
    assert pending == []


def test_chart_shape():
  store = make_store({("Artist", "Title"): 4})
  chart, _ = build(store, {key("Artist", "Title"): item(tracks=4)})
  assert set(chart) == {
    "version", "username", "display_name", "year", "generated_at", "pending", "top", "featured",
    "albums", "rankings",
  }  # fmt: skip
  assert chart["version"] == core.CHART_VERSION == 1
  assert (chart["username"], chart["display_name"], chart["year"]) == ("alice", "Alice", 2026)
  assert chart["generated_at"] == "2026-11-22T12:00:00Z"
  assert chart["featured"] == core.FEATURED_N
  assert set(chart["rankings"]) == {"track", "album"}  # no Weighted for one user
  assert chart["albums"][0] == {
    "artist": "Artist", "album": "Title", "plays": 4, "tracks": 4, "single": False,
    "art": caa_url("rg-1"), "fallbacks": [],
  }  # fmt: skip


def test_display_name_falls_back_to_the_username():
  chart, _ = core.build_chart(sync_core.new_store(2026), {}, username="alice", display_name=None, now=NOW)
  assert chart["display_name"] == "alice"
