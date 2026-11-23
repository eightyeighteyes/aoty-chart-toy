"""The API's sync and status routes (lambdas/api/handler.py) against in-memory fakes."""

import importlib.util
import json
from pathlib import Path

import api_core as core
import pytest

from common import dynamo, guardrails

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "api" / "handler.py"
BASE = "/toys/aoty-test/"
NOW = 1_795_400_000
ENV = {"USERS_TABLE": "users", "SYNC_QUEUE_URL": "q/sync"}


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

  def get_item(self, TableName, Key, **kw):
    row = self.rows.get(Key["username"]["S"])
    if row is None:
      return {}
    item = dynamo.to_item({k: v for k, v in row.items() if k != "progress"})
    if "progress" in row:
      item["progress"] = {"M": dynamo.to_item(row["progress"])}
    return {"Item": item}

  def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeNames=None,
                  ExpressionAttributeValues=None, ConditionExpression=None):  # fmt: skip
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


def deps(h, ddb=None, sqs=None, fm=None, now=NOW, **settings):
  s = guardrails.Settings(**settings)
  return h.Deps(ddb=ddb or FakeDDB(), sqs=sqs or FakeSQS(), env=ENV, lastfm_client=fm or FakeLastfm(),
                clock=lambda: now, settings=lambda: s)  # fmt: skip


def call(h, d, method, name, action, ctype="application/json"):
  event = {"rawPath": f"{BASE}api/users/{name}/{action}", "requestContext": {"http": {"method": method}},
           "headers": {"content-type": ctype} if ctype else {}}  # fmt: skip
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


def test_status_reads_progress_and_queue_depth(h):
  ddb = FakeDDB()
  ddb.rows["alice"] = {"username": "alice", "state": "queued", "sync_requested_at": NOW - 5,
                       "progress": {"weeks_done": 0, "weeks_total": 4, "plays": 0}}  # fmt: skip
  code, body = status(h, deps(h, ddb, FakeSQS(depth=3)))
  assert code == 200 and body["stage"] == "queued" and body["queue_ahead_approx"] == 3
  assert body["weeks"] == {"done": 0, "total": 4}


def test_status_matches_the_core(h):
  ddb = FakeDDB()
  row = {"username": "alice", "state": "done", "sync_requested_at": NOW - 300, "sync_finished_at": NOW - 30,
         "chart_built_at": NOW - 10, "chart_plays": 100, "chart_albums": 50, "chart_pending": 0,
         "chart_hits": 9, "progress": {"weeks_done": 4, "weeks_total": 4, "plays": 100}}  # fmt: skip
  ddb.rows["alice"] = row
  assert status(h, deps(h, ddb))[1] == core.status(row, NOW, 600)


def test_logs_never_name_the_user(h, caplog):
  with caplog.at_level("INFO"):
    sync(h, deps(h), "alice")
    sync(h, deps(h, sqs=FakeSQS(fail=True)), "alice")
  assert caplog.records and not any("alice" in r.getMessage() for r in caplog.records)
