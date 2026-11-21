"""Compare MusicBrainz benchmark arms across one or more tools/mb_cold_run.py runs.

  .venv/bin/python tools/mb_analyze.py data/mb_bench/run-*

Prints markdown tables: per arm per run, per arm over all runs, 503s by minute,
rate-limit headers seen, and a sanity diff of each run's years against the real cache.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REAL_CACHE = REPO / "data" / "mb_release_year_cache.json"


def pct(values, p):
  if not values:
    return 0.0
  ordered = sorted(values)
  return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))]


def load_run(run_dir):
  run_dir = Path(run_dir)
  meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
  with open(run_dir / "mb_requests.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
  for r in rows:
    for k in ("t", "slept", "elapsed"):
      r[k] = float(r[k] or 0)
    r["n_albums"] = int(r["n_albums"])
    r["limit"] = int(r["limit"])
  cache_file = run_dir / "cache.json"
  cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
  return {"name": run_dir.name, "meta": meta, "rows": rows, "cache": cache}


def arm_stats(rows, chunks):
  ok = [r["elapsed"] for r in rows if r["status"] == "200"]
  throttled = sum(1 for r in rows if r["status"] == "503")
  slept = sum(r["slept"] for r in rows)
  server = sum(r["elapsed"] for r in rows)
  singles = [r for r in rows if r["n_albums"] == 1]
  answered = sum(c["answered"] for c in chunks)
  albums = sum(c["albums"] for c in chunks)
  return {
      "attempts": len(rows),
      "503": throttled,
      "503%": 100 * throttled / len(rows) if rows else 0.0,
      "fail": sum(1 for r in rows if r["status"] not in ("200", "503")),
      "batch": sum(1 for r in rows if r["n_albums"] > 1),
      "single": len(singles),
      "single_s": sum(r["slept"] + r["elapsed"] for r in singles),
      "saturated": sum(1 for r in rows if r["results"] and int(r["results"]) >= r["limit"]),
      "p50": pct(ok, 50),
      "p95": pct(ok, 95),
      "slept": slept,
      "server": server,
      "albums": albums,
      "answered": answered,
      "alb/s": answered / (slept + server) if slept + server else 0.0,
  }


COLUMNS = [
    ("attempts", "{:d}"), ("503", "{:d}"), ("503%", "{:.0f}%"), ("fail", "{:d}"),
    ("batch", "{:d}"), ("single", "{:d}"), ("single_s", "{:.0f}s"), ("saturated", "{:d}"),
    ("p50", "{:.2f}s"), ("p95", "{:.2f}s"), ("slept", "{:.0f}s"), ("server", "{:.0f}s"),
    ("answered", "{:d}"), ("alb/s", "{:.2f}"),
]


def table(title, labelled):
  print(f"\n### {title}\n")
  print("| " + " | ".join(["arm"] + [c for c, _ in COLUMNS]) + " |")
  print("|" + "---|" * (len(COLUMNS) + 1))
  for label, s in labelled:
    print("| " + " | ".join([label] + [fmt.format(s[c]) for c, fmt in COLUMNS]) + " |")


def arm_label(meta, arm):
  a = next(x for x in meta["arms"] if x["arm"] == arm)
  backoff = "retry-after" if a.get("backoff") == "retry-after" else "exp"
  return f"{arm} (b{a['batch_size']}, {a['interval']}s, {backoff} x{a.get('max_attempts', 4)})"


def main(paths):
  runs = [load_run(p) for p in paths]
  real = json.loads(REAL_CACHE.read_text(encoding="utf-8")) if REAL_CACHE.exists() else {}
  pooled_rows, pooled_chunks = defaultdict(list), defaultdict(list)

  for run in runs:
    meta, rows = run["meta"], run["rows"]
    by_arm = defaultdict(list)
    for r in rows:
      by_arm[r["arm"]].append(r)
    chunks = defaultdict(list)
    for c in meta["chunks"]:
      chunks[c["arm"]].append(c)
    flags = " (INTERRUPTED)" if meta.get("interrupted") else ""
    flags += "" if meta.get("real_cache_unchanged", True) else " (REAL CACHE CHANGED!)"
    print(f"\n## {run['name']}: {meta['started']} to {meta['finished']}, "
          f"{meta['albums']} albums, seed {meta['seed']}{flags}")
    table("Per arm", [(arm_label(meta, a), arm_stats(by_arm[a], chunks[a])) for a in sorted(by_arm)])
    for a in by_arm:
      pooled_rows[a] += by_arm[a]
      pooled_chunks[a] += chunks[a]

    minutes = defaultdict(lambda: [0, 0])
    for r in rows:
      minutes[int(r["t"] // 60)][0] += 1
      minutes[int(r["t"] // 60)][1] += r["status"] == "503"
    print("\n503s by minute (attempts/503s): " + "  ".join(
        f"m{m}:{n}/{t}" for m, (n, t) in sorted(minutes.items())))

    retry_after = Counter(r.get("retry_after", "") for r in rows if r["status"] == "503")
    print("Retry-After on 503s: " + ", ".join(f"{v or 'none'} x{n}" for v, n in retry_after.most_common()))
    limits = Counter(r.get("rl_limit", "") for r in rows if r["status"] == "200")
    remaining = [int(r["rl_remaining"]) for r in rows if r["status"] == "200" and r.get("rl_remaining")]
    print("X-RateLimit-Limit on 200s: " + ", ".join(f"{v or 'none'} x{n}" for v, n in limits.most_common())
          + f"; X-RateLimit-Remaining min {min(remaining, default=0)}"
          f" p50 {pct(remaining, 50)} max {max(remaining, default=0)}")
    # The bucket's level on the last 200 before each 503: low = we drained it, high = not us.
    before, last = [], None
    for r in rows:
      if r["status"] == "200" and r.get("rl_remaining"):
        last = int(r["rl_remaining"])
      elif r["status"] == "503" and last is not None:
        before.append(last)
    print(f"X-RateLimit-Remaining on the 200 before each 503: {sorted(before)}")

    diff = Counter()
    for k, v in run["cache"].items():
      if k not in real:
        diff["not in real cache"] += 1
      elif v == real[k]:
        diff["same"] += 1
      elif v is None:
        diff["null here, year in real"] += 1
      elif real[k] is None:
        diff["year here, null in real"] += 1
      else:
        diff["different year"] += 1
    print("Years vs real cache: " + ", ".join(f"{k} {n}" for k, n in diff.most_common()))

  if len(runs) > 1:
    def label(a):
      meta = next(r["meta"] for r in runs if any(x["arm"] == a for x in r["meta"]["arms"]))
      return arm_label(meta, a)
    table(f"All {len(runs)} runs pooled",
          [(label(a), arm_stats(pooled_rows[a], pooled_chunks[a])) for a in sorted(pooled_rows)])


if __name__ == "__main__":
  if len(sys.argv) < 2:
    raise SystemExit(__doc__)
  main(sys.argv[1:])
