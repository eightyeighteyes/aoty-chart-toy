# aoty-chart

A one-off-turned-small-project: rank the albums **released in a target year** that a
Discord server's members listened to most, using their Last.fm scrobbles, and render
the result as a shareable web page.

## Milestones

0. Last.fm / musicbrainz scraper that builds a pretty page of a Discord server's top albums for 2026 <-- WE ARE HERE
1. Single page app that lets someone put in a last.fm user ID and get the top albums for that person for 2026
2. Single page app that lets someone put in multiple last.fm user IDs and get the top albums for that group in 2026, with filters by person
3. Sharing features: 4:3/1:1 graphics
4. Album of any year: let the user choose what year to show album rankings for
5. Monetization: Introduce a monetized "Supporter" tier (the cost of compute and storage for a user for a year)
6. Persistence: A Supporter feature that remembers the last.fm users the chart is for, so someone can come back to it over and over and watch the top list change by year.

## Layout

| Path | What |
|------|------|
| `aoty.py` | Data step. Fetches each member's Last.fm top albums (last 12 months), looks up each album's original release year on MusicBrainz, writes the ranked CSV. Holds all shared config: `TARGET_YEAR`; from `.env`: `LASTFM_API_KEY`, `LASTFM_USERS` (comma-separated), `MB_CONTACT`. |
| `build_page.py` | Page step. Reads the CSV, fetches cover art from Last.fm `album.getInfo`, writes `site/index.html`. Imports `aoty` for config so the two can't drift. Knobs: `TOP_N` (100), `FEATURED_N` (10), `TITLE`. |
| `data/server_top_albums_<year>.csv` | Generated ranking (all matching albums, not just the top 50). `Listener Plays` column is JSON `{username: plays}`, which the member filter needs. |
| `data/lastfm_art_cache.json` | Cover URL per album (`""` = no cover). Member views can show any of the ~570 albums, so covers are cached; failed lookups aren't stored. |
| `data/mb_release_year_cache.json` | Persistent MusicBrainz cache. Precious: rebuilding it from empty takes ~13 min. |
| `data/mb_track_count_cache.json` | Track count per album (MusicBrainz release search, Last.fm tracklist fallback), filled by `build_page.py`. ~2 min to rebuild for ~570 albums. |
| `site/index.html` | Generated, self-contained page (only external deps: Google Fonts, Last.fm cover CDN). |
| `.env` | `LASTFM_API_KEY`, `LASTFM_USERS`, `MB_CONTACT` — git-ignored. `.env.example` documents it. |

## Running

Always use the project venv. The shell's `python3` is a pyenv 3.13 **without** `requests`;
it's a moving target (it changed between sessions once already), so never rely on it.

```sh
.venv/bin/python -m pip install -r requirements.txt   # if .venv is missing: python3 -m venv .venv first
.venv/bin/python aoty.py          # ~13 min cold, seconds with a warm cache
.venv/bin/python build_page.py    # ~10–20 s (100 parallel cover lookups)
open site/index.html
```

`build_page.py` does `import aoty`, which works because the script's own directory is on
`sys.path` — run it by path, not as a module from elsewhere.

Ranking: unique listeners first, then total plays. A single play counts as a listener
(user decision, 2026-11-19 — no minimum-plays threshold, same as fmbot).

The page has three rankings (pill switch next to Poster | List, persisted as `aoty-rank`),
all computed in `build_page.py` from the CSV:
- **By Track**: the CSV order above.
- **By Album**: plays ÷ track count ("album plays").
- **Weighted**: plays ÷ tracks ÷ listeners (mean album plays per listener).

User decisions (2026-11-20): singles are left out of By Album and Weighted only, because
dividing by 1 track put them on top. MusicBrainz's release-group type decides,
whatever the length: gladie "I Want That For You" is a 4-track `Single` (out); a 1-track
`Album` like Dopesmoker or The Necks is in (user's call). `MIN_ALBUM_TRACKS` (4) applies
only to untyped releases (Last.fm fallback counts). The track cache stores
`{"tracks", "type"}` per album.
**Member filter** (added 2026-11-20): a `<select>` right-aligned after the switches. It lists
"Everyone" (the default), then members by Last.fm username (the public name people will type
once published), sorted alphabetically ignoring case. Picking a member **re-ranks** by that
member's own plays: By Track = their plays; By Album = their plays ÷ tracks (singles still out);
Weighted = By Album, because listeners = 1 (no `WEIGHTED_MIN_LISTENERS` floor). Ties go to
server listeners, then CSV order. Persisted as `?user=<username>` (matched case-insensitively,
unknown name = Everyone, written with `replaceState`), not in localStorage. A member's
rankings are computed **client-side** from embedded JSON (`#aoty-data`, built by `page_data`;
the JS is `PAGE_SCRIPT`). Python picks the albums it needs: each member's top 100 under both
scores, plus every tie at the cutoff. Those 1-play ties pull in all ~570 albums (page ≈285 KB).

Weighted requires `WEIGHTED_MIN_LISTENERS` (3); without that floor, one-listener albums
took 15 of the top 25. Track counts come from the earliest Official matching MusicBrainz
release (the user considers MB the definitive source), with Last.fm `album.getInfo` as
the fallback.

**Discovery vs counting (fixed 2026-11-19):** the script used to count only each member's
top 100 albums, so anyone who played an album a little (rank 100–2,300) wasn't counted as a
listener: "you seem pretty sad…" showed 8 listeners instead of 13. Now `ALBUMS_PER_USER` (100)
only picks which albums are *candidates* (the ones that get MB lookups). Plays and listeners
come from each member's **full** paginated 12-month list (500–5,500 albums, 1000/page). A
failed page aborts the run rather than silently truncating. fmbot's per-album breakdown
(`.scratch/fmbot-album-plays.txt`) is a useful cross-check, but its member set differs from
`LASTFM_USERS`.

## MusicBrainz — hard-won lessons

- **The search URL must name the entity**: `https://musicbrainz.org/ws/2/release-group/`. Bare `/ws/2/` returns a 301 and the original script silently treated every album as "no year".
- **Rate limit is 1 req/s per client and is the bottleneck.** The only real speedup is fewer requests: lookups are batched as one Lucene query `(artist:"A" AND releasegroup:"B") OR ...` of `MB_BATCH_SIZE` (20) albums, `limit=100`. Misses get one individual retry each.
- **A search hit is not a match.** The phrase `"1989"` also returns `1989 [nightcore]`, `1989 (Taylor's Version)`, live bootlegs. `pick_year` compares normalized title *and* artist credit itself, prefers primary type Album > EP > other, then earliest date.
- **Normalization (`norm`)**: NFKC + casefold, curly→straight quotes, punctuation collapsed. `base_title` strips Last.fm edition suffixes (`(Deluxe Edition)`, `[Remastered 2011]`) because MB release-group titles don't carry them — query with the base title, match against either.
- **503 = throttled.** `mb_search` backs off exponentially (up to 4 tries) and never caches a failure, so the next run retries it. Confirmed misses (a 200 with no match) *are* cached as `null`.
- **Unexplained:** the first full run averaged ~2.9 s/request, not 1.1 s (281 requests, 13.5 min, ~1,560 unique albums, 571 from 2026). Either big batched queries are slow server-side or there were silent 503 back-offs. Instrument `mb_search` (log status + elapsed) before tuning `MB_BATCH_SIZE`.
- `MB_HEADERS` User-Agent falls back to a placeholder contact (`contact@example.com`) unless `MB_CONTACT` is set in `.env`; MB asks for a real one.

## Page — design decisions

- Dark "poster" aesthetic; fonts Bricolage Grotesque (display) + Inter. Tokens on `:root` (`--bg`, `--panel`, `--accent` #ff6b4a, `--accent-2` #ffc24b).
- **Poster view**: #1 as a hero (big cover, blurred-cover backdrop, "#1" gradient crown), #2–#10 as a 3-col card grid, #11–#100 as a compact 5-col tile grid (no listener dots there — too small).
- **List view**: all 100 as horizontal cards, #1 with a gold outline. Toggle is a pill switch in the header; choice persisted in `localStorage` (`aoty-view`), wrapped in try/catch because storage can be blocked on `file://`.
- **Listener dots**: one dot per server member, filled for those who played it — the at-a-glance "how shared is this album" signal.
- **Seam fix (keep it):** a full-width grey seam appeared in list view. Pixel sampling showed flat grey vs the real `--bg` — compositor default backdrop showing through GPU layers. Fixes: `html` also paints `--bg` (with `color-scheme: dark`), and entrance animations use `animation-fill-mode: backwards`, **not `both`** (holding the end state kept every card on its own layer). Stagger delay is capped (`min(var(--i), 12)`) so row 100 doesn't wait 2 s.
- Missing cover → gradient tile with the **artist's name** centred (user decision 2026-11-20,
  replacing album initials). Text size is in `cqi` (the tile is a size container) times a
  per-name `--fs` from `nameSize` (in `<head>`, shared by the renderer and `coverFallback`):
  shorter names get bigger text, and it's capped so the longest word fits. Clamped to 5 lines.
- **Cover 404s:** Last.fm's image CDN resizes on first request and can serve a cached 404
  for one size while the rest are fine (JUNK POP 300x300, 2026-11-20). Each `<img>`
  carries `data-fallbacks` (`ar0/` original, then `174s/`); `coverFallback` in `<head>`
  walks those, then swaps in the initials tile. A 200 from curl here doesn't mean the
  user's browser or CDN edge gets one.

## Open items

- **Publishing is undecided.** Options discussed: a Claude artifact (private claude.ai page, shareable link, update in place — recommended), Netlify Drop, or GitHub Pages (would serve `site/`). If publishing as an artifact, load the `artifact-design` skill first; the page is dark-only and the artifact contract wants light/dark tokens.
- **Privacy (done 2026-11-20):** `LASTFM_USERS` and `MB_CONTACT` live in `.env`. The generated
  CSV and `site/` contain usernames + per-member play counts, and `.scratch/` has Discord names,
  so all three are git-ignored. All of `data/` (caches included) is ignored too: it's build-level
  state, and production will likely cache differently (user decision 2026-11-20). Keep
  usernames out of code comments and docs (use `alice`/`bob`).
- Spot-check suspicious years — e.g. Star Moles "Highway to Hell" (#2) shares a title with the 1979 AC/DC album; the artist match is exact, so it's probably a genuine 2026 release, but unverified.
- No remote and no commits yet.

## Working with this user

- The user does all visual verification of the page — don't launch a browser to check it; regenerate and tell them what to look at.
- Their shell hook blocks compound Bash (`&&`, `;`, `cd X && …`, heredocs): issue single commands, use absolute paths, use Read/Edit tools for files.
- When a visual bug is reported with a screenshot, sample the pixels (PIL) before theorizing — it took one command to tell a compositor seam from a CSS colour bug.
- Commit only when asked.
