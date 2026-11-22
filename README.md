# aoty-chart

Our Discord server's albums of the year, from everyone's Last.fm scrobbles.

1. `aoty.py` pulls each member's top albums for the last 12 months from Last.fm, keeps the ones
   MusicBrainz says were first released in `TARGET_YEAR`, and ranks them by how many members
   played each one (then total plays). Output: `data/server_top_albums_<year>.csv`.
2. `build_page.py` turns the top 100 into a web page with cover art: `site/index.html`, with a
   Poster view (top 10 featured, 11–100 as a grid) and a List view, three rankings (By Track,
   By Album, Weighted) and a per-member filter.

## Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # then add your Last.fm API key and the members' usernames
```

`.env` holds `LASTFM_API_KEY`, `LASTFM_USERS` (comma-separated Last.fm usernames) and an
optional `MB_CONTACT` for the MusicBrainz User-Agent. `TARGET_YEAR` is in `aoty.py`.

`data/` (the ranking CSV and local caches) and `site/` are build output and git-ignored; the
CSV and page also contain members' usernames and play counts.

## Run

```sh
.venv/bin/python aoty.py
.venv/bin/python build_page.py
open site/index.html
```

The first `aoty.py` run takes ~11–20 minutes: MusicBrainz allows one request per second and
refuses about half of them when busy, so `mb_search` retries after each 503's `Retry-After`. Release
years are cached in `data/mb_release_year_cache.json`, so later runs only look up new albums.
Both scripts print a MusicBrainz timing summary and append per-request rows to
`data/mb_requests.csv`.

## Tests and lint

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The tests cover release-year matching, track counts and the ranking rules, and run offline
against recorded MusicBrainz searches (`tests/fixtures/mb/`, refreshed with
`tools/record_mb_fixtures.py`). CI runs the same checks plus Terraform `fmt`/`validate` and a
read-only `terraform plan` on every PR. `main` requires them to pass.

## Web app infrastructure

The M1 web app (kamp.fm/toys/aoty/) runs serverless on AWS, managed by Terraform in `infra/`.
See [infra/README.md](infra/README.md).

Deploys are automatic. A merge to `main` that passes CI deploys staging
(kamp.fm/toys/aoty-staging/), and release-please keeps a release PR open. Merging that PR tags
a version and deploys it to production. The Lambda code lives in `lambdas/`. Rollback is
`gh workflow run deploy.yml --ref <older tag> -f environment=production`.

## MusicBrainz benchmark

`tools/mb_cold_run.py` re-runs the MusicBrainz lookups cold, against a scratch cache, comparing
batch sizes and request intervals; `tools/mb_analyze.py` summarises one or more runs. Neither
touches the real caches.

```sh
.venv/bin/python tools/mb_cold_run.py --limit 40 --chunk 10   # 30-second dry run
.venv/bin/python tools/mb_cold_run.py                         # full run, ~15–20 min
.venv/bin/python tools/mb_analyze.py data/mb_bench/run-*
```
