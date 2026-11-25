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
  assert ranked_names(chart, "album") == ["New"]
  assert pending == []
  assert chart["pending"] == 0


def test_by_play_ties_go_to_plays_then_key():
  store = make_store({("B", "Bee"): 5, ("A", "Ay"): 5, ("C", "Sea"): 9})
  items = {k: item() for k in core.year_plays(store)}
  chart, _ = build(store, items)
  assert ranked_names(chart, "album") == ["Sea", "Ay", "Bee"]
  assert [s for _, s in chart["rankings"]["album"]] == [0.9, 0.5, 0.5]


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
  # No By Track since AOTY-63: singles and uncounted albums aren't on the chart at all.
  assert {a["album"] for a in chart["albums"]} == {"Short", "Necks", "Long", "Untyped9"}
  assert not any(a["single"] for a in chart["albums"])


def test_album_scores_are_rounded_like_m0():
  store = make_store({("A", "Third"): 10})
  chart, _ = build(store, {key("A", "Third"): item(tracks=3)})
  assert chart["rankings"]["album"] == [[0, 3.333]]


def test_each_ranking_is_a_strict_top_n():
  store = make_store({("A", f"Album {i:02d}"): 50 - i for i in range(12)})
  items = {k: item() for k in core.year_plays(store)}
  chart, _ = build(store, items, n=5)
  assert len(chart["rankings"]["album"]) == 5
  assert chart["top"] == 5


def test_ties_at_the_cutoff_are_dropped_by_key():
  store = make_store({("A", "X"): 9, ("B", "Y"): 4, ("C", "Z"): 4})
  items = {k: item() for k in core.year_plays(store)}
  chart, _ = build(store, items, n=2)
  assert ranked_names(chart, "album") == ["X", "Y"]  # Y and Z tie; Y's key sorts first


def test_albums_appear_once_and_only_if_shown():
  store = make_store({("A", f"Album {i:02d}"): 50 - i for i in range(8)})
  items = {k: item() for k in core.year_plays(store)}
  items[key("A", "Album 00")] = item(ttype="Single")  # the most played, but a single: not shown
  chart, _ = build(store, items, n=3)
  names = [a["album"] for a in chart["albums"]]
  assert len(names) == len(set(names))
  assert set(names) == set(ranked_names(chart, "album")) == {"Album 01", "Album 02", "Album 03"}
  assert names == sorted(names)  # by plays (plays desc here = name order)


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
    assert chart["rankings"] == {"album": []}
    assert pending == []


def test_chart_shape():
  store = make_store({("Artist", "Title"): 4})
  chart, _ = build(store, {key("Artist", "Title"): item(tracks=4)})
  assert set(chart) == {
    "version", "username", "display_name", "year", "generated_at", "pending", "pending_top", "top",
    "featured", "albums", "rankings",
  }  # fmt: skip
  assert chart["version"] == core.CHART_VERSION == 1
  assert (chart["username"], chart["display_name"], chart["year"]) == ("alice", "Alice", 2026)
  assert chart["generated_at"] == "2026-11-22T12:00:00Z"
  assert chart["featured"] == core.FEATURED_N
  assert set(chart["rankings"]) == {"album"}  # By Play only: no By Track (AOTY-63), no Weighted for one user
  assert chart["albums"][0] == {
    "artist": "Artist", "album": "Title", "plays": 4, "tracks": 4, "single": False,
    "art": caa_url("rg-1"), "fallbacks": [],
  }  # fmt: skip


def test_pending_top_counts_only_undated_albums_in_the_top_150():
  assert core.HOT_N == sync_core.HOT_N  # the chart's "top" is what sync dates first
  plays = {f"k{i:03d}": 1000 - i for i in range(200)}  # k000 most played
  items = {k: {} for k in list(plays)[:149]}  # all but k149 of the top 150 dated
  assert core.pending_top(plays, items) == 1
  assert core.pending_keys(plays, items) == [f"k{i:03d}" for i in range(149, 200)]
  items[f"k{149:03d}"] = {}
  assert core.pending_top(plays, items) == 0  # only the cold tail is left


def test_edition_suffixes_show_as_the_root_album():
  # "(Deluxe)" and friends share the root album's key, so they show as it (user, 2026-11-23).
  store = make_store({("Ratboys", "Singin' to an Empty Chair (Deluxe)"): 4})
  chart, _ = build(store, {key("Ratboys", "Singin' to an Empty Chair"): item(tracks=4)})
  assert chart["albums"][0]["album"] == "Singin' to an Empty Chair"


def test_the_owners_theme_is_published_unless_its_the_default_or_unknown():
  store = make_store({("A", "One"): 4})
  items = {key("A", "One"): item()}
  assert build(store, items, theme="deep-purple")[0]["theme"] == "deep-purple"
  assert build(store, items, theme="match1")[0]["theme"] == "match1"
  for theme in (None, "poster", "nope", 7):
    assert "theme" not in build(store, items, theme=theme)[0], theme


def test_with_theme_sets_and_clears_in_place():
  chart = {"version": 1, "theme": "kamp"}
  assert core.with_theme(chart, "blackpink") is chart and chart["theme"] == "blackpink"
  assert "theme" not in core.with_theme(chart, "poster")


def test_display_name_falls_back_to_the_username():
  chart, _ = core.build_chart(sync_core.new_store(2026), {}, username="alice", display_name=None, now=NOW)
  assert chart["display_name"] == "alice"


def test_cover_lookups_are_for_shown_albums_with_no_scrobble_cover():
  img = IMG.format("have")
  store = make_store({("A", "Has"): 9, ("B", "Lacks"): 8, ("C", "Hidden"): 1}, images={("A", "Has"): img})
  items = {key("A", "Has"): item(), key("B", "Lacks"): item(), key("C", "Hidden"): item()}
  shown = core.shown_keys(store, items, n=2)
  assert core.cover_lookups(shown, store, {}, NOW) == [key("B", "Lacks")]


def test_cover_lookups_respect_the_cache():
  store = make_store({("B", "Lacks"): 8, ("D", "None"): 7, ("E", "Old"): 6})
  items = {k: item() for k in core.year_plays(store)}
  shown = core.shown_keys(store, items)
  cache = {
    key("B", "Lacks"): {"url": "https://x/1.jpg", "at": NOW - 999 * 86400},  # found: kept for good
    key("D", "None"): {"url": "", "at": NOW - 86400},  # none, recently: not yet
    key("E", "Old"): {"url": "", "at": NOW - core.COVER_RETRY_AFTER},  # none, a month ago: retry
  }
  assert core.cover_lookups(shown, store, cache, NOW) == [key("E", "Old")]


def test_looked_up_images_come_before_caa():
  store = make_store({("B", "Lacks"): 8})
  chart, _ = build(store, {key("B", "Lacks"): item(rg="rg-b")}, images={key("B", "Lacks"): IMG.format("got")})
  (a,) = chart["albums"]
  assert a["art"] == IMG.format("got")
  assert a["fallbacks"] == [*art_fallbacks(IMG.format("got")), caa_url("rg-b")]
