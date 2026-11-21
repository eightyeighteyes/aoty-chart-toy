"""Last.fm data-cost spike (AOTY-11): what does an M1 year-to-date backfill cost?

Measures, against the real LASTFM_USERS accounts:
  - the year-to-date scrobble distribution (one cheap call per member),
  - a full user.getRecentTracks backfill for a light, medium and heavy account,
  - raw-scrobble vs per-(album, UTC day) storage size, plain and gzipped,
  - whether user.getWeeklyAlbumChart can replace paging (ranges, counts, current week),
  - which MBIDs / cover URLs come free, with and without extended=1,
  - edge cases: now-playing, nonexistent users, back-dated inserts (window recount).

Output goes to data/lastfm_spike/run-<timestamp>/ (git-ignored, like all of data/).
Members are labelled m01..mNN by year-to-date total and light/medium/heavy; usernames
are never written to the report. Raw pages are not kept (they carry @attr.user).

  .venv/bin/python tools/lastfm_spike.py                     # full run (a few minutes)
  .venv/bin/python tools/lastfm_spike.py --limit-pages 3     # quick dry run
  .venv/bin/python tools/lastfm_spike.py --backfill-only     # second run: failures/latency

Single-threaded at <= 4 req/s. Don't run anything else that hits Last.fm meanwhile.
"""
import argparse
import csv
import gzip
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aoty  # noqa: E402

API = "https://ws.audioscrobbler.com/2.0/"
YEAR_START = 1767225600  # 2026-01-01 00:00 UTC
PAGE_SIZE = 200          # getRecentTracks maximum
MIN_INTERVAL = 0.25      # 4 req/s, under Last.fm's ~5 req/s
MAX_ATTEMPTS = 5
RETRY_CODES = {8, 11, 16, 29}  # operation failed, offline, temporary, rate limited
PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"  # Last.fm's default "no image" star
FLEET = 100_000
FLEET_RPS = 5
SPIKE_DIR = aoty.DATA_DIR / "lastfm_spike"

ATTEMPTS = []   # one row per HTTP attempt
_last_request = [0.0]
_tag = {"label": "", "phase": ""}


class LastfmError(Exception):
  def __init__(self, code, message):
    super().__init__(f"Last.fm error {code}: {message}")
    self.code = code


def call(method, **params):
  """One Last.fm call, paced and retried. Returns (json, bytes, seconds) or raises LastfmError."""
  q = {"method": method, "api_key": aoty.LASTFM_API_KEY, "format": "json", **params}
  error = None
  for attempt in range(1, MAX_ATTEMPTS + 1):
    wait = _last_request[0] + MIN_INTERVAL - time.monotonic()
    if wait > 0:
      time.sleep(wait)
    _last_request[0] = time.monotonic()
    t0 = time.monotonic()
    row = {"method": method, **_tag, "attempt": attempt, "status": "", "code": "", "bytes": 0}
    try:
      r = requests.get(API, params=q, timeout=30)
      row["status"], row["bytes"] = r.status_code, len(r.content)
      data = r.json()
    except (requests.RequestException, ValueError) as e:
      row["seconds"] = round(time.monotonic() - t0, 3)
      row["code"] = "net" if isinstance(e, requests.RequestException) else "badjson"
      ATTEMPTS.append(row)
      error = LastfmError(row["code"], str(e))
      time.sleep(2 * attempt)
      continue
    row["seconds"] = round(time.monotonic() - t0, 3)
    if "error" in data:
      row["code"] = data["error"]
      ATTEMPTS.append(row)
      error = LastfmError(data["error"], data.get("message", ""))
      if data["error"] in RETRY_CODES:
        time.sleep(2 * attempt)
        continue
      raise error
    ATTEMPTS.append(row)
    if r.status_code >= 500:
      error = LastfmError(r.status_code, "server error")
      time.sleep(2 * attempt)
      continue
    return data, row["bytes"], row["seconds"]
  raise error


# ---- scrobble parsing -------------------------------------------------------

def _text(v):
  if isinstance(v, dict):
    return v.get("#text") or v.get("name") or ""
  return v or ""


def _image(images):
  """Largest image URL, or "" when missing or Last.fm's placeholder."""
  url = ""
  for im in images or []:
    if im.get("#text"):
      url = im["#text"]
  return "" if (not url or PLACEHOLDER in url) else url


def parse_track(t):
  artist = t.get("artist", {})
  album = t.get("album", {})
  return {
      "uts": int(t["date"]["uts"]) if t.get("date") else None,
      "nowplaying": (t.get("@attr") or {}).get("nowplaying") == "true",
      "artist": _text(artist),
      "album": _text(album),
      "track": t.get("name", ""),
      "artist_mbid": (artist.get("mbid") if isinstance(artist, dict) else "") or "",
      "album_mbid": (album.get("mbid") if isinstance(album, dict) else "") or "",
      "track_mbid": t.get("mbid") or "",
      "image": _image(t.get("image")),
  }


def recent_page(user, page, frm, to, extended=True, limit=PAGE_SIZE):
  params = {"user": user, "page": page, "limit": limit, "from": frm, "to": to}
  if extended:
    params["extended"] = 1
  data, nbytes, secs = call("user.getrecenttracks", **params)
  rt = data.get("recenttracks", {})
  tracks = rt.get("track", [])
  if isinstance(tracks, dict):
    tracks = [tracks]
  attr = rt.get("@attr", {})
  return [parse_track(t) for t in tracks], attr, nbytes, secs


# ---- phases -----------------------------------------------------------------

def distribution(users, to):
  """Year-to-date total per member: one limit=1 call each, plus getInfo playcount."""
  members, errors = [], Counter()
  _tag["phase"] = "distribution"
  for u in users:
    try:
      _, attr, _, _ = recent_page(u, 1, YEAR_START, to, extended=False, limit=1)
      info, _, _ = call("user.getinfo", user=u)
      members.append({"user": u, "total": int(attr.get("total", 0)),
                      "playcount": int(info["user"].get("playcount", 0))})
    except LastfmError as e:
      errors[e.code] += 1
  members.sort(key=lambda m: m["total"])
  for i, m in enumerate(members, 1):
    m["label"] = f"m{i:02d}"
  return members, errors


def backfill(member, to, limit_pages):
  """Page getRecentTracks (extended=1) over [YEAR_START, to]. Returns scrobbles + stats."""
  _tag.update(label=member["label"], phase="backfill")
  rows, pages, nowplaying_pages = [], [], 0
  page, total_pages = 1, 1
  started = time.monotonic()
  while page <= total_pages and (not limit_pages or page <= limit_pages):
    tracks, attr, nbytes, secs = recent_page(member["user"], page, YEAR_START, to)
    total_pages = int(attr.get("totalPages", 1) or 1)
    nowplaying_pages += any(t["nowplaying"] for t in tracks)
    rows += [t for t in tracks if not t["nowplaying"]]
    pages.append({"bytes": nbytes, "seconds": secs})
    page += 1
  wall = time.monotonic() - started
  keys = Counter((r["uts"], r["artist"], r["track"]) for r in rows)
  return rows, {
      "pages": len(pages), "total_pages": total_pages, "wall": wall,
      "bytes": sum(p["bytes"] for p in pages),
      "p50": statistics.median(p["seconds"] for p in pages) if pages else 0,
      "duplicates": sum(c - 1 for c in keys.values() if c > 1),
      "nowplaying_pages": nowplaying_pages,
      "complete": page > total_pages,
  }


def daily_totals(rows):
  """{(norm artist, norm album, UTC date): plays} with display names and MBID kept."""
  out = {}
  for r in rows:
    day = datetime.fromtimestamp(r["uts"], timezone.utc).strftime("%Y-%m-%d")
    k = (aoty.norm(r["artist"]), aoty.norm(r["album"]), day)
    e = out.setdefault(k, {"artist": r["artist"], "album": r["album"], "day": day,
                           "plays": 0, "album_mbid": r["album_mbid"]})
    e["plays"] += 1
  return list(out.values())


def sizes(obj):
  raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
  return len(raw), len(gzip.compress(raw, 6))


def storage(rows):
  raw = [{k: r[k] for k in ("uts", "artist", "album", "track", "artist_mbid",
                            "album_mbid", "track_mbid")} for r in rows]
  daily = daily_totals(rows)
  rb, rgz = sizes(raw)
  db, dgz = sizes(daily)
  return {"scrobbles": len(rows), "daily_rows": len(daily),
          "raw_json": rb, "raw_gz": rgz, "daily_json": db, "daily_gz": dgz}


def coverage(rows):
  """Share of scrobbles and of distinct albums with an album MBID / a real cover."""
  albums = {}
  for r in rows:
    k = (aoty.norm(r["artist"]), aoty.norm(r["album"]))
    a = albums.setdefault(k, {"mbid": False, "image": False})
    a["mbid"] |= bool(r["album_mbid"])
    a["image"] |= bool(r["image"])
  n, na = len(rows) or 1, len(albums) or 1
  return {
      "scrobbles": len(rows), "albums": len(albums),
      "mbid_scrobbles": sum(bool(r["album_mbid"]) for r in rows) / n,
      "image_scrobbles": sum(bool(r["image"]) for r in rows) / n,
      "mbid_albums": sum(a["mbid"] for a in albums.values()) / na,
      "image_albums": sum(a["image"] for a in albums.values()) / na,
      "no_album": sum(not r["album"] for r in rows) / n,
  }


def extended_compare(member, to, n_pages):
  """Same pages with and without extended=1: coverage, bytes, latency."""
  out = {}
  for ext in (True, False):
    _tag.update(label=member["label"], phase=f"ext{int(ext)}")
    rows, nbytes, secs = [], [], []
    for page in range(1, n_pages + 1):
      tracks, attr, b, s = recent_page(member["user"], page, YEAR_START, to, extended=ext)
      rows += [t for t in tracks if not t["nowplaying"]]
      nbytes.append(b)
      secs.append(s)
      if page >= int(attr.get("totalPages", 1) or 1):
        break
    out["extended" if ext else "plain"] = {
        **coverage(rows), "bytes_per_page": statistics.mean(nbytes),
        "p50": statistics.median(secs), "pages": len(nbytes)}
  return out


def count_window(rows, frm, to, inclusive_end):
  c = Counter()
  for r in rows:
    if frm <= r["uts"] < to or (inclusive_end and r["uts"] == to):
      c[(aoty.norm(r["artist"]), aoty.norm(r["album"]))] += 1
  return c


def weekly_chart(member, frm, to):
  data, _, _ = call("user.getweeklyalbumchart", user=member["user"], **{"from": frm, "to": to})
  wac = data.get("weeklyalbumchart", {})
  albums = wac.get("album", [])
  if isinstance(albums, dict):
    albums = [albums]
  c = Counter()
  for a in albums:
    c[(aoty.norm(_text(a.get("artist"))), aoty.norm(a.get("name", "")))] += int(a.get("playcount", 0))
  attr = wac.get("@attr", {})
  return c, albums, {"from": int(attr.get("from", 0) or 0), "to": int(attr.get("to", 0) or 0)}


def compare(chart, counted):
  keys = set(chart) | set(counted)
  exact = sum(chart.get(k, 0) == counted.get(k, 0) for k in keys)
  return {"albums_chart": len(chart), "albums_counted": len(counted),
          "plays_chart": sum(chart.values()), "plays_counted": sum(counted.values()),
          "exact_albums": exact / (len(keys) or 1),
          "only_chart": len(set(chart) - set(counted)),
          "only_counted": len(set(counted) - set(chart))}


def weekly(member, rows, to):
  """Weekly charts vs counted scrobbles over identical windows, plus arbitrary-range probes."""
  _tag.update(label=member["label"], phase="weekly")
  data, _, _ = call("user.getweeklychartlist", user=member["user"])
  charts = data.get("weeklychartlist", {}).get("chart", [])
  weeks = [(int(w["from"]), int(w["to"])) for w in charts if int(w["to"]) > YEAR_START]
  result = {"weeks": len(weeks), "first_week": weeks[0] if weeks else None,
            "last_week": weeks[-1] if weeks else None, "per_week": [], "has_mbid": 0,
            "has_image": 0, "rows": 0}
  year_chart = Counter()
  for frm, wto in weeks:
    chart, albums, _ = weekly_chart(member, frm, wto)
    year_chart.update(chart)
    result["rows"] += len(albums)
    result["has_mbid"] += sum(bool(a.get("mbid")) for a in albums)
    result["has_image"] += sum(bool(_image(a.get("image"))) for a in albums)
    lo = max(frm, YEAR_START)
    result["per_week"].append({
        "from": frm, "to": wto,
        "excl": compare(chart, count_window(rows, frm, wto, False)) if frm >= YEAR_START else None,
        "incl": compare(chart, count_window(rows, frm, wto, True)) if frm >= YEAR_START else None,
        "partial_plays_counted": sum(count_window(rows, lo, wto, False).values()),
    })
  full_weeks_from = next((f for f, _ in weeks if f >= YEAR_START), YEAR_START)
  last_to = weeks[-1][1] if weeks else to
  result["year_sum"] = compare(year_chart, count_window(rows, full_weeks_from, last_to, False))
  probes = {}
  if weeks:
    mid = weeks[len(weeks) // 2]
    probes["shifted_week"] = (mid[0] + 3 * 86400, mid[1] + 3 * 86400)
    probes["boundary_week"] = mid
  probes["one_month"] = (1772323200, 1775001600)  # 2026-03-01 .. 2026-04-01 UTC
  probes["year_to_date"] = (YEAR_START, to)
  probes["current_partial_week"] = (last_to, to)
  result["probes"] = {}
  for name, (frm, pto) in probes.items():
    try:
      chart, albums, echoed = weekly_chart(member, frm, pto)
      result["probes"][name] = {"asked": [frm, pto], "echoed": [echoed["from"], echoed["to"]],
                                **compare(chart, count_window(rows, frm, pto, False))}
    except LastfmError as e:
      result["probes"][name] = {"asked": [frm, pto], "error": e.code}
  return result


def deltas(rows, to):
  out = {}
  for name, secs in (("1 day", 86400), ("1 week", 7 * 86400), ("30 days", 30 * 86400)):
    n = sum(r["uts"] > to - secs for r in rows)
    out[name] = {"scrobbles": n, "pages": max(1, math.ceil(n / PAGE_SIZE))}
  return out


def edge_cases(members, to):
  _tag.update(label="edge", phase="edge")
  out = {}
  try:
    call("user.getrecenttracks", user=f"aoty-spike-{random.getrandbits(48):012x}", limit=1)
    out["nonexistent"] = "no error"
  except LastfmError as e:
    out["nonexistent"] = str(e)
  # Now-playing vs pinned window: a 60 s window ending an hour ago should hold no tracks.
  heavy = members[-1]
  tracks, attr, _, _ = recent_page(heavy["user"], 1, to - 3660, to - 3600, extended=False)
  out["past_window_nowplaying"] = any(t["nowplaying"] for t in tracks)
  out["past_window_tracks"] = len(tracks)
  return out


# ---- report -----------------------------------------------------------------

def pct(values, p):
  values = sorted(values)
  return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))] if values else 0


def kb(n):
  return f"{n / 1024:,.1f} KB"


def gb(n):
  return f"{n / 1024**3:,.2f} GB"


def utc(ts):
  return datetime.fromtimestamp(ts, timezone.utc).strftime("%a %Y-%m-%d %H:%M UTC")


def report(run, args):
  L = []
  w = L.append
  m = run["members"]
  totals = [x["total"] for x in m]
  pages = [max(1, math.ceil(t / PAGE_SIZE)) for t in totals]
  w(f"# Last.fm data-cost spike: run {run['started']}\n")
  w(f"Window: {utc(YEAR_START)} to {utc(run['to'])} (pinned `to`). "
    f"{len(m)} members, {len(ATTEMPTS)} HTTP attempts at <= {1 / MIN_INTERVAL:.0f} req/s.\n")

  w("## Year-to-date distribution (one call per member)\n")
  w("| | scrobbles | backfill pages |\n|---|---:|---:|")
  for name, fn in (("min", min), ("median", statistics.median), ("mean", statistics.mean),
                   ("p95", lambda v: pct(v, 95)), ("max", max)):
    w(f"| {name} | {fn(totals):,.0f} | {fn(pages):,.0f} |")
  if run["dist_errors"]:
    w(f"\nMembers that errored: {dict(run['dist_errors'])}")
  mean_p, p95_p = statistics.mean(pages), pct(pages, 95)
  w(f"\nFleet of {FLEET:,} shaped like this server: {mean_p * FLEET:,.0f} calls "
    f"(mean), {mean_p * FLEET / FLEET_RPS / 3600:,.0f} h at {FLEET_RPS} req/s from one IP. "
    f"p95 user: {p95_p} pages, {p95_p / FLEET_RPS:.0f} s at {FLEET_RPS} req/s.\n")

  w("## Backfill (getRecentTracks, extended=1, limit=200)\n")
  w("| account | scrobbles | pages | wall | p50/page | bytes | dupes | now-playing pages | complete |")
  w("|---|---:|---:|---:|---:|---:|---:|---:|---|")
  for b in run["backfills"]:
    s = b["stats"]
    w(f"| {b['role']} ({b['label']}) | {b['storage']['scrobbles']:,} | {s['pages']}/{s['total_pages']} | "
      f"{s['wall']:.0f} s | {s['p50']:.2f} s | {kb(s['bytes'])} | {s['duplicates']} | "
      f"{s['nowplaying_pages']} | {s['complete']} |")
  codes = Counter(str(a["code"]) for a in ATTEMPTS if a["code"] != "")
  retried = sum(a["attempt"] > 1 for a in ATTEMPTS)
  w(f"\nErrors by code (all phases): {dict(codes) or 'none'}; retried attempts: {retried}. "
    f"Error 29 (rate limit): {codes.get('29', 0)}.\n")

  w("### Recount (back-dated inserts)\n")
  w("Same pinned window counted again at the end of the run. A change means scrobbles "
    "landed behind the high-water mark during the run.\n")
  w("| account | total at start | total at end | getInfo playcount delta during run |\n|---|---:|---:|---:|")
  for b in run["backfills"]:
    r = b["recount"]
    w(f"| {b['role']} | {r['start']:,} | {r['end']:,} | {r['playcount_delta']:,} |")

  w("\n### Delta fetch cost\n")
  w("| account | 1 day | 1 week | 30 days |\n|---|---|---|---|")
  for b in run["backfills"]:
    d = b["deltas"]
    w(f"| {b['role']} | " + " | ".join(f"{d[k]['scrobbles']} scr / {d[k]['pages']} p"
                                       for k in ("1 day", "1 week", "30 days")) + " |")

  w("\n## Storage\n")
  w("Raw = one row per scrobble (uts, artist, album, track, 3 MBIDs). Daily = one row per "
    "(normalised artist, album, UTC date) with plays and album MBID. JSON, compact.\n")
  w("| account | scrobbles | raw | raw gz | daily rows | daily | daily gz | daily/raw |")
  w("|---|---:|---:|---:|---:|---:|---:|---:|")
  per_scrobble = []
  for b in run["backfills"]:
    st = b["storage"]
    w(f"| {b['role']} | {st['scrobbles']:,} | {kb(st['raw_json'])} | {kb(st['raw_gz'])} | "
      f"{st['daily_rows']:,} | {kb(st['daily_json'])} | {kb(st['daily_gz'])} | "
      f"{st['daily_json'] / (st['raw_json'] or 1):.0%} |")
    if st["scrobbles"]:
      per_scrobble.append({k: st[k] / st["scrobbles"] for k in ("raw_json", "raw_gz", "daily_json", "daily_gz")})
  if per_scrobble:
    fleet_scr = statistics.mean(totals) * FLEET
    w(f"\nx{FLEET:,} users at this server's mean ({statistics.mean(totals):,.0f} scrobbles), "
      "using the range of bytes/scrobble across the three accounts:\n")
    w("| | low | high |\n|---|---:|---:|")
    for k in ("raw_json", "raw_gz", "daily_json", "daily_gz"):
      vals = [p[k] for p in per_scrobble]
      w(f"| {k} | {gb(min(vals) * fleet_scr)} | {gb(max(vals) * fleet_scr)} |")
    w("\nDB rows add index overhead on top of these JSON sizes.")

  if run.get("weekly"):
    w("\n## Weekly album charts\n")
    for wk in run["weekly"]:
      res = wk["result"]
      w(f"### {wk['role']}\n")
      if res["first_week"]:
        w(f"{res['weeks']} chart weeks overlap 2026. First: {utc(res['first_week'][0])} to "
          f"{utc(res['first_week'][1])}. Last: {utc(res['last_week'][0])} to "
          f"{utc(res['last_week'][1])}. Calls for a weekly backfill: {res['weeks'] + 1} "
          f"(chart list + one per week). Break-even vs paging: {(res['weeks'] + 1) * PAGE_SIZE:,} scrobbles.\n")
      full = [p for p in res["per_week"] if p["excl"]]
      if full:
        ex = statistics.mean(p["excl"]["exact_albums"] for p in full)
        inc = statistics.mean(p["incl"]["exact_albums"] for p in full)
        diff = [p["excl"]["plays_chart"] - p["excl"]["plays_counted"] for p in full]
        w(f"Full weeks: mean exact-album match {ex:.1%} with `[from, to)`, {inc:.1%} with "
          f"`[from, to]`. Chart minus counted plays per week: min {min(diff)}, median "
          f"{statistics.median(diff)}, max {max(diff)}.\n")
      ys = res["year_sum"]
      w(f"Summed over full weeks: chart {ys['plays_chart']:,} plays on {ys['albums_chart']} albums; "
        f"counted {ys['plays_counted']:,} plays on {ys['albums_counted']} albums; "
        f"exact per-album match {ys['exact_albums']:.1%}; only in chart {ys['only_chart']}, "
        f"only counted {ys['only_counted']}.\n")
      w(f"Chart rows with an album MBID: {res['has_mbid'] / (res['rows'] or 1):.0%}; "
        f"with a real image: {res['has_image'] / (res['rows'] or 1):.0%}.\n")
      w("| probe | asked | echoed | chart plays | counted plays | exact albums |\n|---|---|---|---:|---:|---:|")
      for name, p in res["probes"].items():
        asked = f"{utc(p['asked'][0])} to {utc(p['asked'][1])}"
        if "error" in p:
          w(f"| {name} | {asked} | error {p['error']} | | | |")
          continue
        echoed = f"{p['echoed'][0]}–{p['echoed'][1]}" if p["echoed"][0] else "-"
        w(f"| {name} | {asked} | {echoed} | {p['plays_chart']:,} | {p['plays_counted']:,} | "
          f"{p['exact_albums']:.0%} |")
      w("")

  if run.get("extended"):
    w("## Free metadata (same pages, heavy account)\n")
    w("| mode | pages | bytes/page | p50 | album MBID (scrobbles) | album MBID (albums) | "
      "cover (scrobbles) | cover (albums) | no album name |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for mode, c in run["extended"].items():
      w(f"| {mode} | {c['pages']} | {kb(c['bytes_per_page'])} | {c['p50']:.2f} s | "
        f"{c['mbid_scrobbles']:.0%} | {c['mbid_albums']:.0%} | {c['image_scrobbles']:.0%} | "
        f"{c['image_albums']:.0%} | {c['no_album']:.1%} |")
    w("\nCover = largest image URL, excluding empty strings and the placeholder star.\n")

  if run.get("edge"):
    e = run["edge"]
    w("## Edge cases\n")
    w(f"- Nonexistent user: {e['nonexistent']}")
    w(f"- 60 s window an hour in the past (heavy account): {e['past_window_tracks']} tracks, "
      f"now-playing present: {e['past_window_nowplaying']}")
    w("- Private (\"hide recent listening\") accounts: see members that errored above.")
  return "\n".join(L) + "\n"


def main():
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--limit-pages", type=int, default=0, help="cap backfill pages per account (dry run)")
  ap.add_argument("--backfill-only", action="store_true", help="skip weekly/extended/edge phases")
  ap.add_argument("--ext-pages", type=int, default=10, help="pages for the extended=1 comparison")
  args = ap.parse_args()
  aoty.require_config()

  started = time.strftime("%Y%m%d-%H%M%S")
  out_dir = SPIKE_DIR / f"run-{started}"
  assert out_dir.resolve().is_relative_to(SPIKE_DIR.resolve())
  out_dir.mkdir(parents=True, exist_ok=False)
  to = int(time.time()) - 60
  run = {"started": started, "to": to}

  print(f"Distribution pass over {len(aoty.LASTFM_USERS)} members...")
  members, run["dist_errors"] = distribution(aoty.LASTFM_USERS, to)
  if not members:
    raise SystemExit("No member returned recent tracks.")
  run["members"] = [{"label": x["label"], "total": x["total"]} for x in members]
  picks = {"light": members[0], "medium": members[len(members) // 2], "heavy": members[-1]}

  run["backfills"], rows_by_role = [], {}
  for role, mem in picks.items():
    print(f"Backfill {role} ({mem['label']}, {mem['total']:,} scrobbles)...")
    rows, stats = backfill(mem, to, args.limit_pages)
    rows_by_role[role] = rows
    _tag["phase"] = "recount"
    _, attr, _, _ = recent_page(mem["user"], 1, YEAR_START, to, extended=False, limit=1)
    info, _, _ = call("user.getinfo", user=mem["user"])
    run["backfills"].append({
        "role": role, "label": mem["label"], "stats": stats, "storage": storage(rows),
        "deltas": deltas(rows, to),
        "recount": {"start": mem["total"], "end": int(attr.get("total", 0)),
                    "playcount_delta": int(info["user"].get("playcount", 0)) - mem["playcount"]},
    })
    with gzip.open(out_dir / f"scrobbles-{role}.json.gz", "wt", encoding="utf-8") as f:
      json.dump([{k: v for k, v in r.items() if k != "nowplaying"} for r in rows], f, ensure_ascii=False)

  if not args.backfill_only:
    run["weekly"] = []
    for role in ("medium", "heavy"):
      print(f"Weekly charts for {role}...")
      run["weekly"].append({"role": role, "result": weekly(picks[role], rows_by_role[role], to)})
    print("extended=1 vs plain...")
    run["extended"] = extended_compare(picks["heavy"], to, args.ext_pages)
    print("Edge cases...")
    run["edge"] = edge_cases(members, to)

  with open(out_dir / "attempts.csv", "w", newline="", encoding="utf-8") as f:
    cols = ["method", "label", "phase", "attempt", "status", "code", "bytes", "seconds"]
    wr = csv.DictWriter(f, fieldnames=cols)
    wr.writeheader()
    wr.writerows(ATTEMPTS)
  (out_dir / "summary.json").write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")
  text = report(run, args)
  (out_dir / "report.md").write_text(text, encoding="utf-8")
  leaked = [u for u in aoty.LASTFM_USERS if u.casefold() in text.casefold()]
  if leaked:
    print(f"WARNING: {len(leaked)} username(s) appear in report.md; scrub before sharing.")
  print(f"\nWrote {out_dir}/report.md ({len(ATTEMPTS)} attempts)")


if __name__ == "__main__":
  main()
