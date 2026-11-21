"""Placeholder for the api, sync and mb Lambdas until their stories deploy real code.

Answers <base>api/health with 200 so the API path can be smoke-tested end to end.
"""
import json
import os


def handler(event, context):
  name = context.function_name if context else "unknown"
  path = (event or {}).get("rawPath", "")
  base = os.environ.get("BASE_PATH", "/")
  if path == f"{base}api/health":
    return {"statusCode": 200, "headers": {"content-type": "application/json"},
            "body": json.dumps({"ok": True, "placeholder": name})}
  if "rawPath" in (event or {}):
    return {"statusCode": 404, "headers": {"content-type": "application/json"},
            "body": json.dumps({"error": "not found", "placeholder": name})}
  return {"ok": True, "placeholder": name}
