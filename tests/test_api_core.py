"""The API's pure core (lambdas/api/api_core.py): routing, the sync decision, status stages."""

import api_core as core
import pytest

BASE = "/toys/aoty-test/"
NOW = 1_795_400_000
COOL = 600


# ---- routing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
  "method, path, expected",
  [
    ("GET", f"{BASE}api/health", ("health", None)),
    ("HEAD", f"{BASE}api/health", ("health", None)),
    ("POST", f"{BASE}api/health", ("method", None)),
    ("POST", f"{BASE}api/users/AliCe/sync", ("sync", "alice")),
    ("GET", f"{BASE}api/users/alice/sync", ("method", None)),
    ("GET", f"{BASE}api/users/alice/status", ("status", "alice")),
    ("GET", f"{BASE}api/users/alice/status/", ("status", "alice")),
    ("POST", f"{BASE}api/users/alice/status", ("method", None)),
    ("GET", f"{BASE}api/users/a%20b/status", ("status", "a b")),
    ("GET", f"{BASE}api/users/alice/other", ("not_found", None)),
    ("GET", f"{BASE}api/users//status", ("not_found", None)),
    ("GET", "/toys/aoty/api/health", ("not_found", None)),
    ("GET", f"{BASE}api/nope", ("not_found", None)),
  ],
)
def test_route(method, path, expected):
  assert core.route(method, path, BASE) == expected


def test_routed_names_still_need_validation():
  _, name = core.route("GET", f"{BASE}api/users/__smoke__/status", BASE)
  assert not core.USERNAME_RE.match(name)


# ---- the sync decision ----------------------------------------------------------------


def test_a_new_user_is_queued():
  assert core.decide_sync({}, NOW, COOL) == "queue"


@pytest.mark.parametrize("state", ["queued", "syncing", "retrying"])
def test_a_running_sync_is_in_progress(state):
  row = {"state": state, "sync_requested_at": NOW - 60}
  assert core.decide_sync(row, NOW, COOL) == "in_progress"


@pytest.mark.parametrize("state", ["queued", "syncing", "retrying"])
def test_a_stuck_sync_no_longer_blocks(state):
  row = {
    "state": state,
    "sync_requested_at": NOW - core.STUCK_AFTER,
    "sync_started_at": NOW - core.STUCK_AFTER,
  }
  assert core.decide_sync(row, NOW, COOL) == "queue"


def test_a_long_sync_that_started_recently_is_still_running():
  row = {"state": "syncing", "sync_requested_at": NOW - core.STUCK_AFTER - 60, "sync_started_at": NOW - 120}
  assert core.decide_sync(row, NOW, COOL) == "in_progress"


@pytest.mark.parametrize("state", ["done", "error", "not_found", "private"])
def test_the_cooldown_holds_after_any_finished_sync(state):
  assert core.decide_sync({"state": state, "sync_requested_at": NOW - COOL + 1}, NOW, COOL) == "cooldown"
  assert core.decide_sync({"state": state, "sync_requested_at": NOW - COOL}, NOW, COOL) == "queue"


def test_a_user_synced_by_hand_has_no_cooldown():
  assert core.decide_sync({"state": "done", "last_synced_at": NOW - 5}, NOW, COOL) == "queue"


# ---- stages ---------------------------------------------------------------------------------


def done(**kw):
  base = {"state": "done", "sync_requested_at": NOW - 300, "sync_started_at": NOW - 290,
          "sync_finished_at": NOW - 200, "chart_built_at": NOW - 190, "chart_pending": 0}  # fmt: skip
  return {**base, **kw}


def test_stages_follow_the_pipeline():
  assert core.stage({"state": "queued", "sync_requested_at": NOW - 5}, NOW) == "queued"
  assert (
    core.stage({"state": "syncing", "sync_requested_at": NOW - 9, "sync_started_at": NOW - 5}, NOW)
    == "reading"
  )
  assert core.stage({"state": "retrying", "sync_requested_at": NOW - 90}, NOW) == "reading"
  assert core.stage(done(chart_built_at=NOW - 900), NOW) == "building"  # an older chart than the sync
  assert core.stage(done(chart_built_at=None), NOW) == "building"  # a first chart, still coming
  assert core.stage(done(chart_pending=12), NOW) == "dating"
  assert core.stage(done(), NOW) == "idle"


@pytest.mark.parametrize("state", ["error", "not_found", "private"])
def test_failures_are_errors(state):
  assert core.stage({"state": state, "sync_finished_at": NOW - 5}, NOW) == "error"


def test_building_gives_up_after_a_while():
  assert core.stage(done(sync_finished_at=NOW - core.BUILDING_MAX, chart_built_at=NOW - 9999), NOW) == "idle"


def test_dating_goes_stale_when_charts_stop_rebuilding():
  row = done(chart_pending=3, sync_finished_at=NOW - 3000, chart_built_at=NOW - core.DATING_FRESH)
  assert core.stage(row, NOW) == "idle"


def test_a_stuck_sync_reads_as_idle():
  row = {
    "state": "syncing",
    "sync_requested_at": NOW - core.STUCK_AFTER,
    "sync_started_at": NOW - core.STUCK_AFTER,
  }
  assert core.stage(row, NOW) == "idle"


# ---- new plays and the status body --------------------------------------------------------


def test_new_plays_is_the_sync_total_minus_the_charts():
  assert core.new_plays(done(progress={"plays": 1042}, chart_plays=1000)) == 42


def test_new_plays_is_never_negative_or_mid_sync():
  assert core.new_plays(done(progress={"plays": 990}, chart_plays=1000)) == 0  # settled weeks shrank
  assert core.new_plays({"state": "syncing", "progress": {"plays": 50}, "chart_plays": 1000}) == 0
  assert core.new_plays(done(progress={"plays": 50})) == 0  # no chart yet


def test_status_body():
  row = done(username="alice", display_name="AliCe", progress={"weeks_done": 4, "weeks_total": 4, "plays": 1042},
             chart_plays=1000, chart_albums=1700, chart_pending=20, chart_hits=560,
             sync_finished_at=NOW - 30, chart_built_at=NOW - 60, last_synced_at=NOW - 290)  # fmt: skip
  out = core.status(row, NOW, COOL)
  assert out == {
    "stage": "building",
    "display_name": "AliCe",
    "weeks": {"done": 4, "total": 4},
    "scrobbles": 1042,
    "albums": {"checked": 1680, "total": 1700, "hits": 560},
    "new_plays": 42,
    "last_synced_at": NOW - 290,
    "chart_built_at": NOW - 60,
    "cooldown_until": NOW - 300 + COOL,
  }


def test_status_extras_for_queued_and_error():
  queued = core.status({"state": "queued", "sync_requested_at": NOW - 5}, NOW, COOL, queue_depth=7)
  assert queued["stage"] == "queued" and queued["queue_ahead_approx"] == 7
  failed = core.status({"state": "private", "sync_finished_at": NOW}, NOW, COOL, queue_depth=7)
  assert failed["reason"] == "private" and "queue_ahead_approx" not in failed


def test_status_without_chart_fields():
  out = core.status({"username": "bob", "state": "queued", "sync_requested_at": NOW}, NOW, COOL)
  assert out["albums"] == {"checked": None, "total": None, "hits": None}
  assert out["display_name"] == "bob" and out["new_plays"] == 0
