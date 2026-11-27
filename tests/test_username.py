"""common/username.py: the one username rule, and the copies that must keep step with it (AOTY-80)."""

import re
from pathlib import Path

import pytest

from common.username import USERNAME_RE

ROOT = Path(__file__).resolve().parent.parent
INTRO_JS = ROOT / "web" / "src" / "intro.js"
DEPLOY_YML = ROOT / ".github" / "workflows" / "deploy.yml"


@pytest.mark.parametrize("name", ["alice", "dj_k.9-x", "_korps_", "__x", "_a_", "a_", "x" * 64, "0day"])
def test_valid_names(name):
  assert USERNAME_RE.match(name)


@pytest.mark.parametrize(
  "name", ["", "-lead", ".lead", ".", "..", "a b", "a/b", "a%2Fb", "Alice", "<script>", "x" * 65, "-smoke-"]
)
def test_invalid_names(name):
  assert not USERNAME_RE.match(name)


def test_the_page_uses_the_same_rule():
  literal = re.search(r"const USERNAME_RE = /(.+)/;", INTRO_JS.read_text()).group(1)
  assert literal == USERNAME_RE.pattern


def test_the_deploy_smoke_name_stays_invalid():
  # CD's smoke test POSTs sync for this name and expects a 400 with no Last.fm call. "__smoke__"
  # was invalid until names could start with "_"; a valid name would reach Last.fm on every deploy.
  names = set(re.findall(r"api/users/([^/\"]+)/sync", DEPLOY_YML.read_text()))
  assert names == {"-smoke-"}
  assert not any(USERNAME_RE.match(n) for n in names)
