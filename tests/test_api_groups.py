"""The api's group routes (AOTY-48) against in-memory fakes.

FakeGroupsDDB knows the handler's own condition expressions by their exact text, not a general
DynamoDB evaluator: a new condition fails loudly here (CONDITIONS) instead of being half-understood.
The race between real concurrent transactions is checked in sandbox (the PR's manual test plan).
"""

import importlib.util
import json
from pathlib import Path

import api_core
import pytest
from test_api_handler import NOW, ConditionFailed, FakeDDB, FakeLastfm, FakeSQS

from common import dynamo, guardrails, session
from common import groups as grp

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "api" / "handler.py"
BASE = "/toys/aoty-test/"
KEY = "k" * 48
ENV = {"USERS_TABLE": "users", "GUARD_TABLE": "guard", "GROUPS_TABLE": "groups", "SYNC_QUEUE_URL": "q/sync",
       "CHART_FUNCTION": "chart", "GROUP_WRITES": "true"}  # fmt: skip


@pytest.fixture
def h(monkeypatch):
  monkeypatch.setenv("BASE_PATH", BASE)
  spec = importlib.util.spec_from_file_location("api_handler_groups", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class Cancelled(Exception):
  def __init__(self, reasons):
    self.response = {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": reasons}


def _has(item):
  return item is not None


CONDITIONS = {
  None: lambda it, v: True,
  "attribute_not_exists(pk)": lambda it, v: it is None,
  "attribute_exists(pk)": lambda it, v: it is not None,
  "attribute_exists(pk) AND #o = :me": lambda it, v: _has(it) and it.get("owner") == v[":me"],
  "attribute_exists(pk) AND #o = :me AND member_count < :max":
    lambda it, v: _has(it) and it.get("owner") == v[":me"] and it.get("member_count", 0) < v[":max"],
  "#o = :me AND member_count = :n":
    lambda it, v: _has(it) and it.get("owner") == v[":me"] and it.get("member_count") == v[":n"],
}  # fmt: skip


class FakeGroupsDDB(FakeDDB):
  """The users and guard tables (FakeDDB), plus the groups table: (pk, sk) -> plain item."""

  def __init__(self, **kw):
    super().__init__(**kw)
    self.groups, self.conflicts, self.before_transact = {}, 0, None
    self.transactions = []

  # -- groups table helpers
  def seed(self, slug, owner, members, name=None, **meta):
    pk = grp.pk(slug)
    self.groups[(pk, "meta")] = {"pk": pk, "sk": "meta", "name": name or slug, "owner": owner,
                                 "member_count": len(members), "updated_at": NOW - 100, **meta}  # fmt: skip
    for m in members:
      self.groups[(pk, f"member#{m}")] = {"pk": pk, "sk": f"member#{m}", "username": m, "added_at": NOW - 100}

  def meta(self, slug):
    return self.groups.get((grp.pk(slug), "meta"))

  def members(self, slug):
    pk = grp.pk(slug)
    return sorted(sk[len("member#") :] for p, sk in self.groups if p == pk and sk.startswith("member#"))

  @staticmethod
  def _k(key):
    return key["pk"]["S"], key["sk"]["S"]

  @staticmethod
  def _apply_update(item, expr, names, values):
    names = names or {}
    adds, _, sets = (
      expr.removeprefix("ADD ").partition(" SET ") if expr.startswith("ADD ") else ("", "", expr)
    )
    sets = sets.removeprefix("SET ")
    for part in filter(None, adds.split(", ")):
      field, value = part.split(" ")
      item[field] = item.get(field, 0) + values[value]
    for part in filter(None, sets.split(", ")):
      field, value = part.split(" = ")
      item[names.get(field, field)] = values[value]

  # -- the DynamoDB calls the handler makes on the groups table
  def transact_write_items(self, TransactItems):
    self.transactions.append(TransactItems)
    if self.before_transact:
      hook, self.before_transact = self.before_transact, None
      hook(self)
    if self.conflicts:
      self.conflicts -= 1
      raise Cancelled([{"Code": "TransactionConflict"} for _ in TransactItems])
    assert len(TransactItems) <= 100
    reasons, plans = [], []
    for op in TransactItems:
      ((kind, spec),) = op.items()
      assert spec["TableName"] == "groups"
      key = self._k(spec["Key"]) if "Key" in spec else (spec["Item"]["pk"]["S"], spec["Item"]["sk"]["S"])
      current = self.groups.get(key)
      values = dynamo.from_item(spec.get("ExpressionAttributeValues") or {})
      ok = CONDITIONS[spec.get("ConditionExpression")](current, values)
      reason = {"Code": "None" if ok else "ConditionalCheckFailed"}
      if not ok and current and spec.get("ReturnValuesOnConditionCheckFailure") == "ALL_OLD":
        reason["Item"] = dynamo.to_item(current)
      reasons.append(reason)
      plans.append((kind, key, spec, values))
    if any(r["Code"] != "None" for r in reasons):
      raise Cancelled(reasons)
    for kind, key, spec, values in plans:
      if kind == "Put":
        self.groups[key] = dynamo.from_item(spec["Item"])
      elif kind == "Delete":
        self.groups.pop(key, None)
      else:
        self._apply_update(self.groups.setdefault(key, {}), spec["UpdateExpression"],
                           spec.get("ExpressionAttributeNames"), values)  # fmt: skip

  def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues, IndexName=None,
            ExpressionAttributeNames=None, **kw):  # fmt: skip
    assert TableName == "groups"
    v = dynamo.from_item(ExpressionAttributeValues)
    if IndexName == "by_owner":
      assert KeyConditionExpression == "#o = :o"
      rows = [it for it in self.groups.values() if it.get("sk") == "meta" and it.get("owner") == v[":o"]]
    else:
      assert IndexName is None and KeyConditionExpression == "pk = :pk"
      rows = [it for (p, _), it in sorted(self.groups.items()) if p == v[":pk"]]
    return {"Items": [dynamo.to_item(r) for r in rows]}

  def get_item(self, TableName, Key, **kw):
    if TableName == "groups":
      item = self.groups.get(self._k(Key))
      return {"Item": dynamo.to_item(item)} if item else {}
    return super().get_item(TableName, Key, **kw)

  def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeNames=None,
                  ExpressionAttributeValues=None, ConditionExpression=None, **kw):  # fmt: skip
    if TableName != "groups":
      return super().update_item(TableName, Key, UpdateExpression, ExpressionAttributeNames,
                                 ExpressionAttributeValues, ConditionExpression, **kw)  # fmt: skip
    key, values = self._k(Key), dynamo.from_item(ExpressionAttributeValues or {})
    current = self.groups.get(key)
    if not CONDITIONS[ConditionExpression](current, values):
      e = ConditionFailed()
      e.response = {**e.response, **({"Item": dynamo.to_item(current)} if current else {})}
      raise e
    self._apply_update(current, UpdateExpression, ExpressionAttributeNames, values)

  def batch_get_item(self, RequestItems):
    ((table, req),) = RequestItems.items()
    assert table == "users" and len(req["Keys"]) <= 100
    rows = [self.rows[k["username"]["S"]] for k in req["Keys"] if k["username"]["S"] in self.rows]
    return {"Responses": {table: [dynamo.to_item({k: v for k, v in r.items() if k != "progress"}) for r in rows]},
            "UnprocessedKeys": {}}  # fmt: skip


class FakeLambda:
  def __init__(self, fail=False):
    self.events, self.fail = [], fail

  def invoke(self, FunctionName, InvocationType, Payload):
    assert FunctionName == "chart" and InvocationType == "Event"
    if self.fail:
      raise RuntimeError("lambda down")
    self.events.append(json.loads(Payload))


def deps(h, ddb=None, sqs=None, fm=None, lam=None, env=None, auth=None, **settings):
  s = guardrails.Settings(**settings)
  return h.Deps(ddb=ddb or FakeGroupsDDB(), sqs=sqs or FakeSQS(), env={**ENV, **(env or {})},
                lastfm_client=fm or FakeLastfm(), clock=lambda: NOW, settings=lambda: s,
                auth_secrets=lambda: ({"session_key": KEY} if auth is None else auth), lam=lam or FakeLambda(),
                sleep=lambda s: None)  # fmt: skip


def call(h, d, method, path, body=None, user="alice", ctype="application/json"):
  event = {"rawPath": f"{BASE}api/{path}", "requestContext": {"http": {"method": method, "sourceIp": "1.2.3.4"}},
           "headers": {"content-type": ctype} if ctype else {}}  # fmt: skip
  if user:
    event["cookies"] = [f"{session.SESSION_COOKIE}={session.new_session(user, KEY, NOW)}"]
  if body is not None:
    event["body"] = json.dumps(body)
  out = h.handler(event, None, d)
  assert out["headers"]["cache-control"] == "no-store"
  return out["statusCode"], (json.loads(out["body"]) if out["body"] else None)


def known(ddb, *names):
  """Users who have synced before: no user.getInfo, no new-user limits."""
  for n in names:
    ddb.rows[n] = {"username": n, "state": "done", "last_synced_at": NOW - 9000, "sync_finished_at": NOW - 8000,
                   "sync_requested_at": NOW - 9000, "chart_built_at": NOW - 7000}  # fmt: skip


# ---- create, read, list ---------------------------------------------------------------------


def test_create_makes_the_owner_a_member_and_queues_their_sync(h):
  d = deps(h)
  status, body = call(h, d, "POST", "groups", {"name": "  The   Server "})
  assert status == 201 and body == {"id": "the-server", "sync": "queued"}
  meta = d.ddb.meta("the-server")
  assert (meta["name"], meta["owner"], meta["member_count"]) == ("The Server", "alice", 1)
  assert d.ddb.members("the-server") == ["alice"]
  assert d.sqs.sent[0]["MessageGroupId"] == "alice"
  assert d.lam.events == [{"group": "the-server", "requested_at": NOW}]


def test_create_refuses_a_taken_slug_and_takes_a_chosen_one(h):
  d = deps(h)
  d.ddb.seed("the-server", "bob", ["bob"])
  assert call(h, d, "POST", "groups", {"name": "The Server"}) == (409, {"error": "taken"})
  status, body = call(h, d, "POST", "groups", {"name": "The Server", "slug": "the-server-2"})
  assert status == 201 and body["id"] == "the-server-2"
  assert d.ddb.meta("the-server")["owner"] == "bob"  # untouched


@pytest.mark.parametrize(
  ("body", "error"),
  [({}, "bad_name"), ({"name": "x" * 61}, "bad_name"), ({"name": "日本"}, "needs_slug"),
   ({"name": "ok", "slug": "../x"}, "needs_slug")],
)  # fmt: skip
def test_create_validates(h, body, error):
  status, out = call(h, deps(h), "POST", "groups", body)
  assert status == 400 and out["error"] == error


def test_get_is_public_and_counts_ready_members(h):
  d = deps(h)
  d.ddb.seed("the-server", "alice", ["alice", "bob", "carol", "dave"], name="The Server")
  known(d.ddb, "alice", "bob")
  d.ddb.rows["carol"] = {"username": "carol", "state": "syncing"}  # a first sync: waiting
  d.ddb.rows["dave"] = {"username": "dave", "state": "private", "sync_finished_at": NOW}
  status, body = call(h, d, "GET", "groups/the-server", user=None, ctype=None)
  assert status == 200
  assert body == {"id": "the-server", "name": "The Server", "owner": "alice",
                  "members": ["alice", "bob", "carol", "dave"], "ready": 2, "total": 4}  # fmt: skip
  assert call(h, d, "GET", "groups/nope", user=None)[0] == 404
  assert call(h, d, "GET", "groups/..%2Fusers", user=None)[0] == 404


def test_my_groups_lists_only_the_callers_newest_first(h):
  d = deps(h)
  d.ddb.seed("old", "alice", ["alice"], updated_at=NOW - 500)
  d.ddb.seed("new", "alice", [], updated_at=NOW - 10)  # removed themselves: still theirs
  d.ddb.seed("bobs", "bob", ["alice", "bob"])
  status, body = call(h, d, "GET", "me/groups", ctype=None)
  assert status == 200 and [g["id"] for g in body["groups"]] == ["new", "old"]
  assert call(h, d, "GET", "me/groups", user=None)[0] == 401


# ---- auth and gates ------------------------------------------------------------------------

WRITES = [
  ("POST", "groups", {"name": "x"}),
  ("PATCH", "groups/g", {"name": "y"}),
  ("DELETE", "groups/g", None),
  ("POST", "groups/g/members", {"username": "carol"}),
  ("DELETE", "groups/g/members/bob", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_every_write_needs_a_session(h, method, path, body):
  assert call(h, deps(h), method, path, body, user=None) == (401, {"error": "signed_out"})


@pytest.mark.parametrize(("method", "path", "body"), WRITES[1:])
def test_another_users_writes_are_forbidden_and_change_nothing(h, method, path, body):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  before = dict(d.ddb.groups)
  assert call(h, d, method, path, body, user="mallory")[0] == 403
  assert d.ddb.groups == before and d.lam.events == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES[1:])
def test_writes_to_a_missing_group_are_404(h, method, path, body):
  assert call(h, deps(h), method, path, body)[0] == 404


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_writes_are_off_unless_enabled_and_json_only(h, method, path, body):
  assert call(h, deps(h, env={"GROUP_WRITES": "false"}), method, path, body) == (
    503,
    {"error": "groups_unavailable"},
  )
  assert call(h, deps(h), method, path, body, ctype="text/plain")[0] == 415


def test_unreadable_session_keys_are_503_never_401(h):
  d = deps(h, auth={})
  assert call(h, d, "POST", "groups", {"name": "x"}) == (503, {"error": "signin_unavailable"})
  assert call(h, d, "GET", "me/groups")[0] == 503
  d.ddb.seed("g", "alice", ["alice"])
  assert call(h, d, "GET", "groups/g", user=None)[0] == 200  # public reads don't need keys


def test_a_body_that_isnt_a_json_object_is_400(h):
  d = deps(h)
  event = {"rawPath": f"{BASE}api/groups", "requestContext": {"http": {"method": "POST"}},
           "headers": {"content-type": "application/json"}, "body": "[1, 2]",
           "cookies": [f"{session.SESSION_COOKIE}={session.new_session('alice', KEY, NOW)}"]}  # fmt: skip
  assert h.handler(event, None, d)["statusCode"] == 400


# ---- members -------------------------------------------------------------------------------


def test_adding_a_new_member_checks_lastfm_and_queues_their_sync(h):
  d = deps(h, fm=FakeLastfm(name="CaRoL"))
  d.ddb.seed("g", "alice", ["alice"])
  status, body = call(h, d, "POST", "groups/g/members", {"username": " CaRoL "})
  assert status == 201 and body["member"] == "carol" and body["sync"] == "queued"
  assert body["status"]["stage"] == "queued"
  assert d.ddb.members("g") == ["alice", "carol"] and d.ddb.meta("g")["member_count"] == 2
  assert d.ddb.meta("g")["chart_requested_at"] == NOW
  assert d.lastfm.calls == 1
  assert d.ddb.rows["carol"]["display_name"] == "CaRoL"
  assert [m["MessageGroupId"] for m in d.sqs.sent] == ["carol"]
  assert d.lam.events == [{"group": "g", "requested_at": NOW}]
  assert len([k for k in d.ddb.guard if k.startswith("ip#")]) == 1  # counted against the IP's new names


def test_adding_an_existing_user_skips_lastfm_and_respects_the_cooldown(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob")
  d.ddb.rows["bob"]["sync_requested_at"] = NOW - 10
  status, body = call(h, d, "POST", "groups/g/members", {"username": "bob"})
  assert status == 201 and body["sync"] == "cooldown"
  assert d.lastfm.calls == 0 and d.sqs.sent == [] and d.ddb.guard == {}


def test_adding_a_member_twice_is_200_and_changes_nothing(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  assert call(h, d, "POST", "groups/g/members", {"username": "bob"}) == (
    200,
    {"member": "bob", "added": False},
  )
  assert d.ddb.meta("g")["member_count"] == 2 and d.lam.events == []


def test_unknown_lastfm_user_is_404_and_not_added(h):
  d = deps(h, fm=FakeLastfm(name=None))
  d.ddb.seed("g", "alice", ["alice"])
  assert call(h, d, "POST", "groups/g/members", {"username": "nobody"})[0] == 404
  assert d.ddb.members("g") == ["alice"]


def test_the_26th_member_is_refused_before_any_lastfm_call(h):
  d = deps(h)
  d.ddb.seed("g", "alice", [f"m{i:02}" for i in range(25)])
  assert call(h, d, "POST", "groups/g/members", {"username": "late"}) == (409, {"error": "full", "max": 25})
  assert d.lastfm.calls == 0 and d.ddb.guard == {}


def test_a_concurrent_add_at_24_cant_push_the_count_past_25(h):
  d = deps(h)
  d.ddb.seed("g", "alice", [f"m{i:02}" for i in range(24)])
  known(d.ddb, "late", "racer")

  def racer(ddb):  # another tab's add lands between our read and our transaction
    ddb.groups[(grp.pk("g"), "member#racer")] = {"pk": grp.pk("g"), "sk": "member#racer", "username": "racer"}
    ddb.groups[(grp.pk("g"), "meta")]["member_count"] = 25

  d.ddb.before_transact = racer
  assert call(h, d, "POST", "groups/g/members", {"username": "late"}) == (409, {"error": "full", "max": 25})
  assert len(d.ddb.members("g")) == 25 and "late" not in d.ddb.members("g")


def test_a_transaction_conflict_is_retried(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob")
  d.ddb.conflicts = 2
  assert call(h, d, "POST", "groups/g/members", {"username": "bob"})[0] == 201
  assert len(d.ddb.transactions) == 3


def test_someone_who_blocked_adds_gets_a_neutral_refusal(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob", "alice")
  d.ddb.rows["bob"]["block_group_adds"] = True
  d.ddb.rows["alice"]["block_group_adds"] = True
  assert call(h, d, "POST", "groups/g/members", {"username": "bob"}) == (409, {"error": "cannot_add"})
  d.ddb.groups.pop((grp.pk("g"), "member#alice"))
  d.ddb.meta("g")["member_count"] = 0
  assert call(h, d, "POST", "groups/g/members", {"username": "alice"})[0] == 201  # their own group


@pytest.mark.parametrize(
  ("settings", "depth", "status", "error"),
  [({"pause_new_users": True}, 0, 503, "paused"), ({"max_queue_for_new": 5}, 6, 503, "busy"),
   ({"new_users_per_ip_hour": 1}, 0, 429, "too_many_new")],
)  # fmt: skip
def test_new_member_guardrails_refuse_before_writing(h, settings, depth, status, error):
  d = deps(h, sqs=FakeSQS(depth=depth), **settings)
  d.ddb.seed("g", "alice", ["alice"])
  # This IP's one new name this hour is already used.
  d.ddb.guard[api_core.ip_limit_key("1.2.3.4", NOW // 3600)] = {
    "names": {"someone"},
    "expires_at": NOW + 9000,
  }
  assert call(h, d, "POST", "groups/g/members", {"username": "carol"}) == (status, {"error": error})
  assert d.ddb.members("g") == ["alice"] and d.sqs.sent == []


def test_pause_syncs_still_adds_an_existing_user(h):
  d = deps(h, pause_syncs=True)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob")
  status, body = call(h, d, "POST", "groups/g/members", {"username": "bob"})
  assert status == 201 and body["sync"] == "paused" and d.sqs.sent == []


def test_a_failed_send_keeps_the_member(h):
  d = deps(h, sqs=FakeSQS(fail=True))
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob")
  status, body = call(h, d, "POST", "groups/g/members", {"username": "bob"})
  assert status == 201 and body["sync"] == "not_queued"
  assert "bob" in d.ddb.members("g")


def test_a_failed_chart_poke_never_fails_the_write(h):
  d = deps(h, lam=FakeLambda(fail=True))
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob")
  assert call(h, d, "POST", "groups/g/members", {"username": "bob"})[0] == 201


def test_remove_is_idempotent_and_the_owner_may_leave(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  assert call(h, d, "DELETE", "groups/g/members/bob") == (204, None)
  assert d.ddb.members("g") == ["alice"] and d.ddb.meta("g")["member_count"] == 1
  assert d.lam.events == [{"group": "g", "requested_at": NOW}]
  assert call(h, d, "DELETE", "groups/g/members/bob") == (204, None)
  assert d.ddb.meta("g")["member_count"] == 1 and len(d.lam.events) == 1  # nothing written
  assert call(h, d, "DELETE", "groups/g/members/alice") == (204, None)
  assert d.ddb.members("g") == [] and d.ddb.meta("g")["owner"] == "alice"


# ---- rename and delete ---------------------------------------------------------------------


def test_rename_moves_the_group_to_the_new_names_slug(h):
  d = deps(h)
  d.ddb.seed("test-group", "alice", ["alice", "bob"], name="Test Group", created_at=NOW - 999,
             chart_built_at=NOW - 50)  # fmt: skip
  status, body = call(h, d, "PATCH", "groups/test-group", {"name": "Best Group"})
  assert status == 200 and body == {"id": "best-group"}
  assert d.ddb.meta("test-group") is None and d.ddb.members("test-group") == []
  meta = d.ddb.meta("best-group")
  assert (meta["name"], meta["owner"], meta["member_count"], meta["created_at"]) == (
    "Best Group",
    "alice",
    2,
    NOW - 999,
  )
  assert "chart_built_at" not in meta and meta["chart_requested_at"] == NOW
  assert d.ddb.members("best-group") == ["alice", "bob"]
  assert d.lam.events == [
    {"group": "test-group", "deleted": True},
    {"group": "best-group", "requested_at": NOW},
  ]


def test_rename_to_the_same_slug_changes_only_the_name(h):
  d = deps(h)
  d.ddb.seed("best-group", "alice", ["alice"], name="Best Group")
  assert call(h, d, "PATCH", "groups/best-group", {"name": "best group!"}) == (200, {"id": "best-group"})
  assert d.ddb.meta("best-group")["name"] == "best group!"
  assert d.lam.events == [{"group": "best-group", "requested_at": NOW}]


def test_rename_onto_a_taken_slug_is_refused_and_moves_nothing(h):
  d = deps(h)
  d.ddb.seed("a", "alice", ["alice"])
  d.ddb.seed("b", "bob", ["bob"])
  assert call(h, d, "PATCH", "groups/a", {"name": "B"}) == (409, {"error": "taken"})
  assert d.ddb.members("a") == ["alice"] and d.ddb.meta("b")["owner"] == "bob"


def test_delete_removes_every_item_and_the_chart(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  d.ddb.seed("other", "alice", ["bob"])
  assert call(h, d, "DELETE", "groups/g") == (204, None)
  assert not [k for k in d.ddb.groups if k[0] == grp.pk("g")]
  assert d.ddb.members("other") == ["bob"]
  assert d.lam.events == [{"group": "g", "deleted": True}]
  assert call(h, d, "GET", "groups/g", user=None)[0] == 404


def test_delete_retries_once_when_an_add_lands_in_between(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"])

  def add(ddb):
    ddb.groups[(grp.pk("g"), "member#bob")] = {"pk": grp.pk("g"), "sk": "member#bob", "username": "bob"}
    ddb.groups[(grp.pk("g"), "meta")]["member_count"] = 2

  d.ddb.before_transact = add
  assert call(h, d, "DELETE", "groups/g") == (204, None)
  assert not [k for k in d.ddb.groups if k[0] == grp.pk("g")]  # bob went too


def test_group_writes_emit_one_line_with_the_outcome(h, capsys):
  d = deps(h)
  call(h, d, "POST", "groups", {"name": "x"})
  lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
  assert [(line["action"], line["outcome"]) for line in lines] == [("group_create", "created")]
