"""AOTY-26 load test: synthetic new users against sandbox with Last.fm faked.

  .venv/bin/python tools/load_test.py --users 300            # about 15 min with the default rates
  .venv/bin/python tools/load_test.py --users 20 --watch 5   # a quick dry run
  .venv/bin/python scripts/sandbox_reset.py --yes --keep-albums   # afterwards

What it checks: the queue grows while sync concurrency, Last.fm's request rate and spend stay flat.

  1. Refuses anything that isn't sandbox (scripts/sandbox_reset.guard: names, account, region).
  2. Turns the fake on: LASTFM_FAKE=1 on sandbox's api, sync, chart and mb functions
     (common/fakelastfm.py, which only answers in sandbox), and raises the per-IP and new-user
     queue limits in SSM, since every POST comes from this one machine.
  3. POSTs sync for loadtest-0001.. at --rate per second (under the POST route's throttle) and
     polls a few users' status, recording their place in line.
  4. Samples the sync queue's depth every 30 s until it drains or --watch minutes pass.
  5. Reads CloudWatch per minute: sync ConcurrentExecutions (max), FakeLastfmCalls (the fake's EMF
     count, all functions), sync invocations and duration, chart throttles, the oldest message's
     age. Writes data/load_test/run-<ts>/report.md.
  6. Always (in `finally`) removes LASTFM_FAKE and puts the SSM values back as they were.

Needs admin credentials (aws CLI) and an initialised infra/envs/sandbox. Run nothing else
against sandbox meanwhile: the mb lane looks up the fake's 30 made-up albums once.
"""

import argparse
import datetime as dt
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import sandbox_reset as sb  # noqa: E402

FAKED = ("api", "sync", "chart", "mb")
RAISED = {"new_users_per_ip_hour": "100000", "max_queue_for_new": "100000"}
SAMPLE_S = 30


# ---- pure parts (tests/test_load_test.py) -------------------------------------------------


def with_fake(variables, on):
  """A function's environment variables with LASTFM_FAKE set or removed."""
  out = {k: v for k, v in (variables or {}).items() if k != "LASTFM_FAKE"}
  if on:
    out["LASTFM_FAKE"] = "1"
  return out


def usernames(n, prefix="loadtest-"):
  return [f"{prefix}{i:04d}" for i in range(1, n + 1)]


def summarize(minutes):
  """Headline numbers from per-minute rows: {minute: {metric: value}}."""
  def col(name):
    return [row.get(name) or 0 for row in minutes.values()]

  calls = [c for c in col("lastfm_calls") if c]
  return {
    "minutes": len(minutes),
    "peak_sync_concurrency": max(col("sync_concurrency"), default=0),
    "peak_lastfm_per_s": round(max(calls, default=0) / 60, 2),
    "mean_lastfm_per_s": round(sum(calls) / len(calls) / 60, 2) if calls else 0,
    "sync_invocations": sum(col("sync_invocations")),
    "sync_gb_s": round(sum(col("sync_duration_ms")) / 1000 * 0.5, 1),  # 512 MB
    "chart_throttles": sum(col("chart_throttles")),
    "peak_oldest_message_s": max(col("oldest_age_s"), default=0),
  }


def report(stack, args, posts, depths, places, minutes, started, ended):
  s = summarize(minutes)
  codes = {}
  for c in posts.values():
    codes[c] = codes.get(c, 0) + 1
  lines = [
    f"# Load test, {started:%Y-%m-%d %H:%M} UTC",
    "",
    f"{args.users} synthetic users at {args.rate}/s against `{stack['environment']}`, Last.fm faked. "
    f"Ran {(ended - started).seconds // 60} min.",
    "",
    "## Summary",
    "",
    "| | |",
    "|---|---|",
    f"| POST answers | {', '.join(f'{k}: {v}' for k, v in sorted(codes.items()))} |",
    f"| Peak sync queue depth | {max((d for _, d in depths), default=0)} |",
    f"| Peak sync concurrency | {s['peak_sync_concurrency']} |",
    f"| Last.fm calls/s, peak minute / mean | {s['peak_lastfm_per_s']} / {s['mean_lastfm_per_s']} |",
    f"| Sync invocations, GB-s | {s['sync_invocations']}, {s['sync_gb_s']} |",
    f"| Oldest message, peak | {s['peak_oldest_message_s']} s |",
    f"| Chart builder throttles | {s['chart_throttles']} |",
    "",
    "## Per minute",
    "",
    "| Minute (UTC) | Sync concurrency | Last.fm calls | Sync invocations | Oldest message (s) | Chart throttles |",
    "|---|---|---|---|---|---|",
  ]
  for minute, row in sorted(minutes.items()):
    lines.append(f"| {minute:%H:%M} | {row.get('sync_concurrency', 0)} | {row.get('lastfm_calls', 0)} | "
                 f"{row.get('sync_invocations', 0)} | {row.get('oldest_age_s', 0)} | {row.get('chart_throttles', 0)} |")
  lines += ["", "## Queue depth", "", "| Time (UTC) | Messages |", "|---|---|"]
  lines += [f"| {t:%H:%M:%S} | {d} |" for t, d in depths]
  lines += ["", "## Places in line (sampled users)", "", "| Time (UTC) | User | Stage | Position |", "|---|---|---|---|"]
  lines += [f"| {t:%H:%M:%S} | {u} | {st} | {p if p is not None else ''} |" for t, u, st, p in places]
  return "\n".join(lines) + "\n"


# ---- AWS and HTTP -------------------------------------------------------------------------


def set_fake(stack, on):
  for key in FAKED:
    fn = stack["function_names"][key]
    conf = sb.run_aws(["lambda", "get-function-configuration", "--function-name", fn])
    env = with_fake((conf.get("Environment") or {}).get("Variables"), on)
    sb.run_aws(["lambda", "update-function-configuration", "--function-name", fn,
                "--environment", json.dumps({"Variables": env})])
    sb.run_aws(["lambda", "wait", "function-updated", "--function-name", fn])
    print(f"{fn}: LASTFM_FAKE {'on' if on else 'off'}")


def ssm_name(stack, name):
  return f"/kamp-aoty/{stack['environment']}/{name}"


def read_params(stack):
  out = sb.run_aws(["ssm", "get-parameters", "--names", *(ssm_name(stack, n) for n in RAISED)])
  return {p["Name"].rsplit("/", 1)[-1]: p["Value"] for p in out.get("Parameters", [])}


def restore_plan(previous):
  """(name, value or None to delete) per raised parameter, to put things back as they were."""
  return [(name, previous.get(name)) for name in RAISED]


def write_params(stack, pairs):
  for name, value in pairs:
    if value is not None:
      sb.run_aws(["ssm", "put-parameter", "--name", ssm_name(stack, name), "--type", "String",
                  "--value", value, "--overwrite"])
      continue
    try:
      sb.run_aws(["ssm", "delete-parameter", "--name", ssm_name(stack, name)])
    except Exception as e:  # already gone
      print(f"{name}: {e}")


def http(method, url, body=None):
  req = urllib.request.Request(url, method=method, data=body,
                               headers={"content-type": "application/json", "user-agent": "aoty-load-test/1"})
  try:
    with urllib.request.urlopen(req, timeout=30) as r:
      return r.status, json.loads(r.read() or b"{}")
  except urllib.error.HTTPError as e:
    try:
      return e.code, json.loads(e.read() or b"{}")
    except ValueError:
      return e.code, {}


def queue_depth(stack):
  out = sb.run_aws(["sqs", "get-queue-attributes", "--queue-url", stack["queue_urls"]["sync"],
                    "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])
  a = out["Attributes"]
  return int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])


def metrics(stack, started, ended):
  """{minute: {name: value}} from CloudWatch."""
  fns, sync_q = stack["function_names"], stack["queue_urls"]["sync"].rsplit("/", 1)[-1]

  def q(id_, ns, metric, dims, stat):
    return {"Id": id_, "MetricStat": {"Metric": {"Namespace": ns, "MetricName": metric,
            "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]}, "Period": 60, "Stat": stat}}

  queries = [
    q("sync_concurrency", "AWS/Lambda", "ConcurrentExecutions", {"FunctionName": fns["sync"]}, "Maximum"),
    q("sync_invocations", "AWS/Lambda", "Invocations", {"FunctionName": fns["sync"]}, "Sum"),
    q("sync_duration_ms", "AWS/Lambda", "Duration", {"FunctionName": fns["sync"]}, "Sum"),
    q("chart_throttles", "AWS/Lambda", "Throttles", {"FunctionName": fns["chart"]}, "Sum"),
    q("oldest_age_s", "AWS/SQS", "ApproximateAgeOfOldestMessage", {"QueueName": sync_q}, "Maximum"),
    q("lastfm_calls", "kamp-aoty", "FakeLastfmCalls", {"Environment": stack["environment"]}, "Sum"),
  ]
  out = sb.run_aws(["cloudwatch", "get-metric-data", "--metric-data-queries", json.dumps(queries),
                    "--start-time", started.isoformat(), "--end-time", (ended + dt.timedelta(minutes=2)).isoformat()])
  minutes = {}
  for r in out.get("MetricDataResults", []):
    for t, v in zip(r["Timestamps"], r["Values"]):
      minute = dt.datetime.fromisoformat(t).astimezone(dt.UTC).replace(second=0, microsecond=0)
      minutes.setdefault(minute, {})[r["Id"]] = int(v)
  return minutes


# ---- the run ------------------------------------------------------------------------------


def run(args):
  stack = sb.stack_output()
  account = sb.run_aws(["sts", "get-caller-identity"])["Account"]
  sb.guard(stack, account)
  api = f"{stack['api_endpoint']}{stack['base_path']}api/users/"
  names = usernames(args.users)
  watched = random.Random(26).sample(names, min(5, len(names)))
  posts, depths, places = {}, [], []
  previous = read_params(stack)
  started = dt.datetime.now(dt.UTC)
  try:
    write_params(stack, RAISED.items())
    set_fake(stack, True)
    print(f"POSTing {len(names)} users at {args.rate}/s")
    for i, name in enumerate(names):
      posts[name], _ = http("POST", f"{api}{name}/sync", b"{}")
      time.sleep(1 / args.rate)
      if i % 50 == 49:
        print(f"  {i + 1} posted; answers so far {sorted(set(posts.values()))}")
    deadline = time.time() + args.watch * 60
    while time.time() < deadline:
      now = dt.datetime.now(dt.UTC)
      depth = queue_depth(stack)
      depths.append((now, depth))
      for name in watched:
        code, s = http("GET", f"{api}{name}/status")
        places.append((now, name, s.get("stage", code), (s.get("queue") or {}).get("position")))
      print(f"{now:%H:%M:%S} queue {depth}")
      if depth == 0:
        break
      time.sleep(SAMPLE_S)
  finally:
    try:
      set_fake(stack, False)
    finally:
      write_params(stack, restore_plan(previous))
      print("fake off, SSM restored")
  ended = dt.datetime.now(dt.UTC)
  print("waiting 3 min for CloudWatch's last minutes")
  time.sleep(180)
  out = REPO / "data" / "load_test" / f"run-{started:%Y%m%dT%H%M%S}"
  out.mkdir(parents=True, exist_ok=True)
  (out / "report.md").write_text(report(stack, args, posts, depths, places, metrics(stack, started, ended),
                                        started, ended))
  print(f"wrote {out / 'report.md'}")


def main():
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--users", type=int, default=300)
  ap.add_argument("--rate", type=float, default=1.5, help="POSTs per second (the POST route allows 2)")
  ap.add_argument("--watch", type=float, default=30, help="minutes to watch the queue drain")
  run(ap.parse_args())


if __name__ == "__main__":
  main()
