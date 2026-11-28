"""The page backgrounds' one list (AOTY-96): common.backgrounds, build_shell and analytics agree."""

import re
from pathlib import Path

from common import backgrounds
from scripts import build_shell

KAMPSTATS = Path(__file__).resolve().parent.parent / "web" / "src" / "kampstats.js"


def test_the_engines_are_the_ones_the_shell_publishes():
  assert set(backgrounds.ENGINES) == set(build_shell.BACKGROUNDS)
  assert backgrounds.NONE not in build_shell.BACKGROUNDS  # "none" is never an engine


def test_analytics_knows_every_choice():
  # kampstats.EVENTS drops a param value it doesn't list, so a new engine must be added there too.
  src = KAMPSTATS.read_text()
  m = re.search(r"background_set: \{background: \[([^\]]*)\]\}", src)
  assert m, "kampstats.js has no background_set event"
  listed = set(re.findall(r'"([^"]+)"', m.group(1)))
  assert listed == backgrounds.IDS
