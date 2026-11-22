"""Build a 2026 release index from the MusicBrainz JSON data dump (AOTY-13).

  .venv/bin/python tools/mb_index_dump.py rg                 # download + parse release-group
  .venv/bin/python tools/mb_index_dump.py release-probe      # time the first ~1 GB of release

`rg` downloads release-group.tar.xz from the latest JSON dump to data/mb_index/dump-<name>/
(resumable, checked against SHA256SUMS), streams it once, and writes:

  index_2026.json  every release group first released in 2026: {id: minimal record}
  keymap.json      norm(title) -> every release group, of any year, with that title, for each
                   title that has at least one 2026 release group. aoty.pick_year runs on these
                   candidates offline, so "2019 album vs 2026 single with the same name" is
                   decided the way M0 decides it, not by the 2026 subset alone.
  meta.json        sizes, download MB/s, parse CPU time, peak RSS, record counts

A minimal record keeps only what pick_year reads: id, title, first-release-date,
primary-type, secondary-types and artist-credit names/joinphrases.

`release-probe` streams the start of release.tar.xz (21 GB) over HTTP without saving it,
to price track counts from the dump: parse rate, fields present, extrapolated full time.

The dump comes from data.metabrainz.org, not the rate-limited web service, so this can run
while a MusicBrainz API job runs.
"""

import argparse
import gzip
import json
import resource
import sys
import tarfile
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jobs" / "index"))
import aoty  # noqa: E402
import dump as mbdump  # noqa: E402  (shared with the AOTY-20 index build)

DUMPS = mbdump.DUMPS
OUT = REPO / "data" / "mb_index"
YEAR = "2026"
UA = aoty.MB_HEADERS["User-Agent"]


def http(url, start=0):
  return mbdump.http(url, UA, start)


def latest_dump():
  return mbdump.latest_dump(UA)


def peak_rss_mb():
  rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
  return rss / 1e6 if sys.platform == "darwin" else rss / 1e3  # bytes on macOS, KiB on Linux


def download(dump, name, dest):
  return mbdump.download(dump, name, dest, UA)


def minimal(rg):
  return {
    "id": rg["id"],
    "title": rg.get("title", ""),
    "first-release-date": rg.get("first-release-date") or "",
    "primary-type": rg.get("primary-type"),
    "secondary-types": rg.get("secondary-types") or [],
    "artist-credit": [
      {"name": c.get("name", ""), "joinphrase": c.get("joinphrase", "")} for c in rg.get("artist-credit", [])
    ],
  }


dump_lines = mbdump.dump_lines


def write_json(path, data):
  raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
  path.write_bytes(raw)
  return {"bytes": len(raw), "gzip_bytes": len(gzip.compress(raw, 6))}


def build_rg(args):
  dump = args.dump or latest_dump()
  out = OUT / f"dump-{dump}"
  out.mkdir(parents=True, exist_ok=True)
  archive = out / "release-group.tar.xz"
  meta = {"dump": dump, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "year": YEAR}
  meta |= download(dump, "release-group.tar.xz", archive)

  # Every dated release group, keyed by norm(title): pick_year skips undated ones.
  by_title = defaultdict(list)
  in_year = {}
  counts = defaultdict(int)
  cpu0, t0 = time.process_time(), time.monotonic()
  with open(archive, "rb") as f:
    for n, line in enumerate(dump_lines(f, "mbdump/release-group"), start=1):
      rg = json.loads(line)
      counts["records"] += 1
      date = rg.get("first-release-date") or ""
      if len(date) < 4:
        counts["undated"] += 1
        continue
      rec = minimal(rg)
      by_title[aoty.norm(rec["title"])].append(rec)
      if date[:4] == YEAR:
        in_year[rec["id"]] = rec
        counts[f"{YEAR} {rec['primary-type'] or 'none'}"] += 1
        if len(date) < 10:
          counts[f"{YEAR} partial date ({len(date)} chars)"] += 1
      if n % 500_000 == 0:
        print(f"  {n:,} records, {len(in_year):,} from {YEAR}, {time.monotonic() - t0:.0f} s", flush=True)
  meta["parse_wall_s"] = round(time.monotonic() - t0, 1)
  meta["parse_cpu_s"] = round(time.process_time() - cpu0, 1)
  meta["peak_rss_mb"] = round(peak_rss_mb())

  keys_with_year = {aoty.norm(r["title"]) for r in in_year.values()}
  keymap = {k: by_title[k] for k in keys_with_year}
  meta["counts"] = dict(sorted(counts.items()))
  meta["titles_all_years"] = len(by_title)
  meta["keymap_titles"] = len(keymap)
  meta["keymap_records"] = sum(len(v) for v in keymap.values())
  meta["index_2026"] = write_json(out / "index_2026.json", in_year)
  meta["keymap"] = write_json(out / "keymap.json", keymap)
  meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
  (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
  print(json.dumps(meta, indent=1))


class Counting:
  """File-like wrapper that counts bytes read and stops after `limit`."""

  def __init__(self, raw, limit):
    self.raw, self.limit, self.read_bytes = raw, limit, 0

  def read(self, n=-1):
    if self.read_bytes >= self.limit:
      return b""
    chunk = self.raw.read(n if n and n > 0 else 1 << 16)
    self.read_bytes += len(chunk)
    return chunk


def release_probe(args):
  dump = args.dump or latest_dump()
  url = f"{DUMPS}/{dump}/release.tar.xz"
  with http(url) as r:
    total = int(r.headers.get("Content-Length", 0))
    src = Counting(r, int(args.bytes))
    cpu0, t0 = time.process_time(), time.monotonic()
    n, fields, with_tracks = 0, defaultdict(int), 0
    sample = None
    try:
      for line in dump_lines(src, "mbdump/release"):
        rel = json.loads(line)
        n += 1
        for k in ("media", "track-count", "release-group", "status", "date"):
          fields[k] += k in rel
        if any("track-count" in m for m in rel.get("media", [])):
          with_tracks += 1
        if sample is None:
          rg = rel.get("release-group", {})
          sample = {
            "keys": sorted(rel),
            "media0_keys": sorted(rel.get("media", [{}])[0]) if rel.get("media") else [],
            "release_group_keys": sorted(rg),
          }
    except (EOFError, tarfile.ReadError):
      pass  # the cut-off stream ends mid-archive
  wall = time.monotonic() - t0
  compressed = src.read_bytes
  meta = {
    "dump": dump,
    "compressed_read_mb": round(compressed / 1e6),
    "archive_mb": round(total / 1e6),
    "records": n,
    "wall_s": round(wall, 1),
    "cpu_s": round(time.process_time() - cpu0, 1),
    "records_per_s": round(n / wall),
    "compressed_mbps": round(compressed / 1e6 / wall, 2),
    "fields_present": dict(fields),
    "with_media_track_count": with_tracks,
    "sample": sample,
    "extrapolated_full_s": round(wall * total / compressed) if compressed else None,
    "extrapolated_records": round(n * total / compressed) if compressed else None,
  }
  out = OUT / f"dump-{dump}"
  out.mkdir(parents=True, exist_ok=True)
  (out / "release_probe.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
  print(json.dumps(meta, indent=1))


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--dump", help="dump folder name (default: LATEST)")
  sub = parser.add_subparsers(dest="cmd", required=True)
  sub.add_parser("rg").set_defaults(fn=build_rg)
  p = sub.add_parser("release-probe")
  p.add_argument("--bytes", type=float, default=1e9, help="compressed bytes to read (default 1e9)")
  p.set_defaults(fn=release_probe)
  args = parser.parse_args()
  args.fn(args)


if __name__ == "__main__":
  main()
