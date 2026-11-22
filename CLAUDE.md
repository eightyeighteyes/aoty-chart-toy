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
| `lambdas/{api,sync,mb,chart}/handler.py` | Lambda code CD deploys (Terraform only creates functions). Shared code goes in `lambdas/common/` (imported as `common.*`; `albumkey` holds `norm`/`base_title`, which `aoty.py` re-exports), runtime deps in `lambdas/requirements.txt` (standard library only so far). `scripts/package_lambdas.py` builds reproducible arm64 zips into `dist/`, each stamped with a `build.json` `{"sha"}` (`-dirty` for uncommitted or untracked packaged files) that `/api/health` reports and CD's smoke test checks (AOTY-35). Don't add a `build.json` to the source: packaging rejects it. |
| `lambdas/sync/` | Sync worker (AOTY-18). `sync_core.py` is pure (week ranges, settle rule, merging, mb selection, covers); `handler.py` does the S3/DynamoDB/SQS I/O behind an injectable `Deps`. Contract and store format are in the module docstrings. |
| `lambdas/mb/` | MusicBrainz lane (AOTY-19). `mb_core.py` is pure (year then track lookups in M0's order, items); `handler.py` drains the queues and writes the `albums` table behind an injectable `Deps`. Contract in the module docstrings and "MusicBrainz lane" below. |
| `lambdas/common/mbmatch.py`, `musicbrainz.py` | The matching rules (`pick_release_group`/`pick_year`, `pick_release`, `title_tier`, `is_single`, query clauses), shared by M0 (`aoty.py`/`build_page.py` re-export them) and the mb Lambda; and the standard-library MusicBrainz client with a per-call deadline. |
| `tools/mb_parity.py` | AOTY-19 acceptance: runs `mb_core` on the laptop over `data/mb_bench/candidates.json` and diffs against M0's year and track caches into `data/mb_parity/run-<ts>/report.md`. A MusicBrainz client: run it alone. |
| `docs/architecture.md` | M1 infrastructure and per-actor sequence diagrams (Mermaid): built vs planned, who reads and writes what. Keep it current when a story adds a component. |
| `lambdas/chart/` | Chart builder (AOTY-21). `chart_core.py` is pure (year filter, By Track / By Album, covers, pending; the chart.json format in its docstring); `handler.py` handles the sync and mb events, the `requested_at` skip and the pending pass behind an injectable `Deps`. Reads the year from the `albums` table at build time, never the index. |
| `web/src/` | The user page shell (AOTY-21): `index.html` template, `app.css`, `app.js`, ported from `build_page.py` (no member filter, dots or Weighted). Routes `<base>user/<name>` client-side. kamp.fm's CSP allows no inline script or handlers: keep everything in `app.js`, use `addEventListener`. `scripts/build_shell.py --base <base>` builds `dist/web` with hashed assets (and fails on inline script); `tools/serve_shell.py --sample alice` previews it locally with kamp.fm's routing and CSP. |
| `tools/chart_check.py` | AOTY-21 acceptance: a deployed chart.json vs M0's rules (`score_rankings` plus the page script's member re-rank, transcribed) on the same store; `--env`, `--user`, prints `--label`. aws CLI, admin credentials. |
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
  deadline, releases that batch's failures (visibility 0), and invokes itself if work is left.
- While a run goes, every other start is throttled and retried by Lambda until the 1 h max event
  age. Harmless: each one exits fast on empty queues. The empty check long-polls both queues.
- **Parity (`tools/mb_parity.py`, 2026-11-22 12:15 ET, laptop):** all 1,536 M0 cold-run candidates
  resolved with none failed. Years were the same for all 1,530 that M0's cache had. The other 6 were
  never looked up by M0; the new answers look right. Track counts and types were the same for all
  568 that M0 had. There were 812 requests, 433 of them 503s (53%), taking 17.2 min at 1.48
  albums/s, including track lookups.

## Chart builder and user page (AOTY-21, 2026-11-22)

- User decisions: **Weighted is hidden** on single-user pages (it equals By Album for one
  listener; it returns with M2). **No analytics** yet (AOTY-38). The route is
  **`<base>user/<name>`**, so bare `<base><x>` stays free for other pages.
- Ranking: By Track = plays, ties by key; By Album = plays ÷ tracks, singles out by
  `is_single`, ties by plays then key. A strict top 100 (ties at the cutoff dropped).
  `tools/chart_check.py` checks a deployed chart against M0's code.
- Covers: Last.fm image → its other sizes → Cover Art Archive `front-250` by `rg_mbid` → the
  artist-name tile (`common/covers.py`). CAA 404s for some release groups.
- `display_name` (Last.fm's casing) is fetched by sync with `user.getInfo` at most every 30
  days and used only if it lowercases to the username. The page rewrites the URL to it.
- Pending rebuilds: mb pokes `{"pending": true}`. The builder rebuilds a user only when one of
  the keys in their `chart_pending.json` now has an item, so an album the lane never answers
  doesn't loop. Index re-checks that change an album already on a chart show up at the next sync.
- Sandbox has no kamp.fm path: test the Lambda half there (chart.json lands in its site bucket),
  and the page with `tools/serve_shell.py`.

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
