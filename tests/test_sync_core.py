"""The sync worker's pure core (lambdas/sync/sync_core.py)."""

import calendar

import pytest

import sync_core as core
from common.albumkey import join_key

JAN1 = calendar.timegm((2026, 1, 1, 0, 0, 0))
FIRST_BOUNDARY = calendar.timegm((2026, 1, 4, 12, 0, 0))  # Sunday 12:00 UTC
NOV21 = calendar.timegm((2026, 11, 21, 18, 0, 0))  # a Saturday


def row(artist, album, plays, mbid=""):
  return {"artist": {"#text": artist, "mbid": ""}, "name": album, "mbid": mbid, "playcount": str(plays)}


# ---- week ranges ------------------------------------------------------------------------


def test_first_range_runs_from_jan_1_to_the_first_sunday_boundary():
  ranges = core.week_ranges(2026, NOV21)
  assert ranges[0] == (JAN1, FIRST_BOUNDARY)
  assert ranges[1] == (FIRST_BOUNDARY, FIRST_BOUNDARY + core.WEEK)


def test_ranges_tile_without_gaps_and_end_with_the_current_partial_week():
  ranges = core.week_ranges(2026, NOV21)
  assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:]))
  frm, to = ranges[-1]
  assert frm <= NOV21 < to  # current week keeps its full end; fetching clips it to now
  assert len(ranges) == 47


def test_ranges_stop_at_the_year_end_after_rollover():
  ranges = core.week_ranges(2026, calendar.timegm((2027, 1, 2, 9, 0, 0)))
  start, end = core.year_bounds(2026)
  assert ranges[0][0] == start and ranges[-1][1] == end
  assert ranges == core.week_ranges(2026, end + 400 * core.DAY)


def test_every_boundary_is_a_sunday_noon_utc_in_another_year():
  for frm, _ in core.week_ranges(2027, calendar.timegm((2027, 3, 1, 0, 0, 0)))[1:]:
    assert (frm - FIRST_BOUNDARY) % core.WEEK == 0


def test_no_ranges_before_the_year_starts():
  assert core.week_ranges(2026, JAN1 - 1) == []


# ---- what to fetch ----------------------------------------------------------------------


def _fetched_all(ranges, at):
  store = core.new_store(2026)
  for rng in ranges:
    core.merge_week(store, rng, {}, {}, at)
  return store


def test_first_sync_fetches_every_week():
  ranges = core.week_ranges(2026, NOV21)
  assert core.weeks_to_fetch(core.new_store(2026), ranges) == ranges


def test_a_second_sync_refetches_only_the_current_week_and_the_last_three_closed():
  ranges = core.week_ranges(2026, NOV21)
  store = _fetched_all(ranges, NOV21)
  assert core.weeks_to_fetch(store, ranges) == ranges[-4:]


def test_weeks_fetched_too_soon_after_closing_are_refetched_after_a_long_gap():
  ranges = core.week_ranges(2026, NOV21)
  # Weeks 0-9 were last fetched the moment week 9 closed, then the user didn't sync for months.
  # Week 6 closed exactly SETTLE before that fetch, so it (and everything older) is final;
  # weeks 7-9 were fetched too soon and are refetched along with everything since.
  store = _fetched_all(ranges[:10], ranges[9][1])
  assert ranges[9][1] == ranges[6][1] + core.SETTLE
  assert core.weeks_to_fetch(store, ranges) == ranges[7:]


def test_full_refetches_every_week_fetched_before_the_pass_began_and_resumes():
  ranges = core.week_ranges(2026, NOV21)
  store = _fetched_all(ranges, NOV21 - core.DAY)
  started = core.full_pass_start(store, NOV21)
  assert core.weeks_to_fetch(store, ranges, started) == ranges
  for rng in ranges[:20]:  # the pass got this far, then was killed
    core.merge_week(store, rng, {}, {}, NOV21 + 60)
  restarted = core.full_pass_start(store, NOV21 + 600)
  assert restarted == started
  assert core.weeks_to_fetch(store, ranges, restarted) == ranges[20:]


def test_a_stale_full_pass_starts_over():
  store = core.new_store(2026)
  core.full_pass_start(store, NOV21)
  assert core.full_pass_start(store, NOV21 + 2 * core.DAY) == NOV21 + 2 * core.DAY


def test_full_resync_is_due_monthly_and_on_first_sync():
  assert core.wants_full_resync(None, NOV21)
  assert not core.wants_full_resync(NOV21 - 29 * core.DAY, NOV21)
  assert core.wants_full_resync(NOV21 - 31 * core.DAY, NOV21)


# ---- cap split --------------------------------------------------------------------------


def test_a_capped_response_is_split_and_the_halves_concatenated():
  calls = []

  def fetch(frm, to):
    calls.append((frm, to))
    n = 999 if to - frm > 4 * core.DAY else 10
    return [row("a", f"x{frm}-{i}", 1) for i in range(n)]

  rows = core.fetch_range(fetch, 0, core.WEEK)
  assert calls == [(0, core.WEEK), (0, core.WEEK // 2), (core.WEEK // 2, core.WEEK)]
  assert len(rows) == 20


def test_a_one_day_range_still_capped_raises():
  with pytest.raises(core.CapError):
    core.fetch_range(lambda f, t: [row("a", "b", 1)] * core.CAP_ROWS, 0, core.WEEK)


# ---- merging ----------------------------------------------------------------------------


def test_chart_rows_collapse_editions_by_album_artist_and_keep_an_mbid():
  plays, meta = core.chart_plays(
    [
      row("Taylor Swift", "Red", 5),
      row("Taylor Swift", "Red (Deluxe Edition)", 3, mbid="m-red"),
      row("Various Artists", "Trojan Singles Box Set", 17),
      row("Nobody", "", 4),  # no album title: skipped
    ]
  )
  red = join_key(("taylor swift", "red"))
  assert plays == {red: 8, join_key(("various artists", "trojan singles box set")): 17}
  assert meta[red]["mbid"] == "m-red"


def test_refetching_a_week_overwrites_it_rather_than_adding():
  store = core.new_store(2026)
  rng = core.week_ranges(2026, NOV21)[5]
  first = core.chart_plays([row("A", "B", 4)])
  core.merge_week(store, rng, *first, NOV21)
  core.merge_week(store, rng, *first, NOV21 + 60)
  assert core.year_plays(store) == {join_key(("a", "b")): 4}
  newer = core.chart_plays([row("A", "B", 6)])
  core.merge_week(store, rng, *newer, NOV21 + 120)
  assert core.year_plays(store) == {join_key(("a", "b")): 6}


def test_merge_never_clears_known_metadata():
  store = core.new_store(2026)
  rng = (JAN1, FIRST_BOUNDARY)
  core.merge_week(store, rng, *core.chart_plays([row("A", "B", 1, mbid="m1")]), NOV21)
  store["albums"][join_key(("a", "b"))]["image"] = "https://img/x.jpg"
  core.merge_week(store, rng, *core.chart_plays([row("A", "B", 2)]), NOV21)
  assert store["albums"][join_key(("a", "b"))] == {
    "artist": "A",
    "album": "B",
    "mbid": "m1",
    "image": "https://img/x.jpg",
  }


# ---- mb queue selection -----------------------------------------------------------------


def _store_with_plays(n_albums):
  store = core.new_store(2026)
  rows = [row("Artist", f"Album {i:03d}", n_albums - i) for i in range(n_albums)]
  core.merge_week(store, (JAN1, FIRST_BOUNDARY), *core.chart_plays(rows), NOV21)
  return store


def test_hot_is_the_users_top_150_by_plays_and_cold_the_rest():
  store = _store_with_plays(200)
  ranked = core.ranked_keys(store)
  unresolved = ranked[140:160] + ranked[:3]
  hot, cold = core.split_hot_cold(store, unresolved)
  assert hot == ranked[:3] + ranked[140:150]
  assert cold == ranked[150:160]


def test_resolved_albums_are_never_candidates_again():
  store = _store_with_plays(3)
  first = core.ranked_keys(store)[0]
  store["albums"][first]["resolved"] = True
  assert first not in core.mb_candidates(store, NOV21 + 365 * core.DAY)


def test_recently_queued_albums_are_not_queued_again_for_a_week():
  store = _store_with_plays(3)
  first = core.ranked_keys(store)[0]
  store["albums"][first]["queued_at"] = NOV21
  assert first not in core.mb_candidates(store, NOV21 + core.DAY)
  assert first in core.mb_candidates(store, NOV21 + core.REQUEUE_AFTER)


# ---- covers -----------------------------------------------------------------------------


def track(artist, album, uts=None, image="https://lastfm/i/u/300x300/abc.jpg", mbid=""):
  t = {
    "artist": {"#text": artist},
    "album": {"#text": album, "mbid": mbid},
    "name": "t",
    "image": [{"size": "small", "#text": "s.jpg"}, {"size": "extralarge", "#text": image}],
  }
  if uts is None:
    t["@attr"] = {"nowplaying": "true"}
  else:
    t["date"] = {"uts": str(uts)}
  return t


def test_covers_match_by_mbid_first_then_by_key_and_skip_now_playing_and_placeholders():
  store = core.new_store(2026)
  rows = [row("Various Artists", "Comp", 9, mbid="m-comp"), row("Band", "LP", 5), row("Solo", "EP", 2)]
  core.merge_week(store, (JAN1, FIRST_BOUNDARY), *core.chart_plays(rows), NOV21)
  tracks = [
    track("Someone On The Comp", "Comp", uts=100, mbid="m-comp", image="https://i/comp.jpg"),
    track("BAND", "LP (Deluxe Edition)", uts=101, image="https://i/lp.jpg"),
    track("Solo", "EP", uts=None, image="https://i/np.jpg"),  # now playing: ignored
    track("Solo", "EP", uts=99, image=f"https://i/{core.PLACEHOLDER}.png"),
  ]
  assert core.apply_covers(store, tracks) == 2
  albums = store["albums"]
  assert albums[join_key(("various artists", "comp"))]["image"] == "https://i/comp.jpg"
  assert albums[join_key(("band", "lp"))]["image"] == "https://i/lp.jpg"
  assert albums[join_key(("solo", "ep"))]["image"] == ""
  assert core.missing_top_covers(store) == 1
  assert core.newest_uts(tracks) == 101
