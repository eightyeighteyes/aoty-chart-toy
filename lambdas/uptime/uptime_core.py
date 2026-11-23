"""The uptime check's pure core (AOTY-27): which URLs to fetch and what counts as up.

No I/O here: handler.py fetches, reads SSM and emits the metric.

Checks, all through kamp.fm (the path a visitor takes, CloudFront included):
  shell   <base>                              200, and the page names this env's base path
  health  <base>api/health                    200, JSON with source == "lambdas/api"
  chart   <base>data/users/<user>/chart.json  200, JSON with a rankings object
The chart check needs a user known to have a chart (SSM uptime_user, kept out of the repo);
without one it's skipped, not failed.
"""

import json

CHECKS = ("shell", "health", "chart")


def urls(origin, base, user):
  """{check: url} for every check that can run; chart only with a user."""
  root = origin.rstrip("/") + base
  out = {"shell": root, "health": f"{root}api/health"}
  if user:
    out["chart"] = f"{root}data/users/{user}/chart.json"
  return out


def _json(body):
  try:
    return json.loads(body)
  except (ValueError, UnicodeDecodeError):
    return None


def judge(check, base, status, body):
  """None when the answer looks healthy, else a short reason (no usernames in it)."""
  if status != 200:
    return f"status {status}"
  if check == "shell":
    return None if base.encode() in body else "not this env's shell"
  data = _json(body)
  if not isinstance(data, dict):
    return "not a JSON object"
  if check == "health":
    return None if data.get("source") == "lambdas/api" else "not the deployed api"
  if check == "chart":
    return None if isinstance(data.get("rankings"), dict) else "no rankings"
  raise ValueError(f"unknown check {check!r}")
