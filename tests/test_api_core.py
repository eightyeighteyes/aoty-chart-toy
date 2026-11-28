"""The API's pure core (lambdas/api/api_core.py): routing, the sync decision, status stages."""

import json
from urllib.parse import parse_qsl, urlsplit

import api_core as core
import pytest


@pytest.mark.parametrize(
  "status,body,new,expected",
  [
    (202, {"queued": True}, False, "queued"),
    (202, {"queued": True}, True, "new_user"),
    (200, {"queued": False, "reason": "cooldown"}, False, "cooldown"),
    (200, {"queued": False, "reason": "paused"}, False, "paused"),
    (503, {"error": "busy"}, True, "busy"),
    (429, {"error": "too_many_new"}, True, "too_many_new"),
    (403, {"error": "forbidden"}, False, "origin_refused"),
    (415, {"error": "send content-type: application/json"}, False, "not_json"),
    (404, {"error": "unknown user"}, False, "unknown_user"),
    (400, {"error": "invalid username"}, False, "bad_name"),
    (500, None, False, "500"),
  ],
)
def test_sync_outcome_names_every_answer(status, body, new, expected):
  assert core.sync_outcome(status, body, new) == expected


@pytest.mark.parametrize(
  "status, body, expected",
  [
    (202, {"queued": True}, "queued"),
    (200, {"reason": "cooldown"}, "cooldown"),
    (200, {"reason": "paused"}, "paused"),
    (503, {"error": "queue_unavailable"}, "not_queued"),
    (503, {"error": "busy"}, "busy"),
    (500, None, "unavailable"),
  ],
)
def test_sync_word_names_a_member_adds_sync(status, body, expected):
  assert core.sync_word(status, body) == expected


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
    ("GET", f"{BASE}api/auth/start", ("auth_start", None)),
    ("POST", f"{BASE}api/auth/start", ("method", None)),
    ("GET", f"{BASE}api/me", ("me", None)),
    ("GET", f"{BASE}api/me/", ("me", None)),
    ("POST", f"{BASE}api/me", ("method", None)),
    ("POST", f"{BASE}api/auth/signout", ("auth_signout", None)),
    ("GET", f"{BASE}api/auth/signout", ("method", None)),
    ("GET", f"{BASE}api/auth/callback/abc_DEF-123", ("auth_callback", "abc_DEF-123")),
    ("POST", f"{BASE}api/auth/callback/abc", ("method", None)),
    ("GET", f"{BASE}api/auth/callback/", ("not_found", None)),
    ("GET", f"{BASE}api/auth/callback/a.b", ("not_found", None)),
    ("GET", f"{BASE}api/auth/callback/{'a' * 65}", ("not_found", None)),
    # groups (AOTY-48)
    ("POST", f"{BASE}api/groups", ("group_create", None)),
    ("POST", f"{BASE}api/groups/", ("group_create", None)),
    ("GET", f"{BASE}api/groups", ("method", None)),
    ("GET", f"{BASE}api/groups/The-Server", ("group_get", "the-server")),
    ("PATCH", f"{BASE}api/groups/g", ("group_rename", "g")),
    ("DELETE", f"{BASE}api/groups/g/", ("group_delete", "g")),
    ("POST", f"{BASE}api/groups/g", ("method", None)),
    ("POST", f"{BASE}api/groups/g/members", ("member_add", "g")),
    ("GET", f"{BASE}api/groups/g/members", ("method", None)),
    ("DELETE", f"{BASE}api/groups/g/members/BoB", ("member_remove", ("g", "bob"))),
    ("POST", f"{BASE}api/groups/g/members/bob", ("method", None)),
    ("GET", f"{BASE}api/groups/g/members/bob/x", ("not_found", None)),
    ("GET", f"{BASE}api/groupsx", ("not_found", None)),
    ("GET", f"{BASE}api/me/groups", ("my_groups", None)),
    ("POST", f"{BASE}api/me/groups", ("method", None)),
    ("GET", f"{BASE}api/me/memberships", ("my_memberships", None)),  # AOTY-49
    ("DELETE", f"{BASE}api/me/memberships", ("method", None)),
    ("PUT", f"{BASE}api/me/settings/", ("my_settings", None)),
    ("GET", f"{BASE}api/me/settings", ("method", None)),
    ("GET", f"{BASE}api/me/other", ("not_found", None)),
  ],
)
def test_route(method, path, expected):
  assert core.route(method, path, BASE) == expected


# ---- sign-in ------------------------------------------------------------------------------


@pytest.mark.parametrize(
  "raw, expected",
  [
    (None, BASE),
    ("", BASE),
    (BASE, BASE),
    (f"{BASE}user/alice", f"{BASE}user/alice"),
    (f"{BASE}group/the-server?user=bob", f"{BASE}group/the-server?user=bob"),
    ("/toys/aoty/", BASE),  # another env's base
    ("//evil.example/", BASE),
    ("https://evil.example/", BASE),
    (f"{BASE}/evil.example", BASE),
    (f"{BASE}\\evil.example", BASE),
    (f"{BASE}x\r\nSet-Cookie: a=b", BASE),
    (f"{BASE}x y", BASE),
    (f"{BASE}<script>", BASE),
    (BASE + "a" * 600, BASE),
  ],
)
def test_safe_next(raw, expected):
  assert core.safe_next(raw, BASE) == expected


def test_with_query_adds_or_replaces_a_param():
  assert core.with_query(BASE, signin="failed") == f"{BASE}?signin=failed"
  assert (
    core.with_query(f"{BASE}group/x?user=bob", signin="failed") == f"{BASE}group/x?user=bob&signin=failed"
  )
  assert core.with_query(f"{BASE}?signin=ok&a=1", signin="failed") == f"{BASE}?a=1&signin=failed"


def test_lastfm_auth_url_puts_the_nonce_in_the_callback_path():
  url = core.lastfm_auth_url("KEY", "https://kamp.fm/", BASE, "n0nce")
  query = dict(parse_qsl(urlsplit(url).query))
  assert url.startswith("https://www.last.fm/api/auth/?")
  assert query == {"api_key": "KEY", "cb": f"https://kamp.fm{BASE}api/auth/callback/n0nce"}


def test_session_username():
  assert core.session_username({"u": "alice"}) == "alice"
  assert core.session_username({"u": "Alice"}) is None
  assert core.session_username({"u": 3}) is None
  assert core.session_username(None) is None


def test_routed_names_still_need_validation():
  _, name = core.route("GET", f"{BASE}api/users/-smoke-/status", BASE)
  assert not core.USERNAME_RE.match(name)
  _, name = core.route("GET", f"{BASE}api/users/_korps_/status", BASE)
  assert core.USERNAME_RE.match(name)


def test_a_session_can_name_an_underscore_user():
  assert core.session_username({"u": "_korps_"}) == "_korps_"


# ---- the client IP ------------------------------------------------------------------------

EDGE = "130.176.1.1"


def test_an_untrusted_request_uses_the_source_ip():
  headers = {"x-forwarded-for": "6.6.6.6", "cloudfront-viewer-address": "6.6.6.6:1234"}
  assert core.client_ip(headers, "198.51.100.7", trusted=False) == "198.51.100.7"


def test_a_trusted_request_prefers_the_viewer_address():
  assert core.client_ip({"cloudfront-viewer-address": "198.51.100.7:46532"}, EDGE, True) == "198.51.100.7"
  v6 = {"cloudfront-viewer-address": "2001:db8::1:46532"}
  assert core.client_ip(v6, EDGE, True) == "2001:db8::1"


@pytest.mark.parametrize("xff", ["198.51.100.7", f"198.51.100.7, {EDGE}", f"6.6.6.6, 198.51.100.7, {EDGE}"])
def test_a_trusted_request_takes_the_last_forwarded_entry_before_the_edge(xff):
  assert core.client_ip({"x-forwarded-for": xff}, EDGE, True) == "198.51.100.7"


def test_ipv6_is_limited_per_64_and_keys_hide_the_address():
  a = core.ip_limit_key("2001:db8:1:2:aaaa::1", 7)
  assert a == core.ip_limit_key("2001:db8:1:2:bbbb::9", 7) != core.ip_limit_key("2001:db8:1:3::1", 7)
  assert core.ip_limit_key("198.51.100.7", 7) != core.ip_limit_key("198.51.100.8", 7)
  assert "2001" not in a and a.endswith("#7")
  assert core.ip_limit_key("not-an-ip", 7).startswith("ip#")


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


def test_a_long_queue_keeps_a_queued_row_alive():
  old = {"state": "queued", "sync_requested_at": NOW - 4 * 3600}
  assert core.decide_sync(old, NOW, COOL, queue_depth=200) == "in_progress"
  assert core.stage(old, NOW, queue_depth=200) == "queued"
  # Once the queue drains, a mark that old is a crash between update and send: free it.
  assert core.decide_sync(old, NOW, COOL, queue_depth=0) == "queue"
  assert core.stage(old, NOW, queue_depth=0) == "idle"


@pytest.mark.parametrize("state", ["syncing", "retrying"])
def test_a_long_queue_doesnt_revive_a_dead_sync(state):
  row = {
    "state": state,
    "sync_requested_at": NOW - core.STUCK_AFTER,
    "sync_started_at": NOW - core.STUCK_AFTER,
  }
  assert core.decide_sync(row, NOW, COOL, queue_depth=200) == "queue"


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


def test_status_carries_the_owners_theme_whatever_the_charts_state():
  # AOTY-65: a first visit has no chart to carry it, so the status does.
  assert core.status({"state": "syncing", "theme": "beach-house"}, NOW, COOL)["theme"] == "beach-house"
  assert core.status(done(theme="match1"), NOW, COOL)["theme"] == "match1"
  for theme in ("poster", "nope", None):
    assert "theme" not in core.status(done(theme=theme), NOW, COOL), theme


def test_status_extras_for_queued_and_error():
  row = {"state": "queued", "sync_requested_at": NOW - 5, "queue_ticket": 50}
  queued = core.status(row, NOW, COOL, queue_depth=70, queue={"started": 40})
  assert queued["stage"] == "queued" and queued["queue"] == {"position": 10}
  assert "queue" not in core.status(row, NOW, COOL, queue_depth=70)  # counters unreadable
  assert "queue" not in core.status({**row, "queue_ticket": None}, NOW, COOL, 70, {"started": 40})
  failed = core.status({"state": "private", "sync_finished_at": NOW, "queue_ticket": 50}, NOW, COOL, 7, {})
  assert failed["reason"] == "private" and "queue" not in failed


def test_the_place_in_line_stays_within_the_queue():
  assert core.queue_place(50, 3, {"started": 10}, 2) == {"position": 3}  # FIFO groups jumped ahead
  assert core.queue_place(50, 90, {"started": 60}, 2) == {"position": 1}  # later tickets started first
  assert core.queue_place(5, None, {}, 2) == {"position": 1}


def test_a_wait_is_estimated_only_from_enough_first_syncs():
  few = {"started": 0, "first_syncs": core.WAIT_MIN_SAMPLES - 1, "first_sync_seconds": 1000}
  assert "wait_s" not in core.queue_place(10, 99, few, 2)
  enough = {"started": 0, "first_syncs": 20, "first_sync_seconds": 20 * 60}
  assert core.queue_place(10, 99, enough, 2)["wait_s"] == 5 * 60  # 10 in line, 2 at a time, 60 s each
  assert core.queue_place(1, 99, enough, 2)["wait_s"] == 60  # next up: just its own sync


def test_status_without_chart_fields():
  out = core.status({"username": "bob", "state": "queued", "sync_requested_at": NOW}, NOW, COOL)
  assert out["albums"] == {"checked": None, "total": None, "hits": None}
  assert out["display_name"] == "bob" and out["new_plays"] == 0


# ---- first-visit fields (AOTY-24) ---------------------------------------------------------


def test_status_carries_the_running_stats():
  progress = {"weeks_done": 9, "weeks_total": 47, "plays": 812, "albums": 140,
              "top_artist": "Band", "top_album": "LP", "top_plays": 61}  # fmt: skip
  out = core.status({"state": "syncing", "sync_started_at": NOW, "progress": progress}, NOW, COOL)
  assert out["so_far"] == {"albums": 140, "top": {"artist": "Band", "album": "LP", "plays": 61}}


def test_no_running_stats_before_any_plays():
  progress = {"weeks_done": 0, "weeks_total": 47, "plays": 0, "albums": 0, "top_plays": 0}
  out = core.status({"state": "syncing", "sync_started_at": NOW, "progress": progress}, NOW, COOL)
  assert "so_far" not in out and "wall" not in out


def test_status_carries_the_wall_and_drops_malformed_entries():
  good = {"artist": "Band", "album": "LP", "art": "https://lastfm.freetls.fastly.net/i/u/300x300/a.jpg",
          "fallbacks": ["https://lastfm.freetls.fastly.net/i/u/ar0/a.jpg", 7]}  # fmt: skip
  preview = json.dumps({"wall": [good, {"artist": "No", "album": None, "art": ""}, "junk"]})
  out = core.status({"state": "syncing", "sync_started_at": NOW, "preview": preview}, NOW, COOL)
  assert out["wall"] == [{**good, "fallbacks": good["fallbacks"][:1]}]


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", '{"wall": "x"}', '{"wall": []}'])
def test_a_bad_or_empty_preview_means_no_wall(raw):
  assert "wall" not in core.status({"state": "syncing", "sync_started_at": NOW, "preview": raw}, NOW, COOL)


@pytest.mark.parametrize(
  ("outcome", "code", "ours"),
  [("unavailable", None, True), ("bad_name", None, True), ("lastfm_failed", None, True),
   ("lastfm_failed", 11, True), ("lastfm_failed", 4, False), ("lastfm_failed", 14, False),
   ("lastfm_failed", 15, False), ("bad_state", None, False), ("no_state", None, False),
   ("no_token", None, False), ("signed_in", None, False), ("anonymous", None, False), (None, None, False)],
)  # fmt: skip
def test_auth_failure_is_only_ours(outcome, code, ours):
  assert core.auth_failure(outcome, code) is ours
