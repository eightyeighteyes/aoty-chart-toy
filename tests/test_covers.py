"""Cover URL chains (lambdas/common/covers.py), shared by M0's page and the chart builder."""

from common.covers import art_fallbacks, caa_url, cover

LASTFM = "https://lastfm.freetls.fastly.net/i/u/300x300/abc123.jpg"
RG = "0f7f1a5e-0000-4000-8000-000000000001"


def test_lastfm_fallbacks_are_the_other_sizes():
  assert art_fallbacks(LASTFM) == [
    "https://lastfm.freetls.fastly.net/i/u/ar0/abc123.jpg",
    "https://lastfm.freetls.fastly.net/i/u/174s/abc123.jpg",
  ]


def test_non_lastfm_or_blank_urls_have_no_size_fallbacks():
  assert art_fallbacks("https://example.com/x.jpg") == []
  assert art_fallbacks("") == []
  assert art_fallbacks(None) == []


def test_caa_url_needs_an_mbid():
  assert caa_url(RG) == f"https://coverartarchive.org/release-group/{RG}/front-250"
  assert caa_url(None) == ""
  assert caa_url("") == ""


def test_cover_tries_lastfm_sizes_then_caa():
  art, fallbacks = cover(LASTFM, RG)
  assert art == LASTFM
  assert fallbacks == [*art_fallbacks(LASTFM), caa_url(RG)]


def test_without_a_lastfm_image_caa_is_the_art():
  assert cover("", RG) == (caa_url(RG), [])


def test_without_either_the_page_shows_the_tile():
  assert cover("", None) == ("", [])
  assert cover(LASTFM, None) == (LASTFM, art_fallbacks(LASTFM))
