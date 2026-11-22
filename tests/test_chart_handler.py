"""The chart builder handler (lambdas/chart/handler.py) against in-memory fakes of S3 and DynamoDB."""

import calendar
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

import sync_core
from common import dynamo
from common.albumkey import album_key, join_key, split_key

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "chart" / "handler.py"
NOW = calendar.timegm((2026, 11, 22, 12, 0, 0))
ENV = {
  "AOTY_ENV": "test",
  "USERS_TABLE": "users",
  "ALBUMS_TABLE": "albums",
  "STORE_BUCKET": "store",
  "SITE_BUCKET": "site",
  "SITE_PREFIX": "toys/aoty-test/",
}
CHART = ("site", "toys/aoty-test/data/users/alice/chart.json")
PENDING = ("store", "users/alice/2026/chart_pending.json")


@pytest.fixture(scope="module")
def h():
  spec = importlib.util.spec_from_file_location("chart_handler", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def key(artist, album):
  return join_key(album_key(artist, album))


class FakeS3:
  class NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}

  def __init__(self):
    self.objects, self.meta = {}, {}

  def get_object(self, Bucket, Key):
    if (Bucket, Key) not in self.objects:
      raise self.NoSuchKey()
    body = self.objects[(Bucket, Key)]

    class Body:
      def read(self):
        return body

    return {"Body": Body()}

  def put_object(self, Bucket, Key, Body, **kw):
    self.objects[(Bucket, Key)] = Body
    self.meta[(Bucket, Key)] = kw

  def put_store(self, plays, username="alice"):
    store = sync_core.new_store(2026)
    store["weeks"]["1767225600"] = {
      "to": 0,
      "fetched_at": NOW,
      "plays": {key(*a): n for a, n in plays.items()},
    }
    for a in plays:
      store["albums"][key(*a)] = {"artist": a[0], "album": a[1], "mbid": "", "image": ""}
    self.objects[("store", f"users/{username}/2026/weeks.json.gz")] = gzip.compress(
      json.dumps(store).encode()
    )

  def json(self, loc):
    return json.loads(self.objects[loc])


class FakeDDB:
  def __init__(self, unprocessed_once=False):
    self.users, self.albums = {}, {}
    self.unprocessed_once, self.batch_gets, self.projections = unprocessed_once, 0, []

  def add_album(self, artist, album, **fields):
    a, t = album_key(artist, album)
    self.albums[join_key((a, t))] = {"artist_key": a, "title_key": t, "year": 2026, "tracks": 10,
                                     "tracks_type": "Album", "rg_mbid": "rg", **fields}  # fmt: skip

  def get_item(self, TableName, Key):
    row = self.users.get(Key["username"]["S"])
    return {"Item": dynamo.to_item(row)} if row else {}

  def update_item(
    self, TableName, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues
  ):
    row = self.users.setdefault(Key["username"]["S"], {"username": Key["username"]["S"]})
    for part in UpdateExpression.removeprefix("SET ").split(", "):
      name, value = part.split(" = ")
      row[ExpressionAttributeNames[name]] = dynamo.from_item({"v": ExpressionAttributeValues[value]})["v"]

  def batch_get_item(self, RequestItems):
    self.batch_gets += 1
    ((table, req),) = RequestItems.items()
    assert table == "albums" and len(req["Keys"]) <= 100
    self.projections.append(req.get("ProjectionExpression"))
    if self.unprocessed_once:
      self.unprocessed_once = False
      return {"Responses": {table: []}, "UnprocessedKeys": RequestItems}
    hits = []
    for k in req["Keys"]:
      item = self.albums.get(join_key((k["artist_key"]["S"], k["title_key"]["S"])))
      if item:
        hits.append(dynamo.to_item(item))
    return {"Responses": {table: hits}, "UnprocessedKeys": {}}

  def scan(self, TableName, FilterExpression, ProjectionExpression, ExpressionAttributeValues, **kw):
    assert FilterExpression == "chart_pending > :zero"
    rows = [r for r in self.users.values() if r.get("chart_pending", 0) > 0]
    # Two pages, to exercise LastEvaluatedKey.
    if "ExclusiveStartKey" not in kw and len(rows) > 1:
      return {
        "Items": [dynamo.to_item(rows[0])],
        "LastEvaluatedKey": {"username": {"S": rows[0]["username"]}},
      }
    rest = rows[1:] if "ExclusiveStartKey" in kw else rows
    return {"Items": [dynamo.to_item(r) for r in rest]}


class Deps:
  def __init__(self, s3, ddb, clock=NOW):
    self.s3, self.ddb, self.env = s3, ddb, ENV
    self.clock, self.mono, self.sleep = (lambda: clock), (lambda: 0.0), (lambda s: None)


@pytest.fixture
def world():
  s3, ddb = FakeS3(), FakeDDB()
  s3.put_store({("A", "Known"): 9, ("B", "Waiting"): 4, ("C", "Old"): 20})
  ddb.add_album("A", "Known")
  ddb.add_album("C", "Old", year=1999)
  ddb.users["alice"] = {"username": "alice", "display_name": "AliCe"}
  return s3, ddb


def test_a_sync_event_writes_the_chart_with_no_cache(h, world):
  s3, ddb = world
  out = h.handler({"username": "Alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  assert out["built"] == 1 and out["failed"] == 0
  chart = s3.json(CHART)
  assert chart["display_name"] == "AliCe" and chart["username"] == "alice"
  assert [chart["albums"][i]["album"] for i, _ in chart["rankings"]["track"]] == ["Known"]
  assert chart["pending"] == 1
  assert s3.meta[CHART] == {"ContentType": "application/json", "CacheControl": "no-cache"}
  assert s3.json(PENDING) == {"keys": [key("B", "Waiting")]}
  assert ddb.users["alice"]["chart_built_at"] == NOW
  assert ddb.users["alice"]["chart_pending"] == 1


def test_a_repeat_sync_event_is_skipped(h, world):
  s3, ddb = world
  ddb.users["alice"]["chart_built_at"] = NOW - 1
  out = h.handler({"username": "alice", "requested_at": NOW - 10}, None, Deps(s3, ddb))
  assert out == {"built": 0, "skipped": 1, "failed": 0, "checked": 0}
  assert CHART not in s3.objects


def test_a_newer_request_rebuilds(h, world):
  s3, ddb = world
  ddb.users["alice"]["chart_built_at"] = NOW - 100
  assert h.handler({"username": "alice", "requested_at": NOW - 10}, None, Deps(s3, ddb))["built"] == 1


def test_no_store_means_no_chart(h):
  s3, ddb = FakeS3(), FakeDDB()
  out = h.handler({"username": "bob", "requested_at": NOW}, None, Deps(s3, ddb))
  assert out["skipped"] == 1 and s3.objects == {}


@pytest.mark.parametrize("name", ["", "../etc", "a b", None])
def test_invalid_usernames_are_dropped(h, name):
  s3 = FakeS3()
  out = h.handler({"username": name}, None, Deps(s3, FakeDDB()))
  assert out["skipped"] == 1 and s3.objects == {}


def test_pending_pass_rebuilds_only_users_with_newly_resolved_albums(h, world):
  s3, ddb = world
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb, clock=NOW - 3))
  s3.put_store({("Z", "Zed"): 3}, username="zoe")
  ddb.users["zoe"] = {"username": "zoe"}
  h.handler({"username": "zoe", "requested_at": NOW - 5}, None, Deps(s3, ddb, clock=NOW - 3))

  # Nothing resolved yet: both are checked, neither rebuilt.
  out = h.handler({"pending": True}, None, Deps(s3, ddb))
  assert out == {"built": 0, "skipped": 2, "failed": 0, "checked": 2}
  assert "artist_key, title_key" in ddb.projections

  # The mb lane answers alice's pending album: only alice is rebuilt.
  ddb.add_album("B", "Waiting", tracks=4)
  out = h.handler({"pending": True}, None, Deps(s3, ddb))
  assert out == {"built": 1, "skipped": 1, "failed": 0, "checked": 2}
  chart = s3.json(CHART)
  assert chart["pending"] == 0 and len(chart["rankings"]["track"]) == 2
  assert ddb.users["alice"]["chart_pending"] == 0

  # Settled: alice drops out of the pending scan.
  out = h.handler({"pending": True}, None, Deps(s3, ddb))
  assert out["checked"] == 1


def test_a_failing_user_doesnt_stop_the_pending_pass(h, world):
  s3, ddb = world
  ddb.users["broken"] = {"username": "broken", "chart_pending": 2}
  s3.objects[("store", "users/broken/2026/weeks.json.gz")] = b"not gzip"
  ddb.users["alice"]["chart_pending"] = 1  # no chart_pending.json yet: rebuild
  out = h.handler({"pending": True}, None, Deps(s3, ddb))
  assert out["failed"] == 1 and out["built"] == 1


def test_unprocessed_keys_are_retried(h, world):
  s3, _ = world
  ddb = FakeDDB(unprocessed_once=True)
  ddb.add_album("A", "Known")
  out = h.handler({"username": "alice"}, None, Deps(s3, ddb))
  assert out["built"] == 1 and ddb.batch_gets == 2
  assert s3.json(CHART)["rankings"]["track"]


def test_batch_gets_are_split_in_hundreds(h):
  s3, ddb = FakeS3(), FakeDDB()
  s3.put_store({("A", f"Album {i}"): i + 1 for i in range(250)})
  h.handler({"username": "alice"}, None, Deps(s3, ddb))
  assert ddb.batch_gets == 3


def test_one_emf_line_per_run(h, world, capsys):
  s3, ddb = world
  h.handler({"username": "alice"}, None, Deps(s3, ddb))
  (line,) = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
  assert line["Environment"] == "test"
  assert {m["Name"] for m in line["_aws"]["CloudWatchMetrics"][0]["Metrics"]} == {
    "ChartsBuilt", "ChartsSkipped", "ChartFailures", "ChartSeconds",
  }  # fmt: skip
  assert line["ChartsBuilt"] == 1


def test_logs_never_name_the_user(h, world, caplog):
  s3, ddb = world
  with caplog.at_level("INFO"):
    h.handler({"username": "alice"}, None, Deps(s3, ddb))
  assert caplog.records and not any("alice" in r.getMessage() for r in caplog.records)


def test_keys_round_trip_through_the_table(h, world):
  s3, ddb = world
  h.handler({"username": "alice"}, None, Deps(s3, ddb))
  assert all(split_key(k) for k in s3.json(PENDING)["keys"])
