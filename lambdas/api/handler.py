"""API Lambda (HTTP API, payload v2). Real routes arrive with AOTY-22; for now only health.

The function sees the full public path (/toys/aoty/api/... or /toys/aoty-staging/api/...)
because kamp.fm's CloudFront forwards it unchanged; BASE_PATH comes from Terraform.
"""

import json
import os


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
    # "source" tells CD's smoke test this is deployed code, not Terraform's placeholder.
    return _response(200, {"ok": True, "source": "lambdas/api", "env": os.environ.get("AOTY_ENV", "")})
  return _response(404, {"error": "not found"})
