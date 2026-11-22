"""The mb lane handler (lambdas/mb/handler.py) against in-memory fakes of SQS, DynamoDB and MusicBrainz."""

import importlib.util
import json
from pathlib import Path

import pytest

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
    found = [k for k in req["Keys"] if (k["artist_key"]["S"], k["title_key"]["S"]) in self.items]
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


def world(h, *, ddb=None, years=None, cost=1.0, page_cap=None):
  clock = Clock()
  sqs = FakeSQS(page_cap)
  deps = h.Deps(sqs=sqs, ddb=ddb or FakeDDB(), lambda_=FakeLambda(), mb=FakeMB(clock, years, cost),
                lastfm_client=FakeLastfm(), env=ENV, clock=lambda: NOW, mono=clock, sleep=lambda s: None)  # fmt: skip
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


def test_to_dynamo_types(h):
  assert h.to_dynamo({"a": "x", "b": 3, "c": None, "d": True}) == {
    "a": {"S": "x"}, "b": {"N": "3"}, "c": {"NULL": True}, "d": {"BOOL": True}
  }  # fmt: skip
