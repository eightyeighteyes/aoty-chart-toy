"""Cold MusicBrainz benchmark: look up a frozen album list against an empty cache.

Compares batch sizes and request intervals ("arms") within one run, so they share
the same time of day. Each arm gets round-robin chunks of a shuffled album list, and
every MusicBrainz attempt is tagged with its arm and chunk (see aoty.MB_TAG).

Never touches the real data/ caches or ranking: all output goes to
data/mb_bench/run-<timestamp>/, which is checked before the first request, and the
real release-year cache is checksummed before and after.

  .venv/bin/python tools/mb_cold_run.py                     # full run
  .venv/bin/python tools/mb_cold_run.py --limit 40 --chunk 10   # quick dry run
  .venv/bin/python tools/mb_analyze.py data/mb_bench/run-*      # then analyse

The album list is fetched from Last.fm once and frozen in data/mb_bench/candidates.json
(artist/album pairs only), so later runs compare like with like; --refresh refetches.
"""
import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aoty  # noqa: E402  (patched as a module object below, never `from aoty import`)

# (arm, batch size, min interval). Back-off is MB_MIN_INTERVAL * 2**attempt, so the
# slower interval also tests a gentler back-off.
ARMS = [
    ("A", 20, 1.1),  # current settings
    ("B", 10, 1.1),
    ("C", 40, 1.1),
    ("D", 20, 2.0),
]

BENCH_DIR = aoty.DATA_DIR / "mb_bench"
CANDIDATES_FILE = BENCH_DIR / "candidates.json"


def sha256(path):
  return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def load_candidates(refresh):
  if CANDIDATES_FILE.exists() and not refresh:
    data = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
    print(f"Using {len(data['albums'])} frozen candidates from {CANDIDATES_FILE} ({data['fetched']})")
    return [tuple(p) for p in data["albums"]]
  aoty.require_config()
  _, candidates = aoty.fetch_plays_and_candidates(aoty.LASTFM_USERS)
  albums = list(dict.fromkeys(candidates))  # unique, first-seen order; no usernames kept
  BENCH_DIR.mkdir(parents=True, exist_ok=True)
  CANDIDATES_FILE.write_text(json.dumps(
      {"fetched": time.strftime("%Y-%m-%dT%H:%M:%S"), "albums": albums},
      ensure_ascii=False, indent=0), encoding="utf-8")
  print(f"Froze {len(albums)} candidates to {CANDIDATES_FILE}")
  return albums


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--seed", type=int, default=2026, help="shuffle seed (default 2026)")
  parser.add_argument("--chunk", type=int, default=100, help="albums per chunk (default 100)")
  parser.add_argument("--limit", type=int, help="only the first N shuffled albums (dry run)")
  parser.add_argument("--refresh", action="store_true", help="refetch candidates from Last.fm")
  args = parser.parse_args()

  real_cache = aoty.CACHE_FILE.resolve()
  real_paths = {real_cache, aoty.OUTPUT_CSV.resolve(), aoty.MB_STATS_CSV.resolve()}
  real_sum = sha256(real_cache)

  run_dir = BENCH_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}"
  run_dir.mkdir(parents=True)
  aoty.CACHE_FILE = run_dir / "cache.json"
  aoty.OUTPUT_CSV = run_dir / "unused.csv"
  aoty.MB_STATS_CSV = run_dir / "mb_requests.csv"
  for path in (aoty.CACHE_FILE, aoty.OUTPUT_CSV, aoty.MB_STATS_CSV):
    if path.resolve() in real_paths or run_dir.resolve() not in path.resolve().parents:
      raise SystemExit(f"Refusing to run: {path} is not a scratch path under {run_dir}")

  albums = load_candidates(args.refresh)
  random.Random(args.seed).shuffle(albums)
  if args.limit:
    albums = albums[:args.limit]

  meta = {"run_dir": str(run_dir), "seed": args.seed, "chunk_size": args.chunk,
          "limit": args.limit, "albums": len(albums), "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "arms": [{"arm": a, "batch_size": b, "interval": i} for a, b, i in ARMS], "chunks": []}
  cache = {}
  try:
    for n, start in enumerate(range(0, len(albums), args.chunk)):
      chunk = albums[start:start + args.chunk]
      arm, batch_size, interval = ARMS[n % len(ARMS)]
      aoty.MB_BATCH_SIZE = batch_size
      aoty.MB_MIN_INTERVAL = interval
      aoty.MB_TAG = {"arm": arm, "chunk": n, "batch_size": batch_size, "interval": interval}
      print(f"\n== chunk {n} arm {arm}: {len(chunk)} albums, batch {batch_size}, interval {interval}s")
      chunk_start = time.monotonic()
      aoty.lookup_years(chunk, cache)
      keys = [aoty.cache_key(*p) for p in chunk]
      meta["chunks"].append({
          "chunk": n, "arm": arm, "albums": len(chunk),
          "answered": sum(1 for k in keys if k in cache),
          "with_year": sum(1 for k in keys if cache.get(k)),
          "wall_s": round(time.monotonic() - chunk_start, 3),
      })
  except KeyboardInterrupt:
    print("\nInterrupted: keeping the partial run")
    meta["interrupted"] = True
  finally:
    meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta["real_cache_unchanged"] = sha256(real_cache) == real_sum
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    aoty.mb_report("mb_cold_run")
    print(f"\nRun folder: {run_dir}")
    print("Real cache unchanged" if meta["real_cache_unchanged"]
          else "WARNING: the real release-year cache CHANGED during this run")


if __name__ == "__main__":
  main()
