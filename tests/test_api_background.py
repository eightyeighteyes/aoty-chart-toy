"""Page backgrounds (AOTY-96): a new page starts with a random one, stored; the owner of a user or
group page sets it (POST users/<name>/background, groups/<id>/background), as with themes."""

import importlib.util
import random

import api_core
import pytest
from test_api_groups import BASE, HANDLER, NOW, call, deps, group_with_members, known

from common import backgrounds


@pytest.fixture
def h(monkeypatch):
  monkeypatch.setenv("BASE_PATH", BASE)
  spec = importlib.util.spec_from_file_location("api_handler_background", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def owner(h, **kw):
  d = deps(h, **kw)
  known(d.ddb, "alice", "bob")
  return d


# ---- the allowlist ---------------------------------------------------------------------------


def test_the_ids_are_none_and_the_engines():
  assert backgrounds.NONE == "none" and backgrounds.NONE not in backgrounds.ENGINES
  assert backgrounds.IDS == {"none", *backgrounds.ENGINES}
  assert backgrounds.valid("none") and backgrounds.valid("borealis")
  for bad in ("Borealis", "", None, ["borealis"], "</style>"):
    assert not backgrounds.valid(bad)
  # what a page shows: an engine, or nothing (none, junk, an engine since removed)
  assert backgrounds.engine("borealis") == "borealis"
  assert [backgrounds.engine(x) for x in ("none", "gone", None)] == [None, None, None]
  rng = random.Random(7)
  assert {backgrounds.random_engine(rng) for _ in range(50)} == set(backgrounds.ENGINES)


# ---- a user's page ----------------------------------------------------------------------------


def test_the_route_is_post_only():
  assert api_core.route("POST", "/b/api/users/Alice/background", "/b/") == ("background", "alice")
  assert api_core.route("GET", "/b/api/users/alice/background", "/b/") == ("method", None)
  assert "background" in api_core.USER_ACTIONS


def test_the_owner_sets_a_background_and_the_chart_is_restyled(h):
  d = owner(h)
  assert call(h, d, "POST", "users/alice/background", {"background": "borealis"}) == (
    200,
    {"background": "borealis"},
  )
  row = d.ddb.rows["alice"]
  assert (row["background"], row["background_set_at"]) == ("borealis", NOW)
  assert d.lam.events == [{"username": "alice", "restyle": True}]
  assert d.sqs.sent == []
  assert call(h, d, "POST", "users/alice/background", {"background": "none"}) == (200, {"background": "none"})
  assert d.ddb.rows["alice"]["background"] == "none"


@pytest.mark.parametrize(
  "body", [{"background": "nope"}, {"background": "Borealis"}, {"background": None}, {}]
)
def test_unknown_backgrounds_are_refused(h, body):
  d = owner(h)
  status, out = call(h, d, "POST", "users/alice/background", body)
  assert status == 400 and out["error"] == "unknown_background" and "none" in out["backgrounds"]
  assert "background" not in d.ddb.rows["alice"] and d.lam.events == []


def test_only_the_owner_sets_it_and_only_with_a_row(h):
  d = owner(h)
  assert call(h, d, "POST", "users/alice/background", {"background": "none"}, user="bob") == (
    403,
    {"error": "not_owner"},
  )
  assert call(h, d, "POST", "users/alice/background", {"background": "none"}, user=None) == (
    401,
    {"error": "signed_out"},
  )
  assert "background" not in d.ddb.rows["alice"]
  d = deps(h)  # carol signed in but has never synced
  assert call(h, d, "POST", "users/carol/background", {"background": "none"}, user="carol") == (
    404,
    {"error": "no_chart"},
  )
  assert "carol" not in d.ddb.rows


def test_the_writes_kill_switch_covers_it(h):
  d = owner(h, env={"GROUP_WRITES": "false"})
  assert call(h, d, "POST", "users/alice/background", {"background": "none"}) == (
    503,
    {"error": "writes_off"},
  )


def test_a_new_users_first_sync_stores_a_random_background_once(h):
  d = deps(h)
  d.rng = random.Random(3)
  status, _ = call(h, d, "POST", "users/carol/sync", {}, user=None)
  assert status == 202
  assert d.ddb.rows["carol"]["background"] in backgrounds.ENGINES
  # Every visitor reads the same one, first visit included.
  status, body = call(h, d, "GET", "users/carol/status", user=None, ctype=None)
  assert status == 200 and body["background"] == d.ddb.rows["carol"]["background"]


def test_a_new_users_own_pick_is_never_re_rolled(h):
  d = deps(h)
  d.ddb.rows["carol"] = {"username": "carol", "background": "none"}  # a row, but never synced
  status, _ = call(h, d, "POST", "users/carol/sync", {}, user=None)
  assert status == 202 and d.ddb.rows["carol"]["background"] == "none"


def test_an_existing_page_with_nothing_stored_keeps_none(h):
  d = owner(h)
  d.ddb.rows["alice"]["sync_requested_at"] = NOW - 10**6  # past the cooldown: a sync is queued
  status, _ = call(h, d, "POST", "users/alice/sync", {}, user=None)
  assert status == 202 and "background" not in d.ddb.rows["alice"]
  _, body = call(h, d, "GET", "users/alice/status", user=None, ctype=None)
  assert "background" not in body


def test_the_status_carries_only_an_engine(h):
  d = owner(h)
  for stored, shown in (("borealis", "borealis"), ("none", None), ("gone", None)):
    d.ddb.rows["alice"]["background"] = stored
    _, body = call(h, d, "GET", "users/alice/status", user=None, ctype=None)
    assert body.get("background") == shown, stored


# ---- a group's page ---------------------------------------------------------------------------


def test_a_new_group_starts_with_a_random_background(h):
  d = deps(h)
  d.rng = random.Random(5)
  status, body = call(h, d, "POST", "groups", {"name": "The Server"})
  assert status == 201
  assert d.ddb.meta("the-server")["background"] in backgrounds.ENGINES


def test_the_owner_sets_the_groups_background(h):
  d = group_with_members(h)  # alice owns g; bob is a member
  assert api_core.route("POST", f"{BASE}api/groups/g/background", BASE) == ("group_background", "g")
  assert api_core.route("GET", f"{BASE}api/groups/g/background", BASE) == ("method", None)
  assert call(h, d, "POST", "groups/g/background", {"background": "borealis"}) == (
    200,
    {"background": "borealis"},
  )
  assert (d.ddb.meta("g")["background"], d.ddb.meta("g")["background_set_at"]) == ("borealis", NOW)
  assert d.lam.events == [{"group": "g", "restyle": True}]
  assert call(h, d, "POST", "groups/g/background", {"background": "none"}, user="bob") == (
    403,
    {"error": "not your group"},
  )
  status, body = call(h, d, "POST", "groups/g/background", {"background": "nope"})
  assert status == 400 and body["error"] == "unknown_background"
  assert d.ddb.meta("g")["background"] == "borealis"


def test_group_get_carries_only_an_engine(h):
  d = group_with_members(h)
  for stored, shown in (("borealis", "borealis"), ("none", None), ("gone", None)):
    d.ddb.meta("g")["background"] = stored
    _, body = call(h, d, "GET", "groups/g", user=None, ctype=None)
    assert body.get("background") == shown, stored


def test_a_background_saved_while_a_rename_runs_is_not_lost(h):
  d = group_with_members(h)
  d.ddb.meta("g").update(background="none", background_set_at=NOW - 50)

  def save_meanwhile(ddb):  # lands between the rename's read and its transaction
    ddb.meta("g").update(background="borealis", background_set_at=NOW)

  d.ddb.before_transact = save_meanwhile
  status, body = call(h, d, "PATCH", "groups/g", {"name": "The Crew"})
  assert status == 200 and body["id"] == "the-crew"
  assert d.ddb.meta("the-crew")["background"] == "borealis"  # the retry read the newer one
