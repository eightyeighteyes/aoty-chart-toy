"""Sync worker Lambda: Last.fm backfill and deltas (AOTY-18). A no-op until then."""


def handler(event, context):
  return {"ok": True, "source": "lambdas/sync"}
