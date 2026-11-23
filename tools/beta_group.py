"""The beta's hand-made group (AOTY-50): create it, queue its members' syncs, build its chart.

  .venv/bin/python tools/beta_group.py create --env staging --name "the server"   # members from .env
  .venv/bin/python tools/beta_group.py sync   --env staging --id <id>             # queue every member
  .venv/bin/python tools/beta_group.py build  --env staging --id <id>             # rebuild the chart
  .venv/bin/python tools/beta_group.py show   --env staging --id <id>             # the chart's counts

A group is s3://kamp-aoty-<env>-store/groups/<id>/group.json {"name", "members"}: private,
like the stores. Its chart is public at kamp.fm<base>data/groups/<id>/chart.json, and the id
(128 random bits, base32) is the only thing that finds it, so share the link, not the id
pattern. Members come from .env's LASTFM_USERS unless --members is given, so no username is
ever in the repo. Admin credentials (aws CLI). Output names members by count only.

Members' syncs go through the sync queue directly (the message the README's "Running a sync by
hand" sends), so they skip the api's cooldown and new-user limits: a beta of a few dozen.
"""

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGION = "us-east-1"
BASES = {"prod": "/toys/aoty/", "staging": "/toys/aoty-staging/", "sandbox": "/toys/aoty-sandbox/"}


def aws(args):
  out = subprocess.run(
    ["aws", *args, "--region", REGION, "--output", "json"], check=True, capture_output=True, text=True
  ).stdout
  return json.loads(out) if out.strip() else None


def new_id():
  """26 lowercase base32 characters: what the chart builder's GROUP_ID_RE accepts."""
  return base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=").lower()


def dotenv_members(path):
  for line in path.read_text().splitlines():
    key, _, value = line.partition("=")
    if key.strip() == "LASTFM_USERS":
      return [u.strip().lower() for u in value.strip().strip("'\"").split(",") if u.strip()]
  raise SystemExit(f"no LASTFM_USERS in {path}")


def _put_group(env, group_id, group):
  with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(group, f)
  try:
    aws(["s3", "cp", f.name, f"s3://kamp-aoty-{env}-store/groups/{group_id}/group.json",
         "--content-type", "application/json", "--only-show-errors"])  # fmt: skip
  finally:
    os.unlink(f.name)


def _get_group(env, group_id):
  out = subprocess.run(
    ["aws", "s3", "cp", f"s3://kamp-aoty-{env}-store/groups/{group_id}/group.json", "-", "--region", REGION],
    check=True, capture_output=True, text=True,
  ).stdout  # fmt: skip
  return json.loads(out)


def create(args):
  members = [m.strip().lower() for m in args.members.split(",")] if args.members else dotenv_members(args.dotenv)
  group_id = new_id()
  _put_group(args.env, group_id, {"name": args.name, "members": sorted(set(members))})
  print(f"group {group_id}: {len(set(members))} members")
  print(f"chart: https://kamp.fm{BASES[args.env]}data/groups/{group_id}/chart.json")
  print("next: tools/beta_group.py sync --env", args.env, "--id", group_id)


def sync(args):
  group = _get_group(args.env, args.id)
  url = aws(["sqs", "get-queue-url", "--queue-name", f"kamp-aoty-{args.env}-sync.fifo"])["QueueUrl"]
  now = int(time.time())
  for m in group["members"]:
    aws(["sqs", "send-message", "--queue-url", url, "--message-group-id", m,
         "--message-deduplication-id", f"{m}-beta-{now}", "--message-body", json.dumps({"username": m})])  # fmt: skip
  print(f"queued {len(group['members'])} syncs; the group chart rebuilds as each finishes")


def build(args):
  payload = json.dumps({"group": args.id})
  aws(["lambda", "invoke", "--function-name", f"kamp-aoty-{args.env}-chart", "--invocation-type", "Event",
       "--cli-binary-format", "raw-in-base64-out", "--payload", payload, os.devnull])  # fmt: skip
  print("build requested")


def show(args):
  key = f"{BASES[args.env].lstrip('/')}data/groups/{args.id}/chart.json"
  out = subprocess.run(
    ["aws", "s3", "cp", f"s3://kamp-aoty-{args.env}-site/{key}", "-", "--region", REGION],
    check=True, capture_output=True, text=True,
  ).stdout  # fmt: skip
  chart = json.loads(out)
  print(f"{chart['name']}: {len(chart['members'])} ready, {chart['waiting']} waiting, "
        f"{chart['unavailable']} unavailable, {chart['pending']} albums undated, "
        f"{len(chart['rankings']['track'])} ranked, built {chart['generated_at']}")  # fmt: skip


def main(argv=None):
  ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  sub = ap.add_subparsers(dest="cmd", required=True)
  for name, fn in (("create", create), ("sync", sync), ("build", build), ("show", show)):
    p = sub.add_parser(name)
    p.set_defaults(fn=fn)
    p.add_argument("--env", choices=sorted(BASES), required=True)
    if name == "create":
      p.add_argument("--name", required=True)
      p.add_argument("--members", help="comma-separated usernames (default: .env's LASTFM_USERS)")
      p.add_argument("--dotenv", type=Path, default=REPO / ".env")
    else:
      p.add_argument("--id", required=True)
  args = ap.parse_args(argv)
  args.fn(args)


if __name__ == "__main__":
  sys.exit(main())
