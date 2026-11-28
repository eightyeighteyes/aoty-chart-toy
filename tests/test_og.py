"""The link preview pages (lambdas/chart/og.py, AOTY-85)."""

import importlib.util
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

OG = Path(__file__).resolve().parent.parent / "lambdas" / "chart" / "og.py"
ORIGIN, BASE = "https://kamp.fm", "/toys/aoty/"
COVER = "https://lastfm.freetls.fastly.net/i/u/300x300/abc.jpg"


@pytest.fixture(scope="module")
def og():
  spec = importlib.util.spec_from_file_location("og", OG)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class _Tags(HTMLParser):
  def __init__(self):
    super().__init__()
    self.meta, self.links, self.title, self._in_title, self.scripts = {}, [], "", False, 0

  def handle_starttag(self, tag, attrs):
    a = dict(attrs)
    if tag == "meta" and "charset" in a:
      self.meta["charset"] = a["charset"]
    elif tag == "meta":
      self.meta[a.get("property") or a.get("name")] = a.get("content")
    elif tag in ("a", "link"):
      self.links.append(a.get("href"))
    elif tag == "title":
      self._in_title = True
    elif tag == "script":
      self.scripts += 1

  def handle_endtag(self, tag):
    if tag == "title":
      self._in_title = False

  def handle_data(self, data):
    if self._in_title:
      self.title += data


def tags(html):
  p = _Tags()
  p.feed(html)
  return p


def user_chart(album="Blue Lines", artist="Massive Attack", art=COVER, ranked=True, display="Alice"):
  return {
    "username": "alice", "display_name": display, "year": 2026,
    "albums": [{"artist": artist, "album": album, "art": art}],
    "rankings": {"album": [[0, 12.5]] if ranked else []},
  }  # fmt: skip


def group_chart(name="Full House", modes=("listener",), ranked=True):
  return {
    "kind": "group", "id": "full-house", "name": name, "year": 2026,
    "albums": [{"artist": "Low", "album": "Hey What", "art": COVER}],
    "rankings": {m: ([[0, 3]] if ranked else []) for m in modes},
  }  # fmt: skip


def test_a_user_page_names_them_and_their_number_one(og):
  t = tags(og.user_page(user_chart(), ORIGIN, BASE))
  assert t.meta["charset"] == "utf-8" and t.scripts == 0
  assert t.meta["og:title"] == "Alice's albums of the year 2026"
  assert t.meta["og:description"] == "#1 so far: Blue Lines by Massive Attack"
  assert t.meta["og:site_name"] == "a kamp toy" and t.meta["og:type"] == "website"
  assert t.meta["og:url"] == "https://kamp.fm/toys/aoty/user/Alice"  # canonical, in display casing
  assert t.meta["og:image"] == COVER and t.meta["twitter:card"] == "summary"
  assert t.title == "Alice's albums of the year 2026 · a kamp toy"
  assert t.links == ["https://kamp.fm/toys/aoty/user/Alice"] * 2  # canonical and the body link


def test_a_group_page_uses_by_listener_then_by_play(og):
  t = tags(og.group_page(group_chart(), ORIGIN, "/toys/aoty-staging/"))
  assert t.meta["og:title"] == "Full House · albums of the year 2026"
  assert t.meta["og:description"] == "#1 so far: Hey What by Low"
  assert t.meta["og:url"] == "https://kamp.fm/toys/aoty-staging/group/full-house"
  older = tags(og.group_page(group_chart(modes=("album",)), ORIGIN, BASE))  # charts before AOTY-63
  assert older.meta["og:description"] == "#1 so far: Hey What by Low"


def test_before_anything_ranks_it_says_what_the_chart_is_never_a_progress_word(og):
  for page in (og.user_page(user_chart(ranked=False), ORIGIN, BASE),
               og.group_page(group_chart(ranked=False), ORIGIN, BASE)):  # fmt: skip
    t = tags(page)
    assert "og:image" not in t.meta and "#1" not in t.meta["og:description"]
    assert not re.search(r"still|pressing…|checking|loading|wait", t.meta["og:description"])
  assert tags(og.user_page(user_chart(ranked=False), ORIGIN, BASE)).meta["og:description"] == (
    "the 2026 albums Alice actually lived with, pressed into a chart. a kamp toy."
  )


def test_no_image_without_an_https_cover(og):
  for art in ("", None, "http://example.com/a.jpg", "javascript:alert(1)"):
    assert "og:image" not in tags(og.user_page(user_chart(art=art), ORIGIN, BASE)).meta


def test_everything_is_escaped(og):
  evil = '"><script>alert(1)</script>&'
  page = og.group_page(group_chart(name=evil), ORIGIN, BASE)
  assert "<script>" not in page and "&quot;&gt;&lt;script&gt;" in page
  t = tags(page)
  assert t.scripts == 0 and t.meta["og:title"] == f"{evil} · albums of the year 2026"
  page = og.user_page(user_chart(album=evil, artist='A & "B"'), ORIGIN, BASE)
  assert "<script>" not in page and tags(page).meta["og:description"] == f'#1 so far: {evil} by A & "B"'


def test_non_ascii_names_survive(og):
  page = og.group_page(group_chart(name="Café 音楽 🎧"), ORIGIN, BASE)
  assert tags(page).meta["og:title"] == "Café 音楽 🎧 · albums of the year 2026"
  t = tags(og.user_page(user_chart(album="Ágætis byrjun", artist="Sigur Rós"), ORIGIN, BASE))
  assert t.meta["og:description"] == "#1 so far: Ágætis byrjun by Sigur Rós"


def test_a_long_description_is_cut_on_a_word(og):
  album = " ".join(["word"] * 80)
  d = tags(og.user_page(user_chart(album=album), ORIGIN, BASE)).meta["og:description"]
  assert len(d) <= og.DESCRIPTION_MAX and d.endswith("…") and not d.endswith(" …")
  assert d[:-1].split()[-1] == "word"


CARD = "https://kamp.fm/toys/aoty/data/users/alice/og.png?v=1795000000"


def test_a_rendered_card_is_the_image_drawn_large(og):
  # AOTY-91: the share card, 1080 square, as summary_large_image.
  t = tags(og.user_page(user_chart(), ORIGIN, BASE, card=CARD))
  assert t.meta["og:image"] == CARD and t.meta["og:image:type"] == "image/png"
  assert (t.meta["og:image:width"], t.meta["og:image:height"]) == ("1080", "1080")
  assert t.meta["twitter:card"] == "summary_large_image"
  g = tags(og.group_page(group_chart(), ORIGIN, BASE, card=CARD))
  assert g.meta["og:image"] == CARD and g.meta["twitter:card"] == "summary_large_image"


def test_without_a_card_the_page_is_aoty_85s(og):
  assert og.user_page(user_chart(), ORIGIN, BASE, card=None) == og.user_page(user_chart(), ORIGIN, BASE)
  t = tags(og.user_page(user_chart(), ORIGIN, BASE, card="http://not-https/og.png"))
  assert (
    t.meta["og:image"] == COVER and t.meta["twitter:card"] == "summary" and "og:image:width" not in t.meta
  )


def test_a_missing_display_name_falls_back_to_the_username(og):
  t = tags(og.user_page(user_chart(display=None), ORIGIN, BASE))
  assert t.meta["og:url"] == "https://kamp.fm/toys/aoty/user/alice"
