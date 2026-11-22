"""Ranking rules: listeners first (aoty), and the By Track / By Album / Weighted orders (build_page)."""

import json

import aoty
import build_page

YEAR = aoty.TARGET_YEAR


# --- aggregate_plays / rank_albums ---------------------------------------------------------


def test_spellings_merge_and_one_play_counts_as_a_listener():
  plays = [
    ("alice", "Ratboys", "Singin’ to an Empty Chair", 10),
    ("bob", "ratboys", "singin' to an empty chair", 1),
  ]
  key = aoty.cache_key("Ratboys", "Singin’ to an Empty Chair")
  stats = aoty.aggregate_plays(plays, {key}, {key: YEAR})
  assert len(stats) == 1
  only = stats[key]
  assert (only["artist"], only["album"], only["plays"]) == ("Ratboys", "Singin’ to an Empty Chair", 11)
  assert dict(only["listeners"]) == {"alice": 10, "bob": 1}


def test_only_target_year_candidates_are_counted():
  plays = [("alice", "A", "New", 5), ("alice", "A", "Old", 50), ("alice", "A", "Unlooked", 9)]
  new, old = aoty.cache_key("A", "New"), aoty.cache_key("A", "Old")
  stats = aoty.aggregate_plays(plays, {new, old}, {new: YEAR, old: "2019"})
  assert list(stats) == [new]


def _stats(listeners, plays):
  return {"listeners": {f"u{i}": 1 for i in range(listeners)}, "plays": plays}


def test_listeners_outrank_plays():
  ranked = aoty.rank_albums({"loop": _stats(1, 900), "shared": _stats(3, 12), "pair": _stats(2, 40)})
  assert [s["plays"] for s in ranked] == [12, 40, 900]


def test_plays_break_listener_ties_and_full_ties_keep_input_order():
  stats = {"a": _stats(2, 10), "b": _stats(2, 30), "c": _stats(2, 10)}
  stats["a"]["name"], stats["b"]["name"], stats["c"]["name"] = "a", "b", "c"
  assert [s["name"] for s in aoty.rank_albums(stats)] == ["b", "a", "c"]


# --- score_rankings ------------------------------------------------------------------------


def row(artist, album, by_user):
  """A CSV row as aoty.py writes it (all strings)."""
  return {
    "Artist": artist,
    "Album": album,
    "Unique Listeners": str(len(by_user)),
    "Total Plays": str(sum(by_user.values())),
    "Listener Plays": json.dumps(by_user),
  }


def releases_for(rows, infos):
  return {aoty.cache_key(r["Artist"], r["Album"]): info for r, info in zip(rows, infos, strict=True)}


ALBUM = lambda tracks: {"tracks": tracks, "type": "Album"}  # noqa: E731


def chart():
  """Rows in CSV order (listeners, then plays) and their release info."""
  rows = [
    row("A", "Shared", {"u1": 10, "u2": 10, "u3": 10, "u4": 10}),  # 40 plays / 10 tracks
    row("B", "Big Single", {"u1": 30, "u2": 5, "u3": 5}),  # Single: out of album modes
    row("C", "Short", {"u1": 6, "u2": 6, "u3": 6}),  # 18 / 3, untyped: under MIN_ALBUM_TRACKS
    row("D", "Dense", {"u1": 50, "u2": 22}),  # 72 / 6 = 12, but only 2 listeners
    row("E", "Long", {"u1": 60, "u2": 30, "u3": 30}),  # 120 / 12 = 10
    row("F", "Tie", {"u1": 10}),  # 10 / 1 = 10, tied with Long on score
  ]
  infos = [
    ALBUM(10),
    {"tracks": 1, "type": "Single"},
    {"tracks": 3, "type": None},
    ALBUM(6),
    ALBUM(12),
    ALBUM(1),
  ]
  return rows, releases_for(rows, infos)


def names(pairs):
  return [r["Album"] for r, _ in pairs]


def test_by_track_keeps_csv_order_with_plays_as_score():
  rows, releases = chart()
  _, rankings, _ = build_page.score_rankings(rows, releases, [], 100)
  assert names(rankings["track"]) == ["Shared", "Big Single", "Short", "Dense", "Long", "Tie"]
  assert [s for _, s in rankings["track"]] == [40, 40, 18, 72, 120, 10]


def test_by_album_divides_by_tracks_drops_singles_and_breaks_ties_by_listeners():
  rows, releases = chart()
  _, rankings, n_singles = build_page.score_rankings(rows, releases, [], 100)
  assert names(rankings["album"]) == ["Dense", "Long", "Tie", "Shared"]  # Long beats Tie: 3 listeners vs 1
  assert n_singles == 2


def test_weighted_needs_the_listener_floor():
  rows, releases = chart()
  _, rankings, _ = build_page.score_rankings(rows, releases, [], 100)
  # Dense (2 listeners) and Tie (1) are under WEIGHTED_MIN_LISTENERS = 3.
  assert names(rankings["weighted"]) == ["Long", "Shared"]
  assert [round(s, 3) for _, s in rankings["weighted"]] == [3.333, 1.0]


def test_top_n_cuts_every_ranking():
  rows, releases = chart()
  _, rankings, _ = build_page.score_rankings(rows, releases, [], 2)
  assert {mode: len(pairs) for mode, pairs in rankings.items()} == {"track": 2, "album": 2, "weighted": 2}


def test_member_views_add_each_members_own_top_albums_in_csv_order():
  rows, releases = chart()
  # n=1. Everyone's #1s: Shared (track), Dense (album), Long (weighted).
  # u1's own #1 by plays is Long (60); by album plays it's Tie (10/1 beats Dense's 50/6).
  kept, _, _ = build_page.score_rankings(rows, releases, ["u1"], 1)
  assert [r["Album"] for r in kept] == ["Shared", "Dense", "Long", "Tie"]


def test_member_view_ties_at_the_cutoff_are_all_kept():
  rows = [
    row("A", "Top", {"u1": 1, "u2": 1, "u3": 1}),
    row("B", "Mine 1", {"u9": 7}),
    row("C", "Mine 2", {"u9": 7}),
    row("D", "Other", {"u8": 1}),
  ]
  releases = releases_for(rows, [ALBUM(1), ALBUM(7), ALBUM(7), ALBUM(1)])
  kept, _, _ = build_page.score_rankings(rows, releases, ["u9"], 1)
  # u9's two albums tie on both scores at the n=1 cutoff, so the page gets both to break the tie.
  assert [r["Album"] for r in kept] == ["Top", "Mine 1", "Mine 2"]


def test_kept_rows_are_flagged_single_or_album():
  rows, releases = chart()
  kept, _, _ = build_page.score_rankings(rows, releases, [], 100)
  assert {r["Album"]: r["single"] for r in kept} == {
    "Shared": False,
    "Big Single": True,
    "Short": True,
    "Dense": False,
    "Long": False,
    "Tie": False,
  }
