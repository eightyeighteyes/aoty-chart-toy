"""The sync worker handler (lambdas/sync/handler.py) against in-memory fakes of AWS and Last.fm."""

import calendar
import gzip
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

import sync_core as core
from common import lastfm
from common.albumkey import album_key, join_key

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "sync" / "handler.py"
NOW = calendar.timegm((2026, 11, 21, 18, 0, 0))
YEAR_START = calendar.timegm((2026, 1, 1, 0, 0, 0))
ENV = {
  "USERS_TABLE": "users",
  "ALBUMS_TABLE": "albums",
  "STORE_BUCKET": "store",
  "SYNC_QUEUE_URL": "q/sync",
  "MB_HOT_URL": "q/hot",
  "MB_COLD_URL": "q/cold",
  "MB_FUNCTION": "mb",
}


@pytest.fixture(scope="module")
def h():
  spec = importlib.util.spec_from_file_location("sync_handler", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


# ---- fakes ------------------------------------------------------------------------------


class Killed(Exception):
  """Stands in for the Lambda dying mid-run."""


def make_world(n_albums=200, seed_plays=None):
  """Deterministic scrobbles: album i gets n_albums - i plays spread over the year.

  Album 0 is a compilation (album artist "Various Artists", track artists vary), and two
  albums come as editions of one title, so grouping and edition merging are exercised.
  """
  scrobbles = []
  span = NOW - YEAR_START - 3600
  for i in range(n_albums):
    plays = (seed_plays or {}).get(i, n_albums - i)
    album = f"Album {i:03d}"
    artist = f"Artist {i % 37}"
    for p in range(plays):
      uts = YEAR_START + (i * 7919 + p * 104729) % span
      edition = " (Deluxe Edition)" if i == 5 and p % 2 else ""
      track_artist = f"Guest {p % 4}" if i == 0 else artist
      scrobbles.append({"uts": uts, "album_artist": "Various Artists" if i == 0 else artist,
                        "artist": track_artist, "album": album + edition})  # fmt: skip
  return scrobbles


def world_totals(scrobbles):
  return Counter(join_key(album_key(s["album_artist"], s["album"])) for s in scrobbles)


class FakeLastfm:
  def __init__(self, scrobbles, *, kill_after=None, chart_error=None, exists=True):
    self.scrobbles = scrobbles
    self.kill_after, self.chart_error, self.exists = kill_after, chart_error, exists
    self.chart_calls, self.calls = [], 0

  def weekly_album_chart(self, user, frm, to):
    if self.chart_error is not None:
      raise lastfm.LastfmError(self.chart_error, "fake")
    if self.kill_after is not None and len(self.chart_calls) >= self.kill_after:
      raise Killed()
    self.chart_calls.append((frm, to))
    c = Counter((s["album_artist"], s["album"]) for s in self.scrobbles if frm <= s["uts"] < to)
    return [{"artist": {"#text": a}, "name": b, "mbid": "", "playcount": str(n)} for (a, b), n in c.items()]

  def recent_tracks(self, user, frm, page=1, limit=200):
    rows = sorted((s for s in self.scrobbles if s["uts"] >= frm), key=lambda s: -s["uts"])
    total = max(1, -(-len(rows) // limit))
    tracks = [
      {
        "artist": {"#text": s["artist"]},
        "album": {"#text": s["album"], "mbid": ""},
        "image": [{"size": "extralarge", "#text": f"https://img/{s['album']}.jpg"}],
        "date": {"uts": str(s["uts"])},
      }
      for s in rows[(page - 1) * limit : page * limit]
    ]
    if page == 1:
      tracks.insert(0, {"artist": {"#text": "X"}, "album": {"#text": "Now"}, "image": [],
                        "@attr": {"nowplaying": "true"}})  # fmt: skip
    return tracks, total

  def user_exists(self, user):
    return self.exists


class FakeS3:
  class NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}

  def __init__(self):
    self.objects, self.puts = {}, 0

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
    self.puts += 1

  def store(self, username="alice", year=2026):
    return json.loads(gzip.decompress(self.objects[("store", f"users/{username}/{year}/weeks.json.gz")]))


def _py(av):
  """A DynamoDB attribute value back to Python (the inverse of handler._av)."""
  (t, v), = av.items()  # fmt: skip
  if t == "M":
    return {k: _py(x) for k, x in v.items()}
  if t == "NULL":
    return None
  return {"S": str, "N": int, "BOOL": bool}[t](v)


class FakeDDB:
  def __init__(self, resolved=(), unprocessed_once=False):
    self.users, self.resolved = {}, set(resolved)
    self.unprocessed_once, self.batch_gets = unprocessed_once, 0

  def get_item(self, TableName, Key):
    item = self.users.get(Key["username"]["S"])
    if item is None:
      return {}
    return {"Item": {k: {"N": str(v)} if isinstance(v, int) else {"S": str(v)} for k, v in item.items()
                     if isinstance(v, int | str)}}  # fmt: skip

  def update_item(
    self, TableName, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues
  ):
    row = self.users.setdefault(Key["username"]["S"], {})
    sets, _, removes = UpdateExpression.removeprefix("SET ").partition(" REMOVE ")
    for part in sets.split(", "):
      name, value = part.split(" = ")
      row[ExpressionAttributeNames[name]] = _py(ExpressionAttributeValues[value])
    for name in filter(None, removes.split(", ")):
      row.pop(ExpressionAttributeNames[name], None)

  def batch_get_item(self, RequestItems):
    self.batch_gets += 1
    (table, req), = RequestItems.items()  # fmt: skip
    keys = req["Keys"]
    assert len(keys) <= 100
    if any(not v["S"] for k in keys for v in k.values()):
      # What real DynamoDB answered on staging (AOTY-18's first real sync).
      raise ValueError("ValidationException: key attribute cannot contain an empty string value")
    if self.unprocessed_once:
      self.unprocessed_once = False
      return {"Responses": {table: []}, "UnprocessedKeys": RequestItems}
    hits = [k for k in keys if join_key((k["artist_key"]["S"], k["title_key"]["S"])) in self.resolved]
    return {"Responses": {table: hits}, "UnprocessedKeys": {}}


class FakeSQS:
  def __init__(self, fail_sends=0, fail_entries_once=False):
    self.sent, self.visibility = {}, []
    self.fail_sends, self.fail_entries_once = fail_sends, fail_entries_once

  def send_message_batch(self, QueueUrl, Entries):
    assert len(Entries) <= 10
    if self.fail_sends:
      self.fail_sends -= 1
      raise Killed()
    failed = []
    if self.fail_entries_once:
      self.fail_entries_once = False
      failed, Entries = Entries[:1], Entries[1:]
    self.sent.setdefault(QueueUrl, []).extend(json.loads(e["MessageBody"]) for e in Entries)
    return {"Successful": [{"Id": e["Id"]} for e in Entries], "Failed": [{"Id": e["Id"]} for e in failed]}

  def change_message_visibility(self, **kw):
    self.visibility.append(kw)

  def all_sent(self):
    return [join_key((m["artist_key"], m["title_key"])) for msgs in self.sent.values() for m in msgs]


class FakeLambda:
  def __init__(self):
    self.invokes = []

  def invoke(self, **kw):
    self.invokes.append(kw)


class Context:
  def __init__(self, remaining):
    self.remaining = list(remaining)

  def get_remaining_time_in_millis(self):
    return self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]


class Env:
  """One fake AWS account, reused across syncs; a fresh FakeLastfm per run."""

  def __init__(self, h, scrobbles, ddb=None, sqs=None):
    self.h, self.scrobbles = h, scrobbles
    self.s3, self.ddb, self.sqs, self.lam = FakeS3(), ddb or FakeDDB(), sqs or FakeSQS(), FakeLambda()
    self.now = NOW

  def deps(self, fm):
    return self.h.Deps(s3=self.s3, ddb=self.ddb, sqs=self.sqs, lambda_=self.lam, lastfm_client=fm,
                       env=ENV, clock=lambda: self.now, sleep=lambda s: None)  # fmt: skip

  def run(self, msg=None, context=None, **fm_kw):
    fm = FakeLastfm(self.scrobbles, **fm_kw)
    result = self.h.sync_user(msg or {"username": "Alice"}, context, self.deps(fm))
    return result, fm

  def record(self, msg=None, receives=1, **fm_kw):
    fm = FakeLastfm(self.scrobbles, **fm_kw)
    rec = {"messageId": "m1", "receiptHandle": "r1", "body": json.dumps(msg or {"username": "alice"}),
           "attributes": {"ApproximateReceiveCount": str(receives)}}  # fmt: skip
    return self.h.handler({"Records": [rec]}, None, self.deps(fm)), fm


# ---- tests ------------------------------------------------------------------------------


def test_first_sync_backfills_the_year_and_matches_counted_scrobbles(h):
  scrobbles = make_world()
  env = Env(h, scrobbles)
  result, fm = env.run()
  assert result == "done"
  assert len(fm.chart_calls) == 47
  store = env.s3.store()
  assert len(store["weeks"]) == 47
  assert core.year_plays(store) == world_totals(scrobbles)
  # The compilation is one album under its album artist, and the editions are one key.
  assert join_key(("various artists", "album 000")) in store["albums"]
  assert join_key(("artist 5", "album 005")) in store["albums"]
  user = env.ddb.users["alice"]
  assert user["state"] == "done" and user["last_full_sync_at"] == NOW
  assert user["progress"] == {"weeks_done": 47, "weeks_total": 47, "plays": len(scrobbles)}
  assert user["last_seen_uts"] == max(s["uts"] for s in scrobbles)


def test_first_sync_queues_unresolved_albums_top_150_hot_and_pokes_mb_once(h):
  env = Env(h, make_world(), ddb=FakeDDB(resolved={join_key(("artist 1", "album 001"))}))
  env.run()
  hot, cold = env.sqs.sent["q/hot"], env.sqs.sent["q/cold"]
  ranked = core.ranked_keys(env.s3.store())
  assert [join_key((m["artist_key"], m["title_key"])) for m in hot] == [
    k for k in ranked[:150] if k != ranked[1]
  ]
  assert len(cold) == 50
  assert hot[0]["album"] == "Album 000" and hot[0]["artist"] == "Various Artists"
  assert len(env.lam.invokes) == 1 and env.lam.invokes[0]["InvocationType"] == "Event"
  assert env.s3.store()["albums"][ranked[1]]["resolved"] is True


def test_a_second_sync_fetches_only_the_refresh_window_and_queues_nothing_new(h):
  env = Env(h, make_world())
  env.run()
  sent = len(env.sqs.all_sent())
  env.now += 3600
  result, fm = env.run()
  assert result == "done"
  assert len(fm.chart_calls) == 4  # current week + last 3 closed
  assert len(env.sqs.all_sent()) == sent
  assert len(env.lam.invokes) == 1
  assert (
    "last_full_sync_at" not in env.ddb.users["alice"] or env.ddb.users["alice"]["last_full_sync_at"] == NOW
  )


def test_new_scrobbles_land_in_the_current_week(h):
  scrobbles = make_world()
  env = Env(h, scrobbles)
  env.run()
  env.now += 3600
  scrobbles.append({"uts": NOW + 60, "album_artist": "New", "artist": "New", "album": "Fresh"})
  env.run()
  assert core.year_plays(env.s3.store())[join_key(("new", "fresh"))] == 1
  assert env.sqs.all_sent()[-1] == join_key(("new", "fresh"))


def test_killing_a_backfill_partway_and_rerunning_gives_the_same_totals(h):
  scrobbles = make_world()
  clean = Env(h, scrobbles)
  clean.run()
  env = Env(h, scrobbles)
  with pytest.raises(Killed):
    env.run(kill_after=20)
  assert len(env.s3.store()["weeks"]) == 16  # checkpoints after weeks 8 and 16
  result, fm = env.run()
  assert result == "done"
  assert len(fm.chart_calls) == 47 - 16  # resumed, not restarted
  assert core.year_plays(env.s3.store()) == core.year_plays(clean.s3.store())
  assert "full_started_at" not in env.s3.store()


def test_a_kill_between_the_store_write_and_mb_queueing_still_queues_each_album_once(h):
  env = Env(h, make_world(), sqs=FakeSQS(fail_sends=1))
  with pytest.raises(Killed):
    env.run()
  env.run()
  sent = env.sqs.all_sent()
  assert len(sent) == len(set(sent)) == 200


def test_failed_batch_entries_and_unprocessed_keys_are_retried(h):
  env = Env(h, make_world(20), ddb=FakeDDB(unprocessed_once=True), sqs=FakeSQS(fail_entries_once=True))
  assert env.run()[0] == "done"
  assert sorted(env.sqs.all_sent()) == sorted(world_totals(env.scrobbles))


def test_low_time_checkpoints_and_redelivers_at_once(h):
  env = Env(h, make_world(20))
  result, fm = env.run(context=Context([900_000] * 10 + [30_000]))
  assert result == "partial"
  assert len(fm.chart_calls) == 10
  assert len(env.s3.store()["weeks"]) == 10
  assert env.ddb.users["alice"]["progress"]["weeks_done"] == 10


def test_handler_reports_partial_as_a_batch_failure_with_zero_visibility(h, monkeypatch):
  env = Env(h, make_world(20))
  monkeypatch.setattr(h, "sync_user", lambda msg, ctx, deps: "partial")
  out, _ = env.record()
  assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
  assert env.sqs.visibility == [{"QueueUrl": "q/sync", "ReceiptHandle": "r1", "VisibilityTimeout": 0}]


def test_confirmed_not_found_and_private_users_are_marked_and_not_retried(h):
  env = Env(h, make_world(5))
  out, _ = env.record(chart_error=lastfm.NOT_FOUND, exists=False)
  assert out == {"batchItemFailures": []}
  assert env.ddb.users["alice"]["state"] == "not_found"
  assert env.ddb.users["alice"]["error_code"] == "6"
  out, _ = env.record(chart_error=lastfm.PRIVATE)
  assert out == {"batchItemFailures": []}
  assert env.ddb.users["alice"]["state"] == "private"


def test_an_unconfirmed_not_found_is_retried(h):
  env = Env(h, make_world(5))
  out, _ = env.record(chart_error=lastfm.NOT_FOUND, exists=True)
  assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
  assert env.ddb.users["alice"]["state"] == "retrying"


def test_a_failed_sync_is_retried_within_minutes_not_after_the_visibility_timeout(h):
  env = Env(h, make_world(5))
  for receives in (1, 2, 3, 4):
    env.record(receives=receives, chart_error=16)
    assert env.ddb.users["alice"]["state"] == "retrying"
  assert [v["VisibilityTimeout"] for v in env.sqs.visibility] == [60, 120, 240, 480]
  assert {v["ReceiptHandle"] for v in env.sqs.visibility} == {"r1"}


def test_the_last_receive_marks_the_user_as_errored_before_the_dlq(h):
  env = Env(h, make_world(5))
  out, _ = env.record(receives=5, chart_error=16)
  assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
  assert env.ddb.users["alice"]["state"] == "error"
  assert env.sqs.visibility == []  # off to the DLQ; no point shortening the wait


def test_all_punctuation_titles_and_artists_are_keyed_and_queued(h):
  # The staging crash: an album titled "( )" normalized to an empty title_key.
  scrobbles = make_world(5) + [
    {"uts": NOW - 3 * 86400, "album_artist": "Sigur Rós", "artist": "Sigur Rós", "album": "( )"},
    {"uts": NOW - 3 * 86400, "album_artist": "!!!", "artist": "!!!", "album": "Shake the Shudder"},
  ]
  env = Env(h, scrobbles)
  assert env.run()[0] == "done"
  sent = env.sqs.all_sent()
  assert join_key(("sigur rós", "( )")) in sent
  assert join_key(("!!!", "shake the shudder")) in sent


def test_a_store_written_before_the_fix_still_syncs(h):
  env = Env(h, make_world(5))
  env.run()
  store = env.s3.store()
  legacy = join_key(("sigur rós", ""))
  week = next(iter(store["weeks"].values()))
  week["plays"][legacy] = 3
  store["albums"][legacy] = {"artist": "Sigur Rós", "album": "( )", "mbid": "", "image": ""}
  env.s3.put_object(Bucket="store", Key="users/alice/2026/weeks.json.gz",
                    Body=gzip.compress(json.dumps(store).encode()))  # fmt: skip
  env.now += 8 * 86400
  assert env.run()[0] == "done"
  assert legacy not in env.sqs.all_sent()


def test_a_new_request_retries_a_user_marked_not_found(h):
  env = Env(h, make_world(5))
  env.record(chart_error=lastfm.NOT_FOUND, exists=False)
  out, _ = env.record()
  assert out == {"batchItemFailures": []}
  assert env.ddb.users["alice"]["state"] == "done"
  assert "error_code" not in env.ddb.users["alice"]


def test_invalid_usernames_are_dropped_without_writes(h):
  env = Env(h, make_world(5))
  out, _ = env.record(msg={"username": "../etc"})
  assert out == {"batchItemFailures": []}
  assert env.ddb.users == {} and env.s3.objects == {}


def test_covers_come_from_recent_tracks(h):
  env = Env(h, make_world(20))
  env.run()
  albums = env.s3.store()["albums"]
  assert albums[join_key(("artist 3", "album 003"))]["image"] == "https://img/Album 003.jpg"
  # Compilation scrobbles carry track artists, so it waits for AOTY-21's fallback.
  assert albums[join_key(("various artists", "album 000"))]["image"] == ""


def test_the_year_is_a_parameter(h):
  env = Env(h, make_world(5))
  env.now = calendar.timegm((2027, 1, 20, 0, 0, 0))
  result, fm = env.run(msg={"username": "alice", "year": 2026})
  assert result == "done"
  assert fm.chart_calls[-1][1] == calendar.timegm((2027, 1, 1, 0, 0, 0))
  assert ("store", "users/alice/2026/weeks.json.gz") in env.s3.objects
