"""Wipe the sandbox environment's data, keeping its infrastructure (AOTY-33).

  .venv/bin/python scripts/sandbox_reset.py               # dry run: what would be deleted
  .venv/bin/python scripts/sandbox_reset.py --yes         # do it
  .venv/bin/python scripts/sandbox_reset.py --yes --keep-albums   # keep the release-year cache

Run it with admin credentials (the sandbox deploy role can't read the data). Names come from
`terraform output -json stack` in infra/envs/sandbox, and the script refuses to run unless
the stack says environment "sandbox", every name starts with kamp-aoty-sandbox-, and the
caller is in the expected account. It never touches staging or prod.

Steps:
  1. Disable the sync queue's event source mapping, so no new sync starts mid-reset.
  2. Purge the queues and their DLQs (SQS allows one purge per queue per 60 s).
  3. Delete every object version and delete marker in the store and site buckets.
  4. Delete every item in the users table, and the albums table unless --keep-albums.
  5. Re-enable the mapping, even if a step failed.
A sync that was already running when the mapping was disabled can still write afterwards
(the sync timeout is 15 min). If that matters, run the reset again after it finishes.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = REPO / "infra" / "envs" / "sandbox"
ACCOUNT = "424416271636"
REGION = "us-east-1"
PREFIX = "kamp-aoty-sandbox-"
DELETE_OBJECTS_MAX = 1000  # S3 DeleteObjects limit per call
BATCH_WRITE_MAX = 25  # DynamoDB BatchWriteItem limit per call
BATCH_RETRIES = 8


class RefuseError(Exception):
  """The stack doesn't look like sandbox; nothing was touched."""


def run_aws(args):
  """One aws CLI call in the sandbox region. Returns parsed JSON, or None for empty output."""
  out = subprocess.run(
    ["aws", *args, "--region", REGION, "--output", "json"], check=True, capture_output=True, text=True
  ).stdout
  return json.loads(out) if out.strip() else None


def stack_output(root=SANDBOX_ROOT):
  out = subprocess.run(
    ["terraform", f"-chdir={root}", "output", "-json", "stack"], check=True, capture_output=True, text=True
  ).stdout
  return json.loads(out)


def _queue_name(url):
  return url.rstrip("/").rsplit("/", 1)[-1]


def _queue_account(url):
  return url.rstrip("/").rsplit("/", 2)[-2]


def resources(stack):
  """The names the reset touches, from the stack output."""
  queues = list(stack["queue_urls"].values()) + list(stack["dlq_urls"].values())
  return {
    "sync_function": stack["function_names"]["sync"],
    "queues": sorted(queues),
    "buckets": [stack["store_bucket_name"], stack["site_bucket_name"]],
    "tables": dict(stack["table_names"]),
  }


def guard(stack, caller_account):
  """Raise RefuseError unless every name is a sandbox name in the expected account."""
  if stack.get("environment") != "sandbox":
    raise RefuseError(f"stack environment is {stack.get('environment')!r}, not 'sandbox'")
  if caller_account != ACCOUNT:
    raise RefuseError(f"credentials are for account {caller_account}, expected {ACCOUNT}")
  r = resources(stack)
  names = [r["sync_function"], *r["buckets"], *r["tables"].values()]
  names += [_queue_name(u) for u in r["queues"]]
  bad = [n for n in names if not n.startswith(PREFIX)]
  if bad:
    raise RefuseError(f"not sandbox names: {', '.join(bad)}")
  for url in r["queues"]:
    if f"sqs.{REGION}." not in url or _queue_account(url) != ACCOUNT:
      raise RefuseError(f"queue outside {ACCOUNT}/{REGION}: {url}")
  return r


def chunks(items, size):
  for i in range(0, len(items), size):
    yield items[i : i + size]


# ---- steps --------------------------------------------------------------------------------


def mapping_uuids(aws, function_name):
  out = aws(["lambda", "list-event-source-mappings", "--function-name", function_name]) or {}
  return [m["UUID"] for m in out.get("EventSourceMappings", [])]


def set_mappings(aws, uuids, enabled, sleep=time.sleep, timeout=120):
  """Enable or disable the mappings and wait until AWS reports the new state."""
  want = "Enabled" if enabled else "Disabled"
  for uuid in uuids:
    aws(["lambda", "update-event-source-mapping", "--uuid", uuid, "--enabled" if enabled else "--no-enabled"])
  deadline = time.monotonic() + timeout
  for uuid in uuids:
    while (aws(["lambda", "get-event-source-mapping", "--uuid", uuid]) or {}).get("State") != want:
      if time.monotonic() > deadline:
        raise TimeoutError(f"mapping {uuid} not {want} after {timeout} s")
      sleep(2)


def purge_queues(aws, urls):
  """Purge each queue. Returns the URLs SQS refused (purged in the last 60 s)."""
  skipped = []
  for url in urls:
    try:
      aws(["sqs", "purge-queue", "--queue-url", url])
    except subprocess.CalledProcessError as e:
      if "PurgeQueueInProgress" not in (e.stderr or ""):
        raise
      skipped.append(url)
  return skipped


def object_versions(aws, bucket):
  """Every version and delete marker, as DeleteObjects identifiers. The CLI pages for us."""
  out = aws(["s3api", "list-object-versions", "--bucket", bucket]) or {}
  return [
    {"Key": v["Key"], "VersionId": v["VersionId"]}
    for v in (out.get("Versions") or []) + (out.get("DeleteMarkers") or [])
  ]


def empty_bucket(aws, bucket, dry_run):
  objects = object_versions(aws, bucket)
  if not dry_run:
    for batch in chunks(objects, DELETE_OBJECTS_MAX):
      out = aws(
        [
          "s3api",
          "delete-objects",
          "--bucket",
          bucket,
          "--delete",
          json.dumps({"Objects": batch, "Quiet": True}),
        ]
      )
      errors = (out or {}).get("Errors") or []
      if errors:
        raise RuntimeError(f"{bucket}: {len(errors)} objects not deleted, e.g. {errors[0]}")
  return len(objects)


def table_keys(aws, table):
  """Every item's primary key. The CLI pages the scan for us."""
  schema = aws(["dynamodb", "describe-table", "--table-name", table])["Table"]["KeySchema"]
  names = [k["AttributeName"] for k in schema]
  attr_names = {f"#k{i}": n for i, n in enumerate(names)}
  out = (
    aws(
      [
        "dynamodb",
        "scan",
        "--table-name",
        table,
        "--projection-expression",
        ", ".join(attr_names),
        "--expression-attribute-names",
        json.dumps(attr_names),
      ]
    )
    or {}
  )
  return out.get("Items") or []


def empty_table(aws, table, dry_run, sleep=time.sleep):
  keys = table_keys(aws, table)
  if not dry_run:
    for batch in chunks(keys, BATCH_WRITE_MAX):
      pending = {table: [{"DeleteRequest": {"Key": k}} for k in batch]}
      for attempt in range(BATCH_RETRIES):
        out = aws(["dynamodb", "batch-write-item", "--request-items", json.dumps(pending)]) or {}
        pending = out.get("UnprocessedItems") or {}
        if not pending:
          break
        sleep(min(0.1 * 2**attempt, 5))
      else:
        raise RuntimeError(f"{table}: unprocessed deletes after {BATCH_RETRIES} tries")
  return len(keys)


def reset(aws, stack, caller_account, *, dry_run=True, keep_albums=False, log=print, sleep=time.sleep):
  r = guard(stack, caller_account)
  tables = [t for k, t in sorted(r["tables"].items()) if not (keep_albums and k == "albums")]
  uuids = mapping_uuids(aws, r["sync_function"])
  log(f"{'DRY RUN: ' if dry_run else ''}resetting sandbox (account {caller_account})")

  if dry_run:
    log(f"would disable mappings {uuids} and purge {len(r['queues'])} queues")
    for b in r["buckets"]:
      log(f"{b}: {empty_bucket(aws, b, True)} object versions")
    for t in tables:
      log(f"{t}: {empty_table(aws, t, True)} items")
    return

  set_mappings(aws, uuids, enabled=False, sleep=sleep)
  try:
    skipped = purge_queues(aws, r["queues"])
    log(f"purged {len(r['queues']) - len(skipped)} queues")
    for url in skipped:
      log(f"  skipped {_queue_name(url)}: purged less than 60 s ago")
    for b in r["buckets"]:
      log(f"{b}: deleted {empty_bucket(aws, b, False)} object versions")
    for t in tables:
      log(f"{t}: deleted {empty_table(aws, t, False, sleep=sleep)} items")
  finally:
    set_mappings(aws, uuids, enabled=True, sleep=sleep)
    log("sync mapping re-enabled")


def main(argv=None):
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
  ap.add_argument("--keep-albums", action="store_true", help="keep the albums table (release-year cache)")
  args = ap.parse_args(argv)

  stack = stack_output()
  account = run_aws(["sts", "get-caller-identity"])["Account"]
  try:
    reset(run_aws, stack, account, dry_run=not args.yes, keep_albums=args.keep_albums)
  except RefuseError as e:
    sys.exit(f"refusing: {e}")


if __name__ == "__main__":
  main()
