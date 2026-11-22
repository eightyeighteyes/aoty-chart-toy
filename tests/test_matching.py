"""Release-year matching (aoty.norm / base_title / pick_year): the MusicBrainz lessons in CLAUDE.md."""

import pytest

import aoty


def rg(title, date, primary_type="Album", artist="Artist", secondary=()):
  return {
    "title": title,
    "first-release-date": date,
    "primary-type": primary_type,
    "secondary-types": list(secondary),
    "artist-credit": [{"name": artist, "joinphrase": ""}],
  }


# --- norm ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
  "a, b",
  [
    ("Singin’ to an Empty Chair", "Singin' to an Empty Chair"),  # curly vs straight apostrophe
    ("“Quoted”", '"Quoted"'),
    ("Guns N’ Roses", "guns n' roses"),
    ("SOMETHING   Worth  waiting", "something worth waiting"),  # case and spacing
    ("Bloodhorse!", "bloodhorse"),  # punctuation dropped
    ("ＡＢＣ", "abc"),  # NFKC folds full-width forms
  ],
)
def test_norm_treats_these_as_equal(a, b):
  assert aoty.norm(a) == aoty.norm(b)


def test_norm_keeps_words_apart():
  assert aoty.norm("AC/DC") == "ac dc"
  assert aoty.norm("Nature's Pill") != aoty.norm("Natures Pill")


# --- base_title ----------------------------------------------------------------------------


@pytest.mark.parametrize(
  "lastfm, base",
  [
    ("Album (Deluxe Edition)", "Album"),
    ("Album [Remastered 2011]", "Album"),
    ("Album (Super Deluxe)", "Album"),
    ("Album (10th Anniversary Edition)", "Album"),
    ("Album (Expanded)", "Album"),
    ("1989 (Taylor's Version)", "1989"),  # stripped for the query; exact match wins in pick_year
    ("Album (Live)", "Album (Live)"),  # not an edition word: kept
    ("Bloodhorse!", "Bloodhorse!"),
  ],
)
def test_base_title(lastfm, base):
  assert aoty.base_title(lastfm) == base


def test_base_title_never_returns_empty():
  assert aoty.base_title("(Deluxe Edition)") == "(Deluxe Edition)"


# --- pick_year against a recorded search -----------------------------------------------------
# The query for "1989" also returns "1989 [nightcore]", "1989 (Taylor’s version)", a karaoke
# album and live sets (tests/fixtures/mb/rg_taylor_swift_1989.json).


def test_1989_is_the_2014_album_not_its_lookalikes(mb_fixture):
  assert aoty.pick_year("Taylor Swift", "1989", mb_fixture("rg_taylor_swift_1989")) == "2014"


def test_taylors_version_is_its_own_2023_album(mb_fixture):
  # Last.fm spells it with a straight apostrophe and capital V; MusicBrainz doesn't.
  assert (
    aoty.pick_year("Taylor Swift", "1989 (Taylor's Version)", mb_fixture("rg_taylor_swift_1989")) == "2023"
  )


def test_deluxe_edition_resolves_to_the_original(mb_fixture):
  assert aoty.pick_year("Taylor Swift", "1989 (Deluxe Edition)", mb_fixture("rg_taylor_swift_1989")) == "2014"


def test_artist_must_match(mb_fixture):
  assert aoty.pick_year("Someone Else", "1989", mb_fixture("rg_taylor_swift_1989")) is None


@pytest.mark.parametrize("title", ["Singin’ to an Empty Chair", "Singin' to an Empty Chair"])
def test_curly_and_straight_quotes_match_the_same_release(mb_fixture, title):
  assert aoty.pick_year("Ratboys", title, mb_fixture("rg_ratboys_singin")) == "2026"


# --- pick_year preference rules ------------------------------------------------------------


def test_album_beats_an_earlier_ep_and_single():
  groups = [rg("X", "2019-01-01", "Single"), rg("X", "2020-01-01", "EP"), rg("X", "2026-03-01", "Album")]
  assert aoty.pick_year("Artist", "X", groups) == "2026"


def test_ep_beats_other_types():
  groups = [rg("X", "2019-01-01", "Single"), rg("X", "2021-01-01", "EP"), rg("X", "2018-01-01", None)]
  assert aoty.pick_year("Artist", "X", groups) == "2021"


def test_earliest_date_among_the_same_type():
  groups = [rg("X", "2026-05-01"), rg("X", "2024-11-30"), rg("X", "2025")]
  assert aoty.pick_year("Artist", "X", groups) == "2024"


def test_release_groups_without_a_year_are_ignored():
  assert aoty.pick_year("Artist", "X", [rg("X", ""), rg("X", "20")]) is None


def test_exact_title_beats_an_edition_stripped_match():
  # Same rule that keeps "(Taylor's Version)" apart: when a release group carries the full
  # Last.fm title, the stripped-title matches don't count.
  groups = [rg("X", "2020-01-01"), rg("X (Deluxe Edition)", "2026-01-01")]
  assert aoty.pick_year("Artist", "X (Deluxe Edition)", groups) == "2026"
  assert aoty.pick_year("Artist", "X", groups) == "2020"


def test_collaboration_credit_matches_either_artist_or_the_full_credit():
  group = {
    "title": "Duets",
    "first-release-date": "2026-02-02",
    "primary-type": "Album",
    "artist-credit": [{"name": "A", "joinphrase": " & "}, {"name": "B", "joinphrase": ""}],
  }
  for artist in ("A", "B", "A & B"):
    assert aoty.pick_year(artist, "Duets", [group]) == "2026"
  assert aoty.pick_year("C", "Duets", [group]) is None
