"""Album identity (lambdas/common/albumkey.py), shared by M0 and the Lambdas."""

import aoty
from common import albumkey


def test_aoty_uses_the_shared_module_not_a_copy():
  assert aoty.norm is albumkey.norm
  assert aoty.base_title is albumkey.base_title


def test_album_key_strips_editions_and_normalizes_both_halves():
  assert albumkey.album_key("Taylor Swift", "Red (Deluxe Edition)") == ("taylor swift", "red")
  assert albumkey.album_key("Sigur Rós", "Takk… [Remastered 2015]") == ("sigur rós", "takk")


def test_all_punctuation_names_keep_a_key_instead_of_going_blank():
  assert albumkey.album_key("Sigur Rós", "( )") == ("sigur rós", "( )")
  assert albumkey.album_key("!!!", "...") == ("!!!", "...")
  # The two that broke the first staging sync: one artist, one title.
  assert albumkey.album_key("@", "Autosmile") == ("@", "autosmile")
  assert albumkey.album_key("Beak>", ">>>>") == ("beak", ">>>>")
  assert albumkey.album_key("  ", "") == ("", "")
  assert not albumkey.is_complete(albumkey.album_key("Artist", "   "))


def test_join_and_split_round_trip_and_keep_the_halves_apart():
  # Without a separator these two would be the same string.
  a, b = ("ab", "c"), ("a", "bc")
  assert albumkey.join_key(a) != albumkey.join_key(b)
  for pair in (a, b, ("x y", "it's")):
    assert albumkey.split_key(albumkey.join_key(pair)) == pair
