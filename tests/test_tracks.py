"""Track counts and the single rule (build_page.pick_release / is_single)."""

import pytest

import build_page


def rel(title, tracks, date="2026-01-01", status="Official", rg_type="Album", artist="Artist"):
  return {
    "title": title,
    "status": status,
    "date": date,
    "track-count": tracks,
    "release-group": {"primary-type": rg_type},
    "artist-credit": [{"name": artist, "joinphrase": ""}],
  }


# --- recorded searches ---------------------------------------------------------------------


def test_gladie_ep_length_single_is_a_single(mb_fixture):
  # 4 tracks, but MusicBrainz types it Single, and the type decides (user decision 2026-11-20).
  info = build_page.pick_release(
    "gladie", "I Want That For You", mb_fixture("rel_gladie_i_want_that_for_you")
  )
  assert info == {"tracks": 4, "type": "Single"}
  assert build_page.is_single(info)


def test_dopesmoker_is_an_album_from_its_earliest_official_release(mb_fixture):
  info = build_page.pick_release("Sleep", "Dopesmoker", mb_fixture("rel_sleep_dopesmoker"))
  assert info == {"tracks": 2, "type": "Album"}  # the 2003 CD; later pressings have 1-10 tracks
  assert not build_page.is_single(info)


def test_unrelated_title_from_the_same_search_is_not_a_match(mb_fixture):
  assert build_page.pick_release(
    "Sleep", "Sexy Dopesmoker Zippy Kid", mb_fixture("rel_sleep_dopesmoker")
  ) == {
    "tracks": 1,
    "type": "Album",
  }
  assert build_page.pick_release("Sleep", "Holy Mountain", mb_fixture("rel_sleep_dopesmoker")) is None


# --- pick_release preference rules ---------------------------------------------------------


def test_official_beats_an_earlier_bootleg():
  releases = [rel("X", 3, "2001-01-01", status="Bootleg"), rel("X", 9, "2026-01-01")]
  assert build_page.pick_release("Artist", "X", releases) == {"tracks": 9, "type": "Album"}


def test_album_type_beats_ep_type_then_earliest_date():
  releases = [rel("X", 5, "2020-01-01", rg_type="EP"), rel("X", 11, "2026-02-01"), rel("X", 12, "2026-01-01")]
  assert build_page.pick_release("Artist", "X", releases) == {"tracks": 12, "type": "Album"}


def test_releases_without_a_track_count_are_skipped():
  assert build_page.pick_release("Artist", "X", [rel("X", 0), rel("X", None)]) is None


def test_exact_title_beats_an_edition_stripped_match():
  releases = [rel("1989", 13, "2014-10-27"), rel("1989 (Taylor’s Version)", 21, "2023-10-27")]
  assert build_page.pick_release("Artist", "1989 (Taylor's Version)", releases) == {
    "tracks": 21,
    "type": "Album",
  }
  assert build_page.pick_release("Artist", "1989 (Deluxe Edition)", releases) == {
    "tracks": 13,
    "type": "Album",
  }


# --- is_single -----------------------------------------------------------------------------


@pytest.mark.parametrize(
  "info, single",
  [
    ({"tracks": 1, "type": "Album"}, False),  # Dopesmoker / The Necks: an Album at any length
    ({"tracks": 4, "type": "Single"}, True),
    ({"tracks": 10, "type": "Single"}, True),
    ({"tracks": 6, "type": "EP"}, False),
    ({"tracks": build_page.MIN_ALBUM_TRACKS, "type": "EP"}, False),
    ({"tracks": 3, "type": "EP"}, True),
    ({"tracks": 1, "type": "EP"}, True),  # a 1-track remix EP (AOTY-82)
    ({"tracks": 1, "type": "Other"}, True),  # a 1-track "(acoustic)" typed Other (AOTY-82)
    ({"tracks": 1, "type": "Broadcast"}, True),
    ({"tracks": 8, "type": "Other"}, False),
    ({"tracks": 3, "type": None}, True),  # untyped (Last.fm count): under MIN_ALBUM_TRACKS
    ({"tracks": build_page.MIN_ALBUM_TRACKS, "type": None}, False),
    ({"tracks": None, "type": None}, True),  # no count: can't be scored per album
    ({"tracks": None, "type": "Album"}, True),
  ],
)
def test_is_single(info, single):
  assert build_page.is_single(info) is single
