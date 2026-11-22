"""Record the MusicBrainz responses the matching tests run against (AOTY-16).

  .venv/bin/python tools/record_mb_fixtures.py

Uses the scripts' own query builders (aoty._clause, build_page._release_clause) and
mb_search, so the fixtures contain the same false hits a real run sees ("1989 [nightcore]",
"(Taylor's Version)" ...). Responses are trimmed to the fields the matchers read and written to
tests/fixtures/mb/. Re-run only to refresh them: about one request per case, so it takes
a few seconds. Don't run it during a MusicBrainz benchmark (tools/mb_cold_run.py).
"""

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import aoty  # noqa: E402
import build_page  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "mb"

# (fixture name, entity, artist, album). Limits match the scripts' batch searches (100),
# the widest net, so crowded-out near misses are in the fixture too.
CASES = [
  ("rg_taylor_swift_1989", "release-group", "Taylor Swift", "1989"),
  ("rg_ratboys_singin", "release-group", "Ratboys", "Singin’ to an Empty Chair"),
  ("rel_gladie_i_want_that_for_you", "release", "gladie", "I Want That For You"),
  ("rel_sleep_dopesmoker", "release", "Sleep", "Dopesmoker"),
]


def trim_credit(credits):
  return [{"name": c.get("name", ""), "joinphrase": c.get("joinphrase", "")} for c in credits or []]


def trim_release_group(rg):
  return {
    "title": rg.get("title", ""),
    "first-release-date": rg.get("first-release-date", ""),
    "primary-type": rg.get("primary-type"),
    "secondary-types": rg.get("secondary-types", []),
    "artist-credit": trim_credit(rg.get("artist-credit")),
  }


def trim_release(rel):
  return {
    "title": rel.get("title", ""),
    "status": rel.get("status"),
    "date": rel.get("date"),
    "track-count": rel.get("track-count"),
    "release-group": {"primary-type": rel.get("release-group", {}).get("primary-type")},
    "artist-credit": trim_credit(rel.get("artist-credit")),
  }


def main():
  OUT.mkdir(parents=True, exist_ok=True)
  for name, entity, artist, album in CASES:
    if entity == "release-group":
      query, trim = aoty._clause(artist, album), trim_release_group
    else:
      query, trim = build_page._release_clause(artist, album), trim_release
    results = aoty.mb_search(query, 100, entity)
    if results is None:
      raise SystemExit(f"{name}: MusicBrainz request failed, nothing written")
    fixture = {
      "recorded": time.strftime("%Y-%m-%d"),
      "entity": entity,
      "query": query,
      "limit": 100,
      "results": [trim(r) for r in results],
    }
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{name}: {len(results)} results -> {path.relative_to(REPO)}")


if __name__ == "__main__":
  main()
