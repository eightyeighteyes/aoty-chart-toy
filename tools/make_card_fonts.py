"""Build the card Lambda's fonts (AOTY-91): static instances of the share card's web fonts.

  .venv/bin/python tools/make_card_fonts.py <downloads dir>

The share card (web/src/sharecard.js) draws with Bricolage Grotesque 800 (titles), Inter 500/600/700
(artist, caption, link, badges) and DM Sans 500 ("a kamp toy"); gabbie's titles are Arial 700, for
which Liberation Sans Bold (metric-compatible, OFL) stands in. Google Fonts ships the first three as
variable fonts, and canvas on the server may draw a variable font's default instance whatever the
CSS weight, so this cuts one static TTF per weight the card uses.

Browsers pick the optical size from the font size (font-optical-sizing: auto); the card draws
titles at 40-64 px and body text at 18-34 px, so the instances pin opsz near those.

The downloads dir holds, as fetched from github.com/google/fonts (ofl/<family>/) and the
liberation-fonts 2.1.5 release: bricolage.ttf, inter.ttf, dmsans.ttf, LiberationSans-Bold.ttf,
and each one's licence (bricolage-OFL.txt, inter-OFL.txt, dmsans-OFL.txt, liberation-LICENSE).
Output: lambdas/card/fonts/*.ttf, their licences, and coverage.json: the code points every font
the card might draw a text with has a glyph for (the Lambda's glyph check, AOTY-91).
"""

import json
import shutil
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parent.parent / "lambdas" / "card" / "fonts"

# (source, output, axis pins). The output names say family and weight; the Lambda registers each
# under its CSS family name (lambdas/card/handler.mjs FONTS).
INSTANCES = [
  ("bricolage.ttf", "BricolageGrotesque-800.ttf", {"wght": 800, "opsz": 56, "wdth": 100}),
  ("inter.ttf", "Inter-500.ttf", {"wght": 500, "opsz": 28}),
  ("inter.ttf", "Inter-600.ttf", {"wght": 600, "opsz": 28}),
  ("inter.ttf", "Inter-700.ttf", {"wght": 700, "opsz": 28}),
  ("dmsans.ttf", "DMSans-500.ttf", {"wght": 500, "opsz": 28}),
]
COPIES = [("LiberationSans-Bold.ttf", "LiberationSans-Bold.ttf")]
LICENCES = [
  ("bricolage-OFL.txt", "BricolageGrotesque-OFL.txt"),
  ("inter-OFL.txt", "Inter-OFL.txt"),
  ("dmsans-OFL.txt", "DMSans-OFL.txt"),
  ("liberation-LICENSE", "LiberationSans-LICENSE.txt"),
]
# The fonts a title, an artist, a name or a link can be drawn in: every text must be covered by all
# of them (the brand line, "a kamp toy", is fixed text).
TEXT_FONTS = ["BricolageGrotesque-800.ttf", "LiberationSans-Bold.ttf", "Inter-500.ttf", "Inter-600.ttf",
              "Inter-700.ttf"]  # fmt: skip


def ranges(codepoints):
  """Sorted code points -> [[first, last], ...] runs."""
  out = []
  for cp in sorted(codepoints):
    if out and cp == out[-1][1] + 1:
      out[-1][1] = cp
    else:
      out.append([cp, cp])
  return out


def main(src):
  src = Path(src)
  OUT.mkdir(parents=True, exist_ok=True)
  for source, name, pins in INSTANCES:
    # Names stay the variable font's: the Lambda registers each file under its CSS family name, and
    # the weight comes from OS/2 (opsz 56 has no STAT value to name the instance by anyway).
    font = instancer.instantiateVariableFont(TTFont(src / source), pins)
    font["OS/2"].usWeightClass = pins["wght"]
    font.save(OUT / name)
  for source, name in COPIES + LICENCES:
    shutil.copyfile(src / source, OUT / name)
  common = None
  for name in TEXT_FONTS:
    cmap = set(TTFont(OUT / name).getBestCmap())
    common = cmap if common is None else common & cmap
  coverage = {"fonts": TEXT_FONTS, "ranges": ranges(common)}
  (OUT / "coverage.json").write_text(json.dumps(coverage, separators=(",", ":")) + "\n")
  print(f"{len(INSTANCES) + len(COPIES)} fonts, {len(common)} code points covered by all text fonts")


if __name__ == "__main__":
  main(sys.argv[1])
