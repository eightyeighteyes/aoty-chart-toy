"""Build the release index from the MusicBrainz JSON dump and publish it (AOTY-20).

Runs as a Fargate Spot task (infra/modules/stack/index.tf), daily. Most days the manifest
already names the latest dump, so a run exits in seconds; the daily schedule is what makes a
run lost to a Spot interruption harmless, because the next day catches up. The dump comes out
twice a week.

One build:
  1. Skip if index/current.json is usable and names the latest dump.
  2. Download release-group.tar.xz (about 1.24 GB) and check its SHA256.
  3. Stream it once, writing each dated release group's record to one of 16 spill files on
     local disk by its shard's first hex digit. That keeps memory to one sixteenth of the
     dump (all of it in memory peaked at 5.4 GB in the AOTY-13 spike).
  4. Group each spill file into its 256 shards and write them gzipped to disk. Every one of
     the 4096 shards is written, empty ones included, so a missing object means an error.
  5. Upload the shards under index/<dump>/, then write index/current.json last.
  6. Delete index/<dump>/ prefixes other than this one and the one it replaced. A reader
     caches the manifest for an hour, and a new dump comes out at most twice a week.
  7. Re-check (common.releaseindex has the rules' background): scan the albums table and
     queue a recheck message to mb-cold for
       (a) items the index now answers differently, with a trusted hit, when the stored
           release group is one this dump knows (otherwise the stored answer came from a live
           search newer than the dump, and requeueing it would undo it on every build);
       (b) confirmed misses (year null) looked up more than 30 days ago that the index still
           can't answer, for a live search: albums get added to MusicBrainz late.
     The mb lane does the rewrite; it stays the only writer of the albums table.
It never calls the MusicBrainz API, so it can't slow the mb lane. One EMF line per run.

Locally, against a real environment's resources (admin credentials):
  STORE_BUCKET=... ALBUMS_TABLE=... MB_COLD_URL=... .venv/bin/python jobs/index/build.py
"""

import gzip
import json
import logging
import os
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

# In the packaged zip, common/ sits next to this file; in the repo it's under lambdas/.
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent / "lambdas")]

import dump  # noqa: E402
from common import musicbrainz  # noqa: E402
from common import releaseindex as ri  # noqa: E402
from common.albumkey import norm  # noqa: E402
from common.dynamo import from_item  # noqa: E402

log = logging.getLogger("index-build")

UA_VERSION = "0.1.0"
SPILL_HEX = 1  # 16 spill files
UPLOAD_THREADS = 16
DELETE_BATCH = 1000  # S3 DeleteObjects limit
SEND_BATCH = 10  # SQS SendMessageBatch limit
MISS_RECHECK_AGE_S = 30 * 86400
MAX_RECHECKS = 5000  # a guard against a bad rule requeueing the whole table
SHARD_CACHE = 512
NAMESPACE = "kamp-aoty"


class Deps:
  """Everything a build touches, so tests can pass fakes. from_env() builds the real ones."""

  def __init__(self, *, s3, ddb, sqs, env, user_agent, work_dir, latest_dump=dump.latest_dump,
               download=dump.download, clock=time.time, mono=time.monotonic):  # fmt: skip
    self.s3, self.ddb, self.sqs, self.env = s3, ddb, sqs, env
    self.user_agent, self.work_dir = user_agent, Path(work_dir)
    self.latest_dump, self.download = latest_dump, download
    self.clock, self.mono = clock, mono

  @classmethod
  def from_env(cls):
    import boto3  # in the task image (public.ecr.aws/lambda/python); not needed by tests
    from botocore.config import Config

    env = os.environ
    contact = ""
    if env.get("SSM_PREFIX"):
      ssm = boto3.client("ssm")
      param = ssm.get_parameter(Name=f"{env['SSM_PREFIX']}mb_contact", WithDecryption=True)
      contact = param["Parameter"]["Value"]
    return cls(
      # One connection per upload thread; the default pool of 10 logs a warning per extra one.
      s3=boto3.client("s3", config=Config(max_pool_connections=UPLOAD_THREADS)),
      ddb=boto3.client("dynamodb"),
      sqs=boto3.client("sqs"),
      env=env,
      user_agent=musicbrainz.user_agent(UA_VERSION, contact),
      work_dir=env.get("WORK_DIR", "/tmp/index-build"),
    )


def run(deps):
  started = deps.mono()
  bucket = deps.env["STORE_BUCKET"]
  latest = deps.latest_dump(deps.user_agent)
  current = read_manifest(deps.s3, bucket)
  if ri.usable(current) and current["dump"] == latest:
    log.info("index already built from %s", latest)
    summary = {"skipped": True, "dump": latest}
    emit(deps, summary, deps.mono() - started)
    return summary

  work = deps.work_dir / latest
  work.mkdir(parents=True, exist_ok=True)
  archive = work / dump.RG_ARCHIVE
  summary = {"skipped": False, "dump": latest}
  summary |= deps.download(latest, dump.RG_ARCHIVE, archive, deps.user_agent, log=log.info)

  spill_dir, shards_dir = work / "spill", work / "shards"
  with open(archive, "rb") as f:
    summary |= spill(dump.dump_lines(f, dump.RG_MEMBER), spill_dir)
  archive.unlink()  # free the disk before the shards land
  summary["titles"] = write_shards(spill_dir, shards_dir)
  shutil.rmtree(spill_dir)

  upload(deps.s3, bucket, latest, shards_dir)
  m = ri.manifest(
    latest, built_at=deps.clock(), records=summary["records"], titles=summary["titles"],
    shards=16**ri.SHARD_HEX,
  )  # fmt: skip
  deps.s3.put_object(
    Bucket=bucket, Key=ri.MANIFEST_KEY, Body=json.dumps(m).encode(), ContentType="application/json"
  )
  keep = {latest} | ({current["dump"]} if isinstance(current, dict) and current.get("dump") else set())
  summary["pruned"] = prune(deps.s3, bucket, keep)

  summary |= recheck(deps, shard_loader(shards_dir), deps.clock())
  shutil.rmtree(work)
  emit(deps, summary, deps.mono() - started)
  return summary


# ---- building ---------------------------------------------------------------------------


def spill(lines, spill_dir):
  """Write [norm(title), record] lines into 16 files by shard prefix. Returns counts."""
  spill_dir.mkdir(parents=True, exist_ok=True)
  files = {}
  counts = {"records": 0, "undated": 0, "untitled": 0}
  try:
    for line in lines:
      rg = json.loads(line)
      if not ri.is_dated(rg):
        counts["undated"] += 1
        continue
      rec = ri.record(rg)
      key = norm(rec["title"])
      if not key:  # nothing a Last.fm title could look up (title_keys skips these too)
        counts["untitled"] += 1
        continue
      prefix = ri.shard_of(key)[:SPILL_HEX]
      if prefix not in files:
        files[prefix] = open(spill_dir / f"{prefix}.jsonl", "w", encoding="utf-8")
      files[prefix].write(json.dumps([key, rec], ensure_ascii=False, separators=(",", ":")) + "\n")
      counts["records"] += 1
      if counts["records"] % 1_000_000 == 0:
        log.info("  %s records", f"{counts['records']:,}")
  finally:
    for f in files.values():
      f.close()
  return counts


def write_shards(spill_dir, shards_dir):
  """Turn the spill files into every shard's gzipped JSON. Returns the number of titles."""
  shards_dir.mkdir(parents=True, exist_ok=True)
  titles = 0
  rest_hex = ri.SHARD_HEX - SPILL_HEX
  for p in range(16**SPILL_HEX):
    prefix = f"{p:0{SPILL_HEX}x}"
    groups = defaultdict(lambda: defaultdict(list))
    path = spill_dir / f"{prefix}.jsonl"
    if path.exists():
      with open(path, encoding="utf-8") as f:
        for line in f:
          key, rec = json.loads(line)
          groups[ri.shard_of(key)][key].append(rec)
    for s in range(16**rest_hex):
      shard = f"{prefix}{s:0{rest_hex}x}"
      body = {key: sorted(recs, key=lambda r: r["id"]) for key, recs in sorted(groups.pop(shard, {}).items())}
      titles += len(body)
      raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
      (shards_dir / f"{shard}.json.gz").write_bytes(gzip.compress(raw, 6, mtime=0))
  return titles


def shard_loader(shards_dir):
  @lru_cache(maxsize=SHARD_CACHE)
  def load(shard):
    return json.loads(gzip.decompress((shards_dir / f"{shard}.json.gz").read_bytes()))

  return load


# ---- S3 ---------------------------------------------------------------------------------


def _error_code(e):
  return getattr(e, "response", {}).get("Error", {}).get("Code")


def read_manifest(s3, bucket):
  """The live manifest, or None if there isn't one yet."""
  try:
    body = s3.get_object(Bucket=bucket, Key=ri.MANIFEST_KEY)["Body"].read()
  except Exception as e:
    if _error_code(e) in ("NoSuchKey", "404"):
      return None
    raise
  return json.loads(body)


def upload(s3, bucket, dump_name, shards_dir):
  paths = sorted(shards_dir.glob("*.json.gz"))

  def put(path):
    s3.put_object(
      Bucket=bucket,
      Key=ri.shard_key(dump_name, path.name.removesuffix(".json.gz")),
      Body=path.read_bytes(),
      ContentType="application/json",
      ContentEncoding="gzip",
    )

  with ThreadPoolExecutor(UPLOAD_THREADS) as pool:
    list(pool.map(put, paths))  # list() re-raises the first failure
  log.info("uploaded %d shards", len(paths))


def prune(s3, bucket, keep):
  """Delete index/<dump>/ prefixes not in keep. Returns the number of objects deleted."""
  out = s3.list_objects_v2(Bucket=bucket, Prefix=ri.PREFIX, Delimiter="/")
  deleted = 0
  for p in out.get("CommonPrefixes", []):
    name = p["Prefix"][len(ri.PREFIX) :].rstrip("/")
    if name in keep:
      continue
    keys, token = [], None
    while True:
      kw = {"ContinuationToken": token} if token else {}
      page = s3.list_objects_v2(Bucket=bucket, Prefix=p["Prefix"], **kw)
      keys += [o["Key"] for o in page.get("Contents", [])]
      token = page.get("NextContinuationToken")
      if not token:
        break
    for i in range(0, len(keys), DELETE_BATCH):
      s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i : i + DELETE_BATCH]]})
    deleted += len(keys)
    log.info("pruned %s (%d objects)", name, len(keys))
  return deleted


# ---- re-check ---------------------------------------------------------------------------


def recheck_reason(item, hit, now):
  """Why an albums-table item should be looked up again, or None. See the module docstring."""
  stored_id, stored_year = item.get("rg_mbid"), item.get("year")
  if hit is not None and hit.trusted:
    differs = (hit.id, int(hit.year)) != (stored_id, stored_year)
    if differs and (stored_id is None or stored_id in hit.candidate_ids):
      return "changed"
    return None
  if stored_year is None and now - (item.get("looked_up_at") or 0) > MISS_RECHECK_AGE_S:
    return "stale_miss"
  return None


def recheck(deps, load_shard, now):
  table, queue = deps.env["ALBUMS_TABLE"], deps.env["MB_COLD_URL"]
  counts = {"rechecked_items": 0, "rechecks_changed": 0, "rechecks_stale_miss": 0, "rechecks_capped": 0}
  pending = []
  start = None
  while True:
    page = deps.ddb.scan(TableName=table, **({"ExclusiveStartKey": start} if start else {}))
    for raw in page.get("Items", []):
      item = from_item(raw)
      counts["rechecked_items"] += 1
      if not item.get("artist") or not item.get("album"):
        continue
      reason = recheck_reason(item, ri.lookup(item["artist"], item["album"], load_shard), now)
      if not reason:
        continue
      if len(pending) >= MAX_RECHECKS:
        counts["rechecks_capped"] += 1
        continue
      counts[f"rechecks_{reason}"] += 1
      pending.append(recheck_message(item))
    start = page.get("LastEvaluatedKey")
    if not start:
      break
  send(deps.sqs, queue, pending)
  if counts["rechecks_capped"]:
    log.warning(
      "re-check capped at %d; %d more left for the next build", MAX_RECHECKS, counts["rechecks_capped"]
    )
  return counts


def recheck_message(item):
  return {
    "artist_key": item["artist_key"],
    "title_key": item["title_key"],
    "artist": item["artist"],
    "album": item["album"],
    "mbid": item.get("lastfm_mbid") or "",
    "recheck": True,
  }


def send(sqs, queue_url, messages):
  for i in range(0, len(messages), SEND_BATCH):
    batch = messages[i : i + SEND_BATCH]
    entries = [{"Id": str(j), "MessageBody": json.dumps(m)} for j, m in enumerate(batch)]
    out = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
    if out.get("Failed"):
      # Not fatal: the next build finds the same items and queues them again.
      log.warning("could not queue %d re-checks", len(out["Failed"]))


# ---- metrics ----------------------------------------------------------------------------


def emit(deps, summary, elapsed):
  """One CloudWatch Embedded Metric Format line, by Environment, as the Lambdas log."""
  values = {
    "IndexBuildSeconds": (round(elapsed, 1), "Seconds"),
    "IndexBuildSkipped": (1 if summary.get("skipped") else 0, "Count"),
    "IndexDumpAgeHours": (ri.dump_age_hours(summary.get("dump", ""), deps.clock()) or 0, "None"),
  }
  if not summary.get("skipped"):
    values |= {
      "IndexRecords": (summary.get("records", 0), "Count"),
      "IndexTitles": (summary.get("titles", 0), "Count"),
      "RechecksQueued": (summary.get("rechecks_changed", 0) + summary.get("rechecks_stale_miss", 0), "Count"),
    }
  line = {
    "_aws": {
      "Timestamp": int(deps.clock() * 1000),
      "CloudWatchMetrics": [
        {
          "Namespace": NAMESPACE,
          "Dimensions": [["Environment"]],
          "Metrics": [{"Name": n, "Unit": u} for n, (_, u) in values.items()],
        }
      ],
    },
    "Environment": deps.env.get("AOTY_ENV", ""),
    **{n: v for n, (v, _) in values.items()},
    **{k: v for k, v in summary.items() if k not in values},
  }
  print(json.dumps(line, separators=(",", ":")), flush=True)


def main():
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
  run(Deps.from_env())


if __name__ == "__main__":
  main()
