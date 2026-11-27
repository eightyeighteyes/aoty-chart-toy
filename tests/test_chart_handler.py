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
  "GROUPS_TABLE": "groups",
  "STORE_BUCKET": "store",
  "SITE_BUCKET": "site",
  "SITE_PREFIX": "toys/aoty-test/",
  "BASE_PATH": "/toys/aoty-test/",
  "PUBLIC_ORIGIN": "https://kamp.fm",
}
CHART = ("site", "toys/aoty-test/data/users/alice/chart.json")
OG = ("site", "toys/aoty-test/data/users/alice/og.html")
OG_META = {"ContentType": "text/html; charset=utf-8", "CacheControl": "max-age=300"}
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

  def delete_object(self, Bucket, Key):
    self.objects.pop((Bucket, Key), None)

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

  def list_objects_v2(self, Bucket, Prefix):
    return {"Contents": [{"Key": k} for b, k in sorted(self.objects) if b == Bucket and k.startswith(Prefix)]}


class ConditionFailed(Exception):
  response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDDB:
  def __init__(self, unprocessed_once=False):
    self.users, self.albums = {}, {}
    self.groups = {}  # (pk, sk) -> plain item (AOTY-48)
    self.unprocessed_once, self.batch_gets, self.projections = unprocessed_once, 0, []

  def add_group(self, slug, members, name=None, **meta):
    pk = f"group#{slug}"
    self.groups[(pk, "meta")] = {"pk": pk, "sk": "meta", "name": name or slug, "owner": members[0] if members else "x",
                                 "member_count": len(members), **meta}  # fmt: skip
    for m in members:
      self.groups[(pk, f"member#{m}")] = {"pk": pk, "sk": f"member#{m}", "username": m}

  def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues, IndexName=None, **kw):
    assert TableName == "groups"
    v = dynamo.from_item(ExpressionAttributeValues)
    if IndexName == "by_member":
      assert KeyConditionExpression == "username = :u"
      rows = [{"pk": it["pk"], "sk": it["sk"], "username": it["username"]}
              for it in self.groups.values() if it.get("username") == v[":u"]]  # fmt: skip
    else:
      assert IndexName is None and KeyConditionExpression == "pk = :pk" and kw.get("ConsistentRead")
      rows = [it for (p, _), it in sorted(self.groups.items()) if p == v[":pk"]]
    return {"Items": [dynamo.to_item(r) for r in rows]}

  def add_album(self, artist, album, **fields):
    a, t = album_key(artist, album)
    self.albums[join_key((a, t))] = {"artist_key": a, "title_key": t, "year": 2026, "tracks": 10,
                                     "tracks_type": "Album", "rg_mbid": "rg", **fields}  # fmt: skip

  def get_item(self, TableName, Key):
    row = self.users.get(Key["username"]["S"])
    return {"Item": dynamo.to_item(row)} if row else {}

  def update_item(
    self, TableName, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None,
    ConditionExpression=None,
  ):  # fmt: skip
    if TableName == "groups":
      assert ConditionExpression == "attribute_exists(pk)"
      assert UpdateExpression in ("SET chart_read_at = :r", "SET chart_read_at = :r, chart_built_at = :t")
      key = (Key["pk"]["S"], Key["sk"]["S"])
      if key not in self.groups:
        raise ConditionFailed()
      for part in UpdateExpression.removeprefix("SET ").split(", "):
        field, value = part.split(" = ")
        self.groups[key][field] = int(ExpressionAttributeValues[value]["N"])
      return
    row = self.users.setdefault(Key["username"]["S"], {"username": Key["username"]["S"]})
    sets, _, removes = UpdateExpression.removeprefix("SET ").partition(" REMOVE ")
    for part in sets.split(", "):
      name, value = part.split(" = ")
      row[ExpressionAttributeNames[name]] = dynamo.from_item({"v": ExpressionAttributeValues[value]})["v"]
    for name in filter(None, removes.split(", ")):
      row.pop(ExpressionAttributeNames[name], None)

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


class FakeLastfm:
  """album.getInfo covers: `images` maps album title to URL ("" = none); `fail` raises."""

  def __init__(self, images=None, fail=(), mono=None, cost=0.5):
    self.images, self.fail, self.calls = images or {}, set(fail), []
    self.mono, self.cost = mono, cost

  def album_image(self, artist, album):
    self.calls.append(album)
    if self.mono is not None:
      self.mono.t += self.cost
    if album in self.fail:
      from common import lastfm

      raise lastfm.LastfmError(8, "operation failed")
    return self.images.get(album, "")


class Mono:
  def __init__(self):
    self.t = 0.0

  def __call__(self):
    return self.t


class Deps:
  def __init__(self, s3, ddb, clock=NOW, lastfm_client=None, mono=None):
    self.s3, self.ddb, self.env, self.lastfm = s3, ddb, ENV, lastfm_client
    self.clock, self.mono, self.sleep = (lambda: clock), (mono or (lambda: 0.0)), (lambda s: None)


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
  assert [chart["albums"][i]["album"] for i, _ in chart["rankings"]["album"]] == ["Known"]
  assert chart["pending"] == 1
  assert s3.meta[CHART] == {"ContentType": "application/json", "CacheControl": "no-cache"}
  assert s3.json(PENDING) == {"keys": [key("B", "Waiting")]}
  assert ddb.users["alice"]["chart_built_at"] == NOW
  assert ddb.users["alice"]["chart_pending"] == 1


# ---- link previews (AOTY-85) ----------------------------------------------------------------


def test_a_build_writes_the_link_preview_page_beside_the_chart(h, world):
  s3, ddb = world
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  page = s3.objects[OG].decode()
  assert s3.meta[OG] == OG_META
  assert '<meta property="og:title" content="AliCe&#x27;s albums of the year 2026">' in page
  assert '<meta property="og:description" content="#1 so far: Known by A">' in page
  assert '<meta property="og:url" content="https://kamp.fm/toys/aoty-test/user/AliCe">' in page


def test_an_empty_first_chart_still_gets_a_preview_page(h):
  # Written on every build, so a known user's link never meets a 403.
  s3, ddb = FakeS3(), FakeDDB()
  s3.put_store({("B", "Waiting"): 4})
  ddb.users["alice"] = {"username": "alice"}
  h.handler({"username": "alice", "requested_at": NOW}, None, Deps(s3, ddb))
  assert s3.json(CHART)["rankings"]["album"] == []
  assert "the 2026 albums alice actually lived with" in s3.objects[OG].decode()


def test_a_failed_preview_write_never_fails_the_build(h, world, capsys):
  s3, ddb = world
  put = s3.put_object

  def flaky(Bucket, Key, Body, **kw):
    if Key.endswith("og.html"):
      raise RuntimeError("S3 hiccup")
    put(Bucket, Key, Body, **kw)

  s3.put_object = flaky
  out = h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  assert out["built"] == 1 and out["failed"] == 0 and out["og_failed"] == 1
  assert CHART in s3.objects and OG not in s3.objects
  assert ddb.users["alice"]["chart_built_at"] == NOW
  line = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")][-1]
  assert line["og_failed"] == 1


# ---- page themes (AOTY-65) ------------------------------------------------------------------


def test_a_build_carries_the_owners_theme(h, world):
  s3, ddb = world
  ddb.users["alice"]["theme"] = "beach-house"
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  assert s3.json(CHART)["theme"] == "beach-house"


def test_restyle_copies_the_rows_theme_into_the_published_chart_without_a_build(h, world):
  s3, ddb = world
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  built = s3.json(CHART)
  assert "theme" not in built
  ddb.users["alice"]["theme"] = "golden-smog"
  # The event's own fields never matter: only the row's theme is copied.
  out = h.handler(
    {"username": "alice", "restyle": True, "theme": "kamp"}, None, Deps(s3, ddb, clock=NOW + 60)
  )
  assert out == {"built": 1, "skipped": 0, "failed": 0, "checked": 0}
  chart = s3.json(CHART)
  assert chart == {**built, "theme": "golden-smog"}  # nothing else changed: no rebuild
  assert s3.meta[CHART]["CacheControl"] == "no-cache"
  assert ddb.users["alice"]["chart_built_at"] == NOW  # not a build


def test_restyle_back_to_the_default_removes_the_theme(h, world):
  s3, ddb = world
  ddb.users["alice"]["theme"] = "kamp"
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  ddb.users["alice"]["theme"] = "poster"
  h.handler({"username": "alice", "restyle": True}, None, Deps(s3, ddb))
  assert "theme" not in s3.json(CHART)


def test_a_repeated_or_late_restyle_changes_nothing(h, world):
  s3, ddb = world
  ddb.users["alice"]["theme"] = "kamp"
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  out = h.handler({"username": "alice", "restyle": True}, None, Deps(s3, ddb))
  assert out["built"] == 0 and out["skipped"] == 1 and out["failed"] == 0


def test_restyle_with_no_chart_yet_is_a_no_op(h, world):
  s3, ddb = world
  ddb.users["alice"]["theme"] = "kamp"  # signed in and picked, but no chart built yet
  out = h.handler({"username": "alice", "restyle": True}, None, Deps(s3, ddb))
  assert out["skipped"] == 1 and out["failed"] == 0 and CHART not in s3.objects


def test_an_unknown_theme_on_the_row_is_never_published(h, world):
  s3, ddb = world
  ddb.users["alice"]["theme"] = "</style><script>"
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  assert "theme" not in s3.json(CHART)


def test_restyle_never_builds_the_users_groups(h):
  s3, ddb = group_world()
  h.handler({"username": "alice", "requested_at": NOW - 5}, None, Deps(s3, ddb))
  group_before = s3.objects.get(GROUP_CHART)
  ddb.users["alice"]["theme"] = "kamp"
  h.handler({"username": "alice", "restyle": True}, None, Deps(s3, ddb, clock=NOW + 60))
  assert s3.objects.get(GROUP_CHART) == group_before


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


@pytest.mark.parametrize("name", ["", "../etc", "a b", "-lead", ".lead", None])
def test_invalid_usernames_are_dropped(h, name):
  s3 = FakeS3()
  out = h.handler({"username": name}, None, Deps(s3, FakeDDB()))
  assert out["skipped"] == 1 and s3.objects == {}


def test_a_name_starting_with_an_underscore_gets_a_chart(h, world):
  # AOTY-80: older Last.fm accounts such as "_korps_" start with "_".
  s3, ddb = world
  s3.put_store({("Z", "Zed"): 3}, username="_korps_")
  ddb.users["_korps_"] = {"username": "_korps_"}
  out = h.handler({"username": "_korps_", "requested_at": NOW}, None, Deps(s3, ddb))
  assert out["built"] == 1


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
  assert chart["pending"] == 0 and len(chart["rankings"]["album"]) == 2
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
  assert s3.json(CHART)["rankings"]["album"]


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
    "ChartsBuilt", "ChartsSkipped", "ChartFailures", "ChartSeconds", "CoverLookups", "LastfmCalls",
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


# ---- album.getInfo covers ---------------------------------------------------------------

COVERS = ("store", "users/alice/2026/chart_covers.json")
LFM = "https://lastfm-img.freetls.fastly.net/i/u/300x300/{}.jpg"


def art_of(s3, album):
  return next(a for a in s3.json(CHART)["albums"] if a["album"] == album)


def test_shown_albums_without_a_scrobble_cover_get_album_getinfos(h, world):
  s3, ddb = world
  ddb.add_album("B", "Waiting")
  fm = FakeLastfm({"Known": LFM.format("k")})
  out = h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=fm))
  assert sorted(fm.calls) == ["Known", "Waiting"]  # both shown, neither had a scrobble image
  assert out["covers"] == 2
  known = art_of(s3, "Known")
  assert known["art"] == "https://lastfm.freetls.fastly.net/i/u/300x300/k.jpg"  # CSP-allowed host
  assert known["fallbacks"][-1].startswith("https://coverartarchive.org/")  # CAA stays last
  assert art_of(s3, "Waiting")["art"].startswith("https://coverartarchive.org/")  # no Last.fm cover
  cache = s3.json(COVERS)["covers"]
  assert {c["url"] for c in cache.values()} == {LFM.format("k"), ""}


def test_cached_answers_arent_looked_up_again(h, world):
  s3, ddb = world
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=FakeLastfm({"Known": LFM.format("k")})))
  fm = FakeLastfm()
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=fm))
  assert fm.calls == []
  assert art_of(s3, "Known")["art"].endswith("/k.jpg")


def test_no_cover_is_retried_after_a_month(h, world):
  s3, ddb = world
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=FakeLastfm()))
  fm = FakeLastfm({"Known": LFM.format("later")})
  h.handler({"username": "alice"}, None, Deps(s3, ddb, clock=NOW + 86400, lastfm_client=fm))
  assert fm.calls == []
  h.handler({"username": "alice"}, None, Deps(s3, ddb, clock=NOW + 31 * 86400, lastfm_client=fm))
  assert fm.calls == ["Known"] and art_of(s3, "Known")["art"].endswith("/later.jpg")


def test_a_failed_lookup_isnt_cached_and_doesnt_fail_the_build(h, world):
  s3, ddb = world
  out = h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=FakeLastfm(fail={"Known"})))
  assert out["built"] == 1 and out["failed"] == 0
  assert COVERS not in s3.objects
  fm = FakeLastfm({"Known": LFM.format("k")})
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=fm))
  assert fm.calls == ["Known"]


def test_scrobble_covers_need_no_lookup(h, world):
  s3, ddb = world
  store = json.loads(gzip.decompress(s3.objects[("store", "users/alice/2026/weeks.json.gz")]))
  store["albums"][key("A", "Known")]["image"] = LFM.format("scrobbled")
  s3.objects[("store", "users/alice/2026/weeks.json.gz")] = gzip.compress(json.dumps(store).encode())
  fm = FakeLastfm()
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=fm))
  assert fm.calls == []


def test_lookups_stop_at_the_time_budget_and_continue_next_build(h):
  s3, ddb = FakeS3(), FakeDDB()
  s3.put_store({("A", f"Album {i:03d}"): 300 - i for i in range(200)})
  for i in range(200):
    ddb.add_album("A", f"Album {i:03d}")
  mono = Mono()
  fm = FakeLastfm(mono=mono, cost=1.0)
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=fm, mono=mono))
  first = len(fm.calls)
  assert first == h.COVER_BUDGET_S  # one lookup per second of budget
  assert fm.calls[0] == "Album 000"  # chart order: the top first
  h.handler({"username": "alice"}, None, Deps(s3, ddb, lastfm_client=fm, mono=mono))
  assert len(fm.calls) > first and fm.calls[first] == f"Album {first:03d}"


# ---- status fields (AOTY-22) ------------------------------------------------------------


def test_the_users_row_gets_the_status_fields(h, world):
  s3, ddb = world
  h.handler({"username": "alice"}, None, Deps(s3, ddb))
  row = ddb.users["alice"]
  # The store has Known (9, 2026), Waiting (4, no item yet) and Old (20, 1999).
  assert (row["chart_plays"], row["chart_albums"], row["chart_hits"], row["chart_pending"]) == (33, 3, 1, 1)


def test_the_first_visit_wall_is_removed_once_the_chart_ranks_albums(h, world):
  s3, ddb = world
  ddb.users["alice"]["preview"] = '{"wall":[]}'
  ddb.albums.pop(key("A", "Known"))  # nothing from 2026 known yet: the chart ranks nothing
  h.handler({"username": "alice"}, None, Deps(s3, ddb))
  assert ddb.users["alice"]["preview"] == '{"wall":[]}'
  ddb.add_album("A", "Known")
  h.handler({"username": "alice", "requested_at": NOW + 1}, None, Deps(s3, ddb, clock=NOW + 2))
  assert "preview" not in ddb.users["alice"]


def test_chart_plays_is_the_same_sum_sync_reports(h, world):
  s3, ddb = world
  h.handler({"username": "alice"}, None, Deps(s3, ddb))
  store = json.loads(gzip.decompress(s3.objects[("store", "users/alice/2026/weeks.json.gz")]))
  assert ddb.users["alice"]["chart_plays"] == sum(sync_core.year_plays(store).values())


# ---- groups (AOTY-50; in the groups table since AOTY-48) ---------------------------------

GROUP = "the-server"  # a slug, as common/groups.py makes
GROUP_CHART = ("site", f"toys/aoty-test/data/groups/{GROUP}/chart.json")
GROUP_OG = ("site", f"toys/aoty-test/data/groups/{GROUP}/og.html")


def group_world(**meta):
  s3, ddb = FakeS3(), FakeDDB()
  ddb.add_group(GROUP, ["alice", "bob", "carol", "dave", "eve"], name="the server", **meta)
  s3.put_store({("A", "Shared"): 5, ("B", "Mine"): 50}, username="alice")
  s3.put_store({("A", "Shared"): 1}, username="bob")
  s3.put_store({("A", "Shared"): 2}, username="carol")
  ddb.add_album("A", "Shared")
  ddb.add_album("B", "Mine")
  for u in ("alice", "bob", "carol"):
    ddb.users[u] = {"username": u, "state": "done", "sync_finished_at": NOW - 60, "chart_built_at": NOW - 30}
  ddb.users["dave"] = {"username": "dave", "state": "syncing"}  # a first sync: waiting
  ddb.users["eve"] = {"username": "eve", "state": "private"}
  return s3, ddb


def test_a_group_event_writes_the_group_chart_with_m0s_rules(h):
  s3, ddb = group_world()
  out = h.handler({"group": GROUP}, None, Deps(s3, ddb))
  assert out["built"] == 1 and out["failed"] == 0
  chart = s3.json(GROUP_CHART)
  assert s3.meta[GROUP_CHART]["CacheControl"] == "no-cache"
  assert chart["kind"] == "group" and chart["name"] == "the server"
  assert chart["members"] == ["alice", "bob", "carol"]
  assert (chart["waiting"], chart["unavailable"]) == (1, 1)
  names = [chart["albums"][i]["album"] for i, _ in chart["rankings"]["listener"]]
  assert names == ["Shared", "Mine"]  # 3 listeners beat 50 plays from one
  shared = chart["albums"][chart["rankings"]["listener"][0][0]]
  assert shared["listeners"] == 3 and shared["by"] == {"0": 5, "1": 1, "2": 2}
  assert [chart["albums"][i]["album"] for i, _ in chart["rankings"]["weighted"]] == ["Shared"]  # 3+ only
  # By Play: album plays first, so Mine's 50 plays from one listener lead (AOTY-63).
  assert [chart["albums"][i]["album"] for i, _ in chart["rankings"]["album"]] == ["Mine", "Shared"]
  assert set(chart["rankings"]) == {"listener", "album", "weighted"}
  # Its link preview page (AOTY-85) names the group and its By Listener #1.
  page = s3.objects[GROUP_OG].decode()
  assert s3.meta[GROUP_OG] == OG_META
  assert '<meta property="og:title" content="the server · albums of the year 2026">' in page
  assert '<meta property="og:description" content="#1 so far: Shared by A">' in page
  assert f'<meta property="og:url" content="https://kamp.fm/toys/aoty-test/group/{GROUP}">' in page


def test_a_members_sync_rebuilds_their_groups(h):
  s3, ddb = group_world()
  h.handler({"username": "bob", "requested_at": NOW}, None, Deps(s3, ddb))
  assert GROUP_CHART in s3.objects


def test_an_unknown_or_malformed_group_is_skipped(h):
  s3, ddb = group_world()
  assert h.handler({"group": "zzzzzzzzzzzzzzzzzzzzzzzzzz"}, None, Deps(s3, ddb))["skipped"] == 1
  assert h.handler({"group": "../users"}, None, Deps(s3, ddb))["skipped"] == 1
  assert GROUP_CHART not in s3.objects


def test_the_pending_pass_rebuilds_each_group_of_a_rebuilt_member_once(h):
  s3, ddb = group_world()
  s3.put_store({("A", "Shared"): 5, ("C", "Later"): 9}, username="alice")
  s3.put_store({("A", "Shared"): 1, ("C", "Later"): 2}, username="bob")
  for u in ("alice", "bob"):
    h.handler({"username": u}, None, Deps(s3, ddb))  # pending: "Later" isn't dated yet
  del s3.objects[GROUP_CHART]
  puts = []
  real_put = s3.put_object
  s3.put_object = lambda **kw: (puts.append(kw["Key"]), real_put(**kw))[1]

  ddb.add_album("C", "Later")  # the mb lane dates it
  h.handler({"pending": True}, None, Deps(s3, ddb))
  assert puts.count(GROUP_CHART[1]) == 1  # both members rebuilt, the group once
  chart = s3.json(GROUP_CHART)
  assert "Later" in [chart["albums"][i]["album"] for i, _ in chart["rankings"]["listener"]]


def test_a_group_build_is_skipped_once_its_chart_covers_the_request(h):
  s3, ddb = group_world(chart_requested_at=NOW - 10, chart_built_at=NOW - 10)
  out = h.handler({"group": GROUP, "requested_at": NOW - 10}, None, Deps(s3, ddb))
  assert out["skipped"] == 1 and GROUP_CHART not in s3.objects
  out = h.handler({"group": GROUP, "requested_at": NOW}, None, Deps(s3, ddb))
  assert out["built"] == 1 and GROUP_CHART in s3.objects


def test_a_group_build_stamps_the_request_it_read_not_its_end(h):
  s3, ddb = group_world(chart_requested_at=NOW - 30)
  meta = ddb.groups[(f"group#{GROUP}", "meta")]
  real_put = s3.put_object

  def change_mid_build(**kw):  # a member change lands while the build runs
    meta["chart_requested_at"] = NOW - 5
    return real_put(**kw)

  s3.put_object = change_mid_build
  h.handler({"group": GROUP, "requested_at": NOW - 30}, None, Deps(s3, ddb))
  assert meta["chart_built_at"] == NOW - 30
  s3.put_object = real_put
  assert h.handler({"group": GROUP, "requested_at": NOW - 5}, None, Deps(s3, ddb))["built"] == 1


def group_puts(s3, ddb):
  """Record put keys; and make every member's chart older than their sync, so it builds."""
  for row in ddb.users.values():
    row["chart_built_at"] = NOW - 120
  puts, real_put = [], s3.put_object
  s3.put_object = lambda **kw: (puts.append(kw["Key"]), real_put(**kw))[1]
  return puts


def test_a_burst_of_member_syncs_builds_the_group_once_it_has_read_them(h):
  """AOTY-55: a visit syncs every member; once a group build read their finished syncs, each
  member's own build after it doesn't rebuild the group again."""
  s3, ddb = group_world()
  puts = group_puts(s3, ddb)
  for u in ("alice", "bob", "carol"):  # every sync finished at NOW - 60; builds run at NOW
    h.handler({"username": u, "requested_at": NOW - 60}, None, Deps(s3, ddb))
  assert puts.count(GROUP_CHART[1]) == 1
  assert ddb.groups[(f"group#{GROUP}", "meta")]["chart_read_at"] == NOW * 1000


def test_a_group_read_in_the_same_second_as_a_sync_finished_rebuilds(h):
  s3, ddb = group_world(chart_read_at=(NOW - 60) * 1000 + 999)  # read in bob's finishing second
  puts = group_puts(s3, ddb)
  h.handler({"username": "bob", "requested_at": NOW - 60}, None, Deps(s3, ddb))
  assert puts.count(GROUP_CHART[1]) == 1
  s3, ddb = group_world(chart_read_at=(NOW - 59) * 1000)  # the next second: already has it
  puts = group_puts(s3, ddb)
  h.handler({"username": "bob", "requested_at": NOW - 60}, None, Deps(s3, ddb))
  assert GROUP_CHART[1] not in puts


def test_api_and_pending_group_builds_ignore_the_read_stamp(h):
  s3, ddb = group_world(chart_read_at=NOW * 1000, chart_requested_at=NOW, chart_built_at=NOW - 10)
  assert h.handler({"group": GROUP, "requested_at": NOW}, None, Deps(s3, ddb))["built"] == 1
  assert h.handler({"group": GROUP}, None, Deps(s3, ddb))["built"] == 1


def test_a_removed_member_drops_out_of_the_group_chart(h):
  s3, ddb = group_world()
  del ddb.groups[(f"group#{GROUP}", "member#alice")]
  h.handler({"group": GROUP}, None, Deps(s3, ddb))
  chart = s3.json(GROUP_CHART)
  assert chart["members"] == ["bob", "carol"]
  assert [chart["albums"][i]["album"] for i, _ in chart["rankings"]["listener"]] == ["Shared"]


def test_a_deleted_groups_chart_is_removed_unless_the_slug_is_back(h):
  s3, ddb = group_world()
  h.handler({"group": GROUP}, None, Deps(s3, ddb))
  ddb.groups.clear()
  assert GROUP_OG in s3.objects
  assert h.handler({"group": GROUP, "deleted": True}, None, Deps(s3, ddb))["built"] == 1
  assert GROUP_CHART not in s3.objects and GROUP_OG not in s3.objects  # the preview goes too (AOTY-85)
  ddb.add_group(GROUP, ["bob"], name="again")  # re-created before the delete event ran
  h.handler({"group": GROUP, "deleted": True}, None, Deps(s3, ddb))
  assert s3.json(GROUP_CHART)["name"] == "again"
  assert "again · albums of the year 2026" in s3.objects[GROUP_OG].decode()


def test_a_build_for_a_group_deleted_mid_build_doesnt_recreate_its_meta(h):
  s3, ddb = group_world(chart_requested_at=NOW)
  real_put = s3.put_object

  def delete_mid_build(**kw):
    ddb.groups.clear()
    return real_put(**kw)

  s3.put_object = delete_mid_build
  assert h.handler({"group": GROUP}, None, Deps(s3, ddb))["failed"] == 0
  assert ddb.groups == {}


# ---- group themes (AOTY-76) -----------------------------------------------------------------


def group_meta(ddb):
  return ddb.groups[(f"group#{GROUP}", "meta")]


def test_a_group_build_carries_the_owners_theme(h):
  s3, ddb = group_world(theme="lavender-diamond")
  h.handler({"group": GROUP}, None, Deps(s3, ddb))
  assert s3.json(GROUP_CHART)["theme"] == "lavender-diamond"


def test_a_group_restyle_copies_the_metas_theme_without_a_build(h):
  s3, ddb = group_world()
  h.handler({"group": GROUP}, None, Deps(s3, ddb))
  built = s3.json(GROUP_CHART)
  read_at = group_meta(ddb).get("chart_read_at")
  group_meta(ddb)["theme"] = "gabbie"
  out = h.handler({"group": GROUP, "restyle": True, "theme": "kamp"}, None, Deps(s3, ddb, clock=NOW + 60))
  assert out == {"built": 1, "skipped": 0, "failed": 0, "checked": 0}
  assert s3.json(GROUP_CHART) == {
    **built,
    "theme": "gabbie",
  }  # the meta's, never the event's; nothing rebuilt
  assert group_meta(ddb).get("chart_read_at") == read_at  # no build read the stores
  group_meta(ddb)["theme"] = "poster"
  h.handler({"group": GROUP, "restyle": True}, None, Deps(s3, ddb))
  assert "theme" not in s3.json(GROUP_CHART)


def test_a_group_restyle_with_nothing_to_restyle_is_a_no_op(h):
  s3, ddb = group_world(theme="kamp")  # never built: no chart_built_at
  out = h.handler({"group": GROUP, "restyle": True}, None, Deps(s3, ddb))
  assert out["skipped"] == 1 and out["failed"] == 0 and GROUP_CHART not in s3.objects
  out = h.handler({"group": "gone", "restyle": True}, None, Deps(s3, ddb))  # deleted, or moved by a rename
  assert out["skipped"] == 1 and out["failed"] == 0


def test_a_group_restyle_reads_access_denied_as_no_chart(h):
  # The chart role has no s3:ListBucket on the site bucket, so a missing chart answers 403.
  s3, ddb = group_world(theme="kamp", chart_built_at=NOW - 60)

  class Denied(Exception):
    response = {"Error": {"Code": "AccessDenied"}}

  real = s3.get_object

  def get_object(Bucket, Key):
    if (Bucket, Key) == GROUP_CHART:
      raise Denied()
    return real(Bucket=Bucket, Key=Key)

  s3.get_object = get_object
  out = h.handler({"group": GROUP, "restyle": True}, None, Deps(s3, ddb))
  assert out["skipped"] == 1 and out["failed"] == 0
