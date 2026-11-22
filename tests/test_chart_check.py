"""tools/chart_check.py: the chart builder agrees with M0's rules (AOTY-21 acceptance)."""

import calendar
import random

import chart_core

import sync_core
from common.albumkey import album_key, join_key
from tools import chart_check as cc

NOW = calendar.timegm((2026, 11, 22, 12, 0, 0))


def world(n=250, seed=7):
  """A store with ties, singles, other years, null counts and 1-track albums."""
  rnd = random.Random(seed)
  store, items = sync_core.new_store(2026), {}
  plays = {}
  for i in range(n):
    artist, album = f"Artist {i % 31}", f"Album {i:03d}"
    k = join_key(album_key(artist, album))
    plays[k] = rnd.choice([1, 2, 3, 5, 8, 8, 13, 21, 40, 90])  # plenty of ties
    store["albums"][k] = {"artist": artist, "album": album, "mbid": "", "image": ""}
    year = 2026 if i % 5 else 2019
    ttype = rnd.choice(["Album", "Album", "Album", "Single", "EP", None])
    tracks = rnd.choice([None, 1, 3, 4, 7, 10, 12, 18])
    if i % 17 != 3:  # a few have no item yet (pending)
      items[k] = {"year": year, "tracks": tracks, "tracks_type": ttype, "rg_mbid": None}
  store["weeks"]["0"] = {"to": 0, "fetched_at": NOW, "plays": plays}
  return store, items


def test_the_chart_builder_matches_m0s_rules():
  store, items = world()
  chart, _ = chart_core.build_chart(store, items, username="u", display_name="U", now=NOW)
  ref, got = cc.reference(store, items), cc.chart_entries(chart, store)
  assert cc.compare(ref, got) == []
  assert len(ref["track"]) == 100 and 0 < len(ref["album"]) <= 100


def test_a_small_world_matches_exactly_in_scores():
  store, items = world(n=40, seed=3)
  chart, _ = chart_core.build_chart(store, items, username="u", display_name="U", now=NOW)
  ref, got = cc.reference(store, items), cc.chart_entries(chart, store)
  for mode in ("track", "album"):
    assert [round(s, 3) for _, s in ref[mode]] == [s for _, s in got[mode]]


def test_compare_catches_a_wrong_score_and_a_wrong_album():
  ref = {"track": [("a", 9), ("b", 5), ("c", 5), ("d", 1)], "album": []}
  assert cc.compare(ref, {"track": [("a", 9), ("c", 5), ("b", 5), ("d", 1)], "album": []}) == []
  assert cc.compare(ref, {"track": [("a", 9), ("b", 5), ("c", 4), ("d", 1)], "album": []})
  assert cc.compare(ref, {"track": [("x", 9), ("b", 5), ("c", 5), ("d", 1)], "album": []})


def test_ties_at_the_cutoff_may_differ():
  ref = {"track": [("a", 9), ("b", 5)], "album": []}
  assert cc.compare(ref, {"track": [("a", 9), ("z", 5)], "album": []}) == []
