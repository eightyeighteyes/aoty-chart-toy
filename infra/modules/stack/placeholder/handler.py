"""Placeholder for the api, sync, mb and chart Lambdas until CD deploys real code.

Answers <base>api/health with 200 so the API path can be smoke-tested end to end.
Raises on SQS events: returning success would make the event source mapping delete real
messages before the worker's code is deployed.
"""

import json
import os


def handler(event, context):
  name = context.function_name if context else "unknown"
  if "Records" in (event or {}):
    raise RuntimeError(f"{name} is a placeholder; leaving SQS messages on the queue")
  path = (event or {}).get("rawPath", "")
  base = os.environ.get("BASE_PATH", "/")
  if path == f"{base}api/health":
    return {
      "statusCode": 200,
      "headers": {"content-type": "application/json"},
      "body": json.dumps({"ok": True, "placeholder": name}),
    }
  if "rawPath" in (event or {}):
    return {
      "statusCode": 404,
      "headers": {"content-type": "application/json"},
      "body": json.dumps({"error": "not found", "placeholder": name}),
    }
  return {"ok": True, "placeholder": name}
