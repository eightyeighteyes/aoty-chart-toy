"""scripts/sandbox_reset.py against a fake aws CLI: the sandbox-only guard and the delete paths."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sandbox_reset.py"
ACCT = "424416271636"
Q = f"https://sqs.us-east-1.amazonaws.com/{ACCT}/"


@pytest.fixture(scope="module")
def sr():
  spec = importlib.util.spec_from_file_location("sandbox_reset", SCRIPT)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def stack(env="sandbox"):
  p = f"kamp-aoty-{env}-"
  return {
    "environment": env,
    "function_names": {"api": f"{p}api", "sync": f"{p}sync", "mb": f"{p}mb"},
    "queue_urls": {"sync": f"{Q}{p}sync.fifo", "mb-hot": f"{Q}{p}mb-hot", "mb-cold": f"{Q}{p}mb-cold"},
    "dlq_urls": {
      "sync": f"{Q}{p}sync-dlq.fifo",
      "mb-hot": f"{Q}{p}mb-hot-dlq",
      "mb-cold": f"{Q}{p}mb-cold-dlq",
    },
    "store_bucket_name": f"{p}store",
    "site_bucket_name": f"{p}site",
    "table_names": {"users": f"{p}users", "albums": f"{p}albums"},
  }


class FakeAws:
  """Records every call; answers from in-memory buckets, tables and mappings."""

  def __init__(self, versions=None, items=None, unprocessed_once=False, fail_on=None):
    self.calls = []
    self.versions = versions or {}  # bucket -> [(key, version)]
    self.items = items or {}  # table -> [key dict]
    self.state = {"uuid-1": "Enabled"}
    self.unprocessed_once = unprocessed_once
    self.fail_on = fail_on

  def ops(self, name):
    return [c for c in self.calls if c[1] == name]

  def __call__(self, args):
    self.calls.append(args)
    op = args[1]
    if op == self.fail_on:
      raise RuntimeError(f"boom in {op}")
    arg = dict(zip(args[2::2], args[3::2], strict=False))
    if op == "list-event-source-mappings":
      return {"EventSourceMappings": [{"UUID": u} for u in self.state]}
    if op == "update-event-source-mapping":
      self.state[args[3]] = "Enabled" if "--enabled" in args else "Disabled"
      return {}
    if op == "get-event-source-mapping":
      return {"State": self.state[arg["--uuid"]]}
    if op == "purge-queue":
      return None
    if op == "list-object-versions":
      vs = self.versions.get(arg["--bucket"], [])
      return {"Versions": [{"Key": k, "VersionId": v} for k, v in vs]}
    if op == "delete-objects":
      gone = {(o["Key"], o["VersionId"]) for o in json.loads(arg["--delete"])["Objects"]}
      self.versions[arg["--bucket"]] = [kv for kv in self.versions[arg["--bucket"]] if kv not in gone]
      return {}
    if op == "describe-table":
      return {"Table": {"KeySchema": [{"AttributeName": "username"}]}}
    if op == "scan":
      return {"Items": list(self.items.get(arg["--table-name"], []))}
    if op == "batch-write-item":
      req = json.loads(arg["--request-items"])
      ((table, reqs),) = req.items()
      unprocessed = []
      if self.unprocessed_once:
        self.unprocessed_once = False
        reqs, unprocessed = reqs[:-1], reqs[-1:]
      done = [r["DeleteRequest"]["Key"] for r in reqs]
      self.items[table] = [k for k in self.items[table] if k not in done]
      return {"UnprocessedItems": {table: unprocessed} if unprocessed else {}}
    raise AssertionError(f"unexpected call {args}")


def keys(n):
  return [{"username": {"S": f"u{i}"}} for i in range(n)]


# ---- guard ---------------------------------------------------------------------------------


def test_guard_accepts_sandbox(sr):
  r = sr.guard(stack(), ACCT)
  assert r["sync_function"] == "kamp-aoty-sandbox-sync"
  assert len(r["queues"]) == 6


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_guard_refuses_other_envs(sr, env):
  with pytest.raises(sr.RefuseError, match="not 'sandbox'"):
    sr.guard(stack(env), ACCT)


def test_guard_refuses_a_sandbox_label_on_staging_names(sr):
  s = stack("staging")
  s["environment"] = "sandbox"
  with pytest.raises(sr.RefuseError, match="not sandbox names"):
    sr.guard(s, ACCT)


def test_guard_refuses_one_stray_name(sr):
  s = stack()
  s["table_names"]["albums"] = "kamp-aoty-staging-albums"
  with pytest.raises(sr.RefuseError, match="kamp-aoty-staging-albums"):
    sr.guard(s, ACCT)


def test_guard_refuses_wrong_account(sr):
  with pytest.raises(sr.RefuseError, match="account"):
    sr.guard(stack(), "111111111111")


def test_guard_refuses_queue_in_another_account(sr):
  s = stack()
  s["dlq_urls"]["mb-hot"] = "https://sqs.us-east-1.amazonaws.com/111111111111/kamp-aoty-sandbox-mb-hot-dlq"
  with pytest.raises(sr.RefuseError, match="queue outside"):
    sr.guard(s, ACCT)


def test_refusal_touches_nothing(sr):
  aws = FakeAws()
  with pytest.raises(sr.RefuseError):
    sr.reset(aws, stack("staging"), ACCT, dry_run=False, log=lambda *_: None)
  assert aws.calls == []


# ---- delete paths ----------------------------------------------------------------------------


def test_dry_run_only_reads(sr):
  aws = FakeAws(
    versions={"kamp-aoty-sandbox-store": [("a", "1")]}, items={"kamp-aoty-sandbox-users": keys(3)}
  )
  sr.reset(aws, stack(), ACCT, dry_run=True, log=lambda *_: None)
  writes = {"update-event-source-mapping", "purge-queue", "delete-objects", "batch-write-item"}
  assert not [c for c in aws.calls if c[1] in writes]


def test_bucket_versions_are_deleted_in_chunks_of_1000(sr):
  versions = [(f"k{i}", f"v{j}") for i in range(1200) for j in range(2)]  # 2,400 versions
  aws = FakeAws(versions={"b": list(versions)})
  assert sr.empty_bucket(aws, "b", dry_run=False) == 2400
  assert [len(json.loads(c[5])["Objects"]) for c in aws.ops("delete-objects")] == [1000, 1000, 400]
  assert aws.versions["b"] == []


def test_table_deletes_batch_by_25_and_retry_unprocessed(sr):
  aws = FakeAws(items={"t": keys(30)}, unprocessed_once=True)
  assert sr.empty_table(aws, "t", dry_run=False, sleep=lambda _: None) == 30
  sizes = [len(json.loads(c[3])["t"]) for c in aws.ops("batch-write-item")]
  assert sizes == [25, 1, 5]  # the retry resends only the unprocessed item
  assert aws.items["t"] == []


def test_full_reset_keeps_albums_when_asked(sr):
  aws = FakeAws(
    versions={"kamp-aoty-sandbox-store": [("a", "1")], "kamp-aoty-sandbox-site": [("b", "2")]},
    items={"kamp-aoty-sandbox-users": keys(2), "kamp-aoty-sandbox-albums": keys(4)},
  )
  sr.reset(aws, stack(), ACCT, dry_run=False, keep_albums=True, log=lambda *_: None, sleep=lambda _: None)
  assert aws.items == {"kamp-aoty-sandbox-users": [], "kamp-aoty-sandbox-albums": keys(4)}
  assert all(v == [] for v in aws.versions.values())
  assert len(aws.ops("purge-queue")) == 6
  assert aws.state == {"uuid-1": "Enabled"}
  # Disabled before anything was deleted.
  first_delete = next(i for i, c in enumerate(aws.calls) if c[1] == "purge-queue")
  assert "--no-enabled" in aws.ops("update-event-source-mapping")[0]
  assert aws.calls.index(aws.ops("update-event-source-mapping")[0]) < first_delete


def test_full_reset_keeps_the_release_index_when_asked(sr):
  store = [("users/u/2026/weeks.json.gz", "1"), ("index/current.json", "2"), ("index/d/abc.json.gz", "3"),
           ("code/index.zip", "4")]  # fmt: skip
  aws = FakeAws(
    versions={"kamp-aoty-sandbox-store": list(store), "kamp-aoty-sandbox-site": [("index/x", "5")]},
    items={"kamp-aoty-sandbox-users": [], "kamp-aoty-sandbox-albums": []},
  )
  sr.reset(aws, stack(), ACCT, dry_run=False, keep_index=True, log=lambda *_: None, sleep=lambda _: None)
  assert aws.versions["kamp-aoty-sandbox-store"] == store[1:]
  assert aws.versions["kamp-aoty-sandbox-site"] == []  # only the store bucket keeps index/


def test_mapping_is_re_enabled_after_a_failure(sr):
  aws = FakeAws(fail_on="delete-objects", versions={"kamp-aoty-sandbox-store": [("a", "1")]})
  with pytest.raises(RuntimeError, match="boom"):
    sr.reset(aws, stack(), ACCT, dry_run=False, log=lambda *_: None, sleep=lambda _: None)
  assert aws.state == {"uuid-1": "Enabled"}


def test_purge_cooldown_is_skipped_not_fatal(sr):
  def aws(args):
    if args[1] == "purge-queue" and args[-1].endswith("sync.fifo"):
      raise subprocess.CalledProcessError(254, args, stderr="An error occurred (PurgeQueueInProgress)")

  urls = [f"{Q}kamp-aoty-sandbox-sync.fifo", f"{Q}kamp-aoty-sandbox-mb-hot"]
  assert sr.purge_queues(aws, urls) == urls[:1]
