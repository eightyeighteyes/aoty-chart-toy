"""POST api/users/<name>/theme (AOTY-65): only the signed-in owner sets their page's theme."""

import importlib.util

import api_core
import pytest
from test_api_groups import BASE, HANDLER, NOW, FakeLambda, call, deps, known


@pytest.fixture
def h(monkeypatch):
  monkeypatch.setenv("BASE_PATH", BASE)
  spec = importlib.util.spec_from_file_location("api_handler_theme", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def owner(h, **kw):
  d = deps(h, **kw)
  known(d.ddb, "alice", "bob")
  return d


def test_the_route_is_post_only():
  assert api_core.route("POST", "/b/api/users/Alice/theme", "/b/") == ("theme", "alice")
  assert api_core.route("GET", "/b/api/users/alice/theme", "/b/") == ("method", None)
  assert api_core.route("PUT", "/b/api/users/alice/theme", "/b/") == ("method", None)
  assert "theme" in api_core.USER_ACTIONS


def test_the_owner_sets_a_theme_and_the_chart_is_restyled(h):
  d = owner(h)
  assert call(h, d, "POST", "users/alice/theme", {"theme": "beach-house"}) == (200, {"theme": "beach-house"})
  assert (d.ddb.rows["alice"]["theme"], d.ddb.rows["alice"]["theme_set_at"]) == ("beach-house", NOW)
  assert d.lam.events == [{"username": "alice", "restyle": True}]
  assert d.sqs.sent == []  # a theme is never a sync


def test_album_of_the_year_and_the_default_are_themes_too(h):
  d = owner(h)
  for theme in ("match1", "poster"):
    assert call(h, d, "POST", "users/alice/theme", {"theme": theme}) == (200, {"theme": theme})
    assert d.ddb.rows["alice"]["theme"] == theme


@pytest.mark.parametrize(
  "body", [{"theme": "nope"}, {"theme": "Kamp"}, {"theme": None}, {}, {"theme": ["kamp"]}]
)
def test_unknown_themes_are_refused(h, body):
  d = owner(h)
  status, out = call(h, d, "POST", "users/alice/theme", body)
  assert status == 400 and out["error"] == "unknown_theme" and "kamp" in out["themes"]
  assert "theme" not in d.ddb.rows["alice"] and d.lam.events == []


def test_someone_else_cannot_set_your_theme(h):
  d = owner(h)
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}, user="bob") == (
    403,
    {"error": "not_owner"},
  )
  assert "theme" not in d.ddb.rows["alice"] and d.lam.events == []


def test_signed_out_and_revoked_sessions_are_401(h):
  d = owner(h)
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}, user=None) == (
    401,
    {"error": "signed_out"},
  )
  d.ddb.groups[("session#alice", "meta")] = {"pk": "session#alice", "sk": "meta", "revoked_at": NOW}
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}) == (401, {"error": "signed_out"})
  assert "theme" not in d.ddb.rows["alice"]


def test_the_writes_kill_switch_and_unreadable_keys_are_503(h):
  d = owner(h, env={"GROUP_WRITES": "false"})
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}) == (503, {"error": "writes_off"})
  d = owner(h, auth={})
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}) == (503, {"error": "signin_unavailable"})


def test_json_only(h):
  d = owner(h)
  status, _ = call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}, ctype="text/plain")
  assert status == 415 and "theme" not in d.ddb.rows["alice"]


def test_no_users_row_means_no_chart_to_theme(h):
  d = deps(h)  # alice signed in but has never synced
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}) == (404, {"error": "no_chart"})
  assert "alice" not in d.ddb.rows and d.lam.events == []


def test_a_failed_restyle_request_still_saves(h):
  d = owner(h, lam=FakeLambda(fail=True))
  assert call(h, d, "POST", "users/alice/theme", {"theme": "kamp"}) == (200, {"theme": "kamp"})
  assert d.ddb.rows["alice"]["theme"] == "kamp"  # the next build carries it
