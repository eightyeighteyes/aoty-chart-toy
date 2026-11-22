"""Builds site/index.html from the CSV that aoty.py writes.

Run aoty.py first, then this. Album art comes from Last.fm's album.getInfo.
"""

import csv
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# Reuse the main script's config (API key, year, user list) so they can't drift.
import aoty

HERE = Path(__file__).resolve().parent

TOP_N = 100
# Poster view gives these the big treatment; the rest go in a compact grid.
FEATURED_N = 10
OUTPUT_HTML = HERE / "site" / "index.html"
TITLE = "Server Albums of the Year"

# {"tracks": n, "type": MB release-group primary type or null} per album, keyed like
# the year cache. A missing key = not looked up yet; tracks null = looked up, no count
# anywhere. Like the year cache, failures are never stored.
TRACK_CACHE_FILE = aoty.DATA_DIR / "mb_track_count_cache.json"
# {cache key: Last.fm cover URL, or "" for no cover}. Failed lookups aren't stored.
ART_CACHE_FILE = aoty.DATA_DIR / "lastfm_art_cache.json"
# Releases per MusicBrainz search. Each album has several releases (editions,
# regions, formats) and a search returns at most 100, so keep batches small.
MB_TRACK_BATCH_SIZE = 10
# Singles are left out of By Album and Weighted, where dividing by 1 track would put
# them on top (user decision 2026-11-20). MusicBrainz's release-group type decides,
# whatever the length: a 4-track "Single" is out, a 1-track "Album" is in. Only an
# untyped release (Last.fm fallback count) falls back to "under MIN_ALBUM_TRACKS".
MIN_ALBUM_TRACKS = 4
# Weighted only ranks albums at least this many members played (user decision 2026-11-20).
WEIGHTED_MIN_LISTENERS = 3

# The three rankings: (key, button label, how the page explains the order).
MODES = [
  ("track", "By Track", "ranked by how many of our {total} members played them, then total plays"),
  (
    "album",
    "By Album",
    "ranked by album plays: track plays divided by the album's track count (singles left out)",
  ),
  (
    "weighted",
    "Weighted",
    "ranked by album plays per listener, among albums at least {min_listeners} members played",
  ),
]


def load_json(path):
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (FileNotFoundError, json.JSONDecodeError):
    return {}


_STATUS_RANK = {"Official": 0}


def pick_release(artist, album, releases):
  """{"tracks", "type"} of the release matching this artist + title, or None.

  Same matching rule as aoty.pick_year: a search hit isn't a match. Among matches,
  prefer Official, then a release group typed Album > EP > other, then the earliest
  date, which is usually the standard edition rather than a later deluxe.
  """
  want_artist = aoty.norm(artist)
  want_titles = {aoty.norm(album), aoty.norm(aoty.base_title(album))}
  matches = []
  for rel in releases:
    count = rel.get("track-count")
    if not count:
      continue
    if aoty.norm(rel.get("title", "")) not in want_titles:
      continue
    if want_artist not in aoty.credit_names(rel):
      continue
    rg_type = rel.get("release-group", {}).get("primary-type")
    matches.append(
      (
        _STATUS_RANK.get(rel.get("status"), 1),
        aoty._TYPE_RANK.get(rg_type, 2),
        rel.get("date") or "9999",
        count,
        rg_type or "",
      )
    )
  if not matches:
    return None
  best = min(matches)
  return {"tracks": best[3], "type": best[4] or None}


def _release_clause(artist, album):
  e = aoty._lucene_escape
  return f'(artist:"{e(artist)}" AND release:"{e(aoty.base_title(album))}")'


def lastfm_track_count(artist, album):
  """Fallback when MusicBrainz has no match: the length of Last.fm's tracklist (often empty)."""
  try:
    data = requests.get(
      "https://ws.audioscrobbler.com/2.0/",
      params={
        "method": "album.getinfo",
        "artist": artist,
        "album": album,
        "autocorrect": 1,
        "api_key": aoty.LASTFM_API_KEY,
        "format": "json",
      },
      timeout=15,
    ).json()
  except (requests.RequestException, ValueError):
    return None
  tracks = data.get("album", {}).get("tracks") or {}
  tracks = tracks.get("track", []) if isinstance(tracks, dict) else []
  # A one-track album comes back as a bare object, not a list.
  return (1 if isinstance(tracks, dict) else len(tracks)) or None


def lookup_releases(rows):
  """{cache key: {"tracks", "type"}} for every CSV row, using and filling the cache."""
  # Entries from before the type was stored are bare ints: look those up again.
  cache = {k: v for k, v in load_json(TRACK_CACHE_FILE).items() if isinstance(v, dict)}

  def save():
    TRACK_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")

  pairs = [(r["Artist"], r["Album"]) for r in rows]
  todo = [p for p in dict.fromkeys(pairs) if aoty.cache_key(*p) not in cache]
  print(f"Track counts: {len(pairs)} albums, {len(todo)} not cached yet")

  misses = []
  for i in range(0, len(todo), MB_TRACK_BATCH_SIZE):
    batch = todo[i : i + MB_TRACK_BATCH_SIZE]
    print(f"  MusicBrainz batch {i // MB_TRACK_BATCH_SIZE + 1}/{-(-len(todo) // MB_TRACK_BATCH_SIZE)}")
    rels = aoty.mb_search(
      " OR ".join(_release_clause(a, b) for a, b in batch), 100, "release", n_albums=len(batch)
    )
    for artist, album in batch:
      info = pick_release(artist, album, rels or [])
      if info:
        cache[aoty.cache_key(artist, album)] = info
      else:
        misses.append((artist, album))
    save()

  # As with years, a batch can crowd out an album's releases, so retry misses alone,
  # then fall back to Last.fm before concluding there's no count.
  if misses:
    print(f"  Retrying {len(misses)} unmatched albums individually...")
  for artist, album in misses:
    rels = aoty.mb_search(_release_clause(artist, album), 50, "release")
    if rels is None:
      continue  # failed request: leave uncached so the next run retries
    info = pick_release(artist, album, rels)
    if not info:
      info = {"tracks": lastfm_track_count(artist, album), "type": None}
    cache[aoty.cache_key(artist, album)] = info
    save()
  missing = {"tracks": None, "type": None}  # only after a failed request
  return {aoty.cache_key(*p): cache.get(aoty.cache_key(*p), missing) for p in pairs}


def is_single(info):
  """MusicBrainz's type decides: a 1-track Album (The Necks, Dopesmoker) is an album.
  The track floor applies only when there's no type, i.e. a Last.fm fallback count."""
  if not info["tracks"]:
    return True  # no count: can't be scored per album
  if info["type"]:
    return info["type"] == "Single"
  return info["tracks"] < MIN_ALBUM_TRACKS


def album_art(artist, album):
  """Largest Last.fm cover URL, '' if there's none, or None if the lookup failed.

  Under parallel load Last.fm intermittently times out or answers with an error
  body (e.g. 8 "operation failed", 29 rate limit) for albums that do have art, so
  retry those, and say so if one still fails rather than quietly showing the placeholder.
  """
  error = None
  for attempt in range(3):
    if attempt:
      time.sleep(2 * attempt)
    try:
      data = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={
          "method": "album.getinfo",
          "artist": artist,
          "album": album,
          "autocorrect": 1,
          "api_key": aoty.LASTFM_API_KEY,
          "format": "json",
        },
        timeout=15,
      ).json()
    except (requests.RequestException, ValueError) as e:
      error = e
      continue
    if "error" in data:
      error = f"Last.fm error {data['error']}: {data.get('message')}"
      if data["error"] == 6:  # "Album not found": retrying won't help, and it's a real answer
        print(f"  no art for {artist} - {album}: {error}")
        return ""
      continue
    images = data.get("album", {}).get("image", [])
    # Last.fm orders sizes small -> mega; take the biggest non-empty one.
    for img in reversed(images):
      if img.get("#text"):
        return img["#text"]
    return ""  # found, genuinely no cover
  print(f"  no art for {artist} - {album}: {error}")
  return None


def fetch_art(rows):
  """{cache key: cover URL or ''} for these rows, using and filling the art cache.

  The member filter can show nearly every album, so covers are cached; only failed
  lookups (None) are left out, so the next run retries them.
  """
  cache = load_json(ART_CACHE_FILE)
  todo = [r for r in rows if r["key"] not in cache]
  print(f"Cover art: {len(rows)} albums, {len(todo)} not cached yet")
  # 4 at a time: one after another is slow, and Last.fm tolerates a few in parallel.
  with ThreadPoolExecutor(max_workers=4) as pool:
    for r, art in zip(todo, pool.map(lambda r: album_art(r["Artist"], r["Album"]), todo)):
      if art is not None:
        cache[r["key"]] = art
  ART_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
  return {r["key"]: cache.get(r["key"], "") for r in rows}


def build_rankings(n):
  """(kept rows, {mode: everyone's top-n [(row, score), ...]}, {key: art}, members).

  Kept rows are every album any view can show, for everyone or for one member.
  """
  with open(aoty.OUTPUT_CSV, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
  if "Listener Plays" not in rows[0]:
    raise SystemExit("The CSV predates per-member plays: re-run aoty.py first.")
  releases = lookup_releases(rows)
  for r in rows:
    r["key"] = aoty.cache_key(r["Artist"], r["Album"])
    r["plays"], r["listeners"] = int(r["Total Plays"]), int(r["Unique Listeners"])
    r["tracks"] = releases[r["key"]]["tracks"]
    r["by_user"] = json.loads(r["Listener Plays"])

  # By Album / Weighted divide by track count, so a single would score its whole
  # play count; those modes only rank real albums. By Track keeps everything.
  albums = [r for r in rows if not is_single(releases[r["key"]])]
  print(f"{len(rows) - len(albums)} singles (or no track count) left out of By Album / Weighted")
  scored = {
    # The CSV is already in this order: listeners, then plays.
    "track": [(r, r["plays"]) for r in rows],
    "album": sorted(
      ((r, r["plays"] / r["tracks"]) for r in albums), key=lambda x: (x[1], x[0]["listeners"]), reverse=True
    ),
    # Mean album plays across the people who played it. Without a listener floor,
    # one member looping a record takes #1.
    "weighted": sorted(
      (
        (r, r["plays"] / r["tracks"] / r["listeners"])
        for r in albums
        if r["listeners"] >= WEIGHTED_MIN_LISTENERS
      ),
      key=lambda x: (x[1], x[0]["listeners"]),
      reverse=True,
    ),
  }
  rankings = {mode: pairs[:n] for mode, pairs in scored.items()}

  # A member's view is re-ranked in the page by that member's own plays (By Track) or
  # plays per track (By Album; Weighted is the same thing for one listener). The page
  # does the ranking; here we only pick which albums it needs: each member's top n
  # under both scores, keeping ties at the cutoff since the page breaks them itself.
  # Picklist order: case-insensitive alphabetical (user decision 2026-11-20).
  members = sorted(aoty.LASTFM_USERS, key=str.casefold)
  album_keys = {r["key"] for r in albums}
  shown = {r["key"] for pairs in rankings.values() for r, _ in pairs}
  for who in members:
    played = [r for r in rows if who in r["by_user"]]
    for pool, score_of in (
      (played, lambda r: r["by_user"][who]),
      ([r for r in played if r["key"] in album_keys], lambda r: r["by_user"][who] / r["tracks"]),
    ):
      pool = sorted(pool, key=score_of, reverse=True)
      cutoff = score_of(pool[n - 1]) if len(pool) > n else float("-inf")
      shown.update(r["key"] for r in pool if score_of(r) >= cutoff)
  # CSV order, which the page uses to break ties.
  kept = [r for r in rows if r["key"] in shown]
  for r in kept:
    r["single"] = r["key"] not in album_keys

  # Cover art once per album, however many rankings it appears in.
  arts = fetch_art(kept)
  print(f"{len(kept)} albums kept for member views")
  for mode, pairs in rankings.items():
    print(f"\n{mode}: {len(pairs)} albums")
    for i, (r, s) in enumerate(pairs[:FEATURED_N], start=1):
      print(f"  {i:>2}. {r['Artist']} - {r['Album']} ({s:.2f})")
  return kept, rankings, arts, members


def page_data(kept, rankings, arts, members):
  """What the page script renders from: albums once each, rankings as indexes into them."""
  index = {r["key"]: i for i, r in enumerate(kept)}
  albums = [
    {
      "artist": r["Artist"],
      "album": r["Album"],
      "art": arts[r["key"]],
      "fallbacks": " ".join(art_fallbacks(arts[r["key"]])),
      "listeners": r["listeners"],
      "plays": r["plays"],
      "by": r["by_user"],
      "tracks": r["tracks"],
      "single": r["single"],
    }
    for r in kept
  ]
  total = len(aoty.LASTFM_USERS)
  return {
    "year": aoty.TARGET_YEAR,
    "total": total,
    "top": TOP_N,
    "featured": FEATURED_N,
    "members": members,
    "albums": albums,
    # Everyone's rankings; a member's are computed in the page.
    "rankings": {m: [[index[r["key"]], round(s, 3)] for r, s in pairs] for m, pairs in rankings.items()},
    "descs": {m: desc.format(total=total, min_listeners=WEIGHTED_MIN_LISTENERS) for m, _, desc in MODES},
  }


def art_fallbacks(url):
  """Other sizes of the same Last.fm image, to try if the chosen one fails to load.

  The CDN resizes on first request and can cache a 404 for one size (seen 2026-11-20:
  JUNK POP's 300x300 404'd in the browser while every other size was fine).
  """
  m = re.match(r"(https://[^/]+/i/u/)(?:[^/]+/)?([^/]+)$", url)
  if not m:
    return []
  base, name = m.groups()
  return [f"{base}{size}{name}" for size in ("ar0/", "174s/") if f"{base}{size}{name}" != url]


# Renders the chosen ranking for the chosen member from the #aoty-data JSON. A plain
# string (not part of render's f-string) so its braces needn't be doubled.
PAGE_SCRIPT = r"""
  const DATA = JSON.parse(document.getElementById("aoty-data").textContent);
  const poster = document.querySelector(".poster");
  const who = document.getElementById("who");
  const esc = s => String(s).replace(/[&<>"']/g,
      c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
  const num = (x, digits = 0) => x.toLocaleString("en-US",
      {minimumFractionDigits: digits, maximumFractionDigits: digits});

  function cover(a, cls) {
    // On error, coverFallback (in <head>) walks data-fallbacks, then swaps in the artist-name tile.
    if (a.art) return `<img class="${cls}" src="${esc(a.art)}" alt="${esc(a.album)} cover" loading="lazy" `
        + `data-fallbacks="${esc(a.fallbacks)}" data-artist="${esc(a.artist)}" onerror="coverFallback(this)">`;
    // Fallback tile: the artist's name on a gradient so a missing cover still looks deliberate.
    return `<div class="${cls} noart"><span style="--fs:${nameSize(a.artist)}">${esc(a.artist)}</span></div>`;
  }
  function dots(a) {
    let s = "";
    for (let i = 0; i < DATA.total; i++) s += `<span class="dot${i < a.listeners ? " on" : ""}"></span>`;
    return `<div class="dots" title="${a.listeners} of ${DATA.total} listeners">${s}</div>`;
  }
  // The number this ranking sorts by. For one member it's their own plays, not the server's.
  function score(a, mode, name) {
    if (name) return mode === "track" ? `<b>${num(a.score)}</b> plays by ${esc(name)}`
        : `<b>${num(a.score, 1)}</b> album plays by ${esc(name)}`;
    return mode === "track" ? `<b>${num(a.score)}</b> plays`
        : mode === "album" ? `<b>${num(a.score, 1)}</b> album plays`
        : `<b>${num(a.score, 1)}</b> plays per listener`;
  }

  // Everyone's rankings come precomputed. A member's are re-ranked by their own plays
  // (By Track) or plays per track (By Album). Weighted divides that by listeners,
  // which is 1, so for one member it's the same as By Album. Ties: server listeners,
  // then server order (albums are in CSV order and sort is stable).
  function ranked(mode, name) {
    if (!name) return DATA.rankings[mode].map(([i, s]) => ({...DATA.albums[i], score: s}));
    const byTrack = mode === "track";
    return DATA.albums
        .filter(a => name in a.by && (byTrack || !a.single))
        .map(a => ({...a, score: byTrack ? a.by[name] : a.by[name] / a.tracks}))
        .sort((x, y) => y.score - x.score || y.listeners - x.listeners);
  }
  function describe(mode, name) {
    if (!name) return DATA.descs[mode];
    if (mode === "track") return `ranked by ${name}'s plays`;
    const d = `ranked by ${name}'s album plays: their track plays divided by the album's track count (singles left out)`;
    return mode === "weighted" ? `${d}. For one person, Weighted is the same as By Album` : d;
  }

  // The chosen member lives in ?user=, so a view can be bookmarked or shared. Last.fm
  // names are case-insensitive, so ?user=alice finds Alice; an unknown name means Everyone.
  function memberFromUrl() {
    let q = null;
    try { q = new URLSearchParams(location.search).get("user"); } catch (_) {}
    return (q && DATA.members.find(m => m.toLowerCase() === q.toLowerCase())) || "";
  }
  function syncUrl(name) {
    try {
      const url = new URL(location.href);
      if (name) url.searchParams.set("user", name); else url.searchParams.delete("user");
      if (url.href !== location.href) history.replaceState(null, "", url);
    } catch (_) {}  // some browsers refuse replaceState on file://
  }
  const names = a => `<div class="album">${esc(a.album)}</div><div class="artist">${esc(a.artist)}</div>`;

  // One ranking's poster view and list view. Ranks are positions in the filtered list.
  function renderRanking(rows, mode, name) {
    if (!rows.length) return `<p class="empty">None of ${esc(name)}'s ${DATA.year} albums made this ranking.</p>`;
    const [top, rest, more] = [rows[0], rows.slice(1, DATA.featured), rows.slice(DATA.featured)];
    const t = DATA.total;
    const bg = top.art ? `style="--hero-art:url(&quot;${esc(top.art)}&quot;)"` : "";
    const hero = `<section class="hero" ${bg}>${cover(top, "art")}<div>
        <div class="crown">#1</div>${names(top)}${dots(top)}
        <div class="stats"><b>${top.listeners}</b> of ${t} listeners · ${score(top, mode, name)}</div>
      </div></section>`;
    const cards = rest.map((a, i) => `<li class="card" style="--i:${i + 1}">
        <div class="art-wrap">${cover(a, "art")}<span class="rank">${a.rank}</span></div>
        <div class="meta">${names(a)}${dots(a)}
          <div class="stats"><b>${a.listeners}</b> listeners · ${score(a, mode, name)}</div></div>
      </li>`).join("");
    // Past the featured albums: small tiles, no listener dots.
    const tiles = more.map(a => `<li class="tile">
        <div class="art-wrap">${cover(a, "art")}<span class="tile-rank">${a.rank}</span></div>
        ${names(a)}<div class="stats"><b>${a.listeners}</b>/${t} · ${score(a, mode, name)}</div>
      </li>`).join("");
    const moreSection = more.length ? `<section class="more"><h2>${DATA.featured + 1}–${rows.length}</h2>
        <ol class="more-grid">${tiles}</ol></section>` : "";
    // List view: every album (including #1) as one column of horizontal cards.
    const list = rows.map((a, i) => `<li class="row${i ? "" : " first"}" style="--i:${i}">
        <span class="row-rank">${a.rank}</span>${cover(a, "row-art")}
        <div class="row-meta">${names(a)}</div>
        <div class="row-numbers">${dots(a)}
          <div class="stats"><b>${a.listeners}</b>/${t} listeners · ${score(a, mode, name)}</div></div>
      </li>`).join("");
    return `<div class="view-poster">${hero}<ol>${cards}</ol>${moreSection}</div>
      <ol class="list view-list">${list}</ol>`;
  }

  function render() {
    const mode = poster.dataset.rank, name = who.value;
    const rows = ranked(mode, name).slice(0, DATA.top).map((a, k) => ({...a, rank: k + 1}));
    who.classList.toggle("active", !!name);
    syncUrl(name);
    document.getElementById("sub").textContent = `Top ${rows.length} albums released in ${DATA.year}`
        + (name ? ` that ${name} played` : "") + `, ${describe(mode, name)}.`;
    document.getElementById("ranking").innerHTML = renderRanking(rows, mode, name);
  }

  // Two pill switches, view (data-view) and ranking (data-rank), each remembered per
  // browser. Storage can be blocked (e.g. file:// in some browsers), so fall back to
  // the defaults quietly. The member filter is kept in the URL instead (see syncUrl).
  function setupSwitch(attr, storageKey, onChange) {
    const buttons = document.querySelectorAll(`.switch button[data-${attr}]`);
    const values = [...buttons].map(b => b.dataset[attr]);
    function set(value) {
      poster.dataset[attr] = value;
      buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
      try { localStorage.setItem(storageKey, value); } catch (_) {}
    }
    buttons.forEach(b => b.addEventListener("click", () => { set(b.dataset[attr]); if (onChange) onChange(); }));
    let saved = null;
    try { saved = localStorage.getItem(storageKey); } catch (_) {}
    if (values.includes(saved)) set(saved);
  }
  setupSwitch("view", "aoty-view");
  setupSwitch("rank", "aoty-rank", render);
  who.value = memberFromUrl();  // the URL wins over any pick the browser restores on reload
  who.addEventListener("change", render);
  render();
"""


def render(data):
  e = html.escape
  # "</" would end the <script> early; JSON allows the escaped form.
  data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
  member_options = "\n        ".join(f'<option value="{e(m)}">{e(m)}</option>' for m in data["members"])
  rank_buttons = "\n      ".join(
    f'<button type="button" data-rank="{m}" aria-pressed="{str(i == 0).lower()}">{e(label)}</button>'
    for i, (m, label, _) in enumerate(MODES)
  )

  return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(TITLE)} {e(aoty.TARGET_YEAR)}</title>
<script>
  // Defined in <head> so it exists before any <img onerror> can fire. Try the next
  // size of the same cover; when none are left, swap in the artist-name tile.
  function coverFallback(img) {{
    const rest = (img.dataset.fallbacks || "").split(" ").filter(Boolean);
    if (rest.length) {{
      img.dataset.fallbacks = rest.slice(1).join(" ");
      img.src = rest[0];
      return;
    }}
    const tile = document.createElement("div");
    tile.className = img.className + " noart";
    const name = tile.appendChild(document.createElement("span"));
    name.textContent = img.dataset.artist;
    name.style.setProperty("--fs", nameSize(img.dataset.artist));
    img.replaceWith(tile);
  }}
  // Placeholder text size, in % of the tile's width: big for short names, smaller for
  // long ones, and never so big that the longest word overflows (~0.6em per character).
  function nameSize(name) {{
    const longest = Math.max(1, ...name.split(/\\s+/).map(w => w.length));
    const bySize = name.length <= 10 ? 16 : name.length <= 22 ? 12 : 9;
    return Math.min(bySize, 130 / longest).toFixed(1);
  }}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0d0c12;
    --panel: #17151f;
    --ink: #f4f1ea;
    --muted: #9c97a8;
    --accent: #ff6b4a;
    --accent-2: #ffc24b;
    --dot-off: #2c2937;
  }}
  * {{ box-sizing: border-box; }}
  /* Paint the page colour on the root as well as body, so no compositor layer can
     show the browser's default backdrop (a flat grey seam) through a gap. */
  html {{ background: var(--bg); color-scheme: dark; min-height: 100%; }}
  body {{
    min-height: 100vh;
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: Inter, system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .poster {{
    max-width: 1080px;
    margin: 0 auto;
    padding: 56px 40px 48px;
  }}
  header {{ margin-bottom: 36px; }}
  .eyebrow {{
    font-size: 13px; letter-spacing: .22em; text-transform: uppercase;
    color: var(--accent); font-weight: 600;
  }}
  h1 {{
    font-family: "Bricolage Grotesque", sans-serif;
    font-weight: 800;
    font-size: clamp(44px, 8vw, 92px);
    line-height: .92;
    letter-spacing: -.03em;
    margin: 10px 0 0;
  }}
  h1 .year {{
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .sub {{ color: var(--muted); margin-top: 14px; font-size: 15px; }}

  /* #1 */
  .hero {{
    position: relative; overflow: hidden;
    display: grid; grid-template-columns: 340px 1fr; gap: 36px; align-items: center;
    padding: 32px; border-radius: 24px; background: var(--panel);
    margin-bottom: 28px;
  }}
  .hero::before {{
    content: ""; position: absolute; inset: -40px;
    background: var(--hero-art, linear-gradient(135deg, var(--accent), var(--accent-2))) center/cover;
    filter: blur(60px) saturate(1.4); opacity: .45;
  }}
  .hero::after {{
    content: ""; position: absolute; inset: 0;
    background: linear-gradient(90deg, rgba(13,12,18,.2), rgba(13,12,18,.75));
  }}
  .hero > * {{ position: relative; z-index: 1; }}
  .hero .art {{
    width: 100%; aspect-ratio: 1; border-radius: 14px; object-fit: cover;
    box-shadow: 0 30px 60px rgba(0,0,0,.55);
  }}
  .crown {{
    font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
    font-size: 120px; line-height: .8; letter-spacing: -.05em;
    background: linear-gradient(180deg, var(--accent-2), var(--accent));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .hero .album {{
    font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
    font-size: clamp(28px, 4vw, 44px); line-height: 1.02; letter-spacing: -.02em;
    margin-top: 14px;
  }}
  .hero .artist {{ font-size: 20px; color: var(--ink); opacity: .85; margin-top: 8px; }}
  .hero .dots {{ margin-top: 22px; }}
  .hero .dot {{ width: 12px; height: 12px; }}
  .hero .stats {{ font-size: 15px; margin-top: 10px; }}

  /* #2-10 */
  ol {{
    list-style: none; margin: 0; padding: 0;
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px;
  }}
  .card {{
    background: var(--panel); border-radius: 18px; overflow: hidden;
    /* "backwards", not "both": holding the end state keeps each card on its own
       GPU layer after the animation, which is what left a seam behind. */
    animation: rise .5s backwards cubic-bezier(.2,.7,.2,1);
    animation-delay: calc(var(--i) * 50ms);
  }}
  .art-wrap {{ position: relative; }}
  .card .art {{ display: block; width: 100%; aspect-ratio: 1; object-fit: cover; }}
  .rank {{
    position: absolute; left: 12px; bottom: 10px;
    font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
    font-size: 54px; line-height: 1; letter-spacing: -.04em;
    color: var(--ink); text-shadow: 0 4px 24px rgba(0,0,0,.7);
  }}
  .meta {{ padding: 14px 16px 16px; }}
  .card .album {{ font-weight: 600; font-size: 16px; line-height: 1.25; }}
  .card .artist {{ color: var(--muted); font-size: 14px; margin-top: 3px; }}
  .dots {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 12px; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--dot-off); }}
  .dot.on {{ background: linear-gradient(135deg, var(--accent-2), var(--accent)); }}
  .stats {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
  .stats b {{ color: var(--ink); font-weight: 600; }}

  /* Missing cover: the artist's name, centred, sized relative to the tile (cqi) so
     the same rule works from the 64px list thumbnail up to the hero. */
  .noart {{
    display: grid; place-items: center; aspect-ratio: 1; width: 100%;
    container-type: inline-size;
    background: linear-gradient(135deg, #3a2a55, var(--accent));
    font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
    color: rgba(255,255,255,.88);
  }}
  .noart span {{
    font-size: calc(var(--fs, 12) * 1cqi); line-height: 1.02; letter-spacing: -.02em;
    text-align: center; padding: 0 9cqi; overflow-wrap: break-word;
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 5; overflow: hidden;
  }}

  /* Poster: 11-TOP_N */
  .more {{ margin-top: 48px; }}
  .more h2 {{
    font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
    font-size: 32px; letter-spacing: -.02em; margin: 0 0 18px; color: var(--muted);
  }}
  .more-grid {{ grid-template-columns: repeat(5, 1fr); gap: 20px 16px; }}
  .tile .art {{ display: block; width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 10px; }}
  .tile .noart {{ border-radius: 10px; }}
  .tile-rank {{
    position: absolute; left: 6px; top: 6px; min-width: 30px; padding: 3px 7px;
    border-radius: 999px; background: rgba(13,12,18,.82);
    font: 600 13px Inter, system-ui, sans-serif; text-align: center;
  }}
  .tile .album {{ font-weight: 600; font-size: 14px; line-height: 1.25; margin-top: 10px; }}
  .tile .artist {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
  .tile .stats {{ font-size: 12px; margin-top: 4px; }}

  /* View switch */
  .controls {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 22px; }}
  .switch {{
    display: inline-flex; gap: 4px; padding: 4px;
    background: var(--panel); border-radius: 999px;
  }}
  .switch button {{
    font: 600 13px Inter, system-ui, sans-serif; color: var(--muted);
    background: none; border: 0; border-radius: 999px; padding: 8px 18px; cursor: pointer;
  }}
  .switch button[aria-pressed="true"] {{
    color: #1a1014; background: linear-gradient(90deg, var(--accent), var(--accent-2));
  }}
  .switch button:focus-visible {{ outline: 2px solid var(--accent-2); outline-offset: 2px; }}

  /* Member filter: same height and shape as the pill switches, pushed to the right. */
  .filter {{ margin-left: auto; }}
  .filter select {{
    appearance: none; -webkit-appearance: none;
    font: 600 13px Inter, system-ui, sans-serif; color: var(--ink);
    background: var(--panel) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%239c97a8' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 16px center;
    border: 0; border-radius: 999px; padding: 12px 40px 12px 20px; cursor: pointer;
  }}
  .filter select.active {{ box-shadow: inset 0 0 0 1px var(--accent); }}
  .filter select:focus-visible {{ outline: 2px solid var(--accent-2); outline-offset: 2px; }}
  .filter option {{ background: var(--panel); color: var(--ink); }}
  .empty {{ color: var(--muted); font-size: 15px; padding: 48px 0; text-align: center; }}

  .view-list {{ display: none; }}
  .poster[data-view="list"] .view-poster {{ display: none; }}
  .poster[data-view="list"] .view-list {{ display: grid; }}

  /* List of cards */
  .list {{ list-style: none; margin: 0; padding: 0; gap: 12px; grid-template-columns: 1fr; }}
  .row {{
    display: grid; grid-template-columns: 56px 88px 1fr auto; align-items: center; gap: 20px;
    background: var(--panel); border-radius: 16px; padding: 12px 20px 12px 12px;
    animation: rise .4s backwards cubic-bezier(.2,.7,.2,1);
    /* Cap the stagger so the last row isn't left waiting two seconds to appear. */
    animation-delay: calc(min(var(--i), 12) * 40ms);
  }}
  .row.first {{ box-shadow: inset 0 0 0 1px rgba(255,194,75,.45); }}
  .row-rank {{
    font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
    font-size: 40px; letter-spacing: -.04em; text-align: center; color: var(--muted);
  }}
  .row.first .row-rank {{
    background: linear-gradient(180deg, var(--accent-2), var(--accent));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .row-art {{ width: 88px; height: 88px; border-radius: 10px; object-fit: cover; display: block; }}
  /* A placeholder also carries its cover's class, whose display: block would beat
     .noart's centring grid; this comes after all of them and is at least as specific. */
  .card .noart, .tile .noart, .row-art.noart {{ display: grid; }}
  .row-meta {{ min-width: 0; }}
  .row .album {{ font-weight: 600; font-size: 18px; line-height: 1.25; }}
  .row .artist {{ color: var(--muted); font-size: 15px; margin-top: 3px; }}
  .row-numbers {{ width: 200px; }}
  .row-numbers .dots {{ margin-top: 0; }}
  @media (prefers-reduced-motion: reduce) {{ .row {{ animation: none; }} }}

  footer {{ color: var(--muted); font-size: 12px; margin-top: 28px; text-align: center; }}

  @keyframes rise {{ from {{ opacity: 0; transform: translateY(14px); }} }}
  @media (prefers-reduced-motion: reduce) {{ .card {{ animation: none; }} }}

  @media (max-width: 820px) {{
    .poster {{ padding: 36px 16px; }}
    .hero {{ grid-template-columns: 1fr; padding: 20px; gap: 20px; }}
    .crown {{ font-size: 88px; }}
    ol {{ grid-template-columns: repeat(2, 1fr); gap: 12px; }}
    .rank {{ font-size: 40px; }}
    .list {{ grid-template-columns: 1fr; }}
    .more-grid {{ grid-template-columns: repeat(3, 1fr); gap: 16px 10px; }}
    .row {{ grid-template-columns: 36px 64px 1fr; gap: 12px; padding: 10px; }}
    .row-rank {{ font-size: 28px; }}
    .row-art {{ width: 64px; height: 64px; }}
    .row .album {{ font-size: 15px; }}
    .row .artist {{ font-size: 13px; }}
    .row-numbers {{ grid-column: 2 / -1; width: auto; }}
  }}
</style>
</head>
<body>
<main class="poster" data-view="poster" data-rank="track">
  <header>
    <div class="eyebrow">The server has spoken</div>
    <h1>{e(TITLE)}<br><span class="year">{e(aoty.TARGET_YEAR)}</span></h1>
    <div class="sub" id="sub"></div>
    <noscript><div class="sub">This page needs JavaScript to show the rankings.</div></noscript>
    <div class="controls">
      <div class="switch" role="group" aria-label="View">
        <button type="button" data-view="poster" aria-pressed="true">Poster</button>
        <button type="button" data-view="list" aria-pressed="false">List</button>
      </div>
      <div class="switch" role="group" aria-label="Ranking">
      {rank_buttons}
      </div>
      <label class="filter">
        <select id="who" aria-label="Show albums played by" autocomplete="off">
        <option value="">Everyone</option>
        {member_options}
        </select>
      </label>
    </div>
  </header>
  <div id="ranking"></div>

  <footer>Data from Last.fm scrobbles (last 12 months) · release years from MusicBrainz</footer>
</main>
<script type="application/json" id="aoty-data">{data_json}</script>
<script>{PAGE_SCRIPT}</script>
</body>
</html>
"""


def main():
  if not aoty.LASTFM_USERS:
    raise SystemExit("Set LASTFM_USERS in .env (comma-separated Last.fm usernames; see .env.example)")
  OUTPUT_HTML.parent.mkdir(exist_ok=True)
  OUTPUT_HTML.write_text(render(page_data(*build_rankings(TOP_N))), encoding="utf-8")
  print(f"\nWrote {OUTPUT_HTML}")
  aoty.mb_report("build_page")


if __name__ == "__main__":
  main()
