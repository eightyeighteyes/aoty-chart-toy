"""Rebuild every chart in an env (AOTY-82): users with a chart, then every group.

  .venv/bin/python tools/rebuild_charts.py --env staging --dry-run   # counts only
  .venv/bin/python tools/rebuild_charts.py --env prod

For a change to the ranking rules: charts only rebuild after a sync (cooldown-gated) or a pending
poke, so users who don't visit would keep the old chart. A user event without requested_at
forces a build. A forced member build doesn't rebuild that member's groups (a group build after
their last sync already read them, AOTY-55), so each group gets its own event.

Invokes are synchronous and one at a time: the chart Lambda has reserved concurrency 1, and async
events left waiting behind each other could expire after an hour. A throttled invoke (a real build
was running) is retried. Output names users by a hash only. Admin credentials (aws CLI).
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REGION = "us-east-1"
THROTTLE_RETRIES = 10
THROTTLE_WAIT_S = 15


def aws(args):
  out = subprocess.run(
    ["aws", *args, "--region", REGION, "--output", "json"], check=True, capture_output=True, text=True
  ).stdout
  return json.loads(out) if out.strip() else None


def _tag(name):
  return hashlib.sha256(name.encode()).hexdigest()[:10]  # as the Lambdas' logs


def users_with_charts(env):
  out = aws(["dynamodb", "scan", "--table-name", f"kamp-aoty-{env}-users",
             "--filter-expression", "attribute_exists(chart_built_at)",
             "--projection-expression", "username"])  # fmt: skip
  return sorted(it["username"]["S"] for it in out["Items"])


def groups(env):
  out = aws(["dynamodb", "scan", "--table-name", f"kamp-aoty-{env}-groups",
             "--filter-expression", "sk = :m AND begins_with(pk, :g)",
             "--expression-attribute-values", json.dumps({":m": {"S": "meta"}, ":g": {"S": "group#"}}),
             "--projection-expression", "pk"])  # fmt: skip
  return sorted(it["pk"]["S"].removeprefix("group#") for it in out["Items"])


def invoke(env, payload):
  """One synchronous chart run; returns its totals ({"built", "skipped", "failed", ...})."""
  with tempfile.TemporaryDirectory() as tmp:
    result = Path(tmp) / "out.json"
    for attempt in range(THROTTLE_RETRIES):
      try:
        meta = aws(["lambda", "invoke", "--function-name", f"kamp-aoty-{env}-chart",
                    "--invocation-type", "RequestResponse", "--cli-read-timeout", "910",
                    "--cli-binary-format", "raw-in-base64-out", "--payload", json.dumps(payload),
                    str(result)])  # fmt: skip
      except subprocess.CalledProcessError as e:
        if "TooManyRequests" in (e.stderr or "") and attempt < THROTTLE_RETRIES - 1:
          time.sleep(THROTTLE_WAIT_S)
          continue
        raise
      if meta.get("FunctionError"):
        return {"failed": 1}
      return json.loads(result.read_text() or "{}")
  raise AssertionError("unreachable")


def main():
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--env", required=True, choices=["sandbox", "staging", "prod"])
  ap.add_argument("--dry-run", action="store_true", help="count what would be rebuilt; invoke nothing")
  args = ap.parse_args()

  users, slugs = users_with_charts(args.env), groups(args.env)
  print(f"{args.env}: {len(users)} users with a chart, {len(slugs)} groups")
  if args.dry_run:
    return 0

  totals = {"built": 0, "skipped": 0, "failed": 0}
  targets = [("user", u, {"username": u}) for u in users] + [("group", g, {"group": g}) for g in slugs]
  for i, (kind, name, payload) in enumerate(targets, 1):
    started = time.monotonic()
    out = invoke(args.env, payload)
    for k in totals:
      totals[k] += int(out.get(k) or 0)
    label = _tag(name) if kind == "user" else name  # group slugs are public; usernames never printed
    print(f"  {i}/{len(targets)} {kind} {label}: {json.dumps(out)} ({time.monotonic() - started:.0f} s)")
  print(f"done: {json.dumps(totals)}")
  return 1 if totals["failed"] else 0


if __name__ == "__main__":
  sys.exit(main())
