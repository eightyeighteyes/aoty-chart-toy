"""Groups by hand (AOTY-50 beta, on the groups table since AOTY-48): create, migrate, sync, build.

  .venv/bin/python tools/beta_group.py create  --env staging --name "The Server" --owner alice
  .venv/bin/python tools/beta_group.py migrate --env staging --owner alice   # S3 beta files -> table
  .venv/bin/python tools/beta_group.py sync    --env staging --id the-server  # queue every member
  .venv/bin/python tools/beta_group.py build   --env staging --id the-server  # rebuild the chart
  .venv/bin/python tools/beta_group.py show    --env staging --id the-server  # the chart's counts
  .venv/bin/python tools/beta_group.py delete  --env staging --id the-server  # group and chart

A group is the groups table's partition group#<slug> (lambdas/common/groups.py), the same items
the api writes, so a group made here can be edited by its owner once signed in. Its page is
kamp.fm<base>group/<slug>. create refuses a slug that's taken, and more than 25 members.
Members come from .env's LASTFM_USERS unless --members is given, so no username is ever in the
repo. Admin credentials (aws CLI). Output names members by count only.

migrate copies the beta's S3 files (s3://kamp-aoty-<env>-store/groups/<slug>/group.json, before
AOTY-48) into the table under the same slug, with --owner as the owner; groups already in the
table are skipped, and the S3 files are left alone (remove them once the charts check out).
--dry-run shows what it would do. A beta group over 25 members is refused: trim it first.

Members' syncs go through the sync queue directly (the message the README's "Running a sync by
hand" sends), so they skip the api's cooldown and new-user limits: a beta of a few dozen.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lambdas"))

from common import groups as grp  # noqa: E402

REGION = "us-east-1"
BASES = {"prod": "/toys/aoty/", "staging": "/toys/aoty-staging/", "sandbox": "/toys/aoty-sandbox/"}


def aws(args):
  out = subprocess.run(
    ["aws", *args, "--region", REGION, "--output", "json"], check=True, capture_output=True, text=True
  ).stdout
  return json.loads(out) if out.strip() else None


def table(env):
  return f"kamp-aoty-{env}-groups"


def dotenv_members(path):
  for line in path.read_text().splitlines():
    key, _, value = line.partition("=")
    if key.strip() == "LASTFM_USERS":
      return [u.strip().lower() for u in value.strip().strip("'\"").split(",") if u.strip()]
  raise SystemExit(f"no LASTFM_USERS in {path}")


def _s(v):
  return {"N": str(v)} if isinstance(v, int) else {"S": str(v)}


def load(env, slug):
  """(meta, [members]) from the table, or (None, [])."""
  out = aws(["dynamodb", "query", "--table-name", table(env), "--consistent-read",
             "--key-condition-expression", "pk = :pk",
             "--expression-attribute-values", json.dumps({":pk": {"S": grp.pk(slug)}})])  # fmt: skip
  items = [{k: (v.get("S") if "S" in v else int(v["N"])) for k, v in it.items()} for it in out["Items"]]
  meta, members = grp.split_items(items)
  return (meta, members) if meta else (None, [])


def _transact(items):
  with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(items, f)
  try:
    aws(["dynamodb", "transact-write-items", "--transact-items", f"file://{f.name}"])
  finally:
    Path(f.name).unlink()


def put_group(env, slug, name, owner, members):
  """Write a new group (meta + members) in one transaction; refuses a taken slug."""
  members = sorted(set(members))
  if len(members) > grp.MAX_MEMBERS:
    raise SystemExit(f"{slug}: {len(members)} members, over the cap of {grp.MAX_MEMBERS}")
  now = int(time.time())
  meta = {"pk": grp.pk(slug), "sk": grp.META, "name": name, "owner": owner, "member_count": len(members),
          "created_at": now, "updated_at": now, "chart_requested_at": now}  # fmt: skip
  items = [{"Put": {"TableName": table(env), "Item": {k: _s(v) for k, v in meta.items()},
                    "ConditionExpression": "attribute_not_exists(pk)"}}]  # fmt: skip
  for m in members:
    item = {"pk": grp.pk(slug), "sk": grp.member_sk(m), "username": m, "added_at": now}
    items.append({"Put": {"TableName": table(env), "Item": {k: _s(v) for k, v in item.items()}}})
  try:
    _transact(items)
  except subprocess.CalledProcessError as e:
    if "ConditionalCheckFailed" in (e.stderr or ""):
      raise SystemExit(f"group {slug!r} already exists in {env}: pick another --slug, or delete it first") from e
    raise


def poke_chart(env, payload):
  aws(["lambda", "invoke", "--function-name", f"kamp-aoty-{env}-chart", "--invocation-type", "Event",
       "--cli-binary-format", "raw-in-base64-out", "--payload", json.dumps(payload), "/dev/null"])  # fmt: skip


def create(args):
  members = [m.strip().lower() for m in args.members.split(",")] if args.members else dotenv_members(args.dotenv)
  owner = args.owner.strip().lower()
  slug = grp.slug_for(args.name, args.slug)
  if slug is None:
    raise SystemExit("no usable slug: lowercase letters, digits and inner dashes, 1-50 (give --slug)")
  put_group(args.env, slug, grp.clean_name(args.name) or args.name, owner, [owner, *members])
  print(f"group {slug}: {len(set([owner, *members]))} members")
  print(f"page: https://kamp.fm{BASES[args.env]}group/{slug}")
  print("next: tools/beta_group.py sync --env", args.env, "--id", slug)


def migrate(args):
  bucket = f"kamp-aoty-{args.env}-store"
  listing = aws(["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", "groups/"]) or {}
  keys = [o["Key"] for o in listing.get("Contents", []) if o["Key"].endswith("/group.json")]
  if not keys:
    print("no beta group files")
  for key in keys:
    slug = key.split("/")[1]
    if not grp.SLUG_RE.match(slug):
      print(f"{slug}: not a usable slug, skipped")
      continue
    if load(args.env, slug)[0] is not None:
      print(f"{slug}: already in the table, skipped")
      continue
    body = subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{key}", "-", "--region", REGION],
                          check=True, capture_output=True, text=True).stdout  # fmt: skip
    group = json.loads(body)
    members = [str(m).strip().lower() for m in group.get("members", []) if str(m).strip()]
    print(f"{slug}: {len(set(members))} members{' (dry run)' if args.dry_run else ''}")
    if not args.dry_run:
      put_group(args.env, slug, str(group.get("name") or slug), args.owner.strip().lower(), members)
      poke_chart(args.env, {"group": slug})


def delete(args):
  meta, members = load(args.env, args.id)
  if meta is None:
    raise SystemExit(f"no group {args.id!r} in {args.env}")
  items = [{"Delete": {"TableName": table(args.env), "Key": {"pk": _s(grp.pk(args.id)), "sk": _s(sk)}}}
           for sk in [grp.META, *map(grp.member_sk, members)]]  # fmt: skip
  _transact(items)
  poke_chart(args.env, {"group": args.id, "deleted": True})
  print(f"deleted group {args.id}; the chart builder removes its chart")


def sync(args):
  meta, members = load(args.env, args.id)
  if meta is None:
    raise SystemExit(f"no group {args.id!r} in {args.env}")
  url = aws(["sqs", "get-queue-url", "--queue-name", f"kamp-aoty-{args.env}-sync.fifo"])["QueueUrl"]
  now = int(time.time())
  for m in members:
    aws(["sqs", "send-message", "--queue-url", url, "--message-group-id", m,
         "--message-deduplication-id", f"{m}-beta-{now}", "--message-body", json.dumps({"username": m})])  # fmt: skip
  print(f"queued {len(members)} syncs; the group chart rebuilds as each finishes")


def build(args):
  poke_chart(args.env, {"group": args.id})
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
  commands = (("create", create), ("migrate", migrate), ("sync", sync), ("build", build), ("show", show),
              ("delete", delete))  # fmt: skip
  for name, fn in commands:
    p = sub.add_parser(name)
    p.set_defaults(fn=fn)
    p.add_argument("--env", choices=sorted(BASES), required=True)
    if name == "create":
      p.add_argument("--name", required=True, help="shown on the page")
      p.add_argument("--owner", required=True, help="the Last.fm username who may edit it once signed in")
      p.add_argument("--slug", help="the URL: <base>group/<slug> (default: the name, slugified)")
      p.add_argument("--members", help="comma-separated usernames (default: .env's LASTFM_USERS)")
      p.add_argument("--dotenv", type=Path, default=REPO / ".env")
    elif name == "migrate":
      p.add_argument("--owner", required=True, help="who owns the migrated groups")
      p.add_argument("--dry-run", action="store_true")
    else:
      p.add_argument("--id", required=True)
  args = ap.parse_args(argv)
  args.fn(args)


if __name__ == "__main__":
  sys.exit(main())
