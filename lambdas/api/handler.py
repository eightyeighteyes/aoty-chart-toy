"""API Lambda (HTTP API, payload v2). Real routes arrive with AOTY-22; for now only health.

The function sees the full public path (/toys/aoty/api/... or /toys/aoty-staging/api/...)
because kamp.fm's CloudFront forwards it unchanged; BASE_PATH comes from Terraform.
"""

import json
import os
from pathlib import Path


def _build_sha(path):
  """The commit scripts/package_lambdas.py stamped into the zip's build.json (AOTY-35).

  "unknown" when there's no readable stamp: run from a checkout, or a zip built without one.
  """
  try:
    sha = json.loads(Path(path).read_text())["sha"]
  except (OSError, ValueError, KeyError, TypeError):
    return "unknown"
  return sha if isinstance(sha, str) and sha else "unknown"


BUILD_SHA = _build_sha(Path(__file__).with_name("build.json"))


def _response(status, body):
  return {
    "statusCode": status,
    # no-store: no cache between here and the browser may keep an API answer, errors included.
    "headers": {"content-type": "application/json", "cache-control": "no-store"},
    "body": json.dumps(body),
  }


def handler(event, context):
  base = os.environ.get("BASE_PATH", "/")
  path = (event or {}).get("rawPath", "")
  if path == f"{base}api/health":
    # "source" tells CD's smoke test this is deployed code, not Terraform's placeholder, and
    # "sha" which commit: the smoke test fails unless it's the one just deployed.
    env = os.environ.get("AOTY_ENV", "")
    return _response(200, {"ok": True, "source": "lambdas/api", "env": env, "sha": BUILD_SHA})
  return _response(404, {"error": "not found"})
