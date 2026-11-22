# aoty-chart

A one-off-turned-small-project: rank the albums **released in a target year** that a
Discord server's members listened to most, using their Last.fm scrobbles, and render
the result as a shareable web page.

## Milestones

Milestones are Jira epics in project **AOTY** (eightyeighteyes.atlassian.net); the epics hold the
detail, so read them rather than restating it here.

| Epic | Milestone |
|------|-----------|
| AOTY-1 | M0: Discord server AOTY page (this repo's scraper + page) <-- WE ARE HERE |
| AOTY-2 | M1: Single-user web app |
| AOTY-3 | M2: Group charts with per-person filter |
| AOTY-4 | M3: Sharing graphics (4:3, 1:1) |
| AOTY-5 | M4: Albums of any year |
| AOTY-6 | M5: Monetization (Supporter tier) |
| AOTY-7 | M6: Persistence (saved groups, chart over time) |

Remote: GitHub `eightyeighteyes/aoty-chart-toy`. Commits use Conventional Commits (releases will be cut from history).

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
| `tools/mb_cold_run.py` | MusicBrainz benchmark (AOTY-10). Cold lookups of a frozen, shuffled candidate list (`data/mb_bench/candidates.json`, no usernames) into `data/mb_bench/run-<ts>/`, rotating batch-size / interval arms. Never touches the real caches (path assert + checksum). `--limit 40 --chunk 10` is a 30 s dry run. Don't run anything else that hits MusicBrainz at the same time. |
| `tools/mb_analyze.py` | Per-arm markdown tables for one or more `run-*` folders: 503 rate, server p50/p95, sleep vs server, retry cost, albums/s, rate-limit headers, years diff vs the real cache. |
| `data/mb_requests.csv` | Per-attempt MusicBrainz stats appended by `mb_report` after normal runs (git-ignored). |
| `tools/lastfm_spike.py` | Last.fm data-cost spike (AOTY-11): year-to-date distribution, `getRecentTracks` backfill, storage sizes, weekly/range album charts vs counted scrobbles, `extended=1` coverage. Writes `data/lastfm_spike/run-<ts>/report.md`, using member labels (`m01`, light/medium/heavy), never usernames. About 6 min; `--limit-pages 3` for a dry run, `--backfill-only` for a repeat timing run. |
| `.env` | `LASTFM_API_KEY`, `LASTFM_USERS`, `MB_CONTACT` — git-ignored. `.env.example` documents it. |
| `infra/` | Terraform for M1 (AOTY-14): `global/` (GitHub OIDC provider, CI roles, admin-only), `envs/{staging,prod}/` (thin roots), `modules/stack/` (one environment). `infra/README.md` has the layout, ownership rules and bootstrap. |
| `tests/` | pytest (AOTY-16): matching rules against recorded MusicBrainz searches in `tests/fixtures/mb/`, track counts and singles, ranking orders. `conftest.py` blanks the `.env` keys before `aoty` is imported. |
| `tools/record_mb_fixtures.py` | Re-records the MusicBrainz fixtures with the scripts' own query builders (4 requests). Only to refresh them; never during a benchmark. |
| `.github/workflows/ci.yml` | CI (AOTY-16): `lint`, `test`, `terraform` (fmt + validate), `terraform-plan` (staging + prod via the OIDC plan role, one PR comment edited in place). All four are required on `main`. |
| `.github/workflows/cd.yml`, `deploy.yml` | CD (AOTY-17): after CI passes on `main`, deploy staging, then release-please; a `v*` tag deploys prod; `workflow_dispatch` for rollback. Flow, rollback and the release App setup are in `infra/README.md`, "Deploys and releases". |
| `lambdas/{api,sync,mb}/handler.py` | Lambda code CD deploys (Terraform only creates functions). Shared code goes in `lambdas/common/`, runtime deps in `lambdas/requirements.txt`. `scripts/package_lambdas.py` builds reproducible arm64 zips into `dist/`. |
| `release-please-config.json`, `.release-please-manifest.json`, `version.txt`, `CHANGELOG.md` | release-please state. Don't edit the version or changelog by hand. |
| `pyproject.toml`, `requirements-dev.txt` | ruff (2-space indent, 110 columns, `tools/` excluded until AOTY-12 merges) and pytest config; pinned dev tools. |

## Running

Always use the project venv. The shell's `python3` is a pyenv 3.13 **without** `requests`;
it's a moving target (it changed between sessions once already), so never rely on it.

```sh
.venv/bin/python -m pip install -r requirements.txt   # if .venv is missing: python3 -m venv .venv first
.venv/bin/python aoty.py          # ~13 min cold, seconds with a warm cache
.venv/bin/python build_page.py    # ~10–20 s (100 parallel cover lookups)
open site/index.html

.venv/bin/python -m pip install -r requirements-dev.txt   # once, for the checks below
.venv/bin/ruff check .
.venv/bin/ruff format --check .   # `ruff format .` to fix
.venv/bin/python -m pytest
```

The pure ranking cores are `aoty.aggregate_plays`, `aoty.rank_albums` and
`build_page.score_rankings`. Keep I/O out of them; the tests and the M1 Lambdas both use them.

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
- **An exact title beats an edition-stripped one** (`title_tier`, fixed 2026-11-21). `base_title` strips any bracket mentioning "version", so `1989 (Taylor's Version)` used to take the 2014 `1989` release group's year. Both `pick_year` and `pick_release` sort on the tier first. Deluxe editions still resolve to the original, because MusicBrainz keeps them in the original release group.
- **Normalization (`norm`)**: NFKC + casefold, curly→straight quotes, punctuation collapsed. `base_title` strips Last.fm edition suffixes (`(Deluxe Edition)`, `[Remastered 2011]`) because MB release-group titles don't carry them — query with the base title, match against either.
- **503 = throttled.** `mb_search` waits the 503's `Retry-After` (at least `MB_MIN_INTERVAL`, plus up to `MB_JITTER` 0.5 s), up to `MB_MAX_ATTEMPTS` (8) tries. It never caches a failure, so the next run retries it. Confirmed misses (a 200 with no match) *are* cached as `null`. `MB_BACKOFF = "exponential"` restores the old `MB_MIN_INTERVAL * 2**attempt` x4.
- **Throughput is set by MusicBrainz's 503s, not by our pacing or the queries** (AOTY-10,
  two cold runs of 1,536 albums, 2026-11-21 ~10:00 and ~12:00 ET, `tools/mb_cold_run.py`):
  - ~45–55% of *all* attempts got a 503, steady minute to minute (not bursts), whatever the
    batch size or interval. A slower 2.0 s interval made it worse (61%), not better.
  - Every 503 carried `Retry-After: 1`. `X-RateLimit-Limit` is 400 and `-Remaining` had
    15–399 (median ~230) left on the 200 before each 503, so we aren't draining it. It's
    shared load on MusicBrainz's side.
  - Server time is small: p50 ~0.6 s, p95 ~1.4 s, for batches of 10–40.
  - With the old exponential x4 back-off, ~80% of a run was spent asleep, and 1–2% of albums
    gave up after four straight 503s (dropped, then retried next run).
  - Albums answered per second (run 1 / run 2): batch 20 1.53 / 1.14, batch 40 1.07 / 1.10,
    batch 10 0.79 / 0.62, batch 20 at 2.0 s 0.20 / -, and **Retry-After x8: - / 2.34, with
    no albums lost**. That's why the defaults are now Retry-After x8, batch 20, 1.1 s.
  - Retry-After was only in run 2. Re-run the benchmark if MusicBrainz's behaviour seems
    to change.
  - Expect a full cold `aoty.py` (~1,540 candidates) to take ~11 min at 2.3 albums/s, and up to
    ~20 min on a bad day.
  - For M1 (AOTY-2): one client warms ~2 albums/s at best, and half its requests are refused,
    so a shared, persistent release-year cache is mandatory. A cold user with ~100 new albums
    is ~45 s of lookups.
  - `mb_report` prints a per-run summary and appends per-attempt rows (status, timing, rate-limit
    headers) to `data/mb_requests.csv`.
- `MB_HEADERS` User-Agent falls back to a placeholder contact (`contact@example.com`) unless `MB_CONTACT` is set in `.env`; MB asks for a real one.

## Last.fm data cost (AOTY-11 spike, 2026-11-21, 19 members)

- Year to date, members have 3.4k–34k scrobbles (mean 11.3k), which is 17–170 `getRecentTracks` pages
  at 200 per page. Each page takes about 1 s, so a heavy backfill is 3 min single-threaded. There were
  no error-29 rate limits at 4 req/s.
- **`user.getWeeklyAlbumChart` takes arbitrary `from`/`to`**, not only `getWeeklyChartList` boundaries
  (Sunday 12:00 UTC). Shifted weeks, a calendar month and the current partial week match counted
  scrobbles exactly. It is live, with no lag. **Each response is capped at about 1,000 album rows**
  (`limit` doesn't lift it), so the year-to-date range comes back short. Split any range whose result
  is near the cap.
- **Charts group by album artist; scrobbles carry only the track artist.** Compilations ("Various
  Artists") and featured-artist tracks split into many keys when you count scrobbles. Weekly play totals
  are still identical. Charts match `getTopAlbums`/M0's grouping; counting scrobbles doesn't.
- Charts have album MBIDs (69–83% of rows) but **no images**. Recent tracks have covers for about 94%
  of albums and MBIDs for about 84%. `extended=1` adds nothing for either and makes pages 61% bigger,
  so don't use it.
- The now-playing track is returned even for a pinned window in the past, so drop rows with no `date`.
  A nonexistent user returns error 6. The private-account error (17?) wasn't tested, because no member
  hides their listening.

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

## Infrastructure (AOTY-14, 2026-11-21)

Details are in `infra/README.md`. Things to keep in mind when changing it:

- **Never give the PR plan role broad reads.** The repo is public, any same-repo PR can use the
  role, and kamp-site's state (`site/` in the same bucket) holds the kamp deploy user's secret key.
- **A blanket `kms:Decrypt` deny hides Lambda environment variables** from `GetFunction` and makes
  a permanent diff. Deny decryption through SSM only.
- **Assumed-role sessions outlive a trust change.** The CLI caches them for up to 1 h, so to test
  that a trust was removed, use a fresh `aws sts assume-role`.
- **The Lambda concurrency limit was 10**, so reserved concurrency is impossible until the quota
  increase is granted (requested 2026-11-21).
- The deploy roles only allow what's in use. New resource types mean editing
  `global/ci_roles.tf` and having the admin apply `global` first.
- Testing role policies from the laptop uses `admin_assume_principal`. Turn it off afterwards.
- **This repo uses GitHub's immutable OIDC subject claims:**
  `repo:eightyeighteyes@6600722/aoty-chart-toy@1466126436:...`, not
  `repo:eightyeighteyes/aoty-chart-toy:...`. A trust policy with the bare names gives "Not
  authorized to perform sts:AssumeRoleWithWebIdentity" (AOTY-16). The prefix is
  `github_sub_prefix` in `infra/global/variables.tf`.
- **Required checks need a job that always reports.** Don't add `paths:` filters to `ci.yml`;
  skip inside a job with `if:` instead, because a skipped job counts as passed.
- **GitHub runs no workflows for PRs, tags or releases made with `GITHUB_TOKEN`.** That's why
  release-please uses the GitHub App token (`RELEASE_APP_CLIENT_ID` / `RELEASE_APP_PRIVATE_KEY`).
  `workflow_dispatch` is the one event `GITHUB_TOKEN` can trigger.
- **`workflow_run`, tag-push and `workflow_dispatch` workflows only run from files on `main`.** A
  change to `cd.yml` or `deploy.yml` is really tested only after merge. actionlint in CI catches
  typos first.
- **The `production` environment only accepts `v*` tags**, so a prod deploy must run on the tag
  ref (`deploy.yml` on tag push), never from a job on `main`.

## Open items

- **Hosting:** the prototype page is on Netlify (shared with the server). The real app will
  live at **kamp.fm/toys/aoty/** (the user's domain), so build for that base path.
- **Privacy (done 2026-11-20):** `LASTFM_USERS` and `MB_CONTACT` live in `.env`. The generated
  CSV and `site/` contain usernames + per-member play counts, and `.scratch/` has Discord names,
  so all three are git-ignored. All of `data/` (caches included) is ignored too: it's build-level
  state, and production will likely cache differently (user decision 2026-11-20). Keep
  usernames out of code comments and docs (use `alice`/`bob`).
- Release years verified: Star Moles "Highway to Hell" is a genuine 2026 Star Moles release, not
  the AC/DC album (user, 2026-11-21). Don't re-flag it.
- AOTY-1 (M0) is prototype work only; AOTY-10 (full cold MusicBrainz run) is its last open task.
  M1 (AOTY-2) is the first production milestone: all deployment / architecture stories go there.

## Working with this user

- The user does all visual verification of the page — don't launch a browser to check it; regenerate and tell them what to look at.
- Their shell hook blocks compound Bash (`&&`, `;`, `cd X && …`, heredocs): issue single commands, use absolute paths, use Read/Edit tools for files.
- When a visual bug is reported with a screenshot, sample the pixels (PIL) before theorizing — it took one command to tell a compositor seam from a CSS colour bug.
- Commit only when asked.
