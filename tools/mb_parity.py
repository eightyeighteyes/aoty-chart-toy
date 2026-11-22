"""AOTY-19 acceptance check: the mb lane's resolver vs M0's caches, on the frozen M0 cold-run set.

  .venv/bin/python tools/mb_parity.py                 # all ~1,536 candidates, ~20-30 min
  .venv/bin/python tools/mb_parity.py --limit 40      # a quick dry run
  .venv/bin/python tools/mb_parity.py --resume DIR    # continue a killed run
  .venv/bin/python tools/mb_parity.py --report DIR    # rewrite report.md only

Runs the Lambda's own code on the laptop: lambdas/mb/mb_core.resolve with the standard-library
clients (common.musicbrainz, common.lastfm), in batches of 20 like the handler, over
data/mb_bench/candidates.json (artist and album names, no usernames). Each resolved item is
appended to data/mb_parity/run-<ts>/items.jsonl, then report.md compares them with
data/mb_release_year_cache.json (years) and data/mb_track_count_cache.json (track counts, which
M0 only has for its ~570 target-year albums).

This is a MusicBrainz client like any other: don't run it alongside aoty.py, the benchmarks, or
a deployed mb Lambda that has work queued. It only reads M0's caches, and checks they're
unchanged afterwards.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lambdas"))
sys.path.insert(0, str(REPO / "lambdas" / "mb"))

import aoty  # noqa: E402  (loads .env; M0's cache keys)
import mb_core  # noqa: E402
from common import lastfm, musicbrainz  # noqa: E402
from common.albumkey import album_key  # noqa: E402
from common.mbmatch import is_single  # noqa: E402

CANDIDATES = aoty.DATA_DIR / "mb_bench" / "candidates.json"
YEAR_CACHE = aoty.CACHE_FILE
TRACK_CACHE = aoty.DATA_DIR / "mb_track_count_cache.json"
OUT_DIR = aoty.DATA_DIR / "mb_parity"
BATCH = 20
TARGET_YEAR = int(aoty.TARGET_YEAR)


def sha256(path):
  return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def load_json(path):
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return {}


def message(artist, album):
  artist_key, title_key = album_key(artist, album)
  return {"artist_key": artist_key, "title_key": title_key, "artist": artist, "album": album, "mbid": ""}


def run(run_dir, limit=None):
  albums = [tuple(a) for a in json.loads(CANDIDATES.read_text(encoding="utf-8"))["albums"]]
  if limit:
    albums = albums[:limit]
  items_file, failed_file = run_dir / "items.jsonl", run_dir / "failed.jsonl"
  done = set()
  if items_file.exists():
    done = {(i["artist_key"], i["title_key"]) for i in map(json.loads, items_file.read_text().splitlines())}
  msgs = [m for m in (message(a, b) for a, b in albums) if mb_core.key_of(m) not in done]
  print(f"{len(albums)} candidates, {len(done)} already resolved in {run_dir.name}, {len(msgs)} to go")

  mb = musicbrainz.Client(musicbrainz.user_agent("parity", os.environ.get("MB_CONTACT", "")))
  fm = lastfm.Client(aoty.LASTFM_API_KEY)
  t0 = time.monotonic()
  totals = Counter()
  for i in range(0, len(msgs), BATCH):
    batch = msgs[i : i + BATCH]
    result = mb_core.resolve(
      batch, search=mb.search, lastfm_tracks=fm.album_track_count, target_year=TARGET_YEAR, now=time.time()
    )
    with open(items_file, "a", encoding="utf-8") as f:
      for item in result.items.values():
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(failed_file, "a", encoding="utf-8") as f:
      for key in result.failed:
        f.write(json.dumps(list(key), ensure_ascii=False) + "\n")
    totals.update({k: v for k, v in result.stats.items() if isinstance(v, int)})
    rate = totals["resolved"] / max(time.monotonic() - t0, 1e-9)
    print(f"  batch {i // BATCH + 1}/{-(-len(msgs) // BATCH)}: {totals['resolved']} resolved, "
          f"{totals['failed']} failed, {rate:.2f} albums/s")  # fmt: skip
  summary = mb.summary()
  (run_dir / "mb_summary.json").write_text(json.dumps({**summary, **totals, "seconds": time.monotonic() - t0}))
  print(f"MusicBrainz: {summary['requests']} requests, {summary['throttled']} throttled")


def report(run_dir):
  """Sort every candidate into a parity category and write report.md."""
  albums = [tuple(a) for a in json.loads(CANDIDATES.read_text(encoding="utf-8"))["albums"]]
  items = {(i["artist_key"], i["title_key"]): i for i in map(json.loads, (run_dir / "items.jsonl").read_text().splitlines())}
  years, tracks = load_json(YEAR_CACHE), load_json(TRACK_CACHE)

  cats, rows = Counter(), {}
  t_cats, t_rows = Counter(), {}
  for artist, album in dict.fromkeys(albums):
    item = items.get(album_key(artist, album))
    ck = aoty.cache_key(artist, album)
    if item is None:
      continue  # not looked up in this run (--limit) or failed: counted below
    m0 = years.get(ck, "absent")
    new = str(item["year"]) if item["year"] is not None else None
    if m0 == "absent":
      cat = "not in M0 cache"
    elif m0 == new:
      cat = "same"
    elif m0 is None:
      cat = "M0 null, now found"
    elif new is None:
      cat = "M0 found, now null"
    else:
      cat = "different year"
    cats[cat] += 1
    if cat != "same":
      rows.setdefault(cat, []).append((artist, album, m0, new, item.get("rg_mbid")))

    if ck in tracks and "tracks" in item and isinstance(tracks[ck], dict):
      old = tracks[ck]
      new_t = {"tracks": item["tracks"], "type": item["tracks_type"]}
      if old == new_t:
        t_cat = "same"
      elif old.get("tracks") and new_t["tracks"] and is_single(old) == is_single(new_t):
        t_cat = "count or type differs, same singles verdict"
      else:
        t_cat = "singles verdict or presence differs"
      t_cats[t_cat] += 1
      if t_cat != "same":
        t_rows.setdefault(t_cat, []).append((artist, album, old, new_t, item.get("tracks_source")))

  failed = [tuple(k) for k in map(json.loads, (run_dir / "failed.jsonl").read_text().splitlines())] if (
    run_dir / "failed.jsonl").exists() else []  # fmt: skip
  failed = [k for k in dict.fromkeys(failed) if k not in items]
  summary = load_json(run_dir / "mb_summary.json")

  out = [f"# mb lane parity: {run_dir.name}", ""]
  out.append(f"{len(items)} albums resolved, {len(failed)} still failed (left on the queue in production).")
  if summary:
    out.append(f"MusicBrainz: {summary.get('requests')} requests, {summary.get('throttled')} 503s, "
               f"{summary.get('seconds', 0) / 60:.1f} min.")  # fmt: skip
  out += ["", "## Years vs data/mb_release_year_cache.json", "", "| Category | Albums |", "|---|---|"]
  out += [f"| {c} | {n} |" for c, n in cats.most_common()]
  for cat, rs in sorted(rows.items()):
    out += ["", f"### {cat}", "", "| Artist | Album | M0 | Now | Release group |", "|---|---|---|---|---|"]
    out += [f"| {a} | {b} | {o} | {n} | {g or ''} |" for a, b, o, n, g in rs]
  out += ["", "## Track counts vs data/mb_track_count_cache.json (keys both have)", "",
          "| Category | Albums |", "|---|---|"]  # fmt: skip
  out += [f"| {c} | {n} |" for c, n in t_cats.most_common()]
  for cat, rs in sorted(t_rows.items()):
    out += ["", f"### {cat}", "", "| Artist | Album | M0 | Now | Source |", "|---|---|---|---|---|"]
    out += [f"| {a} | {b} | {o['tracks']} {o['type']} | {n['tracks']} {n['type']} | {s} |" for a, b, o, n, s in rs]
  (run_dir / "report.md").write_text("\n".join(out) + "\n", encoding="utf-8")
  print(f"Wrote {run_dir / 'report.md'}")
  for c, n in cats.most_common():
    print(f"  years   {c}: {n}")
  for c, n in t_cats.most_common():
    print(f"  tracks  {c}: {n}")


def main():
  p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  p.add_argument("--limit", type=int)
  p.add_argument("--resume", type=Path)
  p.add_argument("--report", type=Path, help="only rewrite the report for this run folder")
  args = p.parse_args()

  guarded = {path: sha256(path) for path in (YEAR_CACHE, TRACK_CACHE)}
  if args.report:
    report(args.report)
  else:
    run_dir = args.resume or OUT_DIR / time.strftime("run-%Y%m%d-%H%M%S")
    assert run_dir.resolve().is_relative_to(OUT_DIR.resolve()), f"runs go under {OUT_DIR}, away from M0's caches"
    run_dir.mkdir(parents=True, exist_ok=True)
    run(run_dir, args.limit)
    report(run_dir)
  for path, digest in guarded.items():
    assert sha256(path) == digest, f"{path} changed during the parity run"


if __name__ == "__main__":
  main()
