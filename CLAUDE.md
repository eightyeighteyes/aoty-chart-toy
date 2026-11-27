# aoty-chart

A one-off-turned-small-project: rank the albums **released in a target year** that a
Discord server's members listened to most, using their Last.fm scrobbles, and render
the result as a shareable web page.

## Milestones

Milestones are Jira epics in project **AOTY** (eightyeighteyes.atlassian.net); the epics hold the
detail, so read them rather than restating it here.

| Epic | Milestone |
|------|-----------|
| AOTY-1 | M0: Discord server AOTY page (this repo's scraper + page) |
| AOTY-2 | M1: Single-user web app |
| AOTY-3 | M2: Group charts with per-person filter (owned groups, Last.fm sign-in) <-- WE ARE HERE |
| AOTY-4 | M3: Sharing, personalization and visual delight |
| AOTY-5 | M4: Albums of any year |
| AOTY-6 | M5: Monetization (Supporter tier) |
| AOTY-7 | M6: Persistence (chart over time; saved groups moved to M2) |

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
| `tools/mb_lambda.py` | AOTY-12 harness: deploys a throwaway us-east-1 Lambda (`tools/mb_lambda/handler.py`), runs `mb_cold_run` arm L there in 250-album slices (`sweep`), and unpacks each slice into `data/mb_bench/run-<ts>-lambda/`. `teardown` removes the function, role and log group. `mb_cold_run.py --offset/--limit` takes the same slices locally. |
| `tools/mb_index_dump.py` | AOTY-13: downloads the MusicBrainz JSON dump's `release-group.tar.xz` (resumable, SHA256-checked) into `data/mb_index/dump-<name>/`. Writes `index_2026.json` and the all-years `keymap.json`. `release-probe` times the first 1 GB of `release.tar.xz`. Doesn't use the rate-limited API. |
| `tools/mb_index_crawl.py` | AOTY-13: crawls every 2026 release group through search, split into id ranges of one page each (`crawl`, about 85 min), into `data/mb_index/crawl-<ts>/`. `offset-probe` records the 500-result cap. Run it on its own. |
| `tools/mb_index_eval.py` | AOTY-13: `report` scores the indexes against every album M0 looked up, explains misses, compares crawl and dump, and writes `report.md`. `track-probe` prices live track-count lookups. AOTY-20: `shards` builds the real shards locally (~10 min) and fails on any trusted answer that disagrees with M0. |
| `jobs/index/` | AOTY-20 release index build: `build.py` (Fargate Spot task: download, spill, shard, publish, re-check) and `dump.py` (dump download/streaming, shared with `tools/mb_index_dump.py`). Packaged into `dist/index.zip`. |
| `lambdas/common/releaseindex.py` | The index format: `shard_of`, `record`, the manifest and its `norm` fingerprint, `lookup` with the trust rule. Pure. `common/dynamo.py` holds DynamoDB value conversion. |
| `tools/mb_analyze.py` | Per-arm markdown tables for one or more `run-*` folders: 503 rate, server p50/p95, sleep vs server, retry cost, albums/s, rate-limit headers, years diff vs the real cache. |
| `data/mb_requests.csv` | Per-attempt MusicBrainz stats appended by `mb_report` after normal runs (git-ignored). |
| `tools/lastfm_spike.py` | Last.fm data-cost spike (AOTY-11): year-to-date distribution, `getRecentTracks` backfill, storage sizes, weekly/range album charts vs counted scrobbles, `extended=1` coverage. Writes `data/lastfm_spike/run-<ts>/report.md`, using member labels (`m01`, light/medium/heavy), never usernames. About 6 min; `--limit-pages 3` for a dry run, `--backfill-only` for a repeat timing run. |
| `.env` | `LASTFM_API_KEY`, `LASTFM_USERS`, `MB_CONTACT` — git-ignored. `.env.example` documents it. |
| `infra/` | Terraform for M1 (AOTY-14): `global/` (GitHub OIDC provider, CI roles, admin-only), `envs/{staging,prod,sandbox}/` (thin roots), `modules/stack/` (one environment). `infra/README.md` has the layout, ownership rules and bootstrap. `envs/sandbox/` (AOTY-33) is where an unmerged branch is tested: not on kamp.fm, its own data and Last.fm key; see "Testing a branch in sandbox". |
| `tests/` | pytest (AOTY-16): matching rules against recorded MusicBrainz searches in `tests/fixtures/mb/`, track counts and singles, ranking orders. `conftest.py` blanks the `.env` keys before `aoty` is imported. |
| `tools/record_mb_fixtures.py` | Re-records the MusicBrainz fixtures with the scripts' own query builders (4 requests). Only to refresh them; never during a benchmark. |
| `.github/workflows/ci.yml` | CI (AOTY-16): `lint`, `test`, `terraform` (fmt + validate), `terraform-plan` (staging + prod via the OIDC plan role, one PR comment edited in place). All four are required on `main`. |
| `.github/workflows/cd.yml`, `deploy.yml` | CD (AOTY-17): after CI passes on `main`, deploy staging, then release-please; a `v*` tag deploys prod; `workflow_dispatch` for rollback, or to deploy any branch to sandbox (`-f environment=sandbox -f skip_terraform=false`, needs a reviewer). Flow, rollback and the release App setup are in `infra/README.md`, "Deploys and releases". |
| `scripts/sandbox_reset.py` | AOTY-33: wipes sandbox data (queues, bucket versions, table items; `--keep-albums`), keeping its infrastructure. Dry run unless `--yes`; refuses anything that isn't a `kamp-aoty-sandbox-*` name in the expected account. Admin credentials. |
| `lambdas/{api,sync,mb,chart,uptime}/handler.py` | Lambda code CD deploys (Terraform only creates functions). Shared code goes in `lambdas/common/` (imported as `common.*`; `albumkey` holds `norm`/`base_title`, which `aoty.py` re-exports), runtime deps in `lambdas/requirements.txt` (standard library only so far). `scripts/package_lambdas.py` builds reproducible arm64 zips into `dist/`, each stamped with a `build.json` `{"sha"}` (`-dirty` for uncommitted or untracked packaged files) that `/api/health` reports and CD's smoke test checks (AOTY-35). Don't add a `build.json` to the source: packaging rejects it. |
| `lambdas/sync/` | Sync worker (AOTY-18). `sync_core.py` is pure (week ranges, settle rule, merging, mb selection, covers); `handler.py` does the S3/DynamoDB/SQS I/O behind an injectable `Deps`. Contract and store format are in the module docstrings. |
| `lambdas/mb/` | MusicBrainz lane (AOTY-19). `mb_core.py` is pure (year then track lookups in M0's order, items); `handler.py` drains the queues and writes the `albums` table behind an injectable `Deps`. Contract in the module docstrings and "MusicBrainz lane" below. |
| `lambdas/common/mbmatch.py`, `musicbrainz.py` | The matching rules (`pick_release_group`/`pick_year`, `pick_release`, `title_tier`, `is_single`, query clauses), shared by M0 (`aoty.py`/`build_page.py` re-export them) and the mb Lambda; and the standard-library MusicBrainz client with a per-call deadline. |
| `tools/mb_parity.py` | AOTY-19 acceptance: runs `mb_core` on the laptop over `data/mb_bench/candidates.json` and diffs against M0's year and track caches into `data/mb_parity/run-<ts>/report.md`. A MusicBrainz client: run it alone. |
| `docs/architecture.md` | M1 infrastructure and per-actor sequence diagrams (Mermaid): built vs planned, who reads and writes what. Keep it current when a story adds a component. |
| `lambdas/api/` | API (AOTY-22). `api_core.py` is pure (routing, `decide_sync`, status stages, the users-row field list in its docstring); `handler.py` serves health, `POST api/users/<name>/sync`, `GET .../status` and sign-in (AOTY-47) behind an injectable `Deps`. JSON-only POSTs, `no-store` on everything. |
| `lambdas/chart/` | Chart builder (AOTY-21). `chart_core.py` is pure (year filter, By Play, covers, pending; the chart.json format in its docstring); `handler.py` handles the sync and mb events, the `requested_at` skip and the pending pass behind an injectable `Deps`. Reads the year from the `albums` table at build time, never the index. |
| `web/src/` | The page shell: `index.html` template, `app.css`, `app.js` (the chart page, AOTY-21/22, ported from `build_page.py`; on group pages also Weighted, listener dots and the member filter), `intro.js` (the intro page's pure rules, AOTY-23), `lastfm.js` (Last.fm album URLs, AOTY-43: see "Last.fm links"), `share.js` (share links and the share button's rules, AOTY-84: see "Sharing"), `sharecard.js` (the share image's layout, text fitting and colours as draw operations, AOTY-70: see "Share images"), `firstvisit.js` (the banner's and first visit's rules, AOTY-24), `group.js` (the group page's member re-rank, AOTY-51), `groupedit.js` (group editing's answers-to-messages map, name check, owner rule and rebuild-watch rule, AOTY-52), `theme.js` (page themes, AOTY-65: see "User themes"), `borealis.js` (the first visit's aurora, ported from kamp, AOTY-67: see "Borealis") and `kampstats.js` (analytics, AOTY-38: see "Analytics"), all tested by `node --test web/test/*.test.mjs` in CI. Routes client-side: `<base>user/<name>` is a chart, `<base>group/<id>` a group chart (`?user=` picks a member), anything else the intro. kamp.fm's CSP allows no inline script or handlers: keep code in the JS files, use `addEventListener`. `scripts/build_shell.py --base <base> [--ga-id G-…] [--short-host aoty.kamp.fm]` fills `{{NAME}}`/`{{TITLE}}`/`{{YEAR}}`/`{{GA}}`/`{{SHORT}}` and hashes the assets (and fails on inline script); `tools/serve_shell.py` previews it with kamp.fm's routing and CSP (`--sample alice`, or `--proxy sandbox|staging|prod`; fake sign-in and groups from `tools/preview_groups.py`, or `--proxy sandbox --sign-in-as <name>`). |
| `lambdas/common/guardrails.py` | AOTY-26 runtime knobs from SSM (`pause_new_users`, `pause_syncs`, `sync_cooldown_seconds`, `new_users_per_ip_hour`, `max_queue_for_new`), cached ~30 s, and the kamp.fm origin-secret check. Runbook: `infra/README.md`, "Guardrails". |
| `lambdas/common/session.py` | AOTY-47 sign-in, pure: signed session (`v1`) and state (`s1`) tokens, cookie parsing and `Set-Cookie` values. The api's `auth/start`, `auth/callback/<nonce>`, `me`, `auth/signout` are in `api/handler.py`; `handler.session_user` is what AOTY-48's writes call. Runbook: `infra/README.md`, "Sign-in". |
| `lambdas/common/fakelastfm.py`, `tools/load_test.py` | AOTY-26 load test: a stand-in Last.fm `urlopen` (only when `LASTFM_FAKE=1` and `AOTY_ENV=sandbox`) and the driver that switches it on in sandbox, POSTs `loadtest-*` users and writes `data/load_test/run-<ts>/report.md`. Admin credentials; restores everything after. |
| `lambdas/uptime/` | Hourly uptime check (AOTY-27). `uptime_core.py` is pure (URLs, what counts as up); `handler.py` fetches `kamp.fm<base>`, `api/health` and the SSM `uptime_user`'s chart.json, and emits `UptimeChecks`/`UptimeFailures` even on a crash. |
| `infra/modules/stack/dashboard.tf`, `lambdas/common/emf.py` | AOTY-41: the per-env CloudWatch dashboard (at most 50 metrics, checked by a precondition) and the shared EMF line builder. |
| `infra/modules/stack/alarms.tf`, `infra/global/alerts.tf` | AOTY-27: the alarms (one table, `alarms` = full/basic/none per env), and the global `kamp-aoty-alerts` topic and $40 budget. Runbook: `infra/README.md`, "Alarms". |
| `lambdas/chart/group_core.py`, `tools/beta_group.py` | AOTY-50: a group's members' stores → `data/groups/<slug>/chart.json` with M0's rules (`tests/test_ranking.py` checks parity with `score_rankings`). Groups are addressed by a readable slug, not a random id (user decision 2026-11-23: guessable is fine, it's public Last.fm data). The tool creates a group in the groups table by hand (members from `.env`), queues its syncs, rebuilds, shows and deletes it, and `migrate`s the pre-AOTY-48 S3 beta files. |
| `lambdas/common/groups.py`, `lambdas/api/groups_store.py` | AOTY-48 owned groups: the table layout and pure rules (slugs, names, `member_state`), and the api's transactional writes (cap of 25, owner checks), plus a visit's sync claim (AOTY-55). Routes in `api/handler.py` ("groups"); tests in `tests/test_api_groups.py`. |
| `tools/prewarm.py` | AOTY-42: pre-warms the shared `albums` table. Candidates under Last.fm's names: `tag.getTopAlbums` for the year's tag (listeners tag albums by release year) and the top artists' `artist.getTopAlbums`. The undated ones go to **mb-cold** in sync's message format, so the lane stays the only writer and hot work is never delayed. No local index check: the lane does it, and old albums with a trusted index hit cost no MusicBrainz request. `--dry-run` sizes it. First prod run (2026-11-23): 2,315 candidates, 411 already dated, 1,904 queued, 214 Last.fm calls. Re-run weekly as the year fills. |
| `tools/chart_check.py` | AOTY-21 acceptance: a deployed chart.json vs M0's rules (`score_rankings` plus the page script's member re-rank, transcribed) on the same store; `--env`, `--user`, prints `--label`. aws CLI, admin credentials. |
| `tools/rebuild_charts.py` | AOTY-82: force-rebuilds every chart in an env (users with a chart, then each group, since a forced member build skips its groups), one synchronous chart invoke at a time; `--dry-run` counts. Users printed by hash. Run after a ranking-rule deploy; it writes, so ask before prod. Admin credentials. |
| `tools/sync_check.py` | AOTY-18 acceptance check: chart year total vs counted scrobbles for one member (default the heaviest; labels `m01`.., never usernames). `--dotenv` points a worktree at the main `.env`. |
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
node --test web/test/*.test.mjs      # the page's pure JS (any recent Node)
```

The pure ranking cores are `aoty.aggregate_plays`, `aoty.rank_albums` and
`build_page.score_rankings`. Keep I/O out of them; the tests and the M1 Lambdas both use them.

`build_page.py` does `import aoty`, which works because the script's own directory is on
`sys.path` — run it by path, not as a module from elsewhere.

Ranking: unique listeners first, then total plays. A single play counts as a listener
(user decision, 2026-11-19 — no minimum-plays threshold, same as fmbot).

**The live app's rankings (user decision 2026-11-25, AOTY-63; M0 is frozen and keeps its own,
below).** By Track is gone everywhere, and with it singles: every ranking divides by track count.
- **User pages: one ranking, By Play** (`album` in chart.json): plays ÷ tracks. There is no
  ranking switch.
- **Group pages** have a switch, By Listener | By Play | Weighted. By Listener is the default; a
  saved `aoty-rank` that isn't one of these (such as the old `track`) falls back to it.
  - **By Listener** (`listener`): unique listeners, then album plays. Without it, one member
    looping a short record beat an album the whole group shared.
  - **By Play** (`album`): album plays, then listeners. This is M0's By Album.
  - **Weighted**: unchanged.
- **A member's view of a group** hides the switch. With one listener, all three are their album
  plays.
- **Charts built before this** keep their old shape until they rebuild. User charts have
  `album`, so they're fine. Group charts have no `listener`, so `group.js` shows `album` for
  By Listener until the next build. No version bump.
- **What counts as a single (`mbmatch.is_single`, AOTY-82, 2026-11-26).**
  - Typed `Album`: in at any length (Dopesmoker, The Necks).
  - Typed `Single`: out.
  - **Anything else** (`EP`, `Other`, `Broadcast`, or untyped): out under `MIN_ALBUM_TRACKS` (4).
  - This replaced "the type decides, whatever the length", which let a 1-track remix EP
    (New Order "Blue Monday (Hawtin Mix…)") and a 1-track "(acoustic)" typed `Other` score a whole
    album's plays from one play.
  - Not covered: secondary types (Remix, Live) aren't stored, so a 1-track remix typed `Album`
    still ranks.
- **A ranking-rule change reaches a chart only when it rebuilds.** Run
  `tools/rebuild_charts.py --env <env>` after the deploy (it writes; ask before prod).

M0's page (frozen) has three rankings (pill switch next to Poster | List, persisted as
`aoty-rank`), all computed in `build_page.py` from the CSV:
- **By Track**: the CSV order above.
- **By Album**: plays ÷ track count ("album plays").
- **Weighted**: plays ÷ tracks ÷ listeners (mean album plays per listener).

User decisions (2026-11-20): singles are left out of By Album and Weighted only, because
dividing by 1 track put them on top. MusicBrainz's release-group type decides,
whatever the length: gladie "I Want That For You" is a 4-track `Single` (out); a 1-track
`Album` like Dopesmoker or The Necks is in (user's call). `MIN_ALBUM_TRACKS` (4) applied
only to untyped releases (Last.fm fallback counts) until AOTY-82, which extended it to every
type except Album and Single; M0 shares `is_single`, so it follows. The track cache stores
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
- **No big "albums being checked" numbers** (user decision 2026-11-23). Charts carry `pending_top`, the undated albums in the top 150 by plays (`chart_core.HOT_N` = sync's, the ones sent to mb-hot); a group counts any member's top 150. While it's > 0 the page says "still checking…" (a user page: "still checking your top albums…"). After that, a user page says nothing, because the cold tail can't move it. A group page says **"still counting deep cuts…"**, because a lightly played album many members share can still surface, since listeners rank first. The cold work still fills the shared `albums` table. `chart_pending` on the users row (the status API, first visit) is unchanged.
- **Charts show the root title** (user decision 2026-11-23): `chart_core` and `group_core` display `base_title(album)`, so "(Deluxe)", "(Remastered)", "(Expanded)" and the like never show. Editions already share the root's store key (`albumkey.album_key`). Side effect: "1989 (Taylor's Version)" also displays as "1989", because `base_title` strips any bracket mentioning "version".
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
- **AWS Lambda is no worse than the laptop** (AOTY-12, 2026-11-21, `tools/mb_lambda.py`).
  - Method: a no-VPC Lambda in us-east-1 ran arm L, which uses arm E's settings. At the same
    time, the laptop ran the same 1,536 albums in the same order.
  - Window 1, 17:22 ET: Lambda 1.64 vs laptop 1.68 albums/s (0.98x), with 48% vs 49% 503s.
  - Window 2, 20:12 ET: Lambda 1.86 vs laptop 1.83 albums/s (1.02x), with 51% vs 51% 503s.
  - Neither side lost an album, and both found the same years.
  - Each window got a different outbound IP (one warm container per sweep). Neither shows any
    sign of other AWS tenants sharing our allowance: the first `X-RateLimit-Remaining` per slice
    was 0–383 of 400, about the same spread as the laptop's.
  - MusicBrainz answers faster from Lambda: p50 ~0.37 s vs ~0.6 s.
  - **Decision: the M1 `mb` lane stays a Lambda.** No Fargate and no NAT.
  - **This AWS account's Lambda concurrency quota is 1,000** (raised from 10 on 2026-11-22). At
    10, `put-function-concurrency` refused even 1, because AWS keeps 10 unreserved. A single
    MusicBrainz client (AOTY-19) can now use reserved concurrency.
  - `mb_lambda.py deploy | run | sweep | teardown` uses the `aws` CLI. Invokes use no CLI
    retries and a 910 s read timeout. The handler resets warm-container state
    (`aoty.reset_run_state`) and stops 150 s before the 15-min cap.

## 2026 release index (AOTY-13 spike, 2026-11-21)

Tools: `tools/mb_index_dump.py`, `tools/mb_index_crawl.py`, `tools/mb_index_eval.py`.
Output goes to `data/mb_index/`.

- **Search can't page past 500 results.** If `offset + limit > 500`, MusicBrainz returns
  HTTP 400 "Must retrieve at most 500 search results".
- **Paging is unreliable even below 500.** Results with equal scores move between pages: a
  3-page partition came back 4% short.
- **Day ranges overlap.** A partial date `2026-01` matches every day in January, and `"2026"`
  matches the whole year.
- **What does work: split by release-group id range.** Take the whole-year query
  `firstreleasedate:[2026 TO 2026-12-31]` and add `rgid:[lo TO hi]`; UUIDs compare as
  strings. Halve each range until it fits on one page of 100. The ranges don't overlap, so
  the per-range counts must add up to the total, and they did exactly. `aoty.mb_get`
  returns `count`/`offset` for this.
- **Counts:** 2026 has about 159.7k release groups:

  | Type | Count |
  |---|---|
  | Single | 82k |
  | Album | 51k |
  | EP | 21k |
  | No type | 2.7k |
  | Broadcast | 1.1k |
  | Other | 1.2k |

  About 2.1k have only a partial date (`2026` or `2026-MM`).
- **Full search crawl:** 79–84 min and 3,600–3,700 attempts, 42–44% of them 503s. The index is
  41 MB of JSON (9.6 MB gzipped).
  - Two back-to-back crawls (21:15 and 22:40 ET) both returned exactly their range counts, with
    no failed pages.
  - The second crawl contains all of the first, plus 25 new release groups, which is exactly how
    much the count grew. No record changed between the two, so the crawl is repeatable.
- **JSON dump:** `release-group.tar.xz` is 1.24 GB. A new dump comes out twice a week.
  - Download: 14.5 min at 1.4 MB/s from the laptop.
  - Parse: 4.66M records take 8 min of CPU, with a peak RSS of 5.4 GB. This builder holds
    every dated title in memory.
  - Records have type, date, credits and aliases, but **no releases or track counts**.
- **Hit rate, scored against all 1,530 albums M0 looked up:**
  - The dump keymap plus `pick_year` found **568/568** of M0's 2026 albums, with **0**
    false positives.
  - It also found Marmozets "CO.WAR.DICE.", which M0's live search had missed (a null
    there).
  - **A 2026-only index isn't enough.** The crawl and the 2026 subset of the dump both call
    Weezer "Weezer" a 2026 album, because a 2026 release group of that name exists. The
    1994 album only wins when release groups from all years are candidates.
  - That's why `keymap.json` holds every release group, of any year, whose `norm(title)`
    matches a 2026 one: 137k titles, 809k candidates, 173 MB (39 MB gzipped).
- **The dump goes stale quickly.** The crawl found 687 release groups from 2026 that weren't
  in a dump made about 22 h earlier. Only 57% of those are dated within the last 30 days or
  later; the rest are older releases being backfilled. So an index miss doesn't prove an
  album isn't from 2026.
- **Track counts:**
  - The release-group dump doesn't have them.
  - `release.tar.xz` (23 GB, about 5M releases) has per-medium `track-count`, `status` and
    the release group's type and date. A full pass is about 3.5 h, mostly download time at
    1.8 MB/s, plus about 2 h of CPU.
  - A live lookup costs 1.8 s per release group (one `release` search on `rgid:`). That's
    about 36 h for all 72k 2026 Albums and EPs.
- The AOTY-20 index that came out of this is in "Release index (AOTY-20)" below. Track counts stay
  live; if the `mb` lane can't keep up, a weekly `release.tar.xz` pass filtered to the target
  year is the fallback.

## Release index (AOTY-20, 2026-11-22)

- **Every dated release group of any year** (user decision), so years are data and the same
  index serves 2027+ and M4. Not the AOTY-13 2026-titles keymap, which would need a per-year build.
- **Format** (`common/releaseindex.py`):
  - 4,096 gzipped shards by `sha1(norm(title))[:3]` under `index/<dump>/` in the store bucket.
  - `index/current.json` is written last, so a build is live only once every shard is uploaded.
  - The manifest carries a fingerprint of `norm`. If `norm` changes, the mb lane turns the index
    off (`IndexErrors`) rather than silently missing everything and sending every album to live
    searches. **Changing `norm` means rebuilding the index.**
- **Trust rule:** an index answer is used only for an exact title (`title_tier` 0) of type Album.
  The dump lags days behind, so last year's single "X" would otherwise stand in for this year's
  album "X" that the dump doesn't have yet. EPs are untrusted too: a new same-named album would
  beat them. Everything else goes to the live search, and an index miss means "unknown".
- **Accuracy** (`tools/mb_index_eval.py shards`, dump 2026-11-21, all 1,530 albums M0 looked up):
  - No trusted answer disagrees with M0.
  - 519/568 of M0's 2026 albums (91%) and 1,183/1,530 overall (77%) need no live year search.
  - The 142 untrusted hits all agree with M0 too: the rule costs lookups, never answers.
  - 6 albums M0 had as null are answered (Marmozets "CO.WAR.DICE." among them).
  - 199 are unknown to both.
- **Build** (`jobs/index/build.py`, Fargate Spot, daily 06:15 UTC):
  - It spills records to 16 files on disk by shard prefix. Peak RSS is 522 MB, against 5.4 GB with
    everything in memory. 10 min wall time on the laptop, plus the download.
  - 4.46M records, 3.1M titles, 280 MB of shards, the largest 195 KB.
  - First sandbox build (2026-11-22): 18.6 min in all, on 1 vCPU. The download ran at 7.6 MB/s
    from AWS (2.7 min, against 15 min on the laptop), and the parse took about 13 min.
  - EMF works from the ECS `awslogs` stream: CloudWatch extracted the build's metrics with no
    agent and no `PutMetricData`.
  - Daily, because Scheduler doesn't retry a task that started and died. The run exits early
    when the manifest already names the latest dump.
  - It keeps the build it replaced and deletes older ones, rather than using an age-based
    lifecycle rule, which would expire a live index if builds kept failing.
  - x86_64, because Fargate Spot has no ARM64. No ECR: AWS's public Lambda Python image, pinned by
    digest, fetches `code/index.zip`.
- **Re-checks**, at the end of each build, to mb-cold with `"recheck": true`:
  - (a) A trusted answer that differs from the stored one, when the stored `rg_mbid` is one the
    dump has. Otherwise a newer live answer would be undone by every build.
  - (b) A null year looked up more than 30 days ago, for a live retry. The rewrite resets
    `looked_up_at`, so it's monthly per item. Capped at 5,000 per build.
- **mb lane:**
  - A re-check overwrites the item, decided per key: any message for an album that's a re-check
    counts.
  - It keeps the stored track count when `rg_mbid` is unchanged.
  - The manifest is re-read at most hourly, and shards are kept in a 128-entry LRU, so the mb
    Lambda has 512 MB.
- **Covers** (the Cover Art Archive fallback from the story) moved to AOTY-21. The stale-index
  alarm is AOTY-37.

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

## Sync worker (AOTY-18, 2026-11-21)

Following the AOTY-11 spike, it stores **per-(album, chart week) totals from `getWeeklyAlbumChart`**,
not daily totals from paging scrobbles (the story text was revised to match).

- **Weeks:** the first range is `[Jan 1 00:00 UTC, first Sunday 12:00)`, then Last.fm's Sunday-12:00
  weeks, clipped to the year end. A bucket's id is its `from`.
- **Settle rule, not a high-water mark:** a week is refetched and **overwritten** until it has
  been fetched at least 3 weeks after it closed. With regular syncs, that's the current week plus
  the last 3 closed ones (4 calls). A monthly full pass (`full_started_at` in the store) catches
  older back-dated imports and resumes after a kill.
- **Checkpoint** = the store object, written every 8 weeks. There are no continuation messages:
  the sync queue is FIFO with content-based dedup, so an identical body sent within 5 minutes is
  dropped. When time runs short, the worker sets the message's visibility to 0 instead.
- **mb queueing:** albums with no `albums` item go to mb-hot (the user's top 150) or mb-cold, at
  most once a week per album (`queued_at`). Albums found in the table are marked `resolved` in the
  store and never re-checked. A crash can still send duplicates, so AOTY-19 must be idempotent.
- **Errors:** 6, confirmed by `user.getInfo`, and 17 end as `not_found` / `private` without an SQS
  retry; a new message always tries again.
  - Any other failure sets `state=retrying` and the message's visibility to 60/120/240/480 s.
    Otherwise it waits out the queue's 90-min visibility timeout, blocking the user's FIFO group,
    which is how the first staging sync looked "stuck".
  - The last of 5 receives writes `state=error`.
- **Keys never go blank:** `norm` strips punctuation, so `album_key` falls back to the casefolded
  name for all-punctuation titles and artists (`( )`, `!!!`). DynamoDB rejects an empty key
  string, and that crashed the first real staging sync after all 47 weeks.
- **Covers:** recent tracks give covers, matched by album MBID, then by key. Compilations rarely
  match, because scrobbles carry the track artist, so AOTY-21 needs an `album.getInfo` fallback.
- **Rate:** 0.5 s per call per worker, with `maximum_concurrency` 2 on the event source mapping
  (the AWS minimum). The quota is 1,000 now, so reserved concurrency is possible if it's needed.
- Logs carry a 10-character hash of the username, never the name.
- **Charts leave out scrobbles that have no album name** (`tools/sync_check.py`, 2026-11-21). For the
  lightest member, the chart total was 3,254 against 3,373 scrobbles: exactly the 119 with no album.
  Compare chart totals with scrobbles *that have an album*. Album-less plays can't rank as albums anyway.

## MusicBrainz lane (AOTY-19, 2026-11-22)

- **One client per environment**, not globally: the mb Lambda has reserved concurrency 1.
  Staging, prod and sandbox each have one, and a laptop tool (`tools/mb_parity.py`,
  `aoty.py`) is another. Run laptop tools while the deployed lanes are idle.
- **The mb Lambda is the only writer of the `albums` table.** The sync worker reads it; any item,
  a confirmed miss (`year` null) included, counts as resolved until the index build asks for a
  re-check (AOTY-20). The index only skips the year search: the lane still writes the item and
  looks up tracks itself.
- **Items are keyed by the message's `artist_key`/`title_key`**, never recomputed: sync's
  `album_key` has a fallback for all-punctuation names that `norm` alone would miss.
- Item fields: `year` (int or null), `rg_mbid`, `primary_type` (the year's release group),
  `lastfm_mbid` (stored, not used for lookup), `looked_up_at`, and for target-year albums only
  `tracks`, `tracks_type` (the matched release's group type, which `is_single` reads) and
  `tracks_source` (`musicbrainz`, `lastfm` or null). **Other years have no tracks**, and sync
  won't requeue them, so M4 needs a backfill (AOTY-36).
- **An album is written whole or not at all.** If the year is found but the track lookup fails,
  nothing is written and the year is looked up again next time (`YearWasted` in EMF). Unlike M0,
  a failed Last.fm fallback is a failure, not a cached `None`.
- **Time:** the client takes a deadline and never starts an attempt that might not finish by it
  (one `mb_get` can take minutes over 8 tries). A run stops starting batches 30 s before its
  deadline and releases that batch's failures (visibility 0). If work is left, it logs
  `work_left`; the next run comes from the schedule.
- **It never invokes itself (AOTY-58, 2026-11-27).** Each self-invoke was one more hop in the same
  request chain, and Lambda's recursive loop detection stops a chain after about 16 hops. Prod's
  1,904-album prewarm backlog hit that, and AWS Health sent runaway-termination notices.
  - An EventBridge Scheduler start every 5 minutes (`schedule.tf`) is outside any chain.
  - Don't set `RecursiveLoop: Allow`: it removes the guard.
  - The `*-recursion-dropped` prod alarms watch `RecursiveInvocationsDropped`.
  - sync's continuations (sync → SQS → sync, up to 20) are a chain too, and the pokes they send to
    mb and chart carry it.
- While a run goes, every other start is throttled and retried by Lambda until the 1 h max event
  age. Harmless: each one exits fast on empty queues. The empty check long-polls both queues. So a
  backlog's runs follow each other within about 5 minutes (retry backoff or the next tick).
- An empty run (most scheduled starts) leaves `RunSeconds` and `AlbumsPerSecond` out of its EMF.
  Otherwise its ~2 s of polling would drag down `mb-slow`'s albums per second in a light hour.
- **Parity (`tools/mb_parity.py`, 2026-11-22 12:15 ET, laptop):** all 1,536 M0 cold-run candidates
  resolved with none failed. Years were the same for all 1,530 that M0's cache had. The other 6 were
  never looked up by M0; the new answers look right. Track counts and types were the same for all
  568 that M0 had. There were 812 requests, 433 of them 503s (53%), taking 17.2 min at 1.48
  albums/s, including track lookups.

## Intro page and kamp chrome (AOTY-23, 2026-11-23)

- Brand (Brand Guardian, 2026-11-21): **"a kamp toy"**, an endorsement, not a sub-brand. Every
  page gets a slim bar: kamp's record mark (inline SVG, from kamp-site's favicon) and "a kamp
  toy" in DM Sans, periwinkle `#7c86e1` (about 5.9:1 on `--bg`), linking to kamp.fm. Periwinkle
  stays in the chrome; the chart keeps its coral/gold poster look.
- **Two endorsements in the top bar** (user, AOTY-29, 2026-11-23): "a kamp toy" at the start and
  "inspired by the community of New Bands for Old Heads" (linked to its Substack) at the end.
  - New Bands for Old Heads is a **primary endorsement**: it belongs in the header, not the footer.
  - Keep the two visually separate: opposite ends of the bar, no shared colour, nothing joining
    them (no "×"). New Bands for Old Heads is never periwinkle, and keeps its own casing.
- **Credits footer** (AOTY-29) on every page, outside `main`: data from last.fm with a "powered by
  audioscrobbler" text link to Last.fm, release years from musicbrainz, covers from last.fm and
  the cover art archive, "not affiliated…".
  - Chart pages add `#footer-user`, the user's Last.fm profile link (ToS 2.7). `state()` hides it.
  - The footer has no id on purpose: nothing may overwrite it with `textContent`.
  - Last.fm's API terms (read 2026-11-23): no Last.fm logo or marks without written approval
    (7.1), and its brand-guidelines page is a 404, so the credit is text. Open questions on
    artwork, commercial use and publicity are AOTY-44; album links to Last.fm are AOTY-43.
- Voice: lowercase, short, warm; explain waits plainly; never snarky about taste; no hype.
- **Product name undecided** (user): the placeholder is `build_shell.NAME` = "albums of the
  year" (header), title "albums of the year · a kamp toy". Avoid "AOTY" alone and "Wrapped".
- The intro validates through `POST sync` (it checks `user.getInfo` and starts the sync the
  visitor wants anyway); the chart page's own POST then answers `in_progress`. A private
  profile is only known there if a sync already found it within the cooldown; otherwise the
  chart page says so when its sync hits Last.fm error 17.
- Preview every intro message with `serve_shell.py`'s special names: `nobody*`, `lastfm-down`,
  `busy-busy`, `broken`, `private-*`, and AOTY-26's `capacity*`, `too-many`, `throttled`. The
  queue screen: `caller*` (a minute in line), `crowded*` (throttled polls that recover).

## First visit (AOTY-24, 2026-11-22)

- User decisions: the progress visual is the **crate-dig cover wall**, not a tonearm or VU
  meters. **Cut:** phase labels (corny), liner-notes lines, and a "record of the year" reveal:
  the #1 can still change while albums are dated, so a reveal would be a false moment.
- **A first visit = nothing ranked yet**: no chart.json, *or* a chart whose By Play (`album`)
  ranking is empty (By Track until AOTY-63). The first build runs before the mb lane dates anything, so that empty chart is the usual
  first chart, not an edge case. The first chart that ranks anything paints with its animation.
- Data: a first sync fetches **one** getRecentTracks page before the weeks and writes the users
  row's `preview` (a JSON string, because `dynamo.from_item` drops lists). The cover harvest reuses
  that page. A retry skips it if `preview` exists. The chart builder REMOVEs `preview` once a chart
  ranks anything. `progress` gains `albums` and `top_*` (all plays, any release year) and is written
  **every week**, not only at the 8-week checkpoints.
- Progress shown only moves forward (`makeProgress`): a retried sync recounts `weeks_total` as the
  weeks left. The banner stays the one live region; it changes only on a stage or a quarter.
- Counts say "plays", not "scrobbles": charts leave out album-less scrobbles.
- Ends: every album checked and none from the year ("none"); the chart stopped rebuilding with
  albums unchecked, or 10 minutes passed ("stalled").
- **The wall shows only covers that loaded** (user decision 2026-11-25, AOTY-66).
  - Never a blank, half-loaded or broken cover, and never an artist-name tile; the chart keeps its
    tiles.
  - Each cover loads off-screen (`app.js` `loadCover`, shared with AOTY-65's theme and AOTY-67).
  - It's added at the end **in arrival order**, not most recent first, so tiles already there
    never move.
  - It fades in staggered by its place in a burst. Under reduced motion covers just appear.
  - Covers that fail every URL, and albums with no art, are dropped (`firstvisit.wallSources`), so
    the wall may be smaller. The wall stays hidden until its first cover lands.
  - Gotchas:
    - The loaded `<img>` itself goes on the wall, so each cover is fetched once.
    - It has no `data-artist`, so the global `coverFallback` never touches it.
    - `decode()` rejecting after `load` (Firefox) still counts as loaded.
    - SVGs need `width`/`height`, or Firefox's `naturalWidth` is 0.
- **The wall drifts in rows** (user decision 2026-11-25, AOTY-68: slow drift, rows at slightly
  different speeds).
  - Up to four rows of fixed-size covers (`firstvisit.wallRows`). Loaded covers go round-robin, so
    rows grow evenly, and new covers land at a row's end.
  - Each row sways ±28 px (±20 on phones) with its own period, 46/59/72/85 s, and neighbours go
    opposite ways (`firstvisit.drift`). Reduced motion: still.
  - Rows are wider than the wall by the sway on both sides and the wall clips them, so a sway never
    shows a bare edge or scrolls the page sideways. The edges fade by `mask-image`, which also
    carries the old 0.5 opacity: one effect, not two.
  - The rows' infinite transforms keep them on their own layers (Seam fix 1's condition).
    Offscreen pixel samples with and without WebGL found the background exactly `--bg`; redo
    that after changing the wall's layering.
- Preview with `serve_shell.py`: `newbie*` (the whole flow, about 40 s) and `nothing*`. The fake
  status goes through `api_core.status()` from fake rows. The fake wall mixes ready, slow
  (`<base>preview/slow-cover`, up to 10 s), broken and missing covers.

## Sync on visit (AOTY-22, 2026-11-23)

- User decisions: status is a **GET API** (`<base>api/users/<name>/status`, reading the users
  row), not a `status.json`; a user with **no chart yet also gets the sync** (minimal "No chart
  yet" panel; AOTY-24 replaces it).
- POST sync order: the **conditional update first** (state=queued, `sync_requested_at`), then
  SendMessage, undone if the send fails. Sending first loses a race: the sync worker can set
  `syncing` before a late update, the condition fails, and the cooldown is never recorded.
  A crash between the two is bounded by `STUCK_AFTER` (30 min), after which a POST queues again.
- The sync worker writes `sync_finished_at` (and `done`) **before** starting the chart build,
  so `chart_built_at` is never earlier and the status can't sit in `building`.
- `last_synced_at` is the sync's *start* time; use `sync_finished_at` for "when it ended".
- Abuse guards: JSON content-type only (no cross-site form POSTs), per-user cooldown, new users
  refused while the sync queue is over `max_queue_for_new`, `not_found` rows expire after a day
  (TTL). AOTY-26 added the rest (below).
- `tools/serve_shell.py` fakes the API (a 12 s pretend sync) so the banner and the in-place
  repaint can be previewed locally.

## Guardrails (AOTY-26, 2026-11-23)

Runbook and knobs: `infra/README.md`, "Guardrails". User decisions: the load test runs in
**sandbox**, not staging (a fake Last.fm must never be switchable where real users are); syncs
stop through `pause_syncs` at the api, plus disabling the sync mapping as the hard stop.

- **A long queue used to read as a dead sync.** `STUCK_AFTER` (30 min) made a `queued` row
  `idle`, so the page said "couldn't build a chart" and the next POST queued the user again. A
  queued row now stays live while the sync queue holds messages (`api_core._running`).
- **Fail closed for new users:** an api container that has never read SSM treats new users as
  paused; a malformed value keeps the last good one. `enforce_origin` is Terraform, not SSM, so an
  SSM outage can't open the lock.
- **The origin secret is kamp-site's** (SSM SecureString it creates; AOTY's state stays secret
  free). Only a request carrying it has its forwarded IP headers believed; otherwise `sourceIp`.
  Confirm on staging which IP header arrives (the api logs the header shape, no addresses)
  before relying on it.
- **Queue position** = tickets handed out minus tickets used up or burnt, clamped to the queue
  depth; the page shows only the minimum it has seen (FIFO doesn't order across users).
- **Sync reserves `sync_concurrency` + 1**, one more than the mapping's `maximum_concurrency`:
  equal values let a stray direct invoke throttle the mapping into the DLQ.
- Last.fm is also called by the chart builder (covers) and the mb lane (track counts), so
  `sync_concurrency` caps the sync worker's rate only. The fake is wired into all four functions.

## Usernames (AOTY-80, 2026-11-27)

- **One rule**, `lambdas/common/username.py` `USERNAME_RE`: lowercase letters, digits, `_`, `.`
  and `-`, up to 64, starting with a letter, digit or `_`.
  - The api, sync and chart builder import it.
  - The page has a copy, `web/src/intro.js`, which `tests/test_username.py` keeps the same.
  - It's also the path-safety rule: a leading `.` or `-` stays refused, so a name is never a `.`
    or `..` segment.
- **Older Last.fm accounts start with `_`** (`_korps_`), even though signup now wants a letter
  first. They were refused before any lookup until AOTY-80.
- **CD's smoke test needs a name the rule refuses**: `-smoke-` (POST sync → 400, no Last.fm call).
  It was `__smoke__` until the rule took a leading `_`. Valid, it would have called Last.fm and
  written a `not_found` row on every deploy. `test_username.py` checks `deploy.yml`'s smoke name,
  so loosen the rule and that test fails first.

## Sign-in (AOTY-47, 2026-11-23)

- **Nothing stored.** The session is an HMAC-signed cookie `{u, iat, exp}` (30 days); Last.fm's
  session key is dropped in `lastfm.auth_get_session`. Secrets (`session_key`,
  `session_key_previous`, `lastfm_api_secret`) are the api's alone: `auth_param_arns`, never
  `ssm_params`, which `function_params` grants to every function.
- **Payload v2 cookies** arrive in `event["cookies"]` (a list), not `headers["cookie"]`, and go
  out in the response's `cookies` list. Tests build events that way.
- **Last.fm returns the name in its casing:** lowercase before signing, or `USERNAME_RE` refuses it.
- **The nonce is in the callback path** (`api/auth/callback/<nonce>`), since Last.fm appends
  `?token=` to `cb` itself. No nonce match, no `auth.getSession` call (login CSRF).
- **Keys unreadable = 503, never 401**, so an SSM outage doesn't read as "signed out". A key
  under 32 bytes counts as missing.
- **Signatures are compared as encoded text:** base64's spare trailing bits let several
  spellings of one signature decode to the same bytes (a test caught it).
- `cb` overrides the registered callback, so each env keeps its own API key. No kamp-site change
  was needed: the `api/*` behaviours already forward cookies (`AllViewerExceptHostHeader`).
- Sign-out clears one browser's cookie; `signout?everywhere=1` revokes the rest (AOTY-57, below).

## Sign out everywhere (AOTY-57, 2026-11-24)

- **A cutoff, not a counter** (user-approved deviation from the ticket's `{u,iat,exp,e}`). The
  stored `revoked_at` (epoch seconds) revokes any session with `iat <= revoked_at`
  (`session.is_revoked`). The cookie format is unchanged, so pre-existing cookies are covered.
  - A sign-in in the same second as the revoke is revoked too. That fails safe; it's documented,
    not fixed with `iat_ms`.
- **Stored in the groups table** (`pk session#<name>`, `sk meta`, `revoked_at` N), not guard, which
  is "not precious": losing a cutoff revives revoked sessions.
  - Never give these items `owner` or `username`, which would put them in the GSIs.
  - `sandbox_reset.py` wipes them; that's accepted.
- **`session_user` always checks**, so a new route can't forget the check. It returns
  `(user, "ok" | "signed_out" | "revoked")`, with a consistent read by default; `api/me` alone
  passes `consistent=False`.
  - An unreadable cutoff raises `SessionUnavailable`, which callers answer with 503, never 401.
  - A cutoff that isn't a number revokes everything.
- **`everywhere` needs a valid, unrevoked session**, so a revoked copy can't keep pushing the cutoff
  forward. Its write only moves the cutoff forward (`groups_store.REVOKE`). On failure it answers
  503 and keeps the cookie.
- Prod's `group_writes` flip was its own PR (AOTY-62), after the release carrying this was live in
  prod, so writes couldn't turn on before the revocation code was deployed.
- Runbook (revoking one user by hand): `infra/README.md`, "Revoking sessions".

## Groups (AOTY-48, 2026-11-24)

- **Membership items, not a list:** `pk group#<slug>` with `sk meta` or `member#<username>`, so the
  `by_member` GSI answers "which groups is X in?" (DynamoDB can't index into a list). The GSI key is
  `username`, because `user` is a reserved word. `by_owner` is sparse, on meta items only, because
  an owner may remove themselves and still owns the group.
- **The cap holds under concurrency:** every membership change is one `TransactWriteItems` with the
  meta (`member_count < 25`, owner check). Cancellation reasons (with `ALL_OLD`) say which condition
  failed; `TransactionConflict` is retried.
- **Renaming moves the slug** (user decision 2026-11-24): one transaction moves the meta and members
  to the new pk (at most 52 items, under the 100 limit); the old URL then 404s, and its chart is
  deleted. The meta's theme moves with it, guarded by `theme_set_at` (AOTY-76, "User themes").
- **Chart deletes go through the chart Lambda** (`{"group", "deleted": true}`), not the api, so the
  Lambda's single concurrency orders them after any build in flight; a delete on a slug that's a
  group again by then rebuilds it instead.
- **`chart_built_at` is the `chart_requested_at` read before the build**, not its end time;
  otherwise a member change made during a 25-member build would be skipped.
- **Member adds reuse POST sync**, split into `check_new_user` (the new-user pause, the per-IP
  limit, `user.getInfo`) and `request_sync` (cooldown, `pause_syncs`, busy, queue). A new member is
  refused before anything is written while new users are paused or the queue is busy. A failed SQS
  send keeps the member (`sync: not_queued`): undoing it would be a second write that can fail too.
- `block_group_adds` on the users row is read here (409 `cannot_add`, neutral); AOTY-49 sets it.
- **`group_writes`** (Terraform) is the writes kill switch: on in every env since AOTY-62.
- **Migration:** the beta's S3 groups move with `tools/beta_group.py migrate` right after each
  env's deploy; until then their charts serve but don't rebuild.

## Group editing UI (AOTY-52, 2026-11-24)

- User decisions: the sign-in state is **its own quiet row under the top bar**, right-aligned,
  never between the two endorsements. "Start a group" is on the intro (signed-in only) and in the
  "your groups" dialog.
- `api/me` carries `group_writes` (signed in or not). Where it's off, the page
  shows no sign-in row, no "start a group" and no edit controls. A 503 from `me` is "unknown",
  never "signed out".
- **One poller per group page**, with a generation counter checked after every await. A rename
  moves the slug, and a stale loop for the old id would paint "there's no group here".
- **A rebuilt chart isn't a ready one:** after an edit, the banner follows `GET api/groups/<id>`
  (`ready`/`total`) and the chart's `waiting`, not only `generated_at` (`groupedit.rebuild`).
- **Refusals are mapped by the body's `error` first:** a 404 is both "unknown user" and "no such
  group". Ownership is `group.owner`, not membership: an owner who removed themselves still owns it.
- Live regions: only the banner speaks on its own; the rest answer the visitor's own action
  (`tests/test_build_shell.py` lists them).

## Group visit sync (AOTY-55, also AOTY-60, 2026-11-24)

- **A group page's visit syncs its members:** `POST api/groups/<id>/sync`, once per page load. It's
  **public and on in prod** (it's syncs, not edits), so it branches out in `_group_dispatch` before the
  session check. Each member goes through `request_sync` (`lean=True`: no status re-read, so 25
  members fit the api's 30 s). This is also how members who never visit stay fresh (AOTY-60).
- **Group cooldown** `group_sync_cooldown_seconds` (SSM, 1 h): the meta's `sync_requested_at`,
  claimed with a conditional update. It's given back only when nobody was queued because every send
  failed, and only if it's still this visit's claim. A partial failure keeps it.
- `pause_syncs`: 200 `paused`, nothing written. Skipped members: `not_found`, `private`,
  `block_group_adds`.
- **The per-user cooldown is the real abuse guard:** anyone can POST any (guessable) slug.
- **Coalescing:** a group build stamps `chart_read_at` (ms) when it starts reading stores. A member's
  build skips the group when `chart_read_at >= (sync_finished_at + 1) * 1000`. `sync_finished_at` is
  whole seconds, so a read in the same second rebuilds. Sync saves the store before
  `sync_finished_at`. `rename` doesn't copy `chart_read_at`.
- The page re-reads quietly while `GET api/groups/<id>`'s `syncing` > 0 (`group.syncWatch`: 90 s
  grace, 15 min cap). No banner: `member_state` keeps re-syncing members ready.
- Preview: any seeded group's first load re-syncs it for 45 s; a reload is the cooldown;
  `paused-crew` is `pause_syncs`.

## Analytics (AOTY-38, 2026-11-24)

- User decisions: **kamp.fm's GA4 property** (`G-QNJVBYWDYN`, kamp-site's), content group `aoty`,
  so the toy → kamp.fm → `file_download` funnel (kamp-site already sends that event) is one
  property. Prod only: `deploy.yml` passes `build_shell.py --ga-id` for prod; staging builds with
  none. **Group slugs are sent** (`group_id`, plus `group_size` on `group_loaded`); **usernames never
  are**.
- **The guarantee is in `kampstats.js`, not in GA settings.** Everything sent comes from templates
  (`/toys/aoty/user/:name`, fixed titles, referrers cut to an origin or templated) and the `EVENTS`
  allowlist. Every `replaceState` that changes the path is followed by `track("setPage")`. A new
  event or param means adding it to `EVENTS`, and the sentinel test checks that no username leaks.
- **One page view per load**, sent by hand after routing (`send_page_view: false`). GA's "page
  changes based on browser history events" must stay off in the stream (shared with kamp.fm,
  which makes no history calls): the member filter alone would be a page view per pick.
- The file is called `kampstats.js` because filter lists block same-origin `analytics.` paths. app.js
  reaches it only through `track()`, which does nothing when it's missing and swallows its errors.
- Events that navigate away (`intro_submit` ok, `group_create`, `kamp_click`) rely on gtag's beacon
  flush on page hide.
- `sign_in` is counted from a sessionStorage mark set on the sign-in link, once `api/me` names
  someone. `last.fm` is on the property's unwanted-referrals list, so the return trip doesn't start
  a new session.
- The kamp.fm link has `referrerpolicy="origin"`: same-origin referrers carry the full path, and
  kamp.fm's own gtag doesn't template it.
- Preview: `tools/serve_shell.py --ga-id G-QNJVBYWDYN`. Off kamp.fm every hit has `debug_mode`, so it
  shows in DebugView and the property's developer-traffic filter keeps it out of reports.

## User themes (AOTY-65, 2026-11-25)

- User decisions:
  - **The owner sets their user page's theme and every visitor sees it.**
  - The choices are the poster look (the default), kamp's 8 dark palettes, and a theme from the
    chart's #1 cover, labelled **"album of the year"** (id `match1`; "match my #1" was only the
    ticket's working name).
  - The picker lists **"album of the year" first, then the presets in rainbow order** by accent hue
    (`theme.ORDER`, red first). The default's id stays `poster`, but it reads **"oranger"**.
  - Visitors see a new pick **within seconds** (a restyle, not the next build).
  - "album of the year" **fades in** once the #1 cover loads, with no fade under reduced motion.
  - Groups (AOTY-76) and light themes (AOTY-75) are their own stories (both below).
- **The ids are an allowlist.**
  - Two copies: `lambdas/common/themes.py` and `web/src/theme.js` (`tests/test_themes.py` keeps them
    the same), plus `kampstats` `theme_set`, which a JS test keeps in step.
  - Only an id is stored or published, never colours or CSS.
  - A new preset means all three places, and its contrast must pass `web/test/theme.test.mjs`.
- **Contrast is tested:** every preset and derived theme keeps text at 4.5:1 on bg and panel,
  muted and both accents included, on-accent on both accents, and kamp's periwinkle on bg.
  - Kamp's values are kept wherever they pass. Measured 2026-11-25: `textDim` on the panel fails in
    beach house (4.27), blackpink (4.46), deep purple (3.78) and golden smog (4.48), and deep
    purple's accent is 4.48. Those are lifted (theme.js header).
  - `accent2` is picked per preset, because the poster has two accents.
- **All of app.css's colours are tokens.** `--line`, `--on-accent`, `--tile` and `--bg-seam` were
  added, and the overlays use `color-mix` with `--bg`.
  - Seam fix 2's other end is `--bg-seam` (`theme.seamOf`: blue ±1, never wrapping), and
    `test_build_shell` still guards it.
  - `.theme-fade` transitions colours only, never transforms, so no layers are promoted (Seam fix).
- **Where it lives:**
  - The users row's `theme`, set by `POST api/users/<name>/theme` (the owner only, through
    `session_user`). It needs an existing row (404 `no_chart`), and `group_writes` is its kill
    switch too.
  - The api then sends the chart Lambda `{"username", "restyle": true}`, which copies **the row's**
    theme (never the event's: async events repeat) into the published chart.json, without a build.
  - Full builds carry it as well. The default is left out of chart.json.
- **On the page:**
  - **A theme doesn't wait for the chart** (user, 2026-11-25). The owner's picker and the saved
    theme show on their page in every state, the first-visit screen included (`themeForPage`).
    - The saved theme comes from chart.json or, with no chart yet, from the status API's `theme`.
    - "album of the year" stays on the default until a chart has a #1 to read.
    - The api's one remaining need is a users row (404 `no_chart`, "still being set up"). A
      visit's sync request creates it, so it's only a moment. The check stays because a bare
      theme-only row would read as a known user and skip the new-user guards.
  - `clearTheme()` runs on every view that isn't a themed page: state, intro.
  - Once the owner picks in a page session, their pick wins over a re-fetched chart.json, which
    may not be restyled yet.
  - "album of the year" uses its own `new Image()` with `crossOrigin` (the chart's `<img>`s aren't
    readable), drawn at 32×32. A tainted canvas or a grey cover keeps the default.
- Preview: `tools/serve_shell.py --sample alice --port 8001`, then "sign in with last.fm" (alice)
  shows the picker; the stand-in API writes the theme into the preview chart at once.
- **Group themes (AOTY-76, 2026-11-25).** The group's owner (`group.owner`, not membership) picks,
  with the same choices and picker. Decisions:
  - **A member's view (`?user=`) keeps the group's theme**: the filter only re-ranks the page.
  - **"album of the year" on a group reads its By Listener #1** (then `track`, then `album` on
    older charts), never a member's re-ranked #1 (`theme.topRanking`).
  - Stored on the group's **meta item** (`theme`, `theme_set_at`) by `POST api/groups/<id>/theme`,
    one conditional update that is also the owner check. `GET api/groups/<id>` carries it, like the
    status API does for a user. The restyle event is `{"group", "restyle": true}`.
  - **Rename vs save:** rename copies the meta, and its delete of the old meta requires
    `theme_set_at` unchanged, so a save in between cancels the transaction and the retry re-reads.
    The page reads the target when its save timer fires, so it posts to the new slug.
  - The page code keys on a **target** `{kind, id}`, not a username (`theme.themeKey`, `savePath`,
    `ownsTheme`). A group's remembered theme is `aoty-theme:group:<slug>`, so a slug equal to a
    username never shares its key.
  - Preview: `group/full-house` signed in as alice (its owner); `bobs-crew` is bob's.
- **Light themes (AOTY-75, 2026-11-25).** User decisions:
  - Seven light presets, named after bands and musicians, one per hue like the dark set: white
    stripes, tangerine dream, yellow magic orchestra, mint royale, the blue nile, lavender diamond,
    and **gabbie** (New Bands for Old Heads' RIYL Old Heads palette; the name is cleared).
  - gabbie's headings are **Arial**. The first visit goes light too, aurora included, and the
    periwinkle darkens on light pages (`LIGHT_KAMP` `#4c55b8`).
  - The picker lists dark presets, then light ones, each group in rainbow order.
- **Tokens they added** (`theme.complete` fills defaults for dark and derived themes):
  - `scheme`: `apply` sets `color-scheme` and `data-scheme` on `<html>`. app.css keys light-page
    rules on `html[data-scheme="light"]`: a lighter hero shadow, and a wall mask at 0.85, because a
    0.5 mask over white washes covers out where over black it only dims.
  - `kamp`: the periwinkle, per theme.
  - `tileInk` and `tileEnd`: the artist-name tile's text and its gradient's end, which eases back
    toward the tile until the name reads in the middle. For beach house, green day and golden smog
    that slightly tames a light accent under light text.
  - `font`: gabbie's `display-arial`. Every display rule reads `--font-display`, `--display-weight`
    and `--display-track`. Arial is 700, because it has no 800, and tracked looser, because its
    glyphs collide at Bricolage's tight spacing.
- **gabbie's own `#ec4899` is ~3.5:1 on white**, too light for text. It colours the tiles, and the
  accents are deeper pinks.
- Rank numbers over covers keep a fixed light ink, whatever `--ink` is.
- A theme fade drops body's Seam fix 2 gradient while it runs, so the background fades instead of
  snapping, since gradients can't transition.
- **The last theme a page had is remembered per viewer** (`localStorage` `aoty-theme:<user>`) and
  painted before chart.json arrives, so a light page doesn't open dark. It's a convenience only:
  the chart and status replace it, and "album of the year" isn't painted early.
- Offscreen captures: every light page's background samples are exactly its `--bg`, white
  included (Seam fix 2's `#fffffe`).

## Borealis (AOTY-67, 2026-11-25)

- User decisions:
  - **kamp's Now Playing "Borealis"** (aurora curtains) fills the viewport behind a **first visit
    only**, coloured by the wall's covers.
  - Ported: the flow, the 1 s palette crossfade, and the **surge band** when a new cover shifts
    the palette. Not kamp's cursor lean, rare flicker or title mask.
  - **The repo is GPLv3**, as kamp is (`LICENSE`). Every ported file has the same single author.
  - More backgrounds, picked at random, are AOTY-77.
- **`web/src/borealis.js`** is the port. Its header lists the kamp files and commit, and the pure
  parts are node-tested.
  - The engine is WebGL 1, low-power, `failIfMajorPerformanceCaveat`. It runs at a quarter of the
    resolution with DPR capped at 1.5, and at 30 fps.
  - **There's no GLSL compiler in CI.** `web/test/borealis.test.mjs` checks statically that every
    declared uniform is set by `draw()`, and that the palette is indexed only by the loop counter.
    The shader was compiled and drawn once by hand in Chromium (kamp's Electron, offscreen). Do
    that again after shader edits.
  - `mount()` returns null without WebGL. The page then stays exactly as before.
  - kamp rules kept:
    - A stopped loop (reduced motion, a hidden tab) lands a new palette directly.
    - A resize clears the buffer, so same-size resizes are skipped and real ones redrawn.
    - `destroy()` calls `WEBGL_lose_context`, because browsers cap live contexts at about 16.
- **On the page** (`app.js` "the aurora"):
  - It mounts 1.5 s into a first visit, so a chart that's already there never flashes it.
  - The canvas is fixed at `z-index: -1`, and `html.has-aurora` makes body's background transparent:
    negative z-index paints over the root's background but under body's. **The class stays until
    the 0.6 s fade-out ends**, or the canvas vanishes at once.
  - The first 6 wall covers load once, with CORS. A CORS failure still puts the cover on the
    wall, through a plain load, without colouring the aurora.
  - The palette is recomputed once per arrival burst and set only on a real change
    (`paletteChanged`), so a warm cache makes one surge, not six.
  - A theme change repaints its background (`applyTheme` → `auroraColors`).
  - Text gets a halo in the page colour. A panel would shift the layout when the aurora mounts.
- Preview: `tools/serve_shell.py --port 8001`, then a fresh `user/newbieN`.

## Chart builder and user page (AOTY-21, 2026-11-22)

- User decisions: the route is **`<base>user/<name>`**, so bare `<base><x>` stays free for other
  pages. Since AOTY-63 a user page has **one ranking and no switch** (see "The live app's
  rankings").
- Ranking: By Play (`album`) = plays ÷ tracks, singles out by `is_single`, ties by plays then
  key. A strict top 100 (ties at the cutoff dropped).
  `tools/chart_check.py` checks a deployed chart against M0's code.
- Covers: the scrobbles' Last.fm image, else `album.getInfo`'s → its other sizes → Cover Art
  Archive `front-250` by `rg_mbid` → the artist-name tile (`common/covers.py`). Sync's harvest
  reads only the newest ~4,000 scrobbles on a first sync, so the builder looks up getInfo covers
  for shown albums with none (user decision 2026-11-22), at most 120 per build within 90 s,
  cached per user in `chart_covers.json` ("none" retried monthly, failures not cached). CAA has
  no art for many small Bandcamp-only releases, which Last.fm has.
- **Cover hosts vs the CSP** (found in sandbox, 2026-11-22: every cover showed as a tile):
  getRecentTracks serves covers from `lastfm-img.freetls.fastly.net`, but kamp.fm's CSP allows
  only `lastfm.freetls.fastly.net` (same paths), so `cover()` rewrites the host. CAA redirects
  `coverartarchive.org` → `archive.org` → `iaNNN.us.archive.org`, and browsers check `img-src`
  on every hop, so the CSP lists the bare `archive.org` as well as `*.archive.org` (kamp-site
  PR #22). A new image source means checking every redirect hop against the CSP.
- `display_name` (Last.fm's casing) is fetched by sync with `user.getInfo` at most every 30
  days and used only if it lowercases to the username. The page rewrites the URL to it.
- Pending rebuilds: mb pokes `{"pending": true}`. The builder rebuilds a user only when one of
  the keys in their `chart_pending.json` now has an item, so an album the lane never answers
  doesn't loop. Index re-checks that change an album already on a chart show up at the next sync.
- Sandbox has no kamp.fm path: test the Lambda half there (chart.json lands in its site bucket),
  and the page with `tools/serve_shell.py`.

## Last.fm links (AOTY-43, 2026-11-27)

- User decisions:
  - **The whole hero, card, tile or row is one link** to the album's Last.fm page, opening in a
    new tab.
  - The heading's name links to the user's own page, even when you're already on it, and a small
    "[last.fm]" beside it links their profile. A group member view (`?user=`) does the same for
    the member's name in the sub line.
  - The footer profile line stays.
  - **No skip link.** Nothing important follows the chart, and landmarks and headings already
    serve screen readers.
- **The link uses `lastfm`, not `album`.** chart.json's `album` is the root title (`base_title`).
  The root title can be another album's page: "1989 (Taylor's Version)" would link the 2014
  "1989". Each album also carries `lastfm`, the name the store kept for its key: the first one
  sync saw, which may be a "(Deluxe)" page, but always a real one.
  - A group takes the first member's name, in page order.
  - Charts built before AOTY-43 link `album`. Run `tools/rebuild_charts.py` after the deploy.
- **Encoding** (`lastfm.js`, checked against `album.getInfo`'s `url`):
  - `encodeURIComponent`, then spaces as `+`.
  - A literal `+` is encoded twice (`%252B`: "+44").
  - `'` becomes `%27`.
  - `$ & , : ; = @` stay raw.
  - Last.fm's site serves curl a bot challenge, so check new cases through the API, not the page.
- **The page:**
  - Links are `.lf` with `rel="noopener noreferrer"`, because the path names the user.
  - The `aria-label` carries the rank, names, stats (the hero's wording) and "opens in a new tab".
    It isn't `aria-describedby`, which some screen readers skip on links. Covers inside links
    have `alt=""`, and the dots are `aria-hidden`.
  - The hover is on the `<li>`, which never moves, so the card lifts without dropping out of hover
    at its bottom edge. The hero doesn't lift: its blurred backdrop would become a layer. Rows
    tint instead.
  - `.theme-fade .card` combines the theme fade's colour transitions with the lift's transform.
    `.theme-fade *` alone would cancel one of them.
  - `album_click` has no params. It's counted on `click` and middle-click `auxclick`. A
    context-menu "open in new tab" can't be seen.
- Preview: `serve_shell.py --sample alice` leads with real awkward names: "1989 (Taylor's
  Version)", AC/DC, +44, Why?, Pg.99 "Document #8".

## Short link (AOTY-69, 2026-11-27)

- User decisions:
  - **`aoty.kamp.fm`**, not `aoty.me`: it's free, it sits under the kamp endorsement, and it
    doesn't commit to a product name.
  - Redirects are **302**, so the targets can still change.
  - A bad path goes to the intro.
  - Only `?user=` on group links is passed through.
  - No `utm_source`: `kampstats` drops the query from `page_location`, and a 302 keeps the
    original referrer.
- **Owned by kamp-site** (`infra/aoty_short.tf`, `infra/functions/aoty-short.js`): its own
  distribution and certificate. **Prod only**: staging has no short host.
- **The redirect table:**
  - `/` → the intro.
  - `/<name>` → `user/<name>`, with casing kept.
  - `/g/<slug>` → `group/<slug>`, lowercased, plus a valid `?user=`.
  - Anything else → the intro.
  - The rule counts segments, so **`/g` alone is the user `g`**.
- **One-letter first segments are reserved.** Last.fm names are at least two characters, so
  short routes like `/g` never collide with a user. A future short route must be one letter too.
- The function's name rule has no `.`, because the shell function treats a dotted last segment as
  a file. If `USERNAME_RE` ever loosens, check the function too.
- **It still wants a letter or digit first.** AOTY-80 let usernames start with `_`, so
  `_korps_`'s share link is the full kamp.fm URL until AOTY-89 changes kamp-site's function, then
  `share.js`.
- Share links use it since AOTY-84 (below). Share images (AOTY-70) move to it in their own ticket.

## Sharing (AOTY-84, 2026-11-27)

- User decisions:
  - **User pages get the share row** group pages have. It shows once a chart is painted.
  - **The system share sheet on touch devices only** (`(pointer: coarse)`, `share.useNative`).
    Desktop browsers with `navigator.share` (Chrome, Safari) still copy, which is what people expect
    there.
  - **A member's view of a group (`?user=`) offers both:** "share group" and "share <member>". The
    member's link goes to their **user page**, not the group view.
  - Link previews (per-user OG images) are their own ticket.
- **The link** (`share.shareUrl`) never has a query.
  - On prod it's `aoty.kamp.fm/<Name>` (display casing) or `aoty.kamp.fm/g/<slug>`.
  - Everywhere else it's the page's own address.
  - So is any name the short host won't take: one letter (reserved), a `.` (which `USERNAME_RE`
    allows), a leading `_` (AOTY-89), or anything not a slug.
  - The first visit's queue line names the same link.
- **The short host is set at build time, never guessed in the page.**
  - `build_shell --short-host` writes `<meta name="aoty-short">`. It accepts only `aoty.kamp.fm`,
    and only with `/toys/aoty/`, because the redirects land on prod alone. Prod's deploy passes it.
  - `serve_shell --short-host` previews it. It's off by default, because its links lead to prod.
- **The click** (`app.js` `shareOrCopy`):
  - `navigator.share` is its first await, because it needs the tap's user activation.
  - A second tap while the sheet is open does nothing (`sharing`). `AbortError` (cancelled) and
    `InvalidStateError` say nothing.
  - Any other failure shows the link to copy by hand, then tries the clipboard. Safari uses up the
    activation on `share()`, so the clipboard usually refuses after it.
  - The title comes from `share.shareTitle` (name and year), never `document.title`, which reads
    "(#12) …" in the queue.
- **Visibility is one function**, `pageTools(target)`. `state()` hides the row only on user routes,
  because a group's passing error state runs before `showGroupTools` and must not lose its button.
  `#page-tools` was `#group-tools`.
- Analytics: `share_copy` (copied) and `share_native` (shared through the sheet), with no params.
  Which page was shared is never sent.

## Share images (AOTY-70, 2026-11-27)

- User decisions:
  - **Drawn in the browser on a canvas**, in the page's theme. No server, no new infra.
  - **Every visitor gets it**, on user and group pages (AOTY-71), once the ranking on screen has a
    #1. AOTY-72 to AOTY-74 add formats.
  - **Ship despite AOTY-44** (Last.fm's artwork clause): "it's fine" (user, 2026-11-27).
  - A person's caption is always 's ("chris's"), as `share.shareTitle`.
- **The split:** `sharecard.js` is pure and returns draw operations (`compose`). `app.js` plays them
  onto a canvas (`drawOp`). New formats are new `FORMATS` and layouts there, tested with a stand-in
  `measure`.
- **Colours come from `shownTokens`**: what `applyTheme` last painted, `th.complete`d. Never
  `getComputedStyle`, so `sharecard.test.mjs`'s contrast check covers what ships. Every text sits on
  plain `bg`; the tile's name on the tile tokens.
- **Text fitting** (`fitText`): the largest size that fits without breaking a word, else one that
  breaks a word (CJK, one giant word), else the smallest with an ellipsis. Graphemes via
  `Intl.Segmenter`, so emoji and accents aren't split.
- **Real-browser rules:**
  - A tainted canvas stays tainted: a cover that loads but taints makes `toBlob` throw, and the tile
    is drawn on a **fresh** canvas. `toBlob` can also give null (iOS memory pressure).
  - Fonts load with `document.fonts.load(font, text)` using the card's real text, so Google Fonts'
    unicode-range subsets for é, Cyrillic or CJK come too.
  - No canvas `letterSpacing` (uneven support), so gabbie's tracking isn't drawn.
  - The kamp mark is `arc()`s, not an SVG image.
  - **iOS refuses `share()` after slow work since the tap.** The image is made ahead: once the button
    is in view (IntersectionObserver), or on hover, focus or touch. Never on page load: it costs a
    CORS fetch of the cover. A tap before it's ready makes it; if that share is refused, the message
    says "tap again".
  - `share({files})` with the file **alone**: iOS splits text into a separate item and Instagram drops
    the image.
  - Files use the sheet only where `canShare({files})` says yes (`share.useNative` returns false for
    files without `canShare`), so Firefox Android downloads.
  - The download's blob URL is revoked after a minute: Firefox cancels one revoked at once.
- The cache key is the chart's `generated_at`, the texts, the cover URL and the resolved tokens, so
  "album of the year" settling after its cover loads makes a new image.
- Analytics: `share_image` with `format` (`top1_square`) and `via` (`native`, `download`).
- Preview: `tools/serve_shell.py --sample alice`, then "save image" on a desktop.

## Group share images (AOTY-71, 2026-11-27)

- User decisions:
  - **The everyone view shares the #1 of the ranking on screen** (By Listener, By Play or Weighted;
    older charts with no `listener` show `album`, as `group.rows`).
  - **The caption is "<group> · album of the year <year>"**, as `share.shareTitle`'s group form, not
    's. A long name is ellipsized, never the year (`sharecard.captionFitted`).
  - **"7 of 9 members"** sits right of "a kamp toy", only while no member is still loading
    (`waiting` 0), because a first build would undercount. The total counts unavailable members,
    as the page's dots do.
  - **A member's view (`?user=`) shares the member's image**: their re-ranked #1, "<member>'s"
    caption, and their user page's link, never the group's. There's no count.
- **One rule decides what's shown:** `share.imageSubject` (user, group or member), fed by
  `share.userTop` or `group.top`. An empty ranking (Weighted's listener floor, a member with no plays
  from the year) is null, which hides the button. The link buttons stay.
- Member names on group charts are the lowercase usernames (no display names there), so a member
  image's caption and link are lowercase.
- The group's file is named by its slug: a name of all emoji or CJK would leave `fileName` empty.
- Analytics: `share_image` is unchanged. `group_id` already rides every hit as a page field.
- Preview: `serve_shell.py --sample alice`, `group/full-house`, then switch rankings and members.

## Story images (AOTY-72, 2026-11-27)

- User decision: the vertical format is **9:16 (1080x1920)**, not 4:3, because Stories, Reels and
  TikTok are vertical.
- `top1_story` is the square's stack, with a bigger cover, moved inside its **safe band**.
  - Instagram and TikTok cover the top 250 px and the bottom 340 px (`FORMATS[..].safe`), so only
    the background is drawn there.
  - `sharecard.test.mjs` checks every op against the band, for each theme, long text, group and
    no-cover case. Text extents in that test are estimates (one em above the baseline, 0.3 below),
    so the layout keeps 40+ px of slack for real fonts.
- **The "square | story" switch** sits beside the image button and hides with it. It's a
  `setupSwitch` (`data-shape` on `<main>`, default square), remembered per viewer as
  `aoty-card-shape` since AOTY-73. AOTY-72's `aoty-card-format` = `top1_story` is carried over
  once.
  - **Each format keeps its own made-ahead image** (`cards[format]`), so flipping back doesn't lose
    a ready one. That matters for iOS's activation rule.
  - Unknown ids read as square (`sharecard.formatOf`, `share.formatId`).
- Story files end in `-story.png`.
- Analytics: `share_image` `format` is the format at the tap. Flipping a switch is
  `control_change` with `control: content | shape`.
- Top 10 (AOTY-74) adds its own layouts beside these.

## Top 3 images (AOTY-73, 2026-11-27)

- User decisions:
  - **#1 big, #2 and #3 smaller.** They sit beside #1 on the square and below it on the story.
  - **Two switches**: "#1 | top 3" (`data-content`, `aoty-card-content`) beside "square | story".
  - **The caption is "alice's top 3 albums of 2026"**, or "Full House · top 3 albums of 2026" on a
    group.
- **The card is slots** (`layout().slots`). Each slot has its cover or tile, then its title and
  artist. A #1 card is one slot.
  - `web/test/fixtures/top1_ops.json` is the #1 cards' ops from before slots. They must still draw
    exactly that, minus the `slot` index.
  - Re-capture it only for an intended change to a #1 card.
- Titles on a top N start with `#n` and a **no-break space**, so a title that breaks (CJK, one long
  word) keeps going after the rank, never "#3…". `wrap` and `fitText` treat every space but a
  no-break one as a break.
- **"top 3" needs three on screen** (`share.cardContent`).
  - With fewer (Weighted's floor, a light member), the switch hides and the image is the #1. The
    saved choice stays for the next view.
  - The pressed button is the one that will be drawn.
- The member count is drawn on #1 cards only.
- **Covers:** each slot loads its own, all at once, each chain capped at 8 s (`COVER_WAIT_MS`).
  - Each loaded cover is checked for taint on its own (`readable`, a 1x1 `getImageData`), so a
    non-CORS cover tiles only its slot.
  - A taint the check misses still falls back to all tiles, on a fresh canvas.
- The cache key covers every album's texts and first cover URL.
- Files end in `-top3.png` or `-top3-story.png`.

## Top 10 images (AOTY-74, 2026-11-27)

- **Layout:** a 5x2 grid of 176 px covers, reading across, over a numbered list in two columns
  that reads down (1–5 left, 6–10 right).
  - Each grid cover has its rank on a **page-colour badge** in its corner, so a cover can be matched
    to its line. Badge text is `on: "badge"`: accent on `bg`, which the contrast test covers.
  - A tile's artist name keeps above the badge strip, at 4 lines at most.
  - The square's list is 1-line titles, in rows of 70 px. The story's is 2-line titles, in rows of
    120.
- **Every cover and tile is drawn before any text**, so no cover's shadow falls on a title drawn
  before it. With one slot that's the old order, so the #1 snapshot still passes.
  - Small covers carry `blur`/`drop` (their shadow scaled from the #1's 48/18). Covers of 520 px
    and up carry neither, so #1 ops are unchanged.
- **The content rule falls back to the biggest that fits**, never past the saved choice: a saved top
  10 with 5 on screen draws a top 3 (`share.cardContent`).
  - Each content button shows only with that many on screen. The switch shows from three.
  - Focus on a button that hides goes to the image button.
- **Covers:** a top 10 waits at most **5 s per cover** (`coverWaitMs`), so a cold cache still gives an
  image within a few seconds. #1 and top 3 keep 8 s.
- Test fixtures: the overlap and safe-band tests use 10-album texts and 10-long cover mixes
  (`TOP10`, `LONG10`, `COVER_MIXES`). The box test checks every slot, not only #1's.
- Files end in `-top10.png` or `-top10-story.png`. Analytics adds the `top10_*` formats and content
  `top10`.

## Monitoring (AOTY-27, 2026-11-23)

- **One SNS topic for everything**, `kamp-aoty-alerts` in `infra/global/`: every env's alarms,
  the budget, and AOTY-37's stale-index alarms when they come. The email subscription is made by
  hand (`infra/README.md`, "Alerts"): the repo is public, so the address must never be in it.
- **Tiers:** prod `full` (about 18 alarms), staging `basic` (DLQs and uptime), sandbox `none`.
  The sandbox deploy role gets **no** alarm writes: any branch can use it.
- Free tier is 10 alarms for the whole account, so the rest is about $0.10/month each. A new
  alarm goes in prod only unless staging really needs it.
- **Thresholds come from measurements, not "any wait":**
  - The sync queue age limit is 4 h, because `max_queue_for_new` 500 at the load test's ~2.3
    syncs/min is a ~3.6 h line by design.
  - mb throughput counts misses as well as resolved albums, or the daily re-check pass would fire it.
  - The 503 threshold is 85%, because 45–61% is normal.
- **The DLQ alarms keep their state on missing data.** SQS stops publishing for an untouched queue
  after about 6 h, even with messages in it, and that would otherwise send a false "OK".
- **API 5xx is counted from the access log**, not API Gateway's 5xx metric. The api's own 503s
  (busy, paused) are deliberate. `status` is a string in the jsonencoded log, so the filter
  compares strings. The env is in the metric name (`ApiServerErrors-prod`), because a filter with a
  default value can't have dimensions.
- `LastfmErrors` is one EMF line per failed `Client.call`, after retries, leaving out 6 and 17
  (and, for `auth.getSession`, the token answers 4, 14 and 15: anyone can send the callback a
  made-up token, which would otherwise trip the alarm at will; 13, a bad signature, counts). It is
  printed only inside a Lambda (`AWS_LAMBDA_FUNCTION_NAME`).
- **Lambda's `AsyncEventAge` is in milliseconds**, unlike SQS ages (seconds). The `chart-event-age`
  threshold was first written as 1800, meaning 1.8 s; it's 1,800,000 now (fixed in AOTY-41).

## Dashboard and metrics (AOTY-41, 2026-11-23)

- **One dashboard per env**, `kamp-aoty-<env>-dashboard` (`infra/modules/stack/dashboard.tf`). A
  precondition keeps it at **50 metrics or fewer**: the free tier is 3 dashboards of 50 metrics
  each. Hidden metrics that feed an expression count; expressions and Logs Insights widgets don't.
- **Every metric name costs about $0.30/month per env.** So the sync and api *outcomes* are plain
  EMF fields, not metrics, and the dashboard's Logs Insights tables break them down. New code
  emits through `common/emf.py`.
- **`LastfmCalls` is one metric that every function adds its own run's calls to.** `Client.calls`
  is a running total for the warm container, so always take a difference (snapshot before, subtract
  after, in a `finally`). The load-test fake emits `FakeLastfmCalls` per call, so the two never
  count the same call.
- The api counts a POST sync's outcome once, in the dispatcher, from the response
  (`api_core.sync_outcome`), so every return path is covered. Status polls aren't counted.
- API Gateway graphs are per stage. Per-route metrics would need detailed metrics, which are billed.
- **Visits (AOTY-64, 2026-11-27)** are counted from the API access log, because ad blockers drop
  gtag. Every page load POSTs its sync.
  - The user query only sums: never `stats by` the path, which would put usernames on the
    dashboard.
  - Group slugs show only for 2xx answers, so a made-up slug (a 404) never appears.
  - The query leaves out CD's smoke-test name, so keep the two in step.
  - Queries in HCL strings need `\\/` for a regex slash; HCL refuses `\/`. Check a new one by
    rendering it (a scratch config with the same `join`) and running it with
    `aws logs start-query`.
- `IndexDumpAgeHours` is left out, not 0, when the dump's age is unknown (0 read as "fresh").
- **The budget is account-wide**, because kamp-site shares the account and a cost allocation tag
  only counts from the day it was activated.
- **A sync that runs out of time continues in a fresh message** (AOTY-46, `sync/handler._continue`):
  same FIFO group, a unique dedup id, `continuation` counted in the body. Re-receiving the same
  message counted every continuation toward `maxReceiveCount` 5, so a long first sync reached the
  DLQ without failing. It still re-receives (and the DLQ bounds it) when a run made no progress,
  after 20 continuations, or when the send fails.

## Page — design decisions

- Dark "poster" aesthetic; fonts Bricolage Grotesque (display) + Inter. Tokens on `:root` (`--bg`, `--panel`, `--accent` #ff6b4a, `--accent-2` #ffc24b).
- **Poster view**: #1 as a hero (big cover, blurred-cover backdrop, "#1" gradient crown), #2–#10 as a 3-col card grid, #11–#100 as a compact 5-col tile grid (no listener dots there — too small).
- **List view**: all 100 as horizontal cards, #1 with a gold outline. Toggle is a pill switch in the header; choice persisted in `localStorage` (`aoty-view`), wrapped in try/catch because storage can be blocked on `file://`.
- **Listener dots**: one dot per server member, filled for those who played it — the at-a-glance "how shared is this album" signal.
- **Seam fix 2 (keep it, AOTY-23, Chrome):** a darker strip at the bottom of the short intro page, whose edge moved with the window height. Pixels: content area `#100f12`, strip `#0d0c12` (exact `--bg`). Chrome draws one-flat-colour tiles by a shortcut that keeps near-black exact, while tiles with content render it slightly lighter. Fix: `body { background-image: linear-gradient(var(--bg), #0d0c13) }`, 1/255 apart, so no tile is ever flat. Three wrong fixes came first (a promoted-layer theory, overscroll-behavior); what found it was console bisecting: colour the regions (`html` red, `body` blue), hide pieces one at a time, and sample screenshots before each guess.
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
- **The Lambda concurrency quota is 1,000** (raised from 10 on 2026-11-22), so reserved
  concurrency works now.
- **Sandbox (AOTY-33) is the one deploy role any branch can get.** Its denies (nothing public,
  no account-wide log policies, no CloudFront) are the real guard; the workflow isn't, because a
  branch can edit it. Its first apply is the admin's, because only the admin can create bucket
  policies and public access blocks. HTTP APIs are scoped by `Environment` tag in every env, and
  tag writes check both `aws:RequestTag` and `aws:ResourceTag`.
- The deploy roles only allow what's in use. New resource types mean editing
  `global/ci_roles.tf` and having the admin apply `global` first.
- **Creating a tagged resource also needs its tag permission.** The provider's `default_tags`
  are sent with the create call, and AWS authorizes them as a separate `TagResource` (for
  Lambda, `lambda:TagResource` on the new ARN). The sync event source mapping failed its first
  deploy on this (AOTY-18). Grant the tag actions with any new resource type.
- Testing role policies from the laptop uses `admin_assume_principal`. Turn it off afterwards.
- **Service-linked roles live in `global/`** (`service_linked_roles.tf`). A deploy role can't
  create them, and AWS doesn't say so plainly. The first sandbox deploy of the ECS index cluster
  (AOTY-20) failed with "Unable to assume the service linked role", because the account had never
  used ECS. A new AWS service may need one too.
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
