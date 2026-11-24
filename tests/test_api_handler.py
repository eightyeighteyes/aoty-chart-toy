"""The API's sync and status routes (lambdas/api/handler.py) against in-memory fakes."""

import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import api_core as core
import pytest

from common import dynamo, guardrails, lastfm, session

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "api" / "handler.py"
BASE = "/toys/aoty-test/"
NOW = 1_795_400_000
ENV = {"USERS_TABLE": "users", "GUARD_TABLE": "guard", "GROUPS_TABLE": "groups", "SYNC_QUEUE_URL": "q/sync"}


@pytest.fixture
def h(monkeypatch):
  monkeypatch.setenv("BASE_PATH", BASE)
  spec = importlib.util.spec_from_file_location("api_handler", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class ConditionFailed(Exception):
  response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDDB:
  """The users table. Conditional updates evaluate the handler's condition with Python."""

  def __init__(self, race=None):
    self.rows, self.race = {}, race  # race(rows) runs just before a conditional update
    self.guard = {}  # the guard table: id -> item (plain values)
    self.groups = {}  # the groups table: (pk, sk) -> item (plain values); here only session# cutoffs

  def _guard_update(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues,
                    ConditionExpression=None, **kw):  # fmt: skip
    """The api's guard updates: the per-IP name set, and the queue counters."""
    item = self.guard.setdefault(Key["id"]["S"], {})
    v = ExpressionAttributeValues
    if Key["id"]["S"] == "queue":  # ADD #x :one
      field = ExpressionAttributeNames[UpdateExpression.split()[1]]
      item[field] = item.get(field, 0) + 1
      return {"Attributes": {field: {"N": str(item[field])}}}
    assert UpdateExpression == "ADD #n :h SET #e = if_not_exists(#e, :exp)"
    names, tag, limit = item.get("names", set()), v[":t"]["S"], int(v[":lim"]["N"])
    if names and len(names) >= limit and tag not in names:
      raise ConditionFailed()
    item["names"] = names | set(v[":h"]["SS"])
    item.setdefault("expires_at", int(v[":exp"]["N"]))
    return {}

  def get_item(self, TableName, Key, **kw):
    if TableName == "groups":
      item = self.groups.get((Key["pk"]["S"], Key["sk"]["S"]))
      return {"Item": dynamo.to_item(item)} if item else {}
    if TableName == "guard":
      item = self.guard.get(Key["id"]["S"])
      return {"Item": {k: {"N": str(v)} for k, v in item.items()}} if item else {}
    row = self.rows.get(Key["username"]["S"])
    if row is None:
      return {}
    item = dynamo.to_item({k: v for k, v in row.items() if k != "progress"})
    if "progress" in row:
      item["progress"] = {"M": dynamo.to_item(row["progress"])}
    return {"Item": item}

  def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeNames=None,
                  ExpressionAttributeValues=None, ConditionExpression=None, **kw):  # fmt: skip
    if TableName == "guard":
      return self._guard_update(Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues,
                                ConditionExpression)  # fmt: skip
    name = Key["username"]["S"]
    names = ExpressionAttributeNames or {}
    values = dynamo.from_item(ExpressionAttributeValues or {})
    if ConditionExpression:
      if self.race:
        self.race(self.rows)
      row = self.rows.get(name, {})
      req, st, state = row.get("sync_requested_at"), row.get("sync_started_at"), row.get("state")
      ok = (req is None or req < values[":cool"]) and (
        state is None
        or state not in (values[":q"], values[":sy"], values[":rt"])
        or (req is not None and req < values[":stuck"] and (st is None or st < values[":stuck"]))
      )
      if not ok:
        raise ConditionFailed()
    row = self.rows.setdefault(name, {"username": name})
    sets, _, removes = UpdateExpression.removeprefix("SET ").partition(" REMOVE ")
    if UpdateExpression.startswith("REMOVE "):
      sets, removes = "", UpdateExpression.removeprefix("REMOVE ")
    for part in filter(None, sets.split(", ")):
      k, v = part.split(" = ")
      row[names.get(k, k)] = values[v]
    for k in filter(None, removes.split(", ")):
      row.pop(names.get(k, k), None)


class FakeSQS:
  def __init__(self, depth=0, fail=False):
    self.sent, self.depth, self.fail = [], depth, fail

  def send_message(self, **kw):
    if self.fail:
      raise RuntimeError("SQS down")
    self.sent.append(kw)

  def get_queue_attributes(self, QueueUrl, AttributeNames):
    return {"Attributes": {"ApproximateNumberOfMessages": str(self.depth)}}


class FakeLastfm:
  def __init__(self, name="AliCe", error=None):
    self.name, self.error, self.calls = name, error, 0

  def user_info(self, user):
    self.calls += 1
    if self.error:
      raise self.error
    return self.name


def deps(h, ddb=None, sqs=None, fm=None, now=NOW, env=None, secrets=(), **settings):
  s = guardrails.Settings(**settings)
  return h.Deps(ddb=ddb or FakeDDB(), sqs=sqs or FakeSQS(), env={**ENV, **(env or {})},
                lastfm_client=fm or FakeLastfm(), clock=lambda: now, settings=lambda: s,
                origin_secrets=lambda: secrets)  # fmt: skip


def call(h, d, method, name, action, ctype="application/json", ip="198.51.100.7", headers=None):
  event = {"rawPath": f"{BASE}api/users/{name}/{action}",
           "requestContext": {"http": {"method": method, "sourceIp": ip}},
           "headers": {**({"content-type": ctype} if ctype else {}), **(headers or {})}}  # fmt: skip
  out = h.handler(event, None, d)
  assert out["headers"]["cache-control"] == "no-store"
  return out["statusCode"], json.loads(out["body"])


def sync(h, d, name="alice", **kw):
  return call(h, d, "POST", name, "sync", **kw)


def status(h, d, name="alice"):
  return call(h, d, "GET", name, "status", ctype=None)


# ---- POST sync --------------------------------------------------------------------------


def test_a_new_user_is_checked_queued_and_named(h):
  d = deps(h)
  code, body = sync(h, d, "AliCe")
  assert code == 202 and body["queued"] is True and body["status"]["stage"] == "queued"
  (msg,) = d.sqs.sent
  assert json.loads(msg["MessageBody"]) == {"username": "alice"}
  assert msg["MessageGroupId"] == "alice" and msg["QueueUrl"] == "q/sync"
  row = d.ddb.rows["alice"]
  assert row["state"] == "queued" and row["sync_requested_at"] == NOW and row["display_name"] == "AliCe"


def test_a_second_request_in_the_cooldown_queues_nothing(h):
  d = deps(h)
  sync(h, d)
  d.ddb.rows["alice"].update(state="done", sync_started_at=NOW + 1, sync_finished_at=NOW + 60)
  code, body = sync(h, deps(h, d.ddb, d.sqs, now=NOW + 300))
  assert code == 200 and body == {**body, "queued": False, "reason": "cooldown"}
  assert len(d.sqs.sent) == 1
  assert body["status"]["cooldown_until"] == NOW + 600


def test_after_the_cooldown_it_queues_again_without_asking_lastfm(h):
  fm = FakeLastfm()
  d = deps(h, fm=fm)
  sync(h, d)
  d.ddb.rows["alice"].update(state="done", last_synced_at=NOW + 1, sync_finished_at=NOW + 60)
  code, _ = sync(h, deps(h, d.ddb, d.sqs, fm, now=NOW + 601))
  assert code == 202 and len(d.sqs.sent) == 2 and fm.calls == 1


def test_a_running_sync_is_in_progress(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "syncing", "sync_requested_at": NOW - 9999,
                       "sync_started_at": NOW - 60, "last_synced_at": NOW - 60}  # fmt: skip
  code, body = sync(h, deps(h, ddb))
  assert code == 200 and body["reason"] == "in_progress" and body["status"]["stage"] == "reading"


def test_an_hour_in_a_long_queue_is_still_queued_not_requeued(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW - 3600}
  d = deps(h, ddb, FakeSQS(depth=120))
  code, body = sync(h, d)
  assert code == 200 and body["reason"] == "in_progress" and d.sqs.sent == []
  assert body["status"]["stage"] == "queued"
  assert status(h, d)[1]["stage"] == "queued"


def test_the_queue_depth_is_cached_briefly(h):
  class Counting(FakeSQS):
    calls = 0

    def get_queue_attributes(self, **kw):
      Counting.calls += 1
      return super().get_queue_attributes(**kw)

  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW - 5}
  d = deps(h, ddb, Counting(depth=3))
  status(h, d)
  status(h, d)
  assert Counting.calls == 1


def test_losing_the_race_answers_in_progress_and_sends_nothing(h):
  def other_request(rows):
    rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW}

  d = deps(h, FakeDDB(race=other_request))
  code, body = sync(h, d)
  assert code == 200 and body["reason"] == "in_progress" and d.sqs.sent == []


def test_a_failed_send_undoes_the_mark(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "done", "last_synced_at": NOW - 5000,
                       "sync_requested_at": NOW - 5000}  # fmt: skip
  code, body = sync(h, deps(h, ddb, FakeSQS(fail=True)))
  assert code == 503 and body["error"] == "queue_unavailable"
  assert ddb.rows["alice"]["state"] == "done" and ddb.rows["alice"]["sync_requested_at"] == NOW - 5000


def test_a_failed_send_for_a_new_user_leaves_no_queued_mark(h):
  ddb = FakeDDB()
  code, _ = sync(h, deps(h, ddb, FakeSQS(fail=True)))
  assert code == 503
  assert "state" not in ddb.rows["alice"] and "sync_requested_at" not in ddb.rows["alice"]


def test_an_unknown_lastfm_user_is_404_and_remembered_for_a_day(h):
  fm = FakeLastfm(name=None)
  d = deps(h, fm=fm)
  code, _ = sync(h, d, "nobody")
  assert code == 404 and d.sqs.sent == []
  row = d.ddb.rows["nobody"]
  assert row["state"] == "not_found" and row["expires_at"] == NOW + h.NOT_FOUND_TTL_S
  code, _ = sync(h, deps(h, d.ddb, d.sqs, fm, now=NOW + 3600), "nobody")
  assert code == 404 and fm.calls == 1  # cached
  code, _ = sync(h, deps(h, d.ddb, d.sqs, FakeLastfm("Nobody"), now=NOW + 86400), "nobody")
  assert code == 202 and "expires_at" not in d.ddb.rows["nobody"]  # Last.fm knows them now


def test_lastfm_trouble_is_503_not_404(h):
  d = deps(h, fm=FakeLastfm(error=TimeoutError("slow")))
  code, body = sync(h, d)
  assert code == 503 and body["error"] == "lastfm_unavailable"
  assert d.ddb.rows == {} and d.sqs.sent == []


def test_a_foreign_casing_from_lastfm_isnt_stored(h):
  d = deps(h, fm=FakeLastfm(name="mallory"))
  sync(h, d)
  assert "display_name" not in d.ddb.rows["alice"]


def test_new_users_wait_when_the_queue_is_busy(h):
  d = deps(h, sqs=FakeSQS(depth=51), max_queue_for_new=50)
  code, body = sync(h, d)
  assert code == 503 and body["error"] == "busy" and d.sqs.sent == []
  assert sync(h, deps(h, sqs=FakeSQS(depth=51)))[0] == 202  # the default limit is higher


def test_pausing_new_users_refuses_them_before_asking_lastfm(h):
  fm = FakeLastfm()
  d = deps(h, fm=fm, pause_new_users=True)
  code, body = sync(h, d)
  assert code == 503 and body["error"] == "paused"
  assert fm.calls == 0 and d.ddb.rows == {} and d.sqs.sent == []


def test_a_cold_container_that_cant_read_ssm_pauses_new_users(h):
  d = deps(h, loaded=False)
  assert sync(h, d)[1]["error"] == "paused"


def test_pausing_new_users_leaves_existing_users_syncing(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "done", "last_synced_at": NOW - 5000}
  assert sync(h, deps(h, ddb, pause_new_users=True))[0] == 202


def test_a_known_name_that_never_finished_a_sync_counts_as_new(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "error", "sync_requested_at": NOW - 5000}
  assert sync(h, deps(h, ddb, pause_new_users=True))[1]["error"] == "paused"


def test_pausing_syncs_queues_nothing_and_existing_users_hear_paused(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "done", "last_synced_at": NOW - 5000}
  d = deps(h, ddb, pause_syncs=True)
  code, body = sync(h, d)
  assert code == 200 and body["queued"] is False and body["reason"] == "paused"
  assert body["status"]["stage"] == "idle" and d.sqs.sent == []
  assert ddb.rows["alice"]["state"] == "done"
  assert sync(h, d, "bob")[1]["error"] == "paused"


def test_a_paused_user_already_queued_is_still_in_progress(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW - 5}
  code, body = sync(h, deps(h, ddb, pause_new_users=True))
  assert code == 200 and body["reason"] == "in_progress"


def test_one_ip_gets_a_few_distinct_new_names_an_hour(h):
  fm = FakeLastfm()
  d = deps(h, fm=fm, new_users_per_ip_hour=2)
  assert sync(h, d, "alice")[0] == 202
  assert sync(h, d, "bob")[0] == 202
  code, body = sync(h, d, "carol")
  assert code == 429 and body["error"] == "too_many_new" and fm.calls == 2
  assert sync(h, d, "carol", ip="203.0.113.9")[0] == 202  # another IP has its own allowance
  later = deps(h, d.ddb, d.sqs, fm, now=NOW + 3600, new_users_per_ip_hour=2)
  assert sync(h, later, "carol")[0] == 202  # a new hour


def test_retrying_a_name_doesnt_use_up_the_allowance(h):
  ddb = FakeDDB()
  down = deps(h, ddb, fm=FakeLastfm(error=TimeoutError("slow")), new_users_per_ip_hour=1)
  assert sync(h, down, "alice")[1]["error"] == "lastfm_unavailable"
  assert sync(h, deps(h, ddb, new_users_per_ip_hour=1), "alice")[0] == 202


def test_known_users_dont_count_against_the_ip(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "done", "last_synced_at": NOW - 5000}
  d = deps(h, ddb, new_users_per_ip_hour=1)
  assert sync(h, d, "alice")[0] == 202 and list(ddb.guard) == ["queue"]


def test_the_guard_table_stores_no_address_or_name(h):
  d = deps(h)
  sync(h, d, "alice", ip="198.51.100.7")
  ((key, item),) = [(k, v) for k, v in d.ddb.guard.items() if k.startswith("ip#")]
  assert "198.51.100.7" not in key and "alice" not in str(item)
  assert item["expires_at"] == (NOW // 3600 + 2) * 3600


# ---- the origin lock ---------------------------------------------------------------------

ENFORCED = {"ENFORCE_ORIGIN": "true"}
VIA_CF = {"x-kamp-origin": "s3cret"}


def test_with_the_lock_on_only_cloudfront_gets_through(h):
  d = deps(h, env=ENFORCED, secrets=("s3cret",))
  assert sync(h, d)[0] == 403 and d.sqs.sent == []
  assert sync(h, d, headers={"x-kamp-origin": "guess"})[0] == 403
  assert status(h, d)[0] == 403
  assert sync(h, d, headers={"X-Kamp-Origin": "s3cret"})[0] == 202


def test_health_is_never_locked(h):
  d = deps(h, env=ENFORCED, secrets=("s3cret",))
  event = {"rawPath": f"{BASE}api/health", "requestContext": {"http": {"method": "GET"}}}
  assert h.handler(event, None, d)["statusCode"] == 200


def test_the_previous_secret_works_during_a_rotation(h):
  d = deps(h, env=ENFORCED, secrets=("new", "s3cret"))
  assert sync(h, d, headers=VIA_CF)[0] == 202


def test_no_secret_readable_with_the_lock_on_fails_closed(h):
  assert sync(h, deps(h, env=ENFORCED, secrets=()), headers=VIA_CF)[0] == 403


def test_with_the_lock_off_anything_gets_through(h):
  assert sync(h, deps(h, secrets=("s3cret",)))[0] == 202


def test_forwarded_ips_count_only_with_the_secret(h):
  d = deps(h, secrets=("s3cret",), new_users_per_ip_hour=1)
  spoof = {"x-forwarded-for": "203.0.113.1"}
  assert sync(h, d, "alice", ip="130.176.1.1")[0] == 202
  assert sync(h, d, "bob", ip="130.176.1.1", headers=spoof)[0] == 429  # spoofed: still the edge's
  real = {**VIA_CF, "x-forwarded-for": "203.0.113.1, 130.176.1.1"}
  assert sync(h, d, "bob", ip="130.176.1.1", headers=real)[0] == 202  # via CloudFront: the viewer's


def test_the_cooldown_is_a_runtime_setting(h):
  d = deps(h)
  sync(h, d)
  d.ddb.rows["alice"].update(state="done", sync_started_at=NOW + 1, sync_finished_at=NOW + 60)
  code, body = sync(h, deps(h, d.ddb, d.sqs, now=NOW + 700, sync_cooldown_seconds=3600))
  assert code == 200 and body["reason"] == "cooldown" and body["status"]["cooldown_until"] == NOW + 3600


def test_known_users_still_queue_when_busy(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "done", "last_synced_at": NOW - 5000}
  code, _ = sync(h, deps(h, ddb, FakeSQS(depth=999)))
  assert code == 202


@pytest.mark.parametrize("ctype", ["application/x-www-form-urlencoded", "text/plain", None])
def test_sync_needs_json(h, ctype):
  d = deps(h)
  code, _ = sync(h, d, ctype=ctype)
  assert code == 415 and d.ddb.rows == {} and d.sqs.sent == []


def test_json_with_a_charset_is_fine(h):
  code, _ = sync(h, deps(h), ctype="application/json; charset=utf-8")
  assert code == 202


@pytest.mark.parametrize("name", ["__smoke__", "a%20b", "x" * 70])
def test_bad_names_are_400(h, name):
  code, _ = sync(h, deps(h), name)
  assert code == 400


def test_wrong_methods_are_405(h):
  assert call(h, deps(h), "GET", "alice", "sync")[0] == 405
  assert call(h, deps(h), "POST", "alice", "status")[0] == 405


def test_an_unexpected_failure_is_503(h):
  class Broken(FakeDDB):
    def get_item(self, **kw):
      raise RuntimeError("DynamoDB down")

  assert sync(h, deps(h, Broken()))[0] == 503


# ---- GET status -------------------------------------------------------------------------


def test_status_of_an_unknown_or_not_found_user_is_404(h):
  ddb = FakeDDB()
  ddb.rows["nobody"] = {"username": "nobody", "state": "not_found", "expires_at": NOW + 99}
  assert status(h, deps(h, ddb), "ghost")[0] == 404
  assert status(h, deps(h, ddb), "nobody")[0] == 404


def test_status_reads_progress_and_the_place_in_line(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW - 5, "queue_ticket": 40,
                       "progress": {"weeks_done": 0, "weeks_total": 4, "plays": 0}}  # fmt: skip
  ddb.guard["queue"] = {"joined": 45, "started": 33}
  code, body = status(h, deps(h, ddb, FakeSQS(depth=12)))
  assert code == 200 and body["stage"] == "queued" and body["queue"] == {"position": 7}
  assert body["weeks"] == {"done": 0, "total": 4}


def test_queueing_hands_out_tickets_in_order(h):
  d = deps(h)
  sync(h, d, "alice")
  sync(h, d, "bob")
  assert d.ddb.rows["alice"]["queue_ticket"] == 1 and d.ddb.rows["bob"]["queue_ticket"] == 2


def test_a_failed_send_burns_its_ticket(h):
  ddb = FakeDDB()
  sync(h, deps(h, ddb, FakeSQS(fail=True)))
  assert ddb.guard["queue"] == {"joined": 1, "started": 1} and "queue_ticket" not in ddb.rows["alice"]


def test_losing_the_race_burns_the_ticket(h):
  def other_request(rows):
    rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW}

  ddb = FakeDDB(race=other_request)
  sync(h, deps(h, ddb))
  assert ddb.guard["queue"] == {"joined": 1, "started": 1}


def test_replacing_a_stale_mark_burns_its_old_ticket(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {
    "username": "alice",
    "state": "queued",
    "sync_requested_at": NOW - 7200,
    "queue_ticket": 9,
  }
  ddb.guard["queue"] = {"joined": 9, "started": 0}
  assert sync(h, deps(h, ddb, FakeSQS(depth=0)))[0] == 202
  assert ddb.rows["alice"]["queue_ticket"] == 10 and ddb.guard["queue"]["started"] == 1


def test_no_ticket_still_queues(h):
  class NoGuard(FakeDDB):
    def update_item(self, TableName, **kw):
      if TableName == "guard" and kw["Key"]["id"]["S"] == "queue":
        raise RuntimeError("DynamoDB down")
      return super().update_item(TableName, **kw)

  ddb = NoGuard()
  code, body = sync(h, deps(h, ddb))
  assert code == 202 and "queue_ticket" not in ddb.rows["alice"] and "queue" not in body["status"]


def test_status_matches_the_core(h):
  ddb = FakeDDB()
  row = {"username": "alice", "state": "done", "sync_requested_at": NOW - 300, "sync_finished_at": NOW - 30,
         "chart_built_at": NOW - 10, "chart_plays": 100, "chart_albums": 50, "chart_pending": 0,
         "chart_hits": 9, "progress": {"weeks_done": 4, "weeks_total": 4, "plays": 100}}  # fmt: skip
  ddb.rows["alice"] = row
  assert status(h, deps(h, ddb))[1] == core.status(row, NOW, 600)


# ---- metrics (AOTY-41) ------------------------------------------------------------------


def _emf(capsys):
  return [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"_aws"' in x]


def test_each_sync_request_emits_one_line_with_its_outcome(h, capsys):
  d = deps(h)
  sync(h, d, "alice")  # a new user, queued
  sync(h, d, "alice")  # still queued
  sync(h, d, "alice", ctype="text/plain")
  sync(h, d, "-bad-")
  status(h, d)  # polls are never counted
  lines = _emf(capsys)
  assert [x["outcome"] for x in lines] == ["new_user", "in_progress", "not_json", "bad_name"]
  assert all(x["SyncRequests"] == 1 for x in lines)
  assert "alice" not in json.dumps(lines)


def test_lastfm_calls_are_this_requests_only(h, capsys):
  fm = FakeLastfm()
  fm.calls = 40  # earlier requests on a warm container
  d = deps(h, fm=fm)
  sync(h, d, "alice")  # one user.getInfo
  sync(h, d, "alice")  # none: a known user in the cooldown
  assert [x["LastfmCalls"] for x in _emf(capsys)] == [1, 0]


def test_refusals_are_named_after_their_error(h, capsys):
  sync(h, deps(h, pause_new_users=True), "bob")
  sync(h, deps(h, fm=FakeLastfm(error=TimeoutError("slow"))), "carol")
  assert [x["outcome"] for x in _emf(capsys)] == ["paused", "lastfm_unavailable"]


def test_logs_never_name_the_user(h, caplog):
  with caplog.at_level("INFO"):
    sync(h, deps(h), "alice")
    sync(h, deps(h, sqs=FakeSQS(fail=True)), "alice")
  assert caplog.records and not any("alice" in r.getMessage() for r in caplog.records)


# ---- sign-in (AOTY-47) --------------------------------------------------------------------

KEY, OLD_KEY = "k" * 48, "o" * 48
AUTH = {"session_key": KEY, "lastfm_api_secret": "SHARED"}
ORIGIN = {"PUBLIC_ORIGIN": "https://kamp.fm"}
STATE_PATH = f"{BASE}api/auth/"


class AuthLastfm(FakeLastfm):
  api_key = "APIKEY"

  def __init__(self, session_name="AliCe", **kw):
    super().__init__(**kw)
    self.session_name, self.sessions = session_name, []

  def auth_get_session(self, token, secret):
    self.calls += 1
    self.sessions.append((token, secret))
    if self.error:
      raise self.error
    return self.session_name.lower() if self.session_name else None


def auth_deps(h, auth=AUTH, fm=None, now=NOW, env=None, **kw):
  d = deps(h, fm=fm or AuthLastfm(), now=now, env={**ORIGIN, **(env or {})}, **kw)
  d.auth_secrets = lambda: dict(auth)
  return d


def auth_call(h, d, method, path, cookies=None, query=None, ctype=None, headers=None):
  event = {"rawPath": f"{BASE}api/{path}", "requestContext": {"http": {"method": method, "sourceIp": "1.2.3.4"}},
           "headers": {**({"content-type": ctype} if ctype else {}), **(headers or {})}}  # fmt: skip
  if cookies:
    event["cookies"] = [f"{k}={v}" for k, v in cookies.items()]  # payload v2: not headers["cookie"]
  if query:
    event["queryStringParameters"] = query
  out = h.handler(event, None, d)
  assert out["headers"]["cache-control"] == "no-store"
  return out


def set_cookies(out):
  """{name: (value, attributes)} from a response's cookies list."""
  got = {}
  for c in out.get("cookies", []):
    head, _, attrs = c.partition("; ")
    name, _, value = head.partition("=")
    got[name] = (value, attrs)
  return got


def start(h, d, next_path=None):
  out = auth_call(h, d, "GET", "auth/start", query={"next": next_path} if next_path else None)
  assert out["statusCode"] == 302
  state = set_cookies(out)[session.STATE_COOKIE][0]
  cb = dict(parse_qsl(urlsplit(out["headers"]["location"]).query))["cb"]
  return state, cb.rsplit("/", 1)[1], out


def test_start_sets_a_state_cookie_and_sends_the_visitor_to_lastfm(h):
  state, nonce, out = start(h, auth_deps(h))
  loc = urlsplit(out["headers"]["location"])
  assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://www.last.fm/api/auth/"
  q = dict(parse_qsl(loc.query))
  assert q == {"api_key": "APIKEY", "cb": f"https://kamp.fm{BASE}api/auth/callback/{nonce}"}
  attrs = set_cookies(out)[session.STATE_COOKIE][1]
  assert (
    f"Path={STATE_PATH}" in attrs and "HttpOnly" in attrs and "Secure" in attrs and "Max-Age=600" in attrs
  )
  assert out["headers"]["referrer-policy"] == "no-referrer"


def test_the_full_sign_in_sets_a_session_and_me_names_the_user(h):
  fm = AuthLastfm(session_name="AliCe")
  d = auth_deps(h, fm=fm)
  state, nonce, _ = start(h, d, f"{BASE}group/the-server")
  out = auth_call(h, d, "GET", f"auth/callback/{nonce}", cookies={session.STATE_COOKIE: state},
                  query={"token": "TOK"})  # fmt: skip
  assert out["statusCode"] == 302 and out["headers"]["location"] == f"{BASE}group/the-server"
  assert fm.sessions == [("TOK", "SHARED")]
  got = set_cookies(out)
  assert got[session.STATE_COOKIE][0] == "" and f"Path={STATE_PATH}" in got[session.STATE_COOKIE][1]
  cookie, attrs = got[session.SESSION_COOKIE]
  assert f"Path={BASE};" in attrs and "SameSite=Lax" in attrs and "Max-Age=2592000" in attrs
  me = auth_call(h, d, "GET", "me", cookies={session.SESSION_COOKIE: cookie})
  assert me["statusCode"] == 200 and json.loads(me["body"]) == {"username": "alice", "group_writes": False}


@pytest.mark.parametrize("case", ["no_cookie", "wrong_nonce", "expired", "session_as_state", "other_key"])
def test_a_bad_state_never_reaches_lastfm(h, case):
  fm = AuthLastfm()
  d = auth_deps(h, fm=fm)
  state, nonce, _ = start(h, d)
  cookies = {session.STATE_COOKIE: state}
  if case == "no_cookie":
    cookies = None
  elif case == "wrong_nonce":
    nonce = ("y" if nonce[0] == "x" else "x") + nonce[1:]  # always a different nonce
  elif case == "expired":
    d.clock = lambda: NOW + session.STATE_TTL_S
  elif case == "session_as_state":
    cookies = {session.STATE_COOKIE: session.new_session("alice", KEY, NOW)}
  elif case == "other_key":
    cookies = {session.STATE_COOKIE: session.new_state(nonce, BASE, "z" * 48, NOW)}
  out = auth_call(h, d, "GET", f"auth/callback/{nonce}", cookies=cookies, query={"token": "TOK"})
  assert out["statusCode"] == 302 and out["headers"]["location"] == f"{BASE}?signin=failed"
  assert fm.sessions == []
  assert session.SESSION_COOKIE not in set_cookies(out)


def test_a_refused_token_lands_back_with_signin_failed(h):
  fm = AuthLastfm(error=lastfm.LastfmError(4, "Invalid authentication token supplied"))
  d = auth_deps(h, fm=fm)
  state, nonce, _ = start(h, d, f"{BASE}user/bob")
  out = auth_call(h, d, "GET", f"auth/callback/{nonce}", cookies={session.STATE_COOKIE: state},
                  query={"token": "TOK"})  # fmt: skip
  assert out["headers"]["location"] == f"{BASE}user/bob?signin=failed"
  assert session.SESSION_COOKIE not in set_cookies(out)


def test_a_callback_without_a_token_is_refused(h):
  d = auth_deps(h)
  state, nonce, _ = start(h, d)
  out = auth_call(h, d, "GET", f"auth/callback/{nonce}", cookies={session.STATE_COOKIE: state})
  assert out["headers"]["location"] == f"{BASE}?signin=failed"


def test_start_ignores_a_next_off_this_site(h):
  d = auth_deps(h)
  for evil in ("//evil.example/", "https://evil.example/", "/toys/aoty/"):
    state, nonce, _ = start(h, d, evil)
    out = auth_call(h, d, "GET", f"auth/callback/{nonce}", cookies={session.STATE_COOKIE: state},
                    query={"token": "TOK"})  # fmt: skip
    assert out["headers"]["location"] == BASE


@pytest.mark.parametrize(
  "auth", [{}, {"session_key": "short", "lastfm_api_secret": "S"}, {"session_key": KEY}]
)
def test_unreadable_or_weak_secrets_make_start_503(h, auth):
  out = auth_call(h, auth_deps(h, auth=auth), "GET", "auth/start")
  assert out["statusCode"] == 503 and "cookies" not in out


def test_no_public_origin_makes_start_503(h):
  d = auth_deps(h)
  d.env.pop("PUBLIC_ORIGIN")
  assert auth_call(h, d, "GET", "auth/start")["statusCode"] == 503


def test_me_is_401_signed_out_and_clears_a_bad_cookie(h):
  d = auth_deps(h)
  out = auth_call(h, d, "GET", "me")
  assert out["statusCode"] == 401 and "cookies" not in out
  good = session.new_session("alice", KEY, NOW)
  tampered = good[:-2] + ("AA" if not good.endswith("AA") else "BB")
  out = auth_call(h, d, "GET", "me", cookies={session.SESSION_COOKIE: tampered})
  assert out["statusCode"] == 401
  assert set_cookies(out)[session.SESSION_COOKIE][0] == ""


def test_an_expired_session_is_401(h):
  cookie = session.new_session("alice", KEY, NOW - session.SESSION_TTL_S)
  out = auth_call(h, auth_deps(h), "GET", "me", cookies={session.SESSION_COOKIE: cookie})
  assert out["statusCode"] == 401


def test_a_session_signed_with_the_previous_key_still_works(h):
  d = auth_deps(h, auth={**AUTH, "session_key_previous": OLD_KEY})
  out = auth_call(
    h, d, "GET", "me", cookies={session.SESSION_COOKIE: session.new_session("bob", OLD_KEY, NOW)}
  )
  assert json.loads(out["body"])["username"] == "bob"


@pytest.mark.parametrize("value, offered", [("true", True), ("false", False), (None, False)])
def test_me_says_whether_group_writes_are_on(h, value, offered):
  d = auth_deps(h)
  d.env.pop("GROUP_WRITES", None)
  if value is not None:
    d.env["GROUP_WRITES"] = value
  out = auth_call(h, d, "GET", "me", cookies={session.SESSION_COOKIE: session.new_session("alice", KEY, NOW)})
  assert json.loads(out["body"]) == {"username": "alice", "group_writes": offered}
  out = auth_call(h, d, "GET", "me")  # signed out too, so an env with writes off can hide "sign in"
  assert out["statusCode"] == 401
  assert json.loads(out["body"]) == {"error": "signed_out", "group_writes": offered}


def test_me_is_503_not_401_when_the_keys_cant_be_read(h):
  cookie = session.new_session("alice", KEY, NOW)
  out = auth_call(h, auth_deps(h, auth={}), "GET", "me", cookies={session.SESSION_COOKIE: cookie})
  assert out["statusCode"] == 503 and "cookies" not in out


def test_signout_clears_the_session_cookie(h):
  out = auth_call(h, auth_deps(h), "POST", "auth/signout", ctype="application/json")
  assert out["statusCode"] == 200
  value, attrs = set_cookies(out)[session.SESSION_COOKIE]
  assert value == "" and f"Path={BASE};" in attrs and "Max-Age=0" in attrs


@pytest.mark.parametrize(
  "ctype", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_a_cross_site_form_cant_sign_anyone_out(h, ctype):
  cookie = session.new_session("alice", KEY, NOW)
  out = auth_call(
    h, auth_deps(h), "POST", "auth/signout", cookies={session.SESSION_COOKIE: cookie}, ctype=ctype
  )
  assert out["statusCode"] == 415 and "cookies" not in out


@pytest.mark.parametrize("method, path", [("GET", "auth/start"), ("GET", "me"), ("GET", "auth/callback/abc"),
                                          ("POST", "auth/signout")])  # fmt: skip
def test_sign_in_routes_are_behind_the_origin_lock(h, method, path):
  d = auth_deps(h, env={"ENFORCE_ORIGIN": "true"}, secrets=("s3cret",))
  out = auth_call(h, d, method, path, ctype="application/json")
  assert out["statusCode"] == 403
  out = auth_call(h, d, method, path, ctype="application/json", headers={"x-kamp-origin": "s3cret"})
  assert out["statusCode"] != 403


def test_sign_in_logs_and_metric_lines_carry_no_token_nonce_or_name(h, caplog, capsys):
  fm = AuthLastfm(session_name="AliCe")
  d = auth_deps(h, fm=fm)
  with caplog.at_level("INFO"):
    state, nonce, _ = start(h, d)
    auth_call(h, d, "GET", f"auth/callback/{nonce}", cookies={session.STATE_COOKIE: state},
              query={"token": "SECRET-TOKEN"})  # fmt: skip
    fm.error = lastfm.LastfmError(14, "This token has not been authorized")
    state, nonce2, _ = start(h, d)
    auth_call(h, d, "GET", f"auth/callback/{nonce2}", cookies={session.STATE_COOKIE: state},
              query={"token": "SECRET-TOKEN"})  # fmt: skip
  text = " ".join(r.getMessage() for r in caplog.records) + capsys.readouterr().out
  assert caplog.records
  for secret in ("SECRET-TOKEN", nonce, nonce2, "alice", "AliCe", "SHARED", KEY):
    assert secret not in text


def test_each_sign_in_request_emits_one_line_with_its_outcome(h, capsys):
  d = auth_deps(h)
  state, nonce, _ = start(h, d)
  auth_call(
    h, d, "GET", f"auth/callback/{nonce}", cookies={session.STATE_COOKIE: state}, query={"token": "T"}
  )
  auth_call(h, d, "GET", "me")
  lines = _emf(capsys)
  assert [x["auth"] for x in lines] == ["started", "signed_in", "anonymous"]
  assert [x["LastfmCalls"] for x in lines] == [0, 1, 0]
  assert all(
    set(m["Name"] for m in x["_aws"]["CloudWatchMetrics"][0]["Metrics"]) == {"LastfmCalls"} for x in lines
  )
