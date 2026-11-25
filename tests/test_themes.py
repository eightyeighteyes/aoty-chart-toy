"""common/themes.py: the theme ids the api and chart builder accept match the page's (AOTY-65)."""

import re
from pathlib import Path

from common import themes

THEME_JS = Path(__file__).resolve().parent.parent / "web" / "src" / "theme.js"


def js_ids():
  src = THEME_JS.read_text()
  block = re.search(r"const THEMES = \{(.*?)\n  \};", src, re.S).group(1)
  presets = re.findall(r'^\s{4}"([a-z0-9-]+)": \{', block, re.M)
  match = re.search(r'const MATCH = "([a-z0-9-]+)";', src).group(1)
  return presets, match


def test_the_ids_match_the_page():
  presets, match = js_ids()
  assert tuple(presets) == themes.PRESETS
  assert match == themes.MATCH
  assert themes.IDS == {*presets, match}


def test_only_known_ids_are_valid():
  assert themes.valid("beach-house") and themes.valid("match1") and themes.valid("poster")
  for bad in ("", "Beach-House", "nope", None, 3, ["kamp"], "kamp; color: red"):
    assert not themes.valid(bad)
