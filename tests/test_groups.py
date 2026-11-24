"""The groups table's pure rules (lambdas/common/groups.py, AOTY-48)."""

import pytest

from common import groups


@pytest.mark.parametrize(
  ("name", "slug"),
  [
    ("New Bands for Old Heads!", "new-bands-for-old-heads"),
    ("  The   Server ", "the-server"),
    ("Café Société", "cafe-societe"),
    ("日本", ""),
    ("---", ""),
    ("x" * 70, "x" * 50),
    ("a" * 49 + " b", "a" * 49),  # truncation never leaves a trailing dash
  ],
)
def test_slugify(name, slug):
  assert groups.slugify(name) == slug
  assert slug == "" or groups.SLUG_RE.match(slug)


def test_clean_name_trims_collapses_and_bounds():
  assert groups.clean_name("  the   server ") == "the server"
  assert groups.clean_name("x" * 60) == "x" * 60
  for bad in ("", "   ", "x" * 61, "tab\x07bell", None, 5):
    assert groups.clean_name(bad) is None


def test_slug_for_prefers_a_requested_slug_and_refuses_unusable_ones():
  assert groups.slug_for("The Server") == "the-server"
  assert groups.slug_for("日本") is None
  assert groups.slug_for("日本", "nihon") == "nihon"
  assert groups.slug_for("The Server", " Server-2 ") == "server-2"
  assert groups.slug_for("The Server", "-bad") is None
  assert groups.slug_for("The Server", "../users") is None


def test_keys_round_trip():
  assert groups.pk("the-server") == "group#the-server"
  assert groups.slug_of("group#the-server") == "the-server"
  assert groups.slug_of("group#../x") is None
  assert groups.slug_of("user#x") is None


def test_split_items():
  items = [
    {"pk": "group#g", "sk": "member#bob"},
    {"pk": "group#g", "sk": "meta", "name": "g", "owner": "alice"},
    {"pk": "group#g", "sk": "member#Alice"},
  ]
  meta, members = groups.split_items(items)
  assert meta["owner"] == "alice"
  assert members == ["Alice", "bob"]
  assert groups.split_items([]) == (None, [])


@pytest.mark.parametrize(
  ("row", "state"),
  [
    ({}, "waiting"),  # never synced
    ({"state": "syncing"}, "waiting"),
    ({"state": "syncing", "sync_finished_at": 1}, "waiting"),  # first chart not built yet
    ({"state": "syncing", "sync_finished_at": 1, "chart_built_at": 2}, "ready"),  # a re-sync
    ({"state": "done", "sync_finished_at": 1}, "ready"),
    ({"state": "private", "sync_finished_at": 1}, "unavailable"),
    ({"state": "not_found"}, "unavailable"),
  ],
)
def test_member_state(row, state):
  assert groups.member_state(row) == state
