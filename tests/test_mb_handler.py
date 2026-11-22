"""The mb lane handler (lambdas/mb/handler.py) against in-memory fakes of SQS, DynamoDB and MusicBrainz."""

import gzip
import importlib.util
import io
import json
from pathlib import Path

import pytest

from common import releaseindex as ri
from common.albumkey import norm

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "mb" / "handler.py"
NOW = 1_795_000_000
ENV = {"ALBUMS_TABLE": "albums", "MB_HOT_URL": "q/hot", "MB_COLD_URL": "q/cold", "MB_FUNCTION": "mb",
       "AOTY_ENV": "test"}  # fmt: skip


@pytest.fixture(scope="module")
def h():
  spec = importlib.util.spec_from_file_location("mb_handler", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


# ---- fakes ------------------------------------------------------------------------------


class Clock:
  def __init__(self):
    self.t = 0.0

  def __call__(self):
    return self.t


class FakeSQS:
  """Standard queues: a received message is in flight until deleted or released."""

  def __init__(self, page_cap=None):
    self.queues = {"q/hot": [], "q/cold": []}  # [receipt, body, in_flight]
    self.received_from, self.waits, self.n = [], [], 0
    self.page_cap = page_cap  # real SQS often returns fewer than asked

  def send(self, url, body):
    self.n += 1
    self.queues[url].append([f"r{self.n}", json.dumps(body) if isinstance(body, dict) else body, False])

  def receive_message(self, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
    self.waits.append(WaitTimeSeconds)
    out = []
    for m in self.queues[QueueUrl]:
      if not m[2] and len(out) < min(MaxNumberOfMessages, self.page_cap or MaxNumberOfMessages):
        m[2] = True
        out.append({"ReceiptHandle": m[0], "Body": m[1]})
    self.received_from += [QueueUrl] * len(out)
    return {"Messages": out} if out else {}

  def delete_message_batch(self, QueueUrl, Entries):
    gone = {e["ReceiptHandle"] for e in Entries}
    self.queues[QueueUrl] = [m for m in self.queues[QueueUrl] if m[0] not in gone]
    return {"Successful": [{"Id": e["Id"]} for e in Entries]}

  def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
    for m in self.queues[QueueUrl]:
      if m[0] == ReceiptHandle:
        m[2] = False

  def get_queue_attributes(self, QueueUrl, AttributeNames):
    visible = sum(1 for m in self.queues[QueueUrl] if not m[2])
    return {"Attributes": {"ApproximateNumberOfMessages": str(visible)}}

  def left(self, url):
    return len(self.queues[url])


class FakeDDB:
  def __init__(self, items=None, unprocessed_once=False):
    self.items = dict(items or {})
    self.unprocessed_once = unprocessed_once

  def batch_get_item(self, RequestItems):
    req = RequestItems["albums"]
    self.gets = getattr(self, "gets", []) + [req]
    found = []
    for k in req["Keys"]:
      item = self.items.get((k["artist_key"]["S"], k["title_key"]["S"]))
      if item is not None:
        found.append(k if "ProjectionExpression" in req else {**item, **k})
    return {"Responses": {"albums": found}}

  def batch_write_item(self, RequestItems):
    reqs = RequestItems["albums"]
    if self.unprocessed_once:
      self.unprocessed_once = False
      return {"UnprocessedItems": {"albums": reqs}}
    for r in reqs:
      item = r["PutRequest"]["Item"]
      self.items[(item["artist_key"]["S"], item["title_key"]["S"])] = item
    return {}


class FakeLambda:
  def __init__(self):
    self.invokes = []

  def invoke(self, **kw):
    self.invokes.append(kw)


class FakeMB:
  """Every album is a 1999 album unless named in `years` (None = MusicBrainz fails)."""

  def __init__(self, clock, years=None, cost=1.0):
    self.clock, self.years, self.cost = clock, years or {}, cost
    self.calls, self.deadlines = [], []

  def search(self, entity, query, limit, deadline=None):
    self.calls.append((entity, query))
    self.deadlines.append(deadline)
    self.clock.t += self.cost
    if deadline is not None and self.clock.t > deadline:
      return None
    results = []
    for title in (
      [p.split('"')[0] for p in query.split('releasegroup:"')[1:]] if entity == "release-group" else []
    ):
      year = self.years.get(title, "1999")
      if year is None:
        return None
      results.append({"id": f"rg-{title}", "title": title, "first-release-date": year,
                      "primary-type": "Album", "artist-credit": [{"name": "Artist"}]})  # fmt: skip
    if entity == "release":
      for title in [p.split('"')[0] for p in query.split('release:"')[1:]]:
        results.append({"title": title, "status": "Official", "date": "2026", "track-count": 10,
                        "release-group": {"primary-type": "Album"}, "artist-credit": [{"name": "Artist"}]})  # fmt: skip
    return results

  def summary(self):
    return {"requests": len(self.calls), "throttled": 0, "errors": 0, "retry_after_s": 0.0,
            "slept_s": 0.0, "server_s": 0.0, "deadline_stops": 0}  # fmt: skip


class FakeLastfm:
  def album_track_count(self, artist, album):
    return None


class Context:
  def __init__(self, clock, budget_s):
    self.clock, self.end = clock, clock.t + budget_s

  def get_remaining_time_in_millis(self):
    return int((self.end - self.clock.t) * 1000)


def msg(title, artist="Artist"):
  return {
    "artist_key": artist.lower(),
    "title_key": title.lower(),
    "artist": artist,
    "album": title,
    "mbid": "",
  }


class S3Error(Exception):
  def __init__(self, code):
    self.response = {"Error": {"Code": code}}


class FakeS3:
  """A store bucket holding a release index built from `records` (dump release-group dicts)."""

  def __init__(self, records=(), manifest=None, dump="20261121-001001"):
    self.objects, self.gets = {}, []
    shards = {}
    for r in records:
      key = norm(r["title"])
      shards.setdefault(ri.shard_of(key), {}).setdefault(key, []).append(ri.record(r))
    for shard, body in shards.items():
      self.objects[ri.shard_key(dump, shard)] = gzip.compress(json.dumps(body).encode())
    if manifest is None:
      manifest = ri.manifest(dump, built_at=NOW, records=len(records), titles=len(shards), shards=4096)
    if manifest is not False:
      self.objects[ri.MANIFEST_KEY] = json.dumps(manifest).encode()

  def get_object(self, Bucket, Key):
    self.gets.append(Key)
    if Key not in self.objects:
      if Key.endswith(".json.gz"):
        return {"Body": io.BytesIO(gzip.compress(b"{}"))}  # the builder writes every shard
      raise S3Error("NoSuchKey")
    return {"Body": io.BytesIO(self.objects[Key])}


def dump_rg(title, date, type_="Album", artist="Artist"):
  return {"id": f"rg-{title}-{date[:4]}", "title": title, "first-release-date": date, "primary-type": type_,
          "artist-credit": [{"name": artist, "joinphrase": ""}]}  # fmt: skip


def world(h, *, ddb=None, years=None, cost=1.0, page_cap=None, s3=None):
  clock = Clock()
  sqs = FakeSQS(page_cap)
  index = h.Index(s3, "store", clock=lambda: NOW) if s3 is not None else None
  deps = h.Deps(sqs=sqs, ddb=ddb or FakeDDB(), lambda_=FakeLambda(), mb=FakeMB(clock, years, cost),
                lastfm_client=FakeLastfm(), env=ENV, index=index, clock=lambda: NOW, mono=clock,
                sleep=lambda s: None)  # fmt: skip
  return deps, sqs, clock


def emf(capsys):
  return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")][-1]


# ---- tests ------------------------------------------------------------------------------


def test_empty_queues_make_no_musicbrainz_request_and_long_poll_both(h, capsys):
  deps, sqs, clock = world(h)
  out = h.handler({}, Context(clock, 900), deps)
  assert out["messages"] == 0 and deps.mb.calls == []
  assert sqs.waits == [h.WAIT_S, h.WAIT_S]  # hot, then cold, each a long poll
  line = emf(capsys)
  assert line["MbRequests"] == 0 and line["Environment"] == "test"
  assert {m["Name"] for m in line["_aws"]["CloudWatchMetrics"][0]["Metrics"]} >= {
    "MbRequests", "MbThrottled", "MbRetryAfterSeconds", "AlbumsPerSecond", "HotQueueDepth", "ColdQueueDepth"
  }  # fmt: skip


def test_resolves_writes_items_and_deletes_messages(h):
  deps, sqs, clock = world(h, years={"New": "2026-02-02"})
  sqs.send("q/cold", msg("Old"))
  sqs.send("q/cold", msg("New"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["resolved"] == 2 and sqs.left("q/cold") == 0
  new = deps.ddb.items[("artist", "new")]
  assert new["year"] == {"N": "2026"} and new["tracks"] == {"N": "10"} and new["rg_mbid"] == {"S": "rg-New"}
  old = deps.ddb.items[("artist", "old")]
  assert old["year"] == {"N": "1999"} and "tracks" not in old


def test_short_receive_pages_still_fill_a_batch_of_twenty(h):
  # Real SQS returned ~4 messages per receive in sandbox; 20 albums must still share one search.
  deps, sqs, clock = world(h, page_cap=3)
  for i in range(40):
    sqs.send("q/cold", msg(f"A{i:02}"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["batches"] == 2
  assert [c[0] for c in deps.mb.calls] == ["release-group", "release-group"]


def test_hot_is_drained_before_cold(h):
  deps, sqs, clock = world(h)
  for i in range(25):
    sqs.send("q/cold", msg(f"C{i}"))
  for i in range(25):
    sqs.send("q/hot", msg(f"H{i}"))
  h.handler({}, Context(clock, 900), deps)
  assert sqs.received_from[:25] == ["q/hot"] * 25
  assert sqs.left("q/hot") == sqs.left("q/cold") == 0


def test_already_resolved_albums_are_deleted_without_a_lookup(h):
  ddb = FakeDDB({("artist", "done"): {"year": {"NULL": True}}})
  deps, sqs, clock = world(h, ddb=ddb)
  sqs.send("q/hot", msg("Done"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["already_resolved"] == 1 and deps.mb.calls == [] and sqs.left("q/hot") == 0


def test_the_same_album_on_both_queues_is_looked_up_once_and_every_message_deleted(h):
  deps, sqs, clock = world(h)
  sqs.send("q/hot", msg("Twice"))
  sqs.send("q/hot", msg("Twice"))
  sqs.send("q/cold", msg("Twice"))
  h.handler({}, Context(clock, 900), deps)
  assert len(deps.mb.calls) == 1
  # The cold copy arrives in a later batch and is deleted as already resolved.
  assert sqs.left("q/hot") == sqs.left("q/cold") == 0


def test_failed_lookups_are_not_written_and_stay_queued(h):
  deps, sqs, clock = world(h, years={"Flaky": None})
  sqs.send("q/hot", msg("Flaky"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["failed"] == 1
  assert ("artist", "flaky") not in deps.ddb.items
  assert sqs.left("q/hot") == 1  # in flight until the visibility timeout, then retried


def test_invalid_messages_are_dropped(h):
  deps, sqs, clock = world(h)
  sqs.send("q/hot", "not json")
  sqs.send("q/hot", {"artist_key": "", "title_key": "x", "artist": "A", "album": "X"})
  out = h.handler({}, Context(clock, 900), deps)
  assert out["invalid"] == 2 and sqs.left("q/hot") == 0 and deps.mb.calls == []


def test_unprocessed_writes_are_retried(h):
  deps, sqs, clock = world(h, ddb=FakeDDB(unprocessed_once=True))
  sqs.send("q/hot", msg("X"))
  h.handler({}, Context(clock, 900), deps)
  assert ("artist", "x") in deps.ddb.items and sqs.left("q/hot") == 0


def test_every_search_gets_the_runs_deadline(h):
  deps, sqs, clock = world(h)
  sqs.send("q/hot", msg("X"))
  h.handler({}, Context(clock, 300), deps)
  assert deps.mb.deadlines == [300 - h.DEADLINE_MARGIN_S]


def test_out_of_time_with_work_left_releases_failures_and_invokes_itself(h):
  # Deadline 80 s (100 - margin); each search costs 45 s. Batch 1 finishes at 45 s. Batch 2's
  # search ends past the deadline, so its 20 albums fail; batch 3 is never received.
  deps, sqs, clock = world(h, cost=45.0)
  for i in range(60):
    sqs.send("q/cold", msg(f"A{i:02}"))
  out = h.handler({}, Context(clock, 100), deps)
  assert out["resolved"] == 20 and out["failed"] == 20
  assert out["out_of_time"] and out["self_invoked"]
  assert deps.lambda_.invokes == [{"FunctionName": "mb", "InvocationType": "Event", "Payload": b"{}"}]
  # The failed batch was released (visible again now), not left to a 15-minute timeout.
  assert sqs.get_queue_attributes(QueueUrl="q/cold", AttributeNames=[])["Attributes"] == {
    "ApproximateNumberOfMessages": "40"
  }


def test_out_of_time_with_nothing_left_does_not_invoke_itself(h):
  deps, sqs, clock = world(h)
  out = h.handler({}, Context(clock, 10), deps)
  assert out["out_of_time"] and not out["self_invoked"] and deps.lambda_.invokes == []
  assert sqs.waits == []  # no time to start: nothing received


def test_index_hits_skip_the_year_search(h, capsys):
  s3 = FakeS3([dump_rg("Old", "1994-05-10"), dump_rg("New", "2026-02-02")])
  deps, sqs, clock = world(h, s3=s3)
  sqs.send("q/cold", msg("Old"))
  sqs.send("q/cold", msg("New"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["index_hits"] == 2 and out["resolved"] == 2
  assert [c[0] for c in deps.mb.calls] == ["release"]  # only New's track count
  assert deps.ddb.items[("artist", "old")]["rg_mbid"] == {"S": "rg-Old-1994"}
  line = emf(capsys)
  assert line["IndexHits"] == 2 and line["IndexErrors"] == 0 and line["IndexAgeHours"] is not None


def test_untrusted_hits_and_misses_go_live(h):
  s3 = FakeS3([dump_rg("Lead", "2025-09-01", "Single")])
  deps, sqs, clock = world(h, s3=s3, years={"Lead": "2026-03-01"})
  sqs.send("q/hot", msg("Lead"))
  sqs.send("q/hot", msg("Unknown"))
  out = h.handler({}, Context(clock, 900), deps)
  assert (out["index_untrusted"], out["index_misses"], out["index_hits"]) == (1, 1, 0)
  assert deps.ddb.items[("artist", "lead")]["year"] == {"N": "2026"}


def test_shards_are_cached_across_runs(h):
  s3 = FakeS3([dump_rg("Old", "1994")])
  deps, sqs, clock = world(h, s3=s3)
  sqs.send("q/hot", msg("Old"))
  h.handler({}, Context(clock, 900), deps)
  deps.ddb.items.clear()
  sqs.send("q/hot", msg("Old"))
  h.handler({}, Context(clock, 900), deps)
  shard_gets = [k for k in s3.gets if k.endswith(".json.gz")]
  assert len(shard_gets) == 1 and s3.gets.count(ri.MANIFEST_KEY) == 1  # manifest cached for an hour


def test_an_unusable_manifest_turns_the_index_off_and_counts_an_error(h, capsys):
  bad = ri.manifest("20261121-001001", built_at=NOW, records=1, titles=1, shards=4096) | {"fingerprint": "x"}
  deps, sqs, clock = world(h, s3=FakeS3([dump_rg("Old", "1994")], manifest=bad))
  sqs.send("q/hot", msg("Old"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["index_errors"] == 1 and out["index_hits"] == 0 and deps.mb.calls  # live search
  assert "IndexAgeHours" not in emf(capsys)


def test_no_index_yet_is_not_an_error(h):
  deps, sqs, clock = world(h, s3=FakeS3(manifest=False))
  sqs.send("q/hot", msg("Old"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["index_errors"] == 0 and out["resolved"] == 1


def test_a_broken_shard_goes_live_and_counts_an_error(h):
  class Broken(FakeS3):
    def get_object(self, Bucket, Key):
      if Key.endswith(".json.gz"):
        raise S3Error("SlowDown")
      return super().get_object(Bucket, Key)

  deps, sqs, clock = world(h, s3=Broken([dump_rg("Old", "1994")]))
  sqs.send("q/hot", msg("Old"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["index_errors"] == 1 and out["resolved"] == 1


def stored(year, rg_mbid, tracks=None):
  item = {"artist_key": {"S": "artist"}, "title_key": {"S": "fixed"}, "year": {"N": str(year)},
          "rg_mbid": {"S": rg_mbid}}  # fmt: skip
  if tracks is not None:
    item |= {
      "tracks": {"N": str(tracks)},
      "tracks_type": {"S": "Album"},
      "tracks_source": {"S": "musicbrainz"},
    }
  return item


def test_a_recheck_overwrites_the_item_and_keeps_its_tracks(h):
  ddb = FakeDDB({("artist", "fixed"): stored(2025, "rg-Fixed-2026", tracks=12)})
  deps, sqs, clock = world(h, ddb=ddb, s3=FakeS3([dump_rg("Fixed", "2026-01-03")]))
  sqs.send("q/cold", {**msg("Fixed"), "recheck": True})
  out = h.handler({}, Context(clock, 900), deps)
  assert out["rechecks"] == 1 and out["already_resolved"] == 0 and out["tracks_reused"] == 1
  item = deps.ddb.items[("artist", "fixed")]
  assert item["year"] == {"N": "2026"} and item["tracks"] == {"N": "12"}
  assert deps.mb.calls == [] and sqs.left("q/cold") == 0


def test_a_recheck_flag_on_any_message_for_the_key_counts(h):
  ddb = FakeDDB({("artist", "fixed"): stored(2025, "rg-Fixed-2026", tracks=12)})
  deps, sqs, clock = world(h, ddb=ddb, s3=FakeS3([dump_rg("Fixed", "2026-01-03")]))
  sqs.send("q/cold", msg("Fixed"))  # a plain duplicate from sync arrives first
  sqs.send("q/cold", {**msg("Fixed"), "recheck": True})
  out = h.handler({}, Context(clock, 900), deps)
  assert out["rechecks"] == 1 and deps.ddb.items[("artist", "fixed")]["year"] == {"N": "2026"}
  assert sqs.left("q/cold") == 0


def test_a_non_boolean_recheck_is_ignored(h):
  ddb = FakeDDB({("artist", "fixed"): stored(2025, "rg-Fixed-2026")})
  deps, sqs, clock = world(h, ddb=ddb)
  sqs.send("q/cold", {**msg("Fixed"), "recheck": "yes"})
  out = h.handler({}, Context(clock, 900), deps)
  assert out["already_resolved"] == 1 and out["rechecks"] == 0


def test_to_dynamo_types(h):
  assert h.to_dynamo({"a": "x", "b": 3, "c": None, "d": True}) == {
    "a": {"S": "x"}, "b": {"N": "3"}, "c": {"NULL": True}, "d": {"BOOL": True}
  }  # fmt: skip


# ---- chart builder pokes (AOTY-21) ------------------------------------------------------


class TimedLambda:
  def __init__(self, clock, fail_chart=False):
    self.clock, self.fail_chart, self.invokes = clock, fail_chart, []

  def invoke(self, **kw):
    if kw["FunctionName"] == "chart" and self.fail_chart:
      raise RuntimeError("invoke failed")
    self.invokes.append({**kw, "t": self.clock.t})

  def charts(self):
    return [kw for kw in self.invokes if kw["FunctionName"] == "chart"]


def chart_world(h, n_albums, cost=10.0, fail_chart=False, ddb=None):
  deps, sqs, clock = world(h, cost=cost, ddb=ddb)
  deps.env = {**ENV, "CHART_FUNCTION": "chart"}
  deps.lambda_ = TimedLambda(clock, fail_chart)
  for i in range(n_albums):
    sqs.send("q/cold", msg(f"P{i:03}"))
  return deps, sqs, clock


def test_charts_are_poked_after_the_first_batch_then_at_most_once_a_minute(h, capsys):
  deps, sqs, clock = chart_world(h, 200)  # 10 batches, ~20 s each
  out = h.handler({}, Context(clock, 900), deps)
  pokes = deps.lambda_.charts()
  assert out["batches"] == 10 and out["written"] == 200
  assert pokes[0]["Payload"] == b'{"pending":true}' and pokes[0]["InvocationType"] == "Event"
  assert pokes[0]["t"] <= 2 * 10.0 + 1  # right after the first batch's searches
  gaps = [b["t"] - a["t"] for a, b in zip(pokes, pokes[1:-1])]
  assert all(g >= h.CHART_POKE_S for g in gaps)
  assert 2 <= len(pokes) < out["batches"]
  assert pokes[-1]["t"] == clock.t or pokes[-1]["t"] >= pokes[-2]["t"]  # the end-of-run poke
  assert emf(capsys)["chart_pokes"] == len(pokes)


def test_nothing_written_means_no_chart_poke(h):
  ddb = FakeDDB()
  deps, sqs, clock = chart_world(h, 5, ddb=ddb)
  h.handler({}, Context(clock, 900), deps)  # writes the 5
  deps.lambda_.invokes.clear()
  for i in range(5):
    sqs.send("q/cold", msg(f"P{i:03}"))  # already resolved now
  out = h.handler({}, Context(clock, 900), deps)
  assert out["already_resolved"] == 5 and deps.lambda_.charts() == []


def test_a_failed_chart_poke_doesnt_fail_the_run(h):
  deps, sqs, clock = chart_world(h, 25, fail_chart=True)
  out = h.handler({}, Context(clock, 900), deps)
  assert out["written"] == 25 and sqs.left("q/cold") == 0
  assert out["chart_pokes"] == 0


def test_without_a_chart_function_nothing_is_poked(h):
  deps, sqs, clock = world(h)
  sqs.send("q/cold", msg("Solo"))
  out = h.handler({}, Context(clock, 900), deps)
  assert out["written"] == 1 and deps.lambda_.invokes == []
