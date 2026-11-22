import csv
import json
import os
import random
import re
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def _load_dotenv(path):
  """Minimal .env reader (KEY=value lines) so the key stays out of git without a dependency."""
  try:
    lines = path.read_text(encoding="utf-8").splitlines()
  except FileNotFoundError:
    return
  for line in lines:
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
      k, v = line.split("=", 1)
      os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv(HERE / ".env")

# ================= CONFIGURATION =================
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
TARGET_YEAR = "2026"

DATA_DIR = HERE / "data"
OUTPUT_CSV = DATA_DIR / f"server_top_albums_{TARGET_YEAR}.csv"
# Release years never change, so cache them across runs: a re-run only
# queries MusicBrainz for albums it hasn't seen before.
CACHE_FILE = DATA_DIR / "mb_release_year_cache.json"

# The Discord server members' Last.fm usernames, comma-separated in .env
# (LASTFM_USERS=alice,bob,carol) so the member list stays out of git.
LASTFM_USERS = [u.strip() for u in os.environ.get("LASTFM_USERS", "").split(",") if u.strip()]

# Discovery depth: an album is a candidate (and gets a MusicBrainz lookup) only
# if it's in at least one member's top this-many. Counting is NOT limited by this:
# plays and listeners come from every member's full 12-month list, so someone
# with 5 plays at rank 800 still counts as a listener.
ALBUMS_PER_USER = 100

# Albums per MusicBrainz search. MB allows 1 request/second, so batching is
# the only way to go faster. AOTY-10 measured 10/20/40: 20 was fastest in both runs
# (10 needs twice the requests; a failed 40-album batch dumps 40 singles). Each album can pull in a few near-miss results
# ("1989 [nightcore]"), and a search returns at most 100, so keep this well
# under 100. Albums that a batch misses get retried one at a time.
MB_BATCH_SIZE = 20
# =================================================

# MusicBrainz requires a descriptive User-Agent header with a way to reach you.
# The contact comes from .env (MB_CONTACT) so a real email isn't committed.
MB_HEADERS = {"User-Agent": f"kamp-aoty/0.1.0 ( {os.environ.get('MB_CONTACT') or 'contact@example.com'} )"}
MB_MIN_INTERVAL = 1.1
# What to do after a 503. "retry-after": wait what the 503's Retry-After header says
# (at least MB_MIN_INTERVAL), plus up to MB_JITTER seconds so retries don't line up.
# "exponential": wait MB_MIN_INTERVAL * 2**attempt (the old behaviour).
# AOTY-10 benchmark: ~half of all requests get a 503 whatever our pacing, always with
# Retry-After: 1, so retry-after x8 resolved albums ~2x faster than exponential x4 and
# lost none, where exponential x4 gave up on ~1% of albums. Details in CLAUDE.md.
MB_BACKOFF = "retry-after"
MB_MAX_ATTEMPTS = 8
MB_JITTER = 0.5

# Edition suffixes Last.fm often has but MusicBrainz release-group titles don't,
# e.g. "Album (Deluxe Edition)" or "Album [Remastered 2011]".
_EDITION_RE = re.compile(
  r"\s*[\(\[][^\)\]]*\b(deluxe|edition|remaster(ed)?|expanded|anniversary|bonus|"
  r"special|version|reissue)\b[^\)\]]*[\)\]]\s*$",
  re.IGNORECASE,
)

_last_mb_request = 0.0

# One row per mb_search attempt (status, server time, sleep, rate-limit headers),
# summarised by mb_report and appended to MB_STATS_CSV, to explain where a run's
# MusicBrainz time goes.
MB_STATS = []
MB_STATS_CSV = DATA_DIR / "mb_requests.csv"
_RUN_STARTED = time.strftime("%Y-%m-%dT%H:%M:%S")
_RUN_T0 = time.monotonic()
# Extra columns merged into every MB_STATS row. Empty in normal runs; a benchmark
# harness (tools/mb_cold_run.py) sets e.g. {"arm": "A", "chunk": 3} to label rows.
MB_TAG = {}
# Response headers worth keeping: MusicBrainz's rate-limit state and back-off hint.
_MB_HEADER_COLUMNS = {
  "retry_after": "Retry-After",
  "rl_limit": "X-RateLimit-Limit",
  "rl_remaining": "X-RateLimit-Remaining",
  "rl_reset": "X-RateLimit-Reset",
}


def norm(s):
  """Loose comparison key: case, curly quotes, punctuation and spacing don't matter."""
  s = unicodedata.normalize("NFKC", s).casefold()
  s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
  s = re.sub(r"[^\w']+", " ", s)
  return " ".join(s.split())


def base_title(album):
  return _EDITION_RE.sub("", album).strip() or album


def cache_key(artist, album):
  return f"{norm(artist)}␞{norm(album)}"


def load_cache():
  try:
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
  except (FileNotFoundError, json.JSONDecodeError):
    return {}


def save_cache(cache):
  CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def _lastfm_top_albums_page(username, period, page):
  """One page (1000 albums) of user.gettopalbums, retried; returns the "topalbums" dict."""
  params = {
    "method": "user.gettopalbums",
    "user": username,
    "period": period,
    "limit": 1000,
    "page": page,
    "api_key": LASTFM_API_KEY,
    "format": "json",
  }
  error = None
  for attempt in range(3):
    if attempt:
      time.sleep(2 * attempt)
    try:
      data = requests.get("https://ws.audioscrobbler.com/2.0/", params=params, timeout=30).json()
    except (requests.RequestException, ValueError) as e:
      error = e
      continue
    if "error" in data:
      error = data.get("message")
      continue
    return data.get("topalbums", {})
  # A silently short list undercounts listeners, so fail the run instead.
  raise SystemExit(f"Last.fm top albums failed for {username} (page {page}): {error}")


def get_user_top_albums(username, period="12month"):
  """Every album the user scrobbled in the period, most played first (all pages)."""
  albums, page = [], 1
  while True:
    top = _lastfm_top_albums_page(username, period, page)
    batch = top.get("album", [])
    albums += batch
    total_pages = int(top.get("@attr", {}).get("totalPages", 1) or 1)
    if not batch or page >= total_pages:
      return albums
    page += 1


def _lucene_escape(s):
  """Escape chars that would break a quoted Lucene phrase (e.g. an album with a " in it)."""
  return s.replace("\\", "\\\\").replace('"', '\\"')


def _clause(artist, album):
  return f'(artist:"{_lucene_escape(artist)}" AND releasegroup:"{_lucene_escape(base_title(album))}")'


def mb_search(query, limit, entity="release-group", n_albums=1):
  """One MusicBrainz search, rate limited, retrying on 503.

  Returns the result list (e.g. release groups) or None on failure. `entity` must be
  named in the URL: a bare /ws/2/ 301s and looks like zero results. `n_albums` is how
  many albums the query covers; it's only recorded, for `mb_report`.
  """
  global _last_mb_request
  url = f"https://musicbrainz.org/ws/2/{entity}/"
  params = {"query": query, "fmt": "json", "limit": limit}
  retry_after = MB_MIN_INTERVAL
  for attempt in range(MB_MAX_ATTEMPTS):
    # Respect MusicBrainz rate limit (max 1 request per second), backing off on 503
    if attempt and MB_BACKOFF == "retry-after":
      gap = max(MB_MIN_INTERVAL, retry_after) + random.uniform(0, MB_JITTER)
    else:
      gap = MB_MIN_INTERVAL * (2**attempt)
    wait = gap - (time.monotonic() - _last_mb_request)
    slept = max(wait, 0.0)
    if slept:
      time.sleep(slept)
    _last_mb_request = time.monotonic()
    stat = {
      **MB_TAG,
      "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
      "t": _last_mb_request - _RUN_T0,
      "entity": entity,
      "n_albums": n_albums,
      "limit": limit,
      "attempt": attempt + 1,
      "slept": slept,
      "status": "error",
      "elapsed": 0.0,
      "results": "",
    }
    MB_STATS.append(stat)
    try:
      response = requests.get(url, params=params, headers=MB_HEADERS, timeout=30)
    except requests.RequestException as e:
      stat["elapsed"] = time.monotonic() - _last_mb_request
      print(f"  MusicBrainz request failed after {stat['elapsed']:.1f}s: {e}")
      continue
    stat["elapsed"] = time.monotonic() - _last_mb_request
    stat["status"] = response.status_code
    for column, header in _MB_HEADER_COLUMNS.items():
      stat[column] = response.headers.get(header, "")
    if response.status_code == 503:
      try:
        retry_after = float(response.headers.get("Retry-After", MB_MIN_INTERVAL))
      except ValueError:
        retry_after = MB_MIN_INTERVAL
      print(f"  MusicBrainz 503 (throttled) on attempt {attempt + 1}/{MB_MAX_ATTEMPTS}, backing off")
      continue
    if response.status_code != 200:
      print(f"  MusicBrainz HTTP {response.status_code}")
      return None
    try:
      found = response.json().get(entity + "s", [])
    except ValueError:
      return None
    stat["results"] = len(found)
    return found
  return None


def _pct(values, p):
  """The p-th percentile (0-100) of a non-empty list, nearest-rank."""
  ordered = sorted(values)
  return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))]


def mb_report(script):
  """Print where this run's MusicBrainz time went, and append the rows to MB_STATS_CSV."""
  if not MB_STATS:
    return
  total_sleep = sum(s["slept"] for s in MB_STATS)
  total_server = sum(s["elapsed"] for s in MB_STATS)
  throttled = sum(1 for s in MB_STATS if s["status"] == 503)
  errors = sum(1 for s in MB_STATS if s["status"] not in (200, 503))
  print(f"\nMusicBrainz: {len(MB_STATS)} attempts, {throttled} throttled (503), {errors} other failures")
  print(
    f"  {total_sleep + total_server:.0f}s total = {total_server:.0f}s waiting on the server"
    f" + {total_sleep:.0f}s sleeping (rate limit / back-off)"
  )
  groups = defaultdict(list)
  for s in MB_STATS:
    groups[(s["entity"], "batch" if s["n_albums"] > 1 else "single")].append(s)
  for (entity, kind), rows in sorted(groups.items()):
    ok = [s["elapsed"] for s in rows if s["status"] == 200] or [0.0]
    print(
      f"  {entity} {kind}: {len(rows)} attempts, {sum(1 for s in rows if s['status'] == 503)} 503s,"
      f" server p50 {_pct(ok, 50):.2f}s p95 {_pct(ok, 95):.2f}s max {max(ok):.2f}s,"
      f" slept {sum(s['slept'] for s in rows):.0f}s"
    )

  base = [
    "run",
    "script",
    "ts",
    "t",
    "entity",
    "n_albums",
    "limit",
    "attempt",
    "status",
    "slept",
    "elapsed",
    "results",
    *_MB_HEADER_COLUMNS,
  ]
  fields = base + sorted({k for s in MB_STATS for k in s} - set(base))
  MB_STATS_CSV.parent.mkdir(parents=True, exist_ok=True)
  # Appending under a different header would misalign columns, so set an old-format
  # file aside rather than mixing the two.
  if MB_STATS_CSV.exists():
    with open(MB_STATS_CSV, encoding="utf-8") as f:
      header = f.readline().strip().split(",")
    if header != fields:
      MB_STATS_CSV.rename(
        MB_STATS_CSV.with_name(f"{MB_STATS_CSV.stem}.{time.strftime('%Y%m%d-%H%M%S')}{MB_STATS_CSV.suffix}")
      )
  new_file = not MB_STATS_CSV.exists()
  with open(MB_STATS_CSV, "a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields, restval="")
    if new_file:
      writer.writeheader()
    for s in MB_STATS:
      writer.writerow(
        {
          **s,
          "run": _RUN_STARTED,
          "script": script,
          "t": f"{s['t']:.3f}",
          "slept": f"{s['slept']:.3f}",
          "elapsed": f"{s['elapsed']:.3f}",
        }
      )
  print(f"  Per-request rows appended to {MB_STATS_CSV}")


def credit_names(rg):
  credits = rg.get("artist-credit", [])
  joined = "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits)
  return {norm(joined)} | {norm(c.get("name", "")) for c in credits}


_TYPE_RANK = {"Album": 0, "EP": 1}


def title_tier(album, title):
  """How a MusicBrainz title matches a Last.fm one: 0 exactly, 1 edition-stripped, None not at all.

  Callers sort on this first, so an exact match always beats a stripped one. Without that,
  "1989 (Taylor's Version)" (stripped to "1989") took the 2014 "1989" release group's year
  over its own 2023 one. Deluxe editions still resolve to the original: MusicBrainz keeps them
  in the original release group, under the original title.
  """
  have = norm(title)
  if have == norm(album):
    return 0
  if have == norm(base_title(album)):
    return 1
  return None


def pick_year(artist, album, release_groups):
  """Year of the release group matching this artist + title, or None.

  A search hit is not a match: the phrase "1989" also hits "1989 [nightcore]",
  so compare the titles ourselves. An exact title beats an edition-stripped one
  (title_tier); then, when several match (an album and a same-named single, say),
  prefer Album, then EP, then the earliest date.
  """
  want_artist = norm(artist)
  matches = []
  for rg in release_groups:
    date = rg.get("first-release-date", "")
    if len(date) < 4:
      continue
    tier = title_tier(album, rg.get("title", ""))
    if tier is None:
      continue
    if want_artist not in credit_names(rg):
      continue
    matches.append((tier, _TYPE_RANK.get(rg.get("primary-type"), 2), date, date[:4]))
  return min(matches)[-1] if matches else None


def lookup_years(pairs, cache):
  """Fill cache with release years for every (artist, album) pair not already in it."""
  todo = [p for p in dict.fromkeys(pairs) if cache_key(*p) not in cache]
  print(f"{len(set(pairs))} unique albums, {len(todo)} not cached yet")

  misses = []
  for i in range(0, len(todo), MB_BATCH_SIZE):
    batch = todo[i : i + MB_BATCH_SIZE]
    print(f"MusicBrainz batch {i // MB_BATCH_SIZE + 1}/{-(-len(todo) // MB_BATCH_SIZE)}")
    rgs = mb_search(" OR ".join(_clause(a, b) for a, b in batch), limit=100, n_albums=len(batch))
    if rgs is None:
      misses.extend(batch)  # failed request: retry these individually
      continue
    for artist, album in batch:
      year = pick_year(artist, album, rgs)
      if year:
        cache[cache_key(artist, album)] = year
      else:
        misses.append((artist, album))
    save_cache(cache)

  # A batch can miss an album whose near-misses got crowded out of the 100
  # results, so give each miss its own search before concluding there's no match.
  if misses:
    print(f"Retrying {len(misses)} unmatched albums individually...")
  for n, (artist, album) in enumerate(misses, start=1):
    rgs = mb_search(_clause(artist, album), limit=25)
    if rgs is None:
      continue  # don't cache a failure, so the next run retries it
    cache[cache_key(artist, album)] = pick_year(artist, album, rgs)
    if n % 10 == 0:
      save_cache(cache)
  save_cache(cache)


def require_config():
  if not LASTFM_API_KEY:
    raise SystemExit("Set LASTFM_API_KEY in .env (see .env.example)")
  if not LASTFM_USERS:
    raise SystemExit("Set LASTFM_USERS in .env (comma-separated Last.fm usernames; see .env.example)")


def fetch_plays_and_candidates(users):
  """Each user's full 12-month album list from Last.fm.

  Returns (plays, candidates): plays is [(user, artist, album, playcount)] over every
  album; candidates is [(artist, album)] from each user's top ALBUMS_PER_USER, with
  repeats (one per user who has it there).
  """
  # Last.fm has a much looser rate limit than MusicBrainz, so fetch users in parallel.
  with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(lambda u: (u, get_user_top_albums(u, period="12month")), users))

  plays = []
  candidates = []
  for user, albums in results:
    print(f"{user}: {len(albums)} albums")
    for rank, item in enumerate(albums):
      artist = item["artist"]["name"].strip()
      album_name = item["name"].strip()
      if artist and album_name:
        plays.append((user, artist, album_name, int(item["playcount"])))
        if rank < ALBUMS_PER_USER:
          candidates.append((artist, album_name))
  return plays, candidates


def aggregate_plays(plays, candidate_keys, years, year=TARGET_YEAR):
  """{key: {"artist", "album", "plays", "listeners": {user: plays}}} for target-year candidates.

  plays are (user, artist, album, playcount) rows. Keyed on the normalized names, so
  "Album" and "album" from two users merge; the first spelling seen is kept.
  """
  album_stats = defaultdict(lambda: {"artist": "", "album": "", "plays": 0, "listeners": defaultdict(int)})
  for user, artist, album_name, playcount in plays:
    key = cache_key(artist, album_name)
    if key not in candidate_keys or years.get(key) != year:
      continue
    stats = album_stats[key]
    stats["artist"] = stats["artist"] or artist
    stats["album"] = stats["album"] or album_name
    stats["plays"] += playcount
    stats["listeners"][user] += playcount
  return album_stats


def rank_albums(album_stats):
  """Unique listeners first, then total plays (a single play counts as a listener).

  Full ties keep aggregation order (the order plays arrived), since sorted() is stable.
  """
  return sorted(album_stats.values(), key=lambda s: (len(s["listeners"]), s["plays"]), reverse=True)


def main():
  require_config()
  DATA_DIR.mkdir(exist_ok=True)
  plays, candidates = fetch_plays_and_candidates(LASTFM_USERS)

  # Look up every distinct candidate once, up front, so they can be batched.
  # Only candidates are looked up: the long tail would cost ~10x the MB requests.
  cache = load_cache()
  lookup_years(candidates, cache)
  mb_report("aoty")
  candidate_keys = {cache_key(a, b) for a, b in candidates}
  ranked_albums = rank_albums(aggregate_plays(plays, candidate_keys, cache))

  # Export to CSV
  with open(OUTPUT_CSV, mode="w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["Rank", "Artist", "Album", "Unique Listeners", "Total Plays", "Listener Plays"])

    for rank, stats in enumerate(ranked_albums, start=1):
      # Listener Plays is JSON {username: plays}, which build_page.py's per-member filter needs.
      writer.writerow(
        [
          rank,
          stats["artist"],
          stats["album"],
          len(stats["listeners"]),
          stats["plays"],
          json.dumps(dict(stats["listeners"]), ensure_ascii=False),
        ]
      )

  print(f"\nDone! Exported {len(ranked_albums)} albums to {OUTPUT_CSV}.")


if __name__ == "__main__":
  main()
