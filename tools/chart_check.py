"""AOTY-21 acceptance check: a deployed chart.json vs M0's rules on the same scrobbles.

  .venv/bin/python tools/chart_check.py --env sandbox --user <lastfm name>
  .venv/bin/python tools/chart_check.py --env staging --user <name> --label m01

Reads, with the aws CLI (admin credentials):
  - the user's store, s3://kamp-aoty-<env>-store/users/<u>/<year>/weeks.json.gz
  - the albums items for every key in it (BatchGetItem)
  - the chart, s3://kamp-aoty-<env>-site/<prefix>data/users/<u>/chart.json
Then it builds the reference with M0's code:
  - the rows M0 would rank, for a one-member server (aoty.aggregate_plays / rank_albums give
    one listener per album, so the order is plays);
  - build_page.score_rankings for which albums are singles and for track counts;
  - M0's per-member re-rank, which lives in the page script (build_page.PAGE_SCRIPT's
    ranked()), transcribed below: By Track = the member's plays, By Album = plays / tracks
    with singles left out.
Albums are joined on the sync worker's keys (common.albumkey), not aoty.cache_key, whose lack
of a punctuation fallback could merge two albums.

Differences only in the order of albums with equal scores, or in which of several albums tied
at the top-100 cutoff made it, are not failures. Anything else exits 1. The user is printed as
--label, never by name.
"""

import argparse
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "lambdas")]

import build_page  # noqa: E402  (imports aoty, which reads .env)
from common import dynamo  # noqa: E402
from common.albumkey import join_key, split_key  # noqa: E402
from common.config import TARGET_YEAR  # noqa: E402

REGION = "us-east-1"
TOP_N = 100
MEMBER = "m01"  # the one member of the reference server
PREFIXES = {"prod": "toys/aoty/", "staging": "toys/aoty-staging/", "sandbox": "toys/aoty-sandbox/"}


# ---- reference (pure) -------------------------------------------------------------------


def year_plays(store):
  total = {}
  for week in store["weeks"].values():
    for k, n in week["plays"].items():
      total[k] = total.get(k, 0) + n
  return {k: n for k, n in total.items() if n > 0}


def reference(store, items, year=TARGET_YEAR, n=TOP_N):
  """{"track": [(key, score)], "album": [(key, score)]} by M0's rules for one member."""
  plays = year_plays(store)
  meta = store.get("albums", {})
  dated = [k for k in plays if (items.get(k) or {}).get("year") == year]
  # aoty.rank_albums: unique listeners (1 each here), then plays; sorted() is stable.
  dated.sort(key=lambda k: (1, plays[k]), reverse=True)
  rows = [
    {"Artist": meta.get(k, {}).get("artist") or split_key(k)[0], "Album": meta.get(k, {}).get("album") or k,
     "Total Plays": str(plays[k]), "Unique Listeners": "1", "Listener Plays": json.dumps({MEMBER: plays[k]}),
     "sync_key": k}  # fmt: skip
    for k in dated
  ]
  # score_rankings keys releases by aoty.cache_key(Artist, Album); give each row its own entry.
  releases = {}
  for r in rows:
    item = items[r["sync_key"]]
    releases[build_page.aoty.cache_key(r["Artist"], r["Album"])] = {
      "tracks": item.get("tracks"), "type": item.get("tracks_type")
    }  # fmt: skip
  build_page.score_rankings(rows, releases, [MEMBER], n)  # annotates rows: tracks, by_user, single
  albums = [r for r in rows if not build_page.is_single(releases[r["key"]])]

  # PAGE_SCRIPT's ranked(mode, name): filter, score, then a stable sort by score desc and
  # server listeners desc (all 1 here), so ties keep server (CSV) order.
  def ranked(pool, score_of):
    return sorted(((r["sync_key"], score_of(r)) for r in pool), key=lambda kv: -kv[1])[:n]

  return {
    "track": ranked(rows, lambda r: r["by_user"][MEMBER]),
    "album": ranked(albums, lambda r: r["by_user"][MEMBER] / r["tracks"]),
  }


def chart_entries(chart, store):
  """{"track": [(key, score)], ...} from chart.json, keyed back to the store's keys."""
  by_names = {(a.get("artist"), a.get("album")): k for k, a in store.get("albums", {}).items()}
  out = {}
  for mode in ("track", "album"):
    out[mode] = [
      (by_names.get((chart["albums"][i]["artist"], chart["albums"][i]["album"])), s)
      for i, s in chart["rankings"].get(mode, [])
    ]
  return out


def compare(ref, got):
  """Problems (strings) between two rankings, ignoring order within equal scores."""
  problems = []
  for mode in ("track", "album"):
    r = [(k, round(s, 3)) for k, s in ref[mode]]
    g = [(k, round(s, 3)) for k, s in got.get(mode, [])]
    if len(r) != len(g):
      problems.append(f"{mode}: {len(g)} albums in the chart, {len(r)} in the reference")
    if [s for _, s in r] != [s for _, s in g]:
      first = next((i for i, (a, b) in enumerate(zip(r, g)) if a[1] != b[1]), min(len(r), len(g)))
      problems.append(f"{mode}: scores differ from position {first + 1}")
      continue
    if any(k is None for k, _ in g):
      problems.append(f"{mode}: chart albums that aren't in the store")
    cutoff = r[-1][1] if r else None
    for score in sorted({s for _, s in r}, reverse=True):
      rk = {k for k, s in r if s == score}
      gk = {k for k, s in g if s == score}
      if rk != gk and score != cutoff:  # at the cutoff, which tied albums made it is arbitrary
        problems.append(f"{mode}: different albums with score {score}")
  return problems


# ---- I/O --------------------------------------------------------------------------------


def aws(*args):
  out = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"], check=True,
                       capture_output=True, text=True).stdout  # fmt: skip
  return json.loads(out) if out.strip() else None


def s3_get(bucket, key):
  with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "obj"
    subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{key}", str(path), "--only-show-errors",
                    "--region", REGION], check=True)  # fmt: skip
    return path.read_bytes()


def get_items(env, keys):
  table, found = f"kamp-aoty-{env}-albums", {}
  for i in range(0, len(keys), 100):
    request = {table: {"Keys": [{"artist_key": {"S": a}, "title_key": {"S": t}}
                                for a, t in map(split_key, keys[i : i + 100])]}}  # fmt: skip
    while request:
      out = aws("dynamodb", "batch-get-item", "--request-items", json.dumps(request))
      for raw in out.get("Responses", {}).get(table, []):
        item = dynamo.from_item(raw)
        found[join_key((item["artist_key"], item["title_key"]))] = item
      request = out.get("UnprocessedKeys") or None
  return found


def main():
  p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  p.add_argument("--env", choices=sorted(PREFIXES), required=True)
  p.add_argument("--user", required=True, help="Last.fm username (never printed)")
  p.add_argument("--label", default="m01", help="how the user is named in the output")
  p.add_argument("--year", type=int, default=TARGET_YEAR)
  args = p.parse_args()
  user = args.user.strip().lower()

  store = json.loads(gzip.decompress(s3_get(f"kamp-aoty-{args.env}-store", f"users/{user}/{args.year}/weeks.json.gz")))
  chart = json.loads(s3_get(f"kamp-aoty-{args.env}-site", f"{PREFIXES[args.env]}data/users/{user}/chart.json"))
  items = get_items(args.env, sorted(year_plays(store)))
  ref, got = reference(store, items, args.year), chart_entries(chart, store)

  print(f"{args.label} ({args.env}, {args.year}): {len(year_plays(store))} albums played, "
        f"{len(items)} with an albums item, chart generated {chart.get('generated_at')}")  # fmt: skip
  for mode in ("track", "album"):
    print(f"  {mode}: chart {len(got[mode])}, reference {len(ref[mode])}")
  problems = compare(ref, got)
  if chart.get("pending"):
    print(f"  note: {chart['pending']} albums were still pending when the chart was built; "
          "albums resolved since then make the reference differ until the next build")  # fmt: skip
  for line in problems:
    print(f"  DIFF {line}")
  print("  OK: the chart matches M0's rules" if not problems else f"  {len(problems)} difference(s)")
  sys.exit(1 if problems else 0)


if __name__ == "__main__":
  main()
