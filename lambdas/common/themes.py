"""Page themes an owner may pick for their chart page (AOTY-65). Pure.

The ids are web/src/theme.js's IDS: the presets (the default "poster" plus kamp's dark palettes)
and "match1", shown as "album of the year", which the visitor's browser derives from the chart's
#1 cover. tests/test_themes.py checks the two lists match. Only an id from this set is ever
stored (users row) or published (chart.json "theme"), so no free-form colour or CSS reaches a page.
"""

DEFAULT = "poster"
MATCH = "match1"
PRESETS = (
  DEFAULT,
  "kamp",
  "strawberry-switchblade",
  "beach-house",
  "blackpink",
  "deep-purple",
  "green-day",
  "foxy-brown",
  "golden-smog",
  # Light presets (AOTY-75), named after bands and musicians like the dark ones.
  "white-stripes",
  "tangerine-dream",
  "yellow-magic-orchestra",
  "mint-royale",
  "the-blue-nile",
  "lavender-diamond",
  "gabbie",
)
IDS = frozenset((*PRESETS, MATCH))


def valid(theme):
  """True for an id a page may carry."""
  return isinstance(theme, str) and theme in IDS
