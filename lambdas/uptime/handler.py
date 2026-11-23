"""Uptime Lambda (AOTY-27): hourly, from EventBridge Scheduler, checks kamp.fm<base> is up.

uptime_core has the checks. One EMF line per run (namespace kamp-aoty, dimension Environment):
  UptimeChecks    1 per run, written even when the run crashes; the "silent" alarm fires on
                  its absence (the schedule or the function stopped)
  UptimeFailures  checks that failed (a crash counts as one)
The chart check's user is the SSM String <SSM_PREFIX>uptime_user, set by hand so no username is
in the repo; missing means that check is skipped. Logs never name the user.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

import uptime_core as core

log = logging.getLogger()
log.setLevel(logging.INFO)

NAMESPACE = "kamp-aoty"
ORIGIN = "https://kamp.fm"
TIMEOUT_S = 15
USER_AGENT = "kamp-aoty-uptime/1 (+https://kamp.fm/toys/aoty/)"


def fetch(url, timeout=TIMEOUT_S):
  """(status, body); status None when nothing answered."""
  req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
  try:
    with urllib.request.urlopen(req, timeout=timeout) as r:
      return r.status, r.read()
  except urllib.error.HTTPError as e:
    return e.code, e.read()
  except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
    log.warning("fetch failed: %s", type(e).__name__)
    return None, b""


class Deps:
  def __init__(self, *, env, fetch, user, clock=time.time):
    self.env, self.fetch, self.user, self.clock = env, fetch, user, clock

  @classmethod
  def from_env(cls):
    import boto3  # in the Lambda runtime; not needed by tests

    env = os.environ
    return cls(env=env, fetch=fetch, user=lambda: _ssm_user(boto3.client("ssm"), env["SSM_PREFIX"]))


def _ssm_user(ssm, prefix):
  try:
    value = ssm.get_parameter(Name=f"{prefix}uptime_user")["Parameter"]["Value"]
  except ssm.exceptions.ParameterNotFound:
    return None
  return value.strip().lower() or None


def handler(event, context, deps=None):
  deps = deps or Deps.from_env()
  failures, results = 1, {}  # until the checks finish, a crash counts as a failure
  try:
    results = run(deps)
    failures = sum(1 for r in results.values() if r not in (None, "skipped"))
  except Exception:
    log.exception("uptime run crashed")
  finally:
    _emit(deps, failures)
  return {"failures": failures, "results": results}


def run(deps):
  base = deps.env.get("BASE_PATH", "/")
  user = deps.user()
  results = {}
  for check, url in core.urls(deps.env.get("UPTIME_ORIGIN", ORIGIN), base, user).items():
    status, body = deps.fetch(url)
    results[check] = core.judge(check, base, status, body)
  if "chart" not in results:
    results["chart"] = "skipped"
    log.warning("no uptime_user parameter: chart check skipped")
  for check in core.CHECKS:
    log.info("uptime %s: %s", check, results[check] or "ok")
  return results


def _emit(deps, failures):
  print(json.dumps({
    "_aws": {
      "Timestamp": int(deps.clock() * 1000),
      "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [["Environment"]],
                             "Metrics": [{"Name": "UptimeChecks", "Unit": "Count"},
                                         {"Name": "UptimeFailures", "Unit": "Count"}]}],
    },
    "Environment": deps.env.get("AOTY_ENV", ""),
    "UptimeChecks": 1,
    "UptimeFailures": failures,
  }))  # fmt: skip
