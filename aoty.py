import csv
import json
import os
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
# the only way to go faster. Each album can pull in a few near-miss results
# ("1989 [nightcore]"), and a search returns at most 100, so keep this well
# under 100. Albums that a batch misses get retried one at a time.
MB_BATCH_SIZE = 20
# =================================================

# MusicBrainz requires a descriptive User-Agent header with a way to reach you.
# The contact comes from .env (MB_CONTACT) so a real email isn't committed.
MB_HEADERS = {
    "User-Agent": f"DiscordServerAOTYAggregator/1.0 ( {os.environ.get('MB_CONTACT') or 'contact@example.com'} )"
}
MB_MIN_INTERVAL = 1.1

# Edition suffixes Last.fm often has but MusicBrainz release-group titles don't,
# e.g. "Album (Deluxe Edition)" or "Album [Remastered 2011]".
_EDITION_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(deluxe|edition|remaster(ed)?|expanded|anniversary|bonus|"
    r"special|version|reissue)\b[^\)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)

_last_mb_request = 0.0


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


def mb_search(query, limit, entity="release-group"):
  """One MusicBrainz search, rate limited, retrying on 503.

  Returns the result list (e.g. release groups) or None on failure. `entity` must be
  named in the URL: a bare /ws/2/ 301s and looks like zero results.
  """
  global _last_mb_request
  url = f"https://musicbrainz.org/ws/2/{entity}/"
  params = {"query": query, "fmt": "json", "limit": limit}
  for attempt in range(4):
    # Respect MusicBrainz rate limit (max 1 request per second), backing off on 503
    wait = MB_MIN_INTERVAL * (2 ** attempt) - (time.monotonic() - _last_mb_request)
    if wait > 0:
      time.sleep(wait)
    _last_mb_request = time.monotonic()
    try:
      response = requests.get(url, params=params, headers=MB_HEADERS, timeout=30)
    except requests.RequestException as e:
      print(f"  MusicBrainz request failed: {e}")
      continue
    if response.status_code == 503:
      continue
    if response.status_code != 200:
      print(f"  MusicBrainz HTTP {response.status_code}")
      return None
    try:
      return response.json().get(entity + "s", [])
    except ValueError:
      return None
  return None


def credit_names(rg):
  credits = rg.get("artist-credit", [])
  joined = "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits)
  return {norm(joined)} | {norm(c.get("name", "")) for c in credits}


_TYPE_RANK = {"Album": 0, "EP": 1}


def pick_year(artist, album, release_groups):
  """Year of the release group matching this artist + title, or None.

  A search hit is not a match: the phrase "1989" also hits "1989 [nightcore]",
  so compare the titles ourselves. When several match (an album and a same-named
  single, say), prefer Album, then EP, then the earliest date.
  """
  want_artist = norm(artist)
  want_titles = {norm(album), norm(base_title(album))}
  matches = []
  for rg in release_groups:
    date = rg.get("first-release-date", "")
    if len(date) < 4:
      continue
    if norm(rg.get("title", "")) not in want_titles:
      continue
    if want_artist not in credit_names(rg):
      continue
    matches.append((_TYPE_RANK.get(rg.get("primary-type"), 2), date, date[:4]))
  return min(matches)[2] if matches else None


def lookup_years(pairs, cache):
  """Fill cache with release years for every (artist, album) pair not already in it."""
  todo = [p for p in dict.fromkeys(pairs) if cache_key(*p) not in cache]
  print(f"{len(set(pairs))} unique albums, {len(todo)} not cached yet")

  misses = []
  for i in range(0, len(todo), MB_BATCH_SIZE):
    batch = todo[i:i + MB_BATCH_SIZE]
    print(f"MusicBrainz batch {i // MB_BATCH_SIZE + 1}/{-(-len(todo) // MB_BATCH_SIZE)}")
    rgs = mb_search(" OR ".join(_clause(a, b) for a, b in batch), limit=100)
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


def main():
  if not LASTFM_API_KEY:
    raise SystemExit("Set LASTFM_API_KEY in .env (see .env.example)")
  if not LASTFM_USERS:
    raise SystemExit("Set LASTFM_USERS in .env (comma-separated Last.fm usernames; see .env.example)")
  DATA_DIR.mkdir(exist_ok=True)

  # Last.fm has a much looser rate limit than MusicBrainz, so fetch users in parallel.
  with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(lambda u: (u, get_user_top_albums(u, period="12month")), LASTFM_USERS))

  plays = []  # (user, artist, album, playcount), from each user's full list
  candidates = []  # (artist, album) in some user's top ALBUMS_PER_USER
  for user, albums in results:
    print(f"{user}: {len(albums)} albums")
    for rank, item in enumerate(albums):
      artist = item["artist"]["name"].strip()
      album_name = item["name"].strip()
      if artist and album_name:
        plays.append((user, artist, album_name, int(item["playcount"])))
        if rank < ALBUMS_PER_USER:
          candidates.append((artist, album_name))

  # Look up every distinct candidate once, up front, so they can be batched.
  # Only candidates are looked up: the long tail would cost ~10x the MB requests.
  cache = load_cache()
  lookup_years(candidates, cache)
  candidate_keys = {cache_key(a, b) for a, b in candidates}

  # Store aggregated stats: {key: {"artist", "album", "plays", "listeners": {user: plays}}}.
  # Keyed on the normalized names so "Album" and "album" from two users merge.
  album_stats = defaultdict(
      lambda: {"artist": "", "album": "", "plays": 0, "listeners": defaultdict(int)})
  for user, artist, album_name, playcount in plays:
    key = cache_key(artist, album_name)
    if key not in candidate_keys or cache.get(key) != TARGET_YEAR:
      continue
    stats = album_stats[key]
    stats["artist"] = stats["artist"] or artist
    stats["album"] = stats["album"] or album_name
    stats["plays"] += playcount
    stats["listeners"][user] += playcount

  # Sort results by unique listeners first, then total plays
  ranked_albums = sorted(
      album_stats.values(),
      key=lambda s: (len(s["listeners"]), s["plays"]),
      reverse=True,
  )

  # Export to CSV
  with open(OUTPUT_CSV, mode="w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(
        ["Rank", "Artist", "Album", "Unique Listeners", "Total Plays", "Listener Plays"]
    )

    for rank, stats in enumerate(ranked_albums, start=1):
      # Listener Plays is JSON {username: plays}, which build_page.py's per-member filter needs.
      writer.writerow([
          rank, stats["artist"], stats["album"], len(stats["listeners"]), stats["plays"],
          json.dumps(dict(stats["listeners"]), ensure_ascii=False),
      ])

  print(f"\nDone! Exported {len(ranked_albums)} albums to {OUTPUT_CSV}.")


if __name__ == "__main__":
  main()
