"""MusicBrainz lane Lambda: the single release-year resolver (AOTY-19). A no-op until then."""


def handler(event, context):
  return {"ok": True, "source": "lambdas/mb"}
