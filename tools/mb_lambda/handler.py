"""AWS Lambda entry point for the MusicBrainz benchmark (AOTY-12).

Deployed and invoked by tools/mb_lambda.py, which bundles this file next to aoty.py,
mb_cold_run.py, data/mb_bench/candidates.json (artist/album pairs only) and requests.

Event: {"offset": 0, "count": 250, "seed": 2026, "chunk": 25, "arms": "L"}
Returns the run folder's files (meta.json, mb_requests.csv, cache.json), each gzipped
and base64-encoded, because the response is the only channel back.
"""
import base64
import gzip
import os
import time
from pathlib import Path

# Lambda runs in UTC; match the laptop's clock so run names and timestamps line up.
os.environ["TZ"] = "America/New_York"
time.tzset()

import requests  # noqa: E402

import aoty  # noqa: E402
import mb_cold_run  # noqa: E402

HERE = Path(__file__).resolve().parent
CANDIDATES = HERE / "candidates.json"
BENCH_DIR = Path("/tmp/mb_bench")  # the only writable path on Lambda
# Stop starting chunks this close to the timeout: a 25-album chunk takes ~50 s at
# 0.5 albums/s, plus its single retries.
STOP_MARGIN_MS = 150_000

_INVOCATIONS = 0  # module globals survive in a warm container


def outbound_ip():
  try:
    return requests.get("https://checkip.amazonaws.com", timeout=5).text.strip()
  except requests.RequestException as e:
    return f"unknown ({type(e).__name__})"


def handler(event, context):
  global _INVOCATIONS
  _INVOCATIONS += 1
  aoty.reset_run_state()
  ip = outbound_ip()
  print(f"Outbound IP {ip}, invocation {_INVOCATIONS} of this container")
  run_dir = mb_cold_run.run(
      mb_cold_run.parse_arms(event.get("arms", "L")),
      bench_dir=BENCH_DIR, candidates_file=CANDIDATES,
      seed=event.get("seed", 2026), chunk=event.get("chunk", 25),
      offset=event.get("offset", 0), limit=event.get("count", 250),
      deadline=lambda: context.get_remaining_time_in_millis() < STOP_MARGIN_MS,
      extra_meta={"where": "lambda", "outbound_ip": ip, "region": os.environ.get("AWS_REGION"),
                  "container_invocation": _INVOCATIONS, "request_id": context.aws_request_id})
  return {
      "run": run_dir.name,
      "files": {p.name: base64.b64encode(gzip.compress(p.read_bytes())).decode("ascii")
                for p in sorted(run_dir.iterdir()) if p.is_file()},
  }
