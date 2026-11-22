"""Score the 2026 release indexes against M0's release-year cache (AOTY-13).

  .venv/bin/python tools/mb_index_eval.py report --dump data/mb_index/dump-<name> \
      --crawl data/mb_index/crawl-<ts> [--crawl data/mb_index/crawl-<ts2>]
  .venv/bin/python tools/mb_index_eval.py track-probe --dump data/mb_index/dump-<name>

Ground truth is every album M0 looked up (data/mb_bench/candidates.json: raw artist/album
pairs, no usernames) with its year from data/mb_release_year_cache.json: 2026, another year,
or null (no MusicBrainz match). Scoring against the whole cache, not just the CSV's 2026
albums, also measures false positives and what the index finds where live search didn't.

For each album the index lookup mirrors pick_year: candidates are the release groups whose
norm(title) equals norm(album) or norm(base_title(album)), and pick_year chooses among them.
  dump:  keymap.json candidates span every year, so an older same-title release group can
         still win (index says "other").
  crawl: only 2026 release groups, so the answer is 2026 or nothing.

`track-probe` (hits the MusicBrainz web service; run it alone) times one release search per
2026 release group for a few samples, to price track counts for the 2026 subset.

Writes report.md next to the dump folder's meta.json.
"""

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import aoty  # noqa: E402

YEAR = "2026"
CANDIDATES = REPO / "data" / "mb_bench" / "candidates.json"


def load(path):
  return json.loads(Path(path).read_text(encoding="utf-8"))


def truth_pairs():
  cache = load(aoty.CACHE_FILE)
  pairs = [tuple(p) for p in load(CANDIDATES)["albums"]]
  out = []
  for artist, album in pairs:
    key = aoty.cache_key(artist, album)
    if key in cache:
      year = cache[key]
      out.append((artist, album, YEAR if year == YEAR else ("null" if year is None else "other")))
  return out


def title_keys(album):
  return dict.fromkeys([aoty.norm(album), aoty.norm(aoty.base_title(album))])


def by_title(records):
  out = defaultdict(list)
  for rec in records:
    out[aoty.norm(rec["title"])].append(rec)
  return out


def answer(artist, album, titlemap):
  cands = {rec["id"]: rec for k in title_keys(album) for rec in titlemap.get(k, [])}
  year = aoty.pick_year(artist, album, list(cands.values()))
  return YEAR if year == YEAR else ("other" if year else "none")


def why_missed(artist, album, idx_titles, idx_artists):
  """Why a 2026 album (per M0) isn't found as 2026 in the index."""
  if any(k in idx_titles for k in title_keys(album)):
    return "title in index, artist credit differs"
  if aoty.norm(artist) in idx_artists:
    return "artist has 2026 release groups, title differs"
  return "no 2026 release group with this title or artist (date or data changed?)"


def table(rows, cols, title):
  lines = [f"\n### {title}\n", "| M0 cache \\ index | " + " | ".join(cols) + " | total |"]
  lines.append("|" + "---|" * (len(cols) + 2))
  for truth in (YEAR, "other", "null"):
    counts = Counter(i for t, i in rows if t == truth)
    lines.append(f"| {truth} | " + " | ".join(str(counts[c]) for c in cols) + f" | {sum(counts.values())} |")
  return lines


def score(name, titlemap, idx_2026, pairs, cols):
  rows, examples = [], defaultdict(list)
  idx_titles = {aoty.norm(r["title"]) for r in idx_2026.values()}
  idx_artists = set()
  for r in idx_2026.values():
    idx_artists |= aoty.credit_names(r)
  for artist, album, truth in pairs:
    got = answer(artist, album, titlemap)
    rows.append((truth, got))
    if truth == YEAR and got != YEAR:
      examples[f"miss: {why_missed(artist, album, idx_titles, idx_artists)} (index: {got})"].append(
        f"{artist} — {album}"
      )
    elif truth != YEAR and got == YEAR:
      examples[f"index says {YEAR}, M0 says {truth}"].append(f"{artist} — {album}")
  lines = table(rows, cols, name)
  hits = sum(1 for t, g in rows if t == YEAR and g == YEAR)
  total = sum(1 for t, _ in rows if t == YEAR)
  false_pos = sum(1 for t, g in rows if t == "other" and g == YEAR)
  lines.append(
    f"\nRecall of M0's {YEAR} albums: **{hits}/{total} ({100 * hits / total:.1f}%)**;"
    f" M0 other-year albums the index calls {YEAR}: {false_pos}."
  )
  for reason, items in sorted(examples.items(), key=lambda kv: -len(kv[1])):
    lines.append(f"\n- {reason}: {len(items)}")
    lines += [f"  - {x}" for x in items[:8]]
  return lines


def sizes(meta, key):
  s = meta.get(key, {})
  return f"{s.get('bytes', 0) / 1e6:.1f} MB JSON, {s.get('gzip_bytes', 0) / 1e6:.1f} MB gzipped"


def report(args):
  dump_dir = Path(args.dump)
  dmeta = load(dump_dir / "meta.json")
  keymap = load(dump_dir / "keymap.json")
  dump_2026 = load(dump_dir / "index_2026.json")
  pairs = truth_pairs()
  lines = [f"# AOTY-13 release index report ({time.strftime('%Y-%m-%d %H:%M')})", ""]
  lines.append(
    f"Ground truth: {len(pairs)} albums M0 looked up ("
    + ", ".join(f"{k} {v}" for k, v in Counter(t for *_, t in pairs).items())
    + ")."
  )

  lines.append("\n## Dump (`release-group.tar.xz`)\n")
  lines.append(
    f"- Dump {dmeta['dump']}: download {dmeta.get('download_s')} s at {dmeta.get('download_mbps')} MB/s"
  )
  lines.append(
    f"- Parse: {dmeta['parse_wall_s']} s wall, {dmeta['parse_cpu_s']} s CPU, peak RSS {dmeta['peak_rss_mb']} MB,"
    f" {dmeta['counts']['records']:,} records ({dmeta['counts'].get('undated', 0):,} undated)"
  )
  lines.append(f"- {YEAR} release groups: {len(dump_2026):,}; index_2026.json {sizes(dmeta, 'index_2026')}")
  lines.append(
    f"- keymap.json ({dmeta['keymap_titles']:,} titles, {dmeta['keymap_records']:,} candidates of any year):"
    f" {sizes(dmeta, 'keymap')}"
  )
  types = {k: v for k, v in dmeta["counts"].items() if k.startswith(YEAR)}
  lines.append("- " + ", ".join(f"{k}: {v:,}" for k, v in types.items()))
  lines += score("Dump keymap (all years, pick_year)", keymap, dump_2026, pairs, [YEAR, "other", "none"])
  lines += score(
    "Dump 2026 subset only (no other-year candidates)",
    by_title(dump_2026.values()),
    dump_2026,
    pairs,
    [YEAR, "other", "none"],
  )

  for crawl in args.crawl or []:
    cdir = Path(crawl)
    cmeta = load(cdir / "meta.json")
    crawl_2026 = load(cdir / "index_2026.json")
    lines.append(f"\n## Search crawl `{cdir.name}`\n")
    lines.append(
      f"- {cmeta['wall_s'] / 60:.0f} min, {cmeta['requests']} requests ({cmeta['503s']} 503s),"
      f" {len(cmeta['partitions'])} id partitions ({cmeta['splits']} splits),"
      f" failed pages left: {len(cmeta['failed'])}"
    )
    lines.append(
      f"- Count {cmeta['overall_count_start']:,} at start, {cmeta['overall_count_end']:,} at end;"
      f" partitions sum to {cmeta['sum_partition_counts']:,}; returned {cmeta['unique_release_groups']:,};"
      f" short partitions: {len(cmeta['short_partitions'])}"
    )
    lines.append(f"- index_2026.json {sizes(cmeta, 'index_2026')}")
    only_c = crawl_2026.keys() - dump_2026.keys()
    only_d = dump_2026.keys() - crawl_2026.keys()
    lines.append(
      f"- vs dump: both {len(crawl_2026.keys() & dump_2026.keys()):,}, only crawl {len(only_c):,},"
      f" only dump {len(only_d):,}"
    )
    for label, ids, src in (("only crawl", only_c, crawl_2026), ("only dump", only_d, dump_2026)):
      for i in sorted(ids)[:5]:
        r = src[i]
        lines.append(f"  - {label}: {r['first-release-date']} {r['primary-type']} — {r['title']} ({i})")
    lines += score(
      f"Crawl {cdir.name} (2026 only)", by_title(crawl_2026.values()), crawl_2026, pairs, [YEAR, "none"]
    )

  probe = dump_dir / "release_probe.json"
  if probe.exists():
    p = load(probe)
    lines.append("\n## Release dump probe (`release.tar.xz`)\n")
    lines.append(
      f"- Read {p['compressed_read_mb']:,} of {p['archive_mb']:,} MB compressed: {p['records']:,} releases"
      f" in {p['wall_s']} s ({p['records_per_s']:,}/s, {p['compressed_mbps']} MB/s compressed)"
    )
    lines.append(
      f"- Extrapolated full pass: {p['extrapolated_full_s'] / 3600:.1f} h, ~{p['extrapolated_records']:,} releases"
    )
    lines.append(f"- Fields: {p['fields_present']}; media with track-count: {p['with_media_track_count']:,}")
  tprobe = dump_dir / "track_probe.json"
  if tprobe.exists():
    t = load(tprobe)
    lines.append("\n## Live track-count probe\n")
    lines.append(
      f"- {t['release_groups']} release groups, {t['requests']} requests ({t['503s']} 503s), {t['wall_s']} s:"
      f" {t['per_rg_s']} s per release group → {t['extrapolated_h']} h for {t['subset']:,} {YEAR} albums+EPs"
    )
    lines.append(f"- Track counts found for {t['with_tracks']}/{t['release_groups']}")

  out = dump_dir / "report.md"
  out.write_text("\n".join(lines) + "\n", encoding="utf-8")
  print("\n".join(lines))
  print(f"\nWrote {out}")


def track_probe(args):
  dump_dir = Path(args.dump)
  idx = load(dump_dir / "index_2026.json")
  subset = [r for r in idx.values() if r["primary-type"] in ("Album", "EP")]
  sample = random.Random(2026).sample(subset, args.n)
  aoty.MB_STATS_CSV = dump_dir / "track_probe_requests.csv"
  aoty.reset_run_state()
  t0, with_tracks = time.monotonic(), 0
  for rg in sample:
    releases = aoty.mb_search(f"rgid:{rg['id']}", limit=100, entity="release")
    if releases and any(r.get("track-count") for r in releases):
      with_tracks += 1
  wall = time.monotonic() - t0
  per = wall / len(sample)
  meta = {
    "release_groups": len(sample),
    "requests": len(aoty.MB_STATS),
    "503s": sum(1 for s in aoty.MB_STATS if s["status"] == 503),
    "wall_s": round(wall, 1),
    "per_rg_s": round(per, 2),
    "subset": len(subset),
    "extrapolated_h": round(per * len(subset) / 3600, 1),
    "with_tracks": with_tracks,
  }
  (dump_dir / "track_probe.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
  aoty.mb_report("mb_index_eval")
  print(json.dumps(meta, indent=1))


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  sub = parser.add_subparsers(dest="cmd", required=True)
  p = sub.add_parser("report")
  p.add_argument("--dump", required=True)
  p.add_argument("--crawl", action="append")
  p.set_defaults(fn=report)
  p = sub.add_parser("track-probe")
  p.add_argument("--dump", required=True)
  p.add_argument("-n", type=int, default=30)
  p.set_defaults(fn=track_probe)
  args = parser.parse_args()
  args.fn(args)


if __name__ == "__main__":
  main()
