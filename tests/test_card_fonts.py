"""The card Lambda's fonts (lambdas/card/fonts, AOTY-91): names, weights and glyph coverage.

The Lambda has no system fonts, so a family sharecard.js asks for that isn't registered, or a
character none of them has, would draw wrong or as an empty box. tools/make_card_fonts.py builds them.
"""

import json
import re
from pathlib import Path

import pytest

fontTools = pytest.importorskip("fontTools.ttLib")

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "lambdas" / "card" / "fonts"
HANDLER = (ROOT / "lambdas" / "card" / "handler.mjs").read_text()
SHARECARD = (ROOT / "web" / "src" / "sharecard.js").read_text()


def registered():
  """[(file, family)] from the handler's FONTS list."""
  block = re.search(r"const FONTS = \[(.*?)\n\];", HANDLER, re.S).group(1)
  return re.findall(r'\["([^"]+\.ttf)", "([^"]+)"\]', block)


def test_each_font_registers_under_its_own_family_name_with_its_weight():
  # @napi-rs/canvas files a font under the name inside it, whatever alias it's given.
  for file, family in registered():
    font = fontTools.TTFont(FONTS / file)
    assert font["name"].getBestFamilyName() == family or font["name"].getDebugName(16) == family, file
    weight = int(re.search(r"-(\d{3})\.ttf$", file).group(1)) if re.search(r"-\d{3}\.ttf$", file) else 700
    assert font["OS/2"].usWeightClass == weight, file
    assert "fvar" not in font, f"{file} is still variable"


def test_every_family_and_weight_the_card_draws_with_is_bundled():
  have = {(family, int(fontTools.TTFont(FONTS / f)["OS/2"].usWeightClass)) for f, family in registered()}
  # sharecard.js: display 800 (Bricolage) or 700 (gabbie's stack, reaching Liberation Sans), body
  # 500/600/700 in Inter, the brand 500 in DM Sans.
  assert {("Bricolage Grotesque", 800), ("Liberation Sans", 700), ("Inter", 500), ("Inter", 600), ("Inter", 700),
          ("DM Sans", 500)} <= have  # fmt: skip
  for family in ('"Liberation Sans"', '"Bricolage Grotesque"', '"DM Sans"', "Inter"):
    assert family in SHARECARD, family


def test_the_coverage_is_what_every_text_font_has():
  coverage = json.loads((FONTS / "coverage.json").read_text())
  common = None
  for name in coverage["fonts"]:
    cmap = set(fontTools.TTFont(FONTS / name).getBestCmap())
    common = cmap if common is None else common & cmap
  listed = {cp for lo, hi in coverage["ranges"] for cp in range(lo, hi + 1)}
  assert listed == common
  # What every card prints: the year, an apostrophe, the caption's middle dot, an ellipsis for a cut
  # title, the no-break space tying a rank to its title, and a URL's characters.
  for ch in "0123456789'’·… :/._-Ááéíóúñçøæß":
    assert ord(ch) in listed, repr(ch)


def test_every_font_has_its_licence():
  for name in ("BricolageGrotesque-OFL.txt", "Inter-OFL.txt", "DMSans-OFL.txt", "LiberationSans-LICENSE.txt"):
    assert "SIL Open Font License" in (FONTS / name).read_text()
