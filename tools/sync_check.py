"""AOTY-18 acceptance check: the sync worker's chart totals vs counted scrobbles.

  .venv/bin/python tools/sync_check.py                    # the heaviest member
  .venv/bin/python tools/sync_check.py --rank 1           # the lightest (m01)
  .venv/bin/python tools/sync_check.py --dotenv ../../../.env   # from a worktree

Runs the sync worker's own fetch path (lambdas/sync/sync_core.py with the Lambdas' Last.fm
client) for one member, no AWS involved, and compares the year's chart play total with
getRecentTracks scrobbles counted over the same pinned UTC window (rows without a date, i.e.
now-playing, dropped). Year totals are what must match: per-album counts legitimately differ
for compilations and featured artists, because charts group by album artist (AOTY-11).

Members are labelled m01..mNN by year-to-date scrobbles, lightest first, as in
tools/lastfm_spike.py; usernames are never printed. Needs LASTFM_API_KEY and LASTFM_USERS.
Ranking costs one call per member; a heavy member then costs ~47 chart calls plus ~170
recent-tracks pages (about 2 minutes at 0.5 s per call).
"""

import argparse
import importlib
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "lambdas"), str(REPO / "lambdas" / "sync")]

import sync_core as core  # noqa: E402
from common import lastfm  # noqa: E402
from common.config import TARGET_YEAR  # noqa: E402


def load_dotenv(path):
  """Fill unset keys from a .env (aoty reads only its own directory's, at import)."""
  if not path.exists():
    return
  for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
      k, v = line.split("=", 1)
      os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def ranked_members(client, users, year, now):
  """Users sorted by year-to-date scrobbles, lightest first (limit=1, so totalPages = count)."""
  start, _ = core.year_bounds(year)
  counted = []
  for user in users:
    _, total = client.recent_tracks(user, start, limit=1, to=now)
    counted.append((total, user))
  return [u for _, u in sorted(counted, key=lambda c: c[0])]


def chart_total(client, user, year, now):
  weeks, total, rows_max = 0, 0, 0
  for frm, to in core.week_ranges(year, now):
    rows = core.fetch_range(lambda f, t: client.weekly_album_chart(user, f, t), frm, min(to, now))
    plays, _ = core.chart_plays(rows)
    total += sum(plays.values())
    rows_max = max(rows_max, len(rows))
    weeks += 1
  return weeks, total, rows_max


def scrobble_total(client, user, year, now):
  """(scrobbles with an album, scrobbles without one, pages). Album charts can't count the latter."""
  start, _ = core.year_bounds(year)
  with_album, without, page, pages = 0, 0, 1, 1
  while page <= pages:
    tracks, pages = client.recent_tracks(user, start, page=page, to=now)
    for t in core.dated(tracks):
      if core._text(t.get("album")).strip():
        with_album += 1
      else:
        without += 1
    page += 1
  return with_album, without, pages


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--rank", type=int, help="1 = lightest member; default: the heaviest")
  ap.add_argument("--year", type=int, default=TARGET_YEAR)
  ap.add_argument("--tolerance", type=float, default=0.5, help="allowed difference, percent of plays")
  ap.add_argument("--dotenv", type=Path, default=REPO / ".env", help="the .env to read")
  args = ap.parse_args()

  load_dotenv(args.dotenv)
  aoty = importlib.import_module("aoty")
  aoty.require_config()
  client = lastfm.Client(aoty.LASTFM_API_KEY)
  now = int(time.time())  # pinned: both sides count the same window

  members = ranked_members(client, aoty.LASTFM_USERS, args.year, now)
  rank = args.rank or len(members)
  if not 1 <= rank <= len(members):
    raise SystemExit(f"--rank must be 1..{len(members)}")
  user, label = members[rank - 1], f"m{rank:02d}"

  t0, calls0 = time.monotonic(), client.calls
  weeks, charted, rows_max = chart_total(client, user, args.year, now)
  chart_s, chart_calls = time.monotonic() - t0, client.calls - calls0
  counted, no_album, pages = scrobble_total(client, user, args.year, now)

  diff = charted - counted
  pct = 100 * abs(diff) / (counted or 1)
  ok = pct <= args.tolerance
  print(f"{label} {args.year}: {weeks} chart weeks in {chart_s:.0f} s ({chart_calls} calls, "
        f"largest week {rows_max} rows)")
  print(f"  chart plays   {charted:>8,}")
  print(f"  scrobbles     {counted:>8,}  with an album ({pages} pages; {no_album:,} more have none)")
  print(f"  difference    {diff:>+8,}  ({pct:.3f}%, tolerance {args.tolerance}%)  {'OK' if ok else 'FAIL'}")
  sys.exit(0 if ok else 1)


if __name__ == "__main__":
  main()
