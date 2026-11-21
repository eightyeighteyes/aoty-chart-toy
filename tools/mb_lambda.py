"""Run the MusicBrainz benchmark from a throwaway AWS Lambda (AOTY-12).

MusicBrainz rate-limits per IP, and a Lambda outside a VPC shares AWS's outbound
addresses with everyone else. This measures whether that costs us, using arm L
(arm E's settings: batch 20, 1.1 s, Retry-After x8) on the frozen candidate list.

  .venv/bin/python tools/mb_lambda.py deploy                  # build + create/update
  .venv/bin/python tools/mb_lambda.py run --offset 0 --count 40   # one slice
  .venv/bin/python tools/mb_lambda.py sweep                   # all 1,536, 250 per slice
  .venv/bin/python tools/mb_lambda.py teardown                # function, role, logs

Each slice comes back into data/mb_bench/run-<ts>-lambda/, so tools/mb_analyze.py reads
it unchanged. For a same-time control, run tools/mb_cold_run.py --arms E --chunk 25
on the laptop alongside a sweep: the same seed gives the same album order.

Uses the aws CLI (no boto3). Invokes are synchronous with CLI retries off and reserved
concurrency 1, so a slow slice is never re-sent while it's still running.
"""
import argparse
import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import aoty  # noqa: E402  (for .env and MB_CONTACT)

REGION = "us-east-1"
FUNCTION = "kamp-aoty-mb-bench"
ROLE = "kamp-aoty-mb-bench-role"
POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
LOG_GROUP = f"/aws/lambda/{FUNCTION}"
TIMEOUT_S = 900
BUILD = REPO / "tools" / "mb_lambda" / "build"
BENCH_DIR = REPO / "data" / "mb_bench"
CANDIDATES = BENCH_DIR / "candidates.json"
TRUST = {"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"}]}


def aws(*args, check=True, retries=True, read_timeout=None):
  """Run an aws CLI command and return its parsed JSON output (or None)."""
  cmd = ["aws", *args, "--region", REGION, "--output", "json"]
  if read_timeout:
    cmd += ["--cli-read-timeout", str(read_timeout), "--cli-connect-timeout", "10"]
  env = {**os.environ, **({} if retries else {"AWS_MAX_ATTEMPTS": "1", "AWS_RETRY_MODE": "standard"})}
  result = subprocess.run(cmd, capture_output=True, text=True, env=env)
  if result.returncode != 0:
    if check:
      raise SystemExit(f"aws {' '.join(args[:2])} failed:\n{result.stderr.strip()}")
    return None
  return json.loads(result.stdout) if result.stdout.strip() else None


def build_zip():
  """Bundle the benchmark code, the candidate list and Linux wheels of requests."""
  if not CANDIDATES.exists():
    raise SystemExit(f"{CANDIDATES} is missing: run tools/mb_cold_run.py once to freeze it")
  shutil.rmtree(BUILD, ignore_errors=True)
  pkg = BUILD / "pkg"
  pkg.mkdir(parents=True)
  # Mac wheels (charset_normalizer has a compiled one) won't load on Lambda.
  subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(pkg),
                  "--platform", "manylinux2014_x86_64", "--implementation", "cp",
                  "--python-version", "3.12", "--only-binary=:all:", "requests"], check=True)
  for src in (REPO / "aoty.py", REPO / "tools" / "mb_cold_run.py",
              REPO / "tools" / "mb_lambda" / "handler.py", CANDIDATES):
    shutil.copy(src, pkg / src.name)
  zip_path = BUILD / "function.zip"
  with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(pkg.rglob("*")):
      if path.is_file() and "__pycache__" not in path.parts:
        z.write(path, path.relative_to(pkg))
  print(f"Built {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")
  return zip_path


def deploy(_args):
  zip_path = build_zip()
  role = aws("iam", "get-role", "--role-name", ROLE, check=False)
  if role is None:
    role = aws("iam", "create-role", "--role-name", ROLE,
               "--assume-role-policy-document", json.dumps(TRUST))
    aws("iam", "attach-role-policy", "--role-name", ROLE, "--policy-arn", POLICY)
    print(f"Created role {ROLE}; waiting for IAM to propagate")
    time.sleep(10)
  role_arn = role["Role"]["Arn"]

  env = json.dumps({"Variables": {"MB_CONTACT": os.environ.get("MB_CONTACT", "")}})
  if aws("lambda", "get-function", "--function-name", FUNCTION, check=False):
    aws("lambda", "update-function-code", "--function-name", FUNCTION,
        "--zip-file", f"fileb://{zip_path}")
    aws("lambda", "wait", "function-updated-v2", "--function-name", FUNCTION)
    aws("lambda", "update-function-configuration", "--function-name", FUNCTION,
        "--environment", env, "--timeout", str(TIMEOUT_S), "--memory-size", "512")
    aws("lambda", "wait", "function-updated-v2", "--function-name", FUNCTION)
    print(f"Updated {FUNCTION}")
  else:
    for attempt in range(6):  # a new role can take a while before Lambda may assume it
      created = aws("lambda", "create-function", "--function-name", FUNCTION,
                    "--runtime", "python3.12", "--handler", "handler.handler",
                    "--role", role_arn, "--zip-file", f"fileb://{zip_path}",
                    "--timeout", str(TIMEOUT_S), "--memory-size", "512",
                    "--architectures", "x86_64", "--environment", env, check=attempt == 5)
      if created:
        break
      print("  role not assumable yet, retrying in 10 s")
      time.sleep(10)
    aws("lambda", "wait", "function-active-v2", "--function-name", FUNCTION)
    print(f"Created {FUNCTION}")
  # Belt and braces against a doubled invoke; refused on accounts whose concurrency
  # limit is 10 (new accounts), where retries-off + sequential invokes have to do.
  if aws("lambda", "put-function-concurrency", "--function-name", FUNCTION,
         "--reserved-concurrent-executions", "1", check=False) is None:
    print("  (reserved concurrency refused by the account limit; relying on no-retry invokes)")
  aws("logs", "create-log-group", "--log-group-name", LOG_GROUP, check=False)
  aws("logs", "put-retention-policy", "--log-group-name", LOG_GROUP, "--retention-in-days", "1")
  if not os.environ.get("MB_CONTACT"):
    print("WARNING: MB_CONTACT is not set in .env; the User-Agent uses a placeholder contact")


def invoke(offset, count, seed, chunk):
  """Run one slice on Lambda and unpack it into data/mb_bench/; returns the run folder."""
  BUILD.mkdir(parents=True, exist_ok=True)
  out = BUILD / "response.json"
  payload = json.dumps({"offset": offset, "count": count, "seed": seed, "chunk": chunk, "arms": "L"})
  started = time.monotonic()
  print(f"Invoking {FUNCTION}: offset {offset}, count {count} ...", flush=True)
  meta = aws("lambda", "invoke", "--function-name", FUNCTION,
             "--cli-binary-format", "raw-in-base64-out", "--payload", payload, str(out),
             retries=False, read_timeout=TIMEOUT_S + 10)
  response = json.loads(out.read_text(encoding="utf-8"))
  if meta.get("FunctionError"):
    raise SystemExit(f"Lambda error: {json.dumps(response, indent=1)[:2000]}")
  run_dir = BENCH_DIR / f"{response['run']}-lambda"
  run_dir.mkdir(parents=True)
  for name, blob in response["files"].items():
    (run_dir / name).write_bytes(gzip.decompress(base64.b64decode(blob)))
  run_meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
  answered = sum(c["answered"] for c in run_meta["chunks"])
  print(f"  {answered}/{run_meta['albums']} albums answered in {time.monotonic() - started:.0f} s"
        f" from {run_meta.get('outbound_ip')}"
        f"{' (INTERRUPTED)' if run_meta.get('interrupted') else ''} -> {run_dir}")
  return run_dir


def run(args):
  invoke(args.offset, args.count, args.seed, args.chunk)


def sweep(args):
  total = len(json.loads(CANDIDATES.read_text(encoding="utf-8"))["albums"])
  dirs = [invoke(offset, args.count, args.seed, args.chunk)
          for offset in range(args.offset, total, args.count)]
  print("\nAnalyse with:\n  .venv/bin/python tools/mb_analyze.py " + " ".join(
      str(d.relative_to(REPO)) for d in dirs))


def teardown(_args):
  aws("lambda", "delete-function", "--function-name", FUNCTION, check=False)
  aws("iam", "detach-role-policy", "--role-name", ROLE, "--policy-arn", POLICY, check=False)
  aws("iam", "delete-role", "--role-name", ROLE, check=False)
  aws("logs", "delete-log-group", "--log-group-name", LOG_GROUP, check=False)
  left = [what for what, gone in (
      ("function", aws("lambda", "get-function", "--function-name", FUNCTION, check=False) is None),
      ("role", aws("iam", "get-role", "--role-name", ROLE, check=False) is None),
      ("log group", not (aws("logs", "describe-log-groups", "--log-group-name-prefix", LOG_GROUP)
                         or {}).get("logGroups")),
  ) if not gone]
  print(f"Still there: {', '.join(left)}" if left else "Torn down: function, role and log group are gone")


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  sub = parser.add_subparsers(dest="cmd", required=True)
  sub.add_parser("deploy").set_defaults(fn=deploy)
  for name, fn in (("run", run), ("sweep", sweep)):
    p = sub.add_parser(name)
    p.add_argument("--offset", type=int, default=0, help="first shuffled album (default 0)")
    p.add_argument("--count", type=int, default=250, help="albums per invocation (default 250)")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--chunk", type=int, default=25, help="albums per chunk (default 25)")
    p.set_defaults(fn=fn)
  sub.add_parser("teardown").set_defaults(fn=teardown)
  args = parser.parse_args()
  args.fn(args)


if __name__ == "__main__":
  main()
