"""The index build (jobs/index/build.py) against a tiny dump and fake S3, DynamoDB and SQS."""

import gzip
import io
import json
import tarfile
from datetime import UTC, datetime

import pytest

import build
import dump
from common import releaseindex as ri

NOW = 1_795_000_000
DAY = 86400


def rg(title, date, type_="Album", artist="Artist", id_=None):
  return {
    "id": id_ or f"{title}-{date}",
    "title": title,
    "first-release-date": date,
    "primary-type": type_,
    "artist-credit": [{"name": artist, "joinphrase": ""}],
  }


DUMP_RGS = [
  rg("Old", "1994-05-10", id_="rg-old"),
  rg("Weezer", "1994-05-10", artist="Weezer", id_="rg-weezer-94"),
  rg("Weezer", "2026-02-01", "Single", artist="Weezer", id_="rg-weezer-26"),
  rg("Fixed Date", "2026-03-01", id_="rg-fixed"),
  rg("Late Add", "2026-01-15", id_="rg-late"),
  rg("No Date", ""),
  rg("!!!", "2007", artist="!!!"),  # norm("!!!") is "": not indexable
]


def make_archive(path, rgs):
  body = b"".join(json.dumps(r).encode() + b"\n" for r in rgs)
  with tarfile.open(path, "w:xz") as tar:
    info = tarfile.TarInfo(dump.RG_MEMBER)
    info.size = len(body)
    tar.addfile(info, io.BytesIO(body))


class S3Error(Exception):
  def __init__(self, code):
    self.response = {"Error": {"Code": code}}


class FakeS3:
  def __init__(self):
    self.objects, self.puts = {}, []

  def get_object(self, Bucket, Key):
    if Key not in self.objects:
      raise S3Error("NoSuchKey")
    return {"Body": io.BytesIO(self.objects[Key])}

  def put_object(self, Bucket, Key, Body, **kw):
    self.objects[Key] = Body
    self.puts.append(Key)

  def list_objects_v2(self, Bucket, Prefix, Delimiter=None, ContinuationToken=None):
    keys = sorted(k for k in self.objects if k.startswith(Prefix))
    if Delimiter:
      prefixes = sorted({Prefix + k[len(Prefix) :].split(Delimiter)[0] + Delimiter for k in keys
                         if Delimiter in k[len(Prefix) :]})  # fmt: skip
      return {"CommonPrefixes": [{"Prefix": p} for p in prefixes]}
    start = int(ContinuationToken or 0)
    page = keys[start : start + 1000]
    out = {"Contents": [{"Key": k} for k in page]}
    if start + 1000 < len(keys):
      out["NextContinuationToken"] = str(start + 1000)
    return out

  def delete_objects(self, Bucket, Delete):
    assert len(Delete["Objects"]) <= 1000
    for o in Delete["Objects"]:
      self.objects.pop(o["Key"], None)


class FakeDDB:
  def __init__(self, items, page=2):
    self.items, self.page = items, page

  def scan(self, TableName, ExclusiveStartKey=None):
    start = int(ExclusiveStartKey["i"]["N"]) if ExclusiveStartKey else 0
    chunk = self.items[start : start + self.page]
    out = {"Items": [to_dynamo(i) for i in chunk]}
    if start + self.page < len(self.items):
      out["LastEvaluatedKey"] = {"i": {"N": str(start + self.page)}}
    return out


class FakeSQS:
  def __init__(self):
    self.sent = []

  def send_message_batch(self, QueueUrl, Entries):
    assert len(Entries) <= 10
    self.sent += [(QueueUrl, json.loads(e["MessageBody"])) for e in Entries]
    return {}


def to_dynamo(item):
  out = {}
  for k, v in item.items():
    if v is None:
      out[k] = {"NULL": True}
    elif isinstance(v, int):
      out[k] = {"N": str(v)}
    else:
      out[k] = {"S": v}
  return out


def item(album, year, rg_mbid, artist="Artist", looked_up_at=NOW - DAY):
  return {
    "artist_key": artist.lower(),
    "title_key": album.lower(),
    "artist": artist,
    "album": album,
    "year": year,
    "rg_mbid": rg_mbid,
    "lastfm_mbid": None,
    "looked_up_at": looked_up_at,
  }


@pytest.fixture(autouse=True)
def fewer_shards(monkeypatch):
  monkeypatch.setattr(ri, "SHARD_HEX", 2)  # 256 shards keep each build fast


@pytest.fixture
def env(tmp_path):
  archive = tmp_path / "src.tar.xz"
  make_archive(archive, DUMP_RGS)

  def fake_download(dump_name, name, dest, ua, log=print):
    dest.write_bytes(archive.read_bytes())
    return {"download_s": 1.0}

  def make(items=(), latest="20261121-001001", s3=None):
    deps = build.Deps(
      s3=s3 or FakeS3(),
      ddb=FakeDDB(list(items)),
      sqs=FakeSQS(),
      env={"STORE_BUCKET": "b", "ALBUMS_TABLE": "albums", "MB_COLD_URL": "cold", "AOTY_ENV": "test"},
      user_agent="test",
      work_dir=tmp_path / "work",
      latest_dump=lambda ua: latest,
      download=fake_download,
      clock=lambda: NOW,
    )
    return deps

  return make


def shard_body(s3, dump_name, title):
  key = ri.shard_key(dump_name, ri.shard_of(title))
  return json.loads(gzip.decompress(s3.objects[key]))


def test_build_writes_every_shard_then_the_manifest(env, capsys):
  deps = env()
  out = build.run(deps)
  s3 = deps.s3
  assert out["records"] == 5 and out["undated"] == 1 and out["untitled"] == 1
  shard_keys = [k for k in s3.objects if k.startswith("index/20261121-001001/")]
  assert len(shard_keys) == 16**ri.SHARD_HEX
  assert s3.puts[-1] == ri.MANIFEST_KEY  # written last
  m = json.loads(s3.objects[ri.MANIFEST_KEY])
  assert ri.usable(m) and m["dump"] == "20261121-001001" and m["records"] == 5 and m["titles"] == 4
  assert [r["id"] for r in shard_body(s3, "20261121-001001", "weezer")["weezer"]] == [
    "rg-weezer-26",
    "rg-weezer-94",
  ]
  emf = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
  assert emf["IndexRecords"] == 5 and emf["IndexBuildSkipped"] == 0 and emf["Environment"] == "test"
  assert not (deps.work_dir / "20261121-001001").exists()  # cleaned up


def test_lookups_through_the_built_shards(env):
  deps = env()
  build.run(deps)
  s3 = deps.s3

  def load(shard):
    return json.loads(gzip.decompress(s3.objects[ri.shard_key("20261121-001001", shard)]))

  assert ri.lookup("Weezer", "Weezer", load).year == "1994"
  assert ri.lookup("Artist", "Old", load).trusted
  assert ri.lookup("Artist", "Unknown", load) is None


def test_an_up_to_date_index_is_skipped(env, capsys):
  deps = env()
  build.run(deps)
  puts = len(deps.s3.puts)
  again = env(s3=deps.s3)
  assert build.run(again) == {"skipped": True, "dump": "20261121-001001"}
  assert len(deps.s3.puts) == puts
  assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["IndexBuildSkipped"] == 1


def test_an_unusable_manifest_is_rebuilt_even_for_the_same_dump(env):
  s3 = FakeS3()
  s3.objects[ri.MANIFEST_KEY] = json.dumps({"format": 0, "dump": "20261121-001001"}).encode()
  assert build.run(env(s3=s3))["skipped"] is False


def test_old_builds_are_pruned_keeping_the_one_replaced(env):
  deps = env(latest="20261118-001001")
  build.run(deps)
  deps.s3.objects["index/20261114-001001/000.json.gz"] = b"ancient"
  build.run(env(latest="20261121-001001", s3=deps.s3))
  dumps = {k.split("/")[1] for k in deps.s3.objects if k.count("/") == 2}
  assert dumps == {"20261118-001001", "20261121-001001"}


def test_recheck_rules():
  hit = ri.Hit(
    year="1994", id="rg-old", type="Album", trusted=True, candidate_ids=frozenset({"rg-old", "rg-x"})
  )
  untrusted = ri.Hit(year="2025", id="rg-s", type="Single", trusted=False, candidate_ids=frozenset({"rg-s"}))
  reason = build.recheck_reason
  # Same answer: nothing to do.
  assert reason(item("Old", 1994, "rg-old"), hit, NOW) is None
  # The dump corrected the date of the stored release group.
  assert reason(item("Old", 1995, "rg-old"), hit, NOW) == "changed"
  # The stored group is in the dump, but a better one now wins.
  assert reason(item("Old", 2001, "rg-x"), hit, NOW) == "changed"
  # The stored group came from a live search newer than the dump: leave it.
  assert reason(item("Old", 2026, "rg-live-only"), hit, NOW) is None
  # A confirmed miss the index can now answer.
  assert reason(item("Old", None, None), hit, NOW) == "changed"
  # Misses without a trusted answer wait 30 days for a live retry.
  assert reason(item("X", None, None, looked_up_at=NOW - 29 * DAY), None, NOW) is None
  assert reason(item("X", None, None, looked_up_at=NOW - 31 * DAY), None, NOW) == "stale_miss"
  assert reason(item("X", None, None, looked_up_at=NOW - 31 * DAY), untrusted, NOW) == "stale_miss"
  # An untrusted hit never overrides a stored answer.
  assert reason(item("X", 2026, "rg-new"), untrusted, NOW) is None


def test_build_queues_rechecks_to_mb_cold(env):
  items = [
    item("Old", 1994, "rg-old"),  # unchanged
    item("Fixed Date", 2025, "rg-fixed"),  # date corrected in the dump
    item("Late Add", None, None),  # added to MusicBrainz after the lookup
    item("Gone", None, None, looked_up_at=NOW - 40 * DAY),  # still unknown: live retry
    item("Gone Recently", None, None),  # looked up yesterday: wait
  ]
  deps = env(items=items)
  out = build.run(deps)
  assert (out["rechecks_changed"], out["rechecks_stale_miss"], out["rechecked_items"]) == (2, 1, 5)
  assert {m["album"] for _, m in deps.sqs.sent} == {"Fixed Date", "Late Add", "Gone"}
  assert all(q == "cold" and m["recheck"] is True for q, m in deps.sqs.sent)
  assert deps.sqs.sent[0][1]["artist_key"] == "artist" and deps.sqs.sent[0][1]["mbid"] == ""


def test_rechecks_are_capped(env, monkeypatch):
  monkeypatch.setattr(build, "MAX_RECHECKS", 1)
  items = [item(f"Gone {i}", None, None, looked_up_at=0) for i in range(3)]
  out = build.run(env(items=items))
  assert out["rechecks_stale_miss"] == 1 and out["rechecks_capped"] == 2


def test_items_without_names_are_skipped(env):
  bare = {"artist_key": "a", "title_key": "t", "year": None, "looked_up_at": 0}
  deps = env(items=[bare])
  assert build.run(deps)["rechecks_stale_miss"] == 0


def test_dump_age_hours():
  made = datetime(2026, 11, 21, tzinfo=UTC).timestamp()
  assert build.dump_age_hours("20261121-000000", made + 36 * 3600) == 36.0
  assert build.dump_age_hours("not-a-dump", NOW) is None


def test_a_manifest_read_error_other_than_missing_is_raised():
  class Broken(FakeS3):
    def get_object(self, Bucket, Key):
      raise S3Error("AccessDenied")

  with pytest.raises(S3Error):
    build.read_manifest(Broken(), "b")
