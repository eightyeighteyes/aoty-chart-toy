"""Page backgrounds an owner may pick for their chart page (AOTY-96). Pure.

ENGINES are the ported kamp backgrounds, the same ids as scripts/build_shell.py BACKGROUNDS (which
publishes their files; tests/test_backgrounds.py checks the two lists match). "none" is the other
choice: no background at all. Only an id from this set is ever stored (users row, group meta) or
published (chart.json "background"), so nothing free-form reaches a page.

User decisions (2026-11-28): a new page starts with a random engine (random_engine, picked by the
api when it first queues a new user's sync or creates a group) and keeps it until its owner picks
another; a page with nothing stored (every page from before this) has none.
"""

import random

NONE = "none"
ENGINES = ("bokeh", "borealis", "chapel")
IDS = frozenset((NONE, *ENGINES))


def valid(background):
  """True for an id an owner may pick: none or an engine."""
  return isinstance(background, str) and background in IDS


def engine(background):
  """The id if it's an engine, else None: what a page shows. Anything stored that isn't (none, an
  engine since removed, a deploy skew) shows nothing."""
  return background if isinstance(background, str) and background in ENGINES else None


def random_engine(rng=random):
  """A new page's starting background."""
  return rng.choice(ENGINES)
