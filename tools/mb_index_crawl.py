"""Crawl every MusicBrainz release group first released in 2026 through search (AOTY-13).

  .venv/bin/python tools/mb_index_crawl.py offset-probe     # how deep offsets behave (~1 min)
  .venv/bin/python tools/mb_index_crawl.py crawl            # full crawl, about an hour

MusicBrainz search refuses offset + limit > 500 (HTTP 400 "Must retrieve at most 500 search
results"), so no query can be paged past 500 hits. Day ranges don't partition cleanly either:
a partial date ("2026-01") matches every day of its month, and "2026" the whole year. So the
crawl takes the whole-year query and splits it by release-group id range (rgid:[lo TO hi],
UUIDs compare as strings), halving any range with too many hits. Id ranges are
disjoint, so leaf counts must add up to the overall count exactly. Ranges are split until
each fits one page (100), because paging drifts even below the cap. A page that fails after
every retry is recorded and retried at the end, never treated as empty.

Writes data/mb_index/crawl-<ts>/: index_2026.json (same format as mb_index_dump.py),
meta.json (partitions, counts, failures, timing) and mb_requests.csv (per attempt).
Run nothing else against the MusicBrainz web service meanwhile.
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from mb_index_dump import minimal, write_json  # noqa: E402

import aoty  # noqa: E402

OUT = REPO / "data" / "mb_index"
YEAR = 2026
PAGE = 100
# MusicBrainz caps offset + limit at 500, and paging even inside that drifts: equal-score hits
# move between pages, so a 3-page partition came back ~4% short (AOTY-13 dry run). Splitting
# until every partition fits one page avoids paging altogether.
MAX_HITS = PAGE
BASE = f"firstreleasedate:[{YEAR} TO {YEAR}-12-31]"
TOP = 1 << 128


def as_uuid(n):
  h = f"{n:032x}"
  return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def range_query(lo, hi):
  """Release groups with lo <= id <= hi (as 128-bit ints), first released in YEAR."""
  return f"{BASE} AND rgid:[{as_uuid(lo)} TO {as_uuid(hi)}]"


def fetch_page(query, offset, tries=3):
  for _ in range(tries):
    body = aoty.mb_get("release-group", {"query": query, "limit": PAGE, "offset": offset}, n_albums=PAGE)
    if body is not None:
      return body
  return None


def keep(body, index, got):
  for rg in body.get("release-groups", []):
    got.add(rg["id"])
    index.setdefault(rg["id"], minimal(rg))


def crawl_range(lo, hi, index, meta, depth=0):
  """Page one id range, or split it in half while it has more than MAX_HITS hits."""
  query = range_query(lo, hi)
  first = fetch_page(query, 0)
  if first is None:
    meta["failed"].append({"lo": lo, "hi": hi, "offset": 0, "depth": depth})
    return
  count = first.get("count", 0)
  got = set()
  keep(first, index, got)  # valid even when we split: dedupe takes care of it
  if count > MAX_HITS:
    meta["splits"] += 1
    mid = (lo + hi) // 2
    crawl_range(lo, mid, index, meta, depth + 1)
    crawl_range(mid + 1, hi, index, meta, depth + 1)
    return
  for offset in range(PAGE, count, PAGE):
    body = fetch_page(query, offset)
    if body is None:
      meta["failed"].append({"lo": lo, "hi": hi, "offset": offset, "depth": depth})
      continue
    keep(body, index, got)
  meta["partitions"].append(
    {"lo": as_uuid(lo), "count": count, "returned": len(got), "depth": depth, "pages": -(-count // PAGE)}
  )
  if len(meta["partitions"]) % 25 == 0:
    print(
      f"  {len(meta['partitions'])} partitions, {len(index):,} release groups,"
      f" {len(meta['failed'])} failed pages, {len(aoty.MB_STATS)} requests",
      flush=True,
    )


def total_count(query):
  body = fetch_page(query, 0)
  return None if body is None else body.get("count")


def crawl(args):
  run_dir = OUT / f"crawl-{time.strftime('%Y%m%d-%H%M%S')}"
  run_dir.mkdir(parents=True)
  aoty.MB_STATS_CSV = run_dir / "mb_requests.csv"
  aoty.reset_run_state()
  meta = {
    "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "query": BASE,
    "buckets": args.buckets,
    "splits": 0,
    "partitions": [],
    "failed": [],
  }
  meta["overall_count_start"] = total_count(BASE)
  index, t0 = {}, time.monotonic()
  step = TOP // args.buckets
  buckets = [(i * step, (i + 1) * step - 1) for i in range(args.buckets)]
  if args.limit:
    buckets = buckets[: args.limit]
  try:
    for lo, hi in buckets:
      crawl_range(lo, hi, index, meta)
    # Retry failed pages once more at the end, when MusicBrainz may be quieter.
    retry, meta["failed"] = meta["failed"], []
    for f in retry:
      body = fetch_page(range_query(f["lo"], f["hi"]), f["offset"])
      if body is None:
        meta["failed"].append(f)
        continue
      keep(body, index, set())
  except KeyboardInterrupt:
    print("\nInterrupted: keeping the partial crawl")
    meta["interrupted"] = True
  meta["overall_count_end"] = total_count(BASE)
  meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
  meta["wall_s"] = round(time.monotonic() - t0, 1)
  meta["unique_release_groups"] = len(index)
  meta["sum_partition_counts"] = sum(p["count"] for p in meta["partitions"])
  meta["short_partitions"] = [p for p in meta["partitions"] if p["returned"] < p["count"]]
  meta["deepest_partition"] = max(meta["partitions"], key=lambda p: p["count"], default=None)
  meta["requests"] = len(aoty.MB_STATS)
  meta["503s"] = sum(1 for s in aoty.MB_STATS if s["status"] == 503)
  meta["index_2026"] = write_json(run_dir / "index_2026.json", index)
  (run_dir / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
  aoty.mb_report("mb_index_crawl")
  summary = {k: v for k, v in meta.items() if k not in ("partitions",)}
  print(json.dumps(summary, indent=1))
  print(f"Run folder: {run_dir}")


def offset_probe(args):
  """Time single pages of the unpartitioned Album query at increasing offsets."""
  run_dir = OUT / f"offsets-{time.strftime('%Y%m%d-%H%M%S')}"
  run_dir.mkdir(parents=True)
  aoty.MB_STATS_CSV = run_dir / "mb_requests.csv"
  aoty.reset_run_state()
  query = f"firstreleasedate:[{YEAR}-01-01 TO {YEAR}-12-31] AND primarytype:album"
  rows = []
  # offset + limit > 500 is refused with HTTP 400, so 400 is the last page there is.
  for offset in (0, 100, 200, 300, 400, 500, 1000):
    before = len(aoty.MB_STATS)
    body = fetch_page(query, offset, tries=1)
    ok = [s for s in aoty.MB_STATS[before:] if s["status"] == 200]
    rows.append(
      {
        "offset": offset,
        "count": body.get("count") if body else None,
        "returned": len(body.get("release-groups", [])) if body else None,
        "server_s": round(ok[-1]["elapsed"], 2) if ok else None,
        "attempts": len(aoty.MB_STATS) - before,
      }
    )
    print(rows[-1], flush=True)
  (run_dir / "offsets.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
  aoty.mb_report("mb_index_crawl")


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  sub = parser.add_subparsers(dest="cmd", required=True)
  p = sub.add_parser("crawl")
  p.add_argument("--limit", type=int, help="only the first N id buckets (dry run)")
  p.add_argument("--buckets", type=int, default=2048, help="initial id buckets (default 2048)")
  p.set_defaults(fn=crawl)
  sub.add_parser("offset-probe").set_defaults(fn=offset_probe)
  args = parser.parse_args()
  args.fn(args)


if __name__ == "__main__":
  main()
