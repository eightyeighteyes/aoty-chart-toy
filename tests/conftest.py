"""Test setup. Runs before any test module imports aoty.

aoty reads .env and the environment at import time (and _load_dotenv only fills keys that
aren't set), so blanking these first keeps a developer's real .env out of the tests and
makes local runs match CI.
"""

import json
import os
from pathlib import Path

import pytest

for _key in ("LASTFM_API_KEY", "LASTFM_USERS", "MB_CONTACT"):
  os.environ[_key] = ""

FIXTURES = Path(__file__).parent / "fixtures" / "mb"


@pytest.fixture
def mb_fixture():
  """Load a recorded MusicBrainz search (tools/record_mb_fixtures.py) by name."""

  def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["results"]

  return load
