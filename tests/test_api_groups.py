"""The api's group routes (AOTY-48) against in-memory fakes.

FakeGroupsDDB knows the handler's own condition expressions by their exact text, not a general
DynamoDB evaluator: a new condition fails loudly here (CONDITIONS) instead of being half-understood.
The race between real concurrent transactions is checked in sandbox (the PR's manual test plan).
"""

import importlib.util
import json
from pathlib import Path

import api_core
import groups_store
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
  # a rename's old meta, guarded against a theme saved since it was read (AOTY-76)
  "#o = :me AND member_count = :n" + groups_store.THEME_UNSET:
    lambda it, v: _has(it) and it.get("owner") == v[":me"] and it.get("member_count") == v[":n"]
    and "theme_set_at" not in it,
  "#o = :me AND member_count = :n" + groups_store.THEME_SAME:
    lambda it, v: _has(it) and it.get("owner") == v[":me"] and it.get("member_count") == v[":n"]
    and it.get("theme_set_at") == v[":ts"],
  # a group visit's sync claim and release (AOTY-55)
  groups_store.CLAIM_SYNC:
    lambda it, v: _has(it) and ("sync_requested_at" not in it or it["sync_requested_at"] <= v[":cool"]),
  groups_store.RELEASE_SYNC: lambda it, v: _has(it) and it.get("sync_requested_at") == v[":now"],
  # sign out everywhere (AOTY-57)
  groups_store.REVOKE: lambda it, v: it is None or "revoked_at" not in it or it["revoked_at"] < v[":now"],
}  # fmt: skip


class FakeGroupsDDB(FakeDDB):
  """The users and guard tables (FakeDDB), plus the groups table: (pk, sk) -> plain item."""

  def __init__(self, **kw):
    super().__init__(**kw)
    self.groups, self.conflicts, self.before_transact = {}, 0, None
    self.transactions = []
    # AOTY-49: by_member rows whose group is gone (the index lags), batch gets to answer unprocessed
    self.stale_index, self.unprocessed, self.group_batches = [], 0, []

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
    elif IndexName == "by_member":  # keys only (AOTY-49)
      assert KeyConditionExpression == "username = :u"
      rows = [{k: it[k] for k in ("pk", "sk", "username")} for _, it in sorted(self.groups.items())
              if it.get("username") == v[":u"]]  # fmt: skip
      rows += self.stale_index
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
    current = self.groups.setdefault(key, {"pk": key[0], "sk": key[1]}) if current is None else current
    before = dict(current)
    self._apply_update(current, UpdateExpression, ExpressionAttributeNames, values)
    if kw.get("ReturnValues") == "UPDATED_OLD":
      changed = {k: v for k, v in before.items() if current.get(k) != v}
      return {"Attributes": dynamo.to_item(changed)}
    return {}

  def batch_get_item(self, RequestItems):
    ((table, req),) = RequestItems.items()
    assert len(req["Keys"]) <= 100
    if table == "groups":  # the metas of a member's groups (AOTY-49)
      self.group_batches.append(len(req["Keys"]))
      keys = [self._k(k) for k in req["Keys"]]
      if self.unprocessed:
        self.unprocessed -= 1
        return {"Responses": {table: []}, "UnprocessedKeys": {table: {"Keys": req["Keys"]}}}
      rows = [self.groups[k] for k in keys if k in self.groups]
      return {"Responses": {table: [dynamo.to_item({f: r[f] for f in ("pk", "name", "owner") if f in r})
                                    for r in rows]}, "UnprocessedKeys": {}}  # fmt: skip
    assert table == "users"
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
  d.ddb.rows["carol"] = {"username": "carol", "state": "syncing", "sync_started_at": NOW - 60}  # a first sync
  d.ddb.rows["dave"] = {"username": "dave", "state": "private", "sync_finished_at": NOW}
  status, body = call(h, d, "GET", "groups/the-server", user=None, ctype=None)
  assert status == 200
  assert body == {"id": "the-server", "name": "The Server", "owner": "alice",
                  "members": ["alice", "bob", "carol", "dave"], "ready": 2, "total": 4,
                  "syncing": 1}  # fmt: skip
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
  ("POST", "groups/g/theme", {"theme": "kamp"}),  # AOTY-76
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


def test_a_member_whose_name_starts_with_an_underscore_can_be_added(h):
  # AOTY-80: "_korps_" is a real, older Last.fm account.
  d = deps(h, fm=FakeLastfm(name="_korps_"))
  d.ddb.seed("g", "alice", ["alice"])
  status, body = call(h, d, "POST", "groups/g/members", {"username": "_korps_"})
  assert status == 201 and body["member"] == "_korps_"
  assert sorted(d.ddb.members("g")) == ["_korps_", "alice"]


def test_adding_an_existing_user_skips_lastfm_and_respects_the_cooldown(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "bob")
  d.ddb.rows["bob"]["sync_requested_at"] = NOW - 10
  status, body = call(h, d, "POST", "groups/g/members", {"username": "bob"})
  assert status == 201 and body["sync"] == "cooldown"
  assert d.lastfm.calls == 0 and d.sqs.sent == []
  assert not [k for k in d.ddb.guard if k.startswith("ip#")]  # a known user spends no IP slot


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


# ---- group guardrails (AOTY-53) ------------------------------------------------------------


def test_pause_groups_stops_creates_and_adds_but_not_the_rest(h):
  d = deps(h, pause_groups=True)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  known(d.ddb, "carol")
  assert call(h, d, "POST", "groups", {"name": "new"}) == (503, {"error": "groups_paused"})
  assert call(h, d, "POST", "groups/g/members", {"username": "carol"}) == (503, {"error": "groups_paused"})
  assert d.lastfm.calls == 0 and d.ddb.guard == {} and "new" not in d.ddb.members("new")
  assert call(h, d, "PATCH", "groups/g", {"name": "renamed"})[0] == 200
  assert call(h, d, "DELETE", "groups/renamed/members/bob")[0] == 204
  assert call(h, d, "GET", "groups/renamed", user=None)[0] == 200


def test_groups_count_as_paused_before_ssm_was_ever_read(h):
  d = deps(h, loaded=False)
  assert call(h, d, "POST", "groups", {"name": "new"}) == (503, {"error": "groups_paused"})


def test_an_owner_at_the_group_cap_cant_start_another(h):
  d = deps(h, max_groups_per_owner=2)
  d.ddb.seed("one", "alice", ["alice"])
  d.ddb.seed("bobs", "bob", ["alice", "bob"])  # in it, not owner: doesn't count
  assert call(h, d, "POST", "groups", {"name": "two"})[0] == 201
  assert call(h, d, "POST", "groups", {"name": "three"}) == (409, {"error": "too_many_groups", "max": 2})
  assert d.ddb.meta("three") is None
  assert call(h, d, "POST", "groups", {"name": "bobs two"}, user="bob")[0] == 201


def test_member_adds_per_owner_hour_are_capped_across_groups(h):
  d = deps(h, max_group_member_adds_per_hour=2)
  d.ddb.seed("a", "alice", ["alice"])
  d.ddb.seed("b", "alice", ["alice"])
  known(d.ddb, "bob", "carol", "dave")
  assert call(h, d, "POST", "groups/a/members", {"username": "bob"})[0] == 201
  assert call(h, d, "POST", "groups/b/members", {"username": "carol"})[0] == 201
  assert call(h, d, "POST", "groups/b/members", {"username": "dave"}) == (
    429, {"error": "too_many_adds", "max": 2},
  )  # fmt: skip
  assert "dave" not in d.ddb.members("b") and d.lastfm.calls == 0
  # The same name again is free (a retry, or adding bob to the other group); others have their own.
  assert call(h, d, "POST", "groups/b/members", {"username": "bob"})[0] == 201
  d.ddb.seed("c", "carol", ["carol"])
  assert call(h, d, "POST", "groups/c/members", {"username": "dave"}, user="carol")[0] == 201
  key = api_core.owner_adds_key("alice", NOW // 3600)
  assert key.startswith("adds#") and "alice" not in key and len(d.ddb.guard[key]["names"]) == 2


def test_the_add_cap_is_counted_before_any_lastfm_call(h):
  d = deps(h, max_group_member_adds_per_hour=1)
  d.ddb.seed("g", "alice", ["alice"])
  assert call(h, d, "POST", "groups/g/members", {"username": "carol"})[0] == 201  # new: one getInfo
  assert call(h, d, "POST", "groups/g/members", {"username": "erin"})[0] == 429
  assert d.lastfm.calls == 1


def test_cheap_refusals_spend_no_add_allowance(h):
  d = deps(h, max_group_member_adds_per_hour=1)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  d.ddb.seed("full", "alice", [f"m{i:02}" for i in range(25)])
  call(h, d, "POST", "groups/g/members", {"username": "bob"})  # already a member
  call(h, d, "POST", "groups/full/members", {"username": "late"})  # full
  call(h, d, "POST", "groups/g/members", {"username": "x"}, user="mallory")  # not theirs
  assert d.ddb.guard == {}


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


# ---- opting out (AOTY-49) ------------------------------------------------------------------


def test_a_member_may_leave_anyones_group_and_its_chart_rebuilds(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob", "carol"])
  assert call(h, d, "DELETE", "groups/g/members/bob", user="bob") == (204, None)
  assert d.ddb.members("g") == ["alice", "carol"] and d.ddb.meta("g")["member_count"] == 2
  assert d.ddb.meta("g")["chart_requested_at"] == NOW and d.ddb.meta("g")["owner"] == "alice"
  assert d.lam.events == [{"group": "g", "requested_at": NOW}]
  # Again, or someone who was never in it: 204, nothing written.
  assert call(h, d, "DELETE", "groups/g/members/bob", user="bob") == (204, None)
  assert call(h, d, "DELETE", "groups/g/members/mallory", user="mallory") == (204, None)
  assert d.ddb.meta("g")["member_count"] == 2 and len(d.lam.events) == 1


def test_a_member_cant_remove_anyone_else(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob", "carol"])
  before = dict(d.ddb.groups)
  assert call(h, d, "DELETE", "groups/g/members/carol", user="bob") == (403, {"error": "not your group"})
  assert call(h, d, "DELETE", "groups/g/members/alice", user="bob")[0] == 403
  assert d.ddb.groups == before and d.lam.events == []


def test_leaving_a_missing_group_is_404(h):
  assert call(h, deps(h), "DELETE", "groups/gone/members/bob", user="bob") == (
    404,
    {"error": "no such group"},
  )


def test_a_member_named_me_is_removed_by_name_not_mistaken_for_the_caller(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "me"])
  assert call(h, d, "DELETE", "groups/g/members/me", user="bob")[0] == 403  # bob isn't "me"
  assert call(h, d, "DELETE", "groups/g/members/me")[0] == 204  # the owner removes the user "me"
  assert d.ddb.members("g") == ["alice"]


def test_memberships_list_others_groups_by_name_with_the_setting(h):
  d = deps(h)
  d.ddb.seed("zed", "bob", ["alice", "bob"], name="Zed's")
  d.ddb.seed("abc", "carol", ["alice", "carol"], name="abc club")
  d.ddb.seed("mine", "alice", ["alice"])  # their own: me/groups lists it
  d.ddb.seed("other", "bob", ["bob"])  # not a member
  d.ddb.stale_index.append({"pk": grp.pk("deleted"), "sk": "member#alice", "username": "alice"})
  status, body = call(h, d, "GET", "me/memberships", ctype=None)
  assert status == 200
  assert body == {"groups": [{"id": "abc", "name": "abc club", "owner": "carol"},
                             {"id": "zed", "name": "Zed's", "owner": "bob"}],
                  "block_group_adds": False}  # fmt: skip
  known(d.ddb, "alice")
  d.ddb.rows["alice"]["block_group_adds"] = True
  assert call(h, d, "GET", "me/memberships", ctype=None)[1]["block_group_adds"] is True
  assert call(h, d, "GET", "me/memberships", user=None)[0] == 401


def test_memberships_read_metas_in_batches_of_100_and_retry_unprocessed(h):
  d = deps(h)
  for i in range(130):
    d.ddb.seed(f"g{i:03}", "bob", ["alice"])
  d.ddb.unprocessed = 1
  body = call(h, d, "GET", "me/memberships", ctype=None)[1]
  assert len(body["groups"]) == 130 and d.ddb.group_batches == [100, 100, 30]


def test_memberships_with_none_is_empty(h):
  assert call(h, deps(h), "GET", "me/memberships", ctype=None) == (
    200,
    {"groups": [], "block_group_adds": False},
  )


def test_blocking_sets_the_flag_making_the_row_and_unblocking_removes_it(h):
  d = deps(h)
  assert call(h, d, "PUT", "me/settings", {"block_group_adds": True}) == (200, {"block_group_adds": True})
  assert d.ddb.rows["alice"] == {"username": "alice", "block_group_adds": True}
  assert call(h, d, "PUT", "me/settings", {"block_group_adds": False}) == (200, {"block_group_adds": False})
  assert d.ddb.rows["alice"] == {"username": "alice"}
  # Unblocking with no row makes none.
  assert call(h, d, "PUT", "me/settings", {"block_group_adds": False}, user="bob")[0] == 200
  assert "bob" not in d.ddb.rows


def test_a_row_made_by_blocking_still_counts_as_new_on_its_first_sync(h):
  d = deps(h, pause_new_users=True)
  call(h, d, "PUT", "me/settings", {"block_group_adds": True})
  event = {"rawPath": f"{BASE}api/users/alice/sync", "requestContext": {"http": {"method": "POST"}},
           "headers": {"content-type": "application/json"}}  # fmt: skip
  out = h.handler(event, None, d)
  assert out["statusCode"] == 503 and json.loads(out["body"]) == {"error": "paused"}


@pytest.mark.parametrize("body", [{}, {"block_group_adds": "yes"}, {"block_group_adds": 1},
                                  {"block_group_adds": True, "theme": "kamp"}])  # fmt: skip
def test_settings_take_only_a_bool(h, body):
  d = deps(h)
  assert call(h, d, "PUT", "me/settings", body) == (400, {"error": "bad_settings"})
  assert d.ddb.rows == {}


def test_settings_need_a_session_json_and_writes_on(h):
  body = {"block_group_adds": True}
  assert call(h, deps(h), "PUT", "me/settings", body, user=None) == (401, {"error": "signed_out"})
  assert call(h, deps(h), "PUT", "me/settings", body, ctype="text/plain")[0] == 415
  off = deps(h, env={"GROUP_WRITES": "false"})
  assert call(h, off, "PUT", "me/settings", body) == (503, {"error": "groups_unavailable"})
  assert call(h, deps(h, auth={}), "PUT", "me/settings", body)[0] == 503
  assert call(h, deps(h), "POST", "me/settings", body)[0] == 405


def test_with_the_block_on_every_other_owners_add_is_refused_and_their_own_groups_work(h):
  d = deps(h)
  known(d.ddb, "bob", "carol")
  call(h, d, "PUT", "me/settings", {"block_group_adds": True}, user="bob")
  d.ddb.seed("alices", "alice", ["alice"])
  d.ddb.seed("carols", "carol", ["carol"])
  for owner, slug in (("alice", "alices"), ("carol", "carols")):
    assert call(h, d, "POST", f"groups/{slug}/members", {"username": "Bob"}, user=owner) == (
      409, {"error": "cannot_add"},
    )  # fmt: skip
  assert call(h, d, "POST", "groups", {"name": "bobs"}, user="bob")[0] == 201
  assert d.ddb.members("bobs") == ["bob"]
  d.ddb.groups.pop((grp.pk("bobs"), "member#bob"))
  d.ddb.meta("bobs")["member_count"] = 0
  assert call(h, d, "POST", "groups/bobs/members", {"username": "bob"}, user="bob")[0] == 201
  assert call(h, d, "POST", "groups/bobs/members", {"username": "carol"}, user="bob")[0] == 201
  call(h, d, "PUT", "me/settings", {"block_group_adds": False}, user="bob")
  assert call(h, d, "POST", "groups/alices/members", {"username": "bob"})[0] == 201


def test_opt_out_writes_emit_their_outcome(h, capsys):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  call(h, d, "DELETE", "groups/g/members/bob", user="bob")
  call(h, d, "PUT", "me/settings", {"block_group_adds": True}, user="bob")
  lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
  assert [(line["action"], line["outcome"]) for line in lines] == [("member_remove", "left"),
                                                                    ("my_settings", "blocked")]  # fmt: skip


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


# ---- a visit's member syncs (AOTY-55, AOTY-60) ----------------------------------------------


def visit(h, d, slug="g", **kw):
  kw.setdefault("user", None)  # anyone may visit: signed out is the usual case
  return call(h, d, "POST", f"groups/{slug}/sync", {}, **kw)


def queued_names(d):
  return sorted(m["MessageGroupId"] for m in d.sqs.sent)


def test_a_visit_queues_every_member_past_their_cooldown_signed_out(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob", "carol"])
  known(d.ddb, "alice", "bob", "carol")
  d.ddb.rows["carol"]["sync_requested_at"] = NOW - 60  # visited her own page a minute ago
  status, body = visit(h, d)
  assert status == 200 and body == {"queued": 2, "members": {"cooldown": 1, "queued": 2}}
  assert queued_names(d) == ["alice", "bob"]
  assert d.ddb.rows["bob"]["state"] == "queued"
  assert d.ddb.meta("g")["sync_requested_at"] == NOW


def test_a_second_visit_within_the_group_cooldown_queues_nothing(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"], sync_requested_at=NOW - 3599)
  known(d.ddb, "alice")
  assert visit(h, d) == (200, {"queued": 0, "reason": "cooldown"})
  assert d.sqs.sent == [] and d.ddb.meta("g")["sync_requested_at"] == NOW - 3599
  d2 = deps(h, ddb=d.ddb, group_sync_cooldown_seconds=1800)  # a shorter knob: past it now
  assert visit(h, d2)[1]["queued"] == 1


def test_pause_syncs_writes_nothing(h):
  d = deps(h, pause_syncs=True)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "alice")
  assert visit(h, d) == (200, {"queued": 0, "reason": "paused"})
  assert d.sqs.sent == [] and "sync_requested_at" not in d.ddb.meta("g")


def test_it_works_where_group_writes_are_off_and_with_no_session_keys(h):
  d = deps(h, env={"GROUP_WRITES": "false"}, auth={})
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "alice")
  assert visit(h, d)[1]["queued"] == 1


def test_a_visit_must_be_json_and_name_a_real_group(h):
  d = deps(h)
  assert call(h, d, "POST", "groups/g/sync", {}, user=None, ctype="text/plain")[0] == 415
  assert visit(h, d, slug="nope") == (404, {"error": "no such group"})
  assert visit(h, d, slug="..%2Fx")[0] == 404
  assert call(h, d, "GET", "groups/g/sync", user=None)[0] == 405


def test_unknown_private_and_blocking_members_are_left_alone(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob", "carol", "dave", "erin"])
  known(d.ddb, "alice", "bob", "carol", "dave")
  d.ddb.rows["bob"]["state"] = "not_found"
  d.ddb.rows["carol"]["state"] = "private"
  d.ddb.rows["dave"]["block_group_adds"] = True
  status, body = visit(h, d)  # erin has no row yet (her add's send failed): queued as new
  assert body["members"] == {"blocked": 1, "not_found": 1, "private": 1, "queued": 2}
  assert queued_names(d) == ["alice", "erin"]


def test_members_all_in_their_own_cooldown_still_claim_the_group(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  known(d.ddb, "alice", "bob")
  for n in ("alice", "bob"):
    d.ddb.rows[n]["sync_requested_at"] = NOW - 10
  assert visit(h, d) == (200, {"queued": 0, "members": {"cooldown": 2}})
  assert d.ddb.meta("g")["sync_requested_at"] == NOW


def test_a_visit_whose_sends_all_failed_gives_the_claim_back(h):
  d = deps(h, sqs=FakeSQS(fail=True))
  d.ddb.seed("g", "alice", ["alice", "bob"], sync_requested_at=NOW - 7200)
  known(d.ddb, "alice", "bob")
  assert visit(h, d)[1] == {"queued": 0, "members": {"not_queued": 2}}
  assert d.ddb.meta("g")["sync_requested_at"] == NOW - 7200
  assert d.ddb.rows["alice"]["sync_requested_at"] == NOW - 9000  # each member's mark undone too


def test_the_release_never_undoes_a_later_visits_claim(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"], sync_requested_at=NOW + 5)  # another visit, a moment later
  assert not groups_store.release_sync(d, "g", NOW, NOW - 7200)
  assert d.ddb.meta("g")["sync_requested_at"] == NOW + 5


def test_a_partial_failure_keeps_the_claim(h):
  class FlakySQS(FakeSQS):
    def send_message(self, **kw):
      if kw["MessageGroupId"] == "bob":
        raise RuntimeError("SQS blip")
      super().send_message(**kw)

  d = deps(h, sqs=FlakySQS())
  d.ddb.seed("g", "alice", ["alice", "bob"])
  known(d.ddb, "alice", "bob")
  assert visit(h, d)[1] == {"queued": 1, "members": {"not_queued": 1, "queued": 1}}
  assert d.ddb.meta("g")["sync_requested_at"] == NOW


def test_one_members_error_doesnt_stop_the_rest(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  known(d.ddb, "alice", "bob")
  real = d.ddb.update_item

  def update_item(TableName, Key, *a, **kw):
    if TableName == "users" and Key["username"]["S"] == "alice":
      raise RuntimeError("throttled")
    return real(TableName, Key, *a, **kw)

  d.ddb.update_item = update_item
  assert visit(h, d)[1] == {"queued": 1, "members": {"queued": 1, "unavailable": 1}}


def test_a_full_group_visit_stays_lean(h):
  """25 members in one request: one batch read of the rows, no per-member re-read or status."""
  d = deps(h)
  names = [f"m{i:02d}" for i in range(grp.MAX_MEMBERS)]
  d.ddb.seed("g", "m00", names)
  known(d.ddb, *names)
  reads = []
  real = d.ddb.get_item

  def get_item(TableName, Key, **kw):
    reads.append(TableName)
    return real(TableName, Key, **kw)

  d.ddb.get_item = get_item
  assert visit(h, d)[1]["queued"] == grp.MAX_MEMBERS
  assert "users" not in reads


def test_a_visit_emits_one_line_with_the_count(h, capsys):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice"])
  known(d.ddb, "alice")
  visit(h, d)
  lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
  assert [(x["action"], x["outcome"], x["queued"]) for x in lines] == [("group_sync", "synced", 1)]
  assert "SyncRequests" not in lines[0]


def test_rename_leaves_the_build_read_stamp_behind(h):
  d = deps(h)
  d.ddb.seed("a", "alice", ["alice"], chart_read_at=NOW * 1000, sync_requested_at=NOW - 10)
  assert call(h, d, "PATCH", "groups/a", {"name": "B"}) == (200, {"id": "b"})
  meta = d.ddb.meta("b")
  assert "chart_read_at" not in meta and meta["sync_requested_at"] == NOW - 10


# ---- sign out everywhere (AOTY-57) ----------------------------------------------------------


def raw(h, d, method, path, cookie=None, body=None, query=None, ctype="application/json"):
  """call() with a given session cookie (or none), returning the whole response."""
  event = {"rawPath": f"{BASE}api/{path}", "requestContext": {"http": {"method": method, "sourceIp": "1.2.3.4"}},
           "headers": {"content-type": ctype} if ctype else {}}  # fmt: skip
  if cookie:
    event["cookies"] = [f"{session.SESSION_COOKIE}={cookie}"]
  if body is not None:
    event["body"] = json.dumps(body)
  if query:
    event["queryStringParameters"] = query
  return h.handler(event, None, d)


def everywhere(h, d, cookie, **kw):
  return raw(h, d, "POST", "auth/signout", cookie, query={"everywhere": "1"}, **kw)


def cleared(out):
  return any(c.startswith(f"{session.SESSION_COOKIE}=;") for c in out.get("cookies", []))


def cutoff(d, user="alice"):
  return (d.ddb.groups.get((grp.session_pk(user), "meta")) or {}).get("revoked_at")


def group_with_members(h):
  d = deps(h)
  d.ddb.seed("g", "alice", ["alice", "bob"])
  known(d.ddb, "alice", "bob", "carol")
  return d


@pytest.mark.parametrize(("method", "path", "body"), [*WRITES, ("GET", "me/groups", None)])
def test_after_sign_out_everywhere_a_copied_cookie_is_401_on_every_write(h, method, path, body):
  d = group_with_members(h)
  copied = session.new_session("alice", KEY, NOW - 3600)
  out = everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  assert out["statusCode"] == 200 and json.loads(out["body"]) == {"signed_out": True, "everywhere": True}
  assert cleared(out) and cutoff(d) == NOW
  before = dict(d.ddb.groups)
  out = raw(h, d, method, path, copied, body, ctype=None if method == "GET" else "application/json")
  assert out["statusCode"] == 401 and json.loads(out["body"]) == {"error": "signed_out"}
  assert d.ddb.groups == before and d.lam.events == []


def test_a_revoked_cookie_reads_as_signed_out_on_me_and_is_cleared(h):
  d = group_with_members(h)
  copied = session.new_session("alice", KEY, NOW - 3600)
  assert raw(h, d, "GET", "me", copied, ctype=None)["statusCode"] == 200
  everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  out = raw(h, d, "GET", "me", copied, ctype=None)
  assert out["statusCode"] == 401 and cleared(out)


def test_signing_in_again_after_the_revoke_works(h):
  d = group_with_members(h)
  everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  d.clock = lambda: NOW + 30
  fresh = session.new_session("alice", KEY, NOW + 20)
  assert raw(h, d, "GET", "me", fresh, ctype=None)["statusCode"] == 200
  assert raw(h, d, "PATCH", "groups/g", fresh, {"name": "renamed"})["statusCode"] == 200


def test_revoking_one_user_leaves_everyone_else_signed_in(h):
  d = group_with_members(h)
  d.ddb.seed("b", "bob", ["bob"])
  everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  bob = session.new_session("bob", KEY, NOW - 3600)
  assert raw(h, d, "PATCH", "groups/b", bob, {"name": "bobs"})["statusCode"] == 200


def test_a_plain_sign_out_revokes_nothing(h):
  d = group_with_members(h)
  copied = session.new_session("alice", KEY, NOW - 3600)
  out = raw(h, d, "POST", "auth/signout", copied)
  assert out["statusCode"] == 200 and json.loads(out["body"]) == {"signed_out": True} and cleared(out)
  assert cutoff(d) is None
  assert raw(h, d, "PATCH", "groups/g", copied, {"name": "still mine"})["statusCode"] == 200


def test_a_revoked_cookie_cant_move_the_cutoff_again(h):
  d = group_with_members(h)
  copied = session.new_session("alice", KEY, NOW - 3600)
  everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  d.clock = lambda: NOW + 500
  out = everywhere(h, d, copied)
  assert out["statusCode"] == 401 and cutoff(d) == NOW


def test_sign_out_everywhere_needs_a_session_and_json(h):
  d = group_with_members(h)
  out = everywhere(h, d, None)
  assert out["statusCode"] == 401 and cutoff(d) is None
  out = everywhere(h, d, session.new_session("alice", KEY, NOW - 60), ctype="text/plain")
  assert out["statusCode"] == 415 and cutoff(d) is None and "cookies" not in out


def test_the_cutoff_only_moves_forward(h):
  d = group_with_members(h)
  d.ddb.groups[(grp.session_pk("alice"), "meta")] = {
    "pk": grp.session_pk("alice"),
    "sk": "meta",
    "revoked_at": NOW + 100,
  }
  out = everywhere(h, d, session.new_session("alice", KEY, NOW + 200))  # signed in after that cutoff
  assert out["statusCode"] == 200 and cutoff(d) == NOW + 100


def test_a_cutoff_that_isnt_a_number_fails_closed(h):
  d = group_with_members(h)
  d.ddb.groups[(grp.session_pk("alice"), "meta")] = {
    "pk": grp.session_pk("alice"),
    "sk": "meta",
    "revoked_at": "soon",
  }
  assert (
    raw(h, d, "PATCH", "groups/g", session.new_session("alice", KEY, NOW), {"name": "x"})["statusCode"] == 401
  )


def test_writes_read_the_cutoff_strongly_and_me_doesnt(h):
  d = group_with_members(h)
  reads, get = [], d.ddb.get_item

  def spy(TableName, Key, **kw):
    if TableName == "groups" and Key["pk"]["S"].startswith("session#"):
      reads.append(kw.get("ConsistentRead"))
    return get(TableName, Key, **kw)

  d.ddb.get_item = spy
  cookie = session.new_session("alice", KEY, NOW)
  raw(h, d, "GET", "me", cookie, ctype=None)
  raw(h, d, "PATCH", "groups/g", cookie, {"name": "y"})
  assert reads == [False, True]


def test_an_unreadable_cutoff_is_503_never_401_and_keeps_the_cookie(h):
  d = group_with_members(h)
  get = d.ddb.get_item

  def broken(TableName, Key, **kw):
    if TableName == "groups" and Key["pk"]["S"].startswith("session#"):
      raise RuntimeError("DynamoDB down")
    return get(TableName, Key, **kw)

  d.ddb.get_item = broken
  cookie = session.new_session("alice", KEY, NOW)
  for method, path, body in [
    ("GET", "me", None),
    ("PATCH", "groups/g", {"name": "y"}),
    ("GET", "me/groups", None),
  ]:
    out = raw(h, d, method, path, cookie, body, ctype="application/json" if body else None)
    assert out["statusCode"] == 503 and json.loads(out["body"]) == {"error": "signin_unavailable"}
    assert not cleared(out)
  out = everywhere(h, d, cookie)
  assert out["statusCode"] == 503 and not cleared(out)
  assert raw(h, d, "GET", "groups/g", ctype=None)["statusCode"] == 200  # public reads don't look


def test_a_failed_revoke_is_503_and_keeps_the_cookie(h):
  d = group_with_members(h)
  update = d.ddb.update_item

  def broken(TableName, Key, *a, **kw):
    if TableName == "groups" and Key["pk"]["S"].startswith("session#"):
      raise RuntimeError("DynamoDB down")
    return update(TableName, Key, *a, **kw)

  d.ddb.update_item = broken
  out = everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  assert out["statusCode"] == 503 and not cleared(out) and cutoff(d) is None
  out = everywhere(h, deps(h, auth={}), session.new_session("alice", KEY, NOW - 60))
  assert out["statusCode"] == 503 and not cleared(out)


def test_revocation_items_stay_out_of_the_gsis(h):
  d = group_with_members(h)
  everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  item = d.ddb.groups[(grp.session_pk("alice"), "meta")]
  assert set(item) == {"pk", "sk", "revoked_at"}


def test_revoked_requests_are_named_in_the_metric_line(h, capsys):
  d = group_with_members(h)
  copied = session.new_session("alice", KEY, NOW - 3600)
  everywhere(h, d, session.new_session("alice", KEY, NOW - 60))
  capsys.readouterr()
  raw(h, d, "GET", "me", copied, ctype=None)
  raw(h, d, "PATCH", "groups/g", copied, {"name": "y"})
  lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
  assert [x.get("auth") or x.get("outcome") for x in lines] == ["revoked", "revoked"]


# ---- group themes (AOTY-76) -----------------------------------------------------------------


def test_the_owner_sets_the_groups_theme_and_its_chart_is_restyled(h):
  d = group_with_members(h)
  assert api_core.route("POST", f"{BASE}api/groups/g/theme", BASE) == ("group_theme", "g")
  assert api_core.route("GET", f"{BASE}api/groups/g/theme", BASE) == ("method", None)
  assert call(h, d, "POST", "groups/g/theme", {"theme": "gabbie"}) == (200, {"theme": "gabbie"})
  assert (d.ddb.meta("g")["theme"], d.ddb.meta("g")["theme_set_at"]) == ("gabbie", NOW)
  assert d.lam.events == [{"group": "g", "restyle": True}]
  status, body = call(h, d, "GET", "groups/g", user=None, ctype=None)
  assert status == 200 and body["theme"] == "gabbie"


def test_only_the_owner_sets_a_groups_theme_even_a_member_cannot(h):
  d = group_with_members(h)  # alice owns g; bob is a member
  assert call(h, d, "POST", "groups/g/theme", {"theme": "kamp"}, user="bob") == (
    403,
    {"error": "not your group"},
  )
  assert call(h, d, "POST", "groups/nope/theme", {"theme": "kamp"}) == (404, {"error": "no such group"})
  assert "theme" not in d.ddb.meta("g") and d.lam.events == []


def test_unknown_group_themes_are_refused(h):
  d = group_with_members(h)
  status, body = call(h, d, "POST", "groups/g/theme", {"theme": "</style>"})
  assert status == 400 and body["error"] == "unknown_theme"
  assert "theme" not in d.ddb.meta("g")


def test_group_get_carries_only_a_valid_non_default_theme(h):
  d = group_with_members(h)
  for stored, shown in (("kamp", "kamp"), ("poster", None), ("junk", None)):
    d.ddb.meta("g")["theme"] = stored
    _, body = call(h, d, "GET", "groups/g", user=None, ctype=None)
    assert body.get("theme") == shown, stored


def test_a_rename_carries_the_theme_to_the_new_slug(h):
  d = group_with_members(h)
  call(h, d, "POST", "groups/g/theme", {"theme": "mint-royale"})
  status, body = call(h, d, "PATCH", "groups/g", {"name": "The Crew"})
  assert status == 200 and body["id"] == "the-crew"
  assert d.ddb.meta("the-crew")["theme"] == "mint-royale"


def test_a_theme_saved_while_a_rename_runs_is_not_lost(h):
  d = group_with_members(h)
  d.ddb.meta("g").update(theme="kamp", theme_set_at=NOW - 50)

  def save_theme_meanwhile(ddb):  # lands between the rename's read and its transaction
    ddb.meta("g").update(theme="gabbie", theme_set_at=NOW)

  d.ddb.before_transact = save_theme_meanwhile
  status, body = call(h, d, "PATCH", "groups/g", {"name": "The Crew"})
  assert status == 200 and body["id"] == "the-crew"
  assert d.ddb.meta("the-crew")["theme"] == "gabbie"  # the retry read the newer theme
