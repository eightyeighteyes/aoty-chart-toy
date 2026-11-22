# M1 architecture

How the pieces of the single-user app (AOTY-2) fit together. Solid boxes are built; dashed
boxes and arrows are planned, labelled with the story that adds them. One copy of the stack
runs per environment (staging, prod, sandbox); names below drop the `kamp-aoty-<env>-` prefix.

Five actors drive everything:

- **Visitor**: a person opening `kamp.fm/toys/aoty/user/<username>`.
- **Sync worker**: reads one user's Last.fm history into the private store.
- **mb lane**: the one MusicBrainz client, which dates albums and counts their tracks. It asks
  the release index first.
- **Index build**: a daily Fargate Spot task that turns the MusicBrainz JSON dump into the
  release index, and queues re-checks for albums whose answer has changed.
- **Chart builder**: turns one user's store and the `albums` table into `chart.json` on the
  site bucket, after each sync and while that user's pending albums resolve (AOTY-21).

The page never waits on MusicBrainz or Last.fm: it reads static JSON from CloudFront, and the
background work keeps that JSON fresh.

## Infrastructure

```mermaid
flowchart LR
  visitor(["Visitor's browser"])

  subgraph edge["kamp.fm (kamp-site repo)"]
    cf["CloudFront"]
  end

  subgraph aws["AWS, one stack per environment"]
    apigw["HTTP API"]
    api["api Lambda"]
    siteb[("site bucket<br/>shell, data/users/*/chart.json")]
    storeb[("store bucket<br/>users/*/YEAR/weeks.json.gz")]
    users[("users table<br/>sync state, progress")]
    albums[("albums table<br/>year, type, tracks per album")]
    syncq[["sync queue (FIFO,<br/>group = username)"]]
    sync["sync Lambda"]
    hot[["mb-hot queue"]]
    cold[["mb-cold queue"]]
    mb["mb Lambda<br/>reserved concurrency 1"]
    sched["Scheduler: hourly"]
    isched["Scheduler: daily 06:15 UTC"]
    ssm[("SSM: Last.fm key,<br/>MB contact")]
    chart["chart Lambda<br/>reserved concurrency 1"]
    index[("store bucket<br/>index/current.json,<br/>index/DUMP/*.json.gz")]
    code[("store bucket<br/>code/index.zip")]
    indexer["index build<br/>Fargate Spot task"]
  end

  lastfm(["Last.fm API"])
  mbapi(["MusicBrainz API<br/>1 req/s, ~50% 503s"])
  mbdump(["MusicBrainz JSON dump"])

  visitor --> cf
  cf -- "/toys/aoty/*" --> siteb
  cf -- "/toys/aoty/api/*" --> apigw --> api
  api -.->|"AOTY-22: queue a sync"| syncq
  api --> users
  syncq --> sync
  sync --> lastfm
  sync --> storeb
  sync --> users
  sync -- "read: already dated?" --> albums
  sync --> hot
  sync --> cold
  sync -- "async poke" --> mb
  sched --> mb
  hot --> mb
  cold --> mb
  mb --> mbapi
  mb -- "track-count fallback" --> lastfm
  mb -- "only writer" --> albums
  sync -- "async: username" --> chart
  mb -- "async: pending" --> chart
  chart -- "read store,<br/>pending keys" --> storeb
  chart -- "BatchGet" --> albums
  chart -- "chart_built_at,<br/>chart_pending" --> users
  chart -- "chart.json" --> siteb
  isched -- "ecs:RunTask" --> indexer
  indexer -- "fetch code" --> code
  indexer -- "download, twice a week" --> mbdump
  indexer -- "shards, then manifest" --> index
  indexer -- "Scan: what changed?" --> albums
  indexer -- "re-checks" --> cold
  mb -- "index first" --> index
  ssm --- sync
  ssm --- mb
  ssm --- indexer

  classDef planned stroke-dasharray: 5 5
```

The index build runs in the default VPC's public subnets with a public IP, with no NAT
gateway. CD uploads its code to `code/index.zip`, as it updates the Lambdas' code.

## Visitor flow

What a visit will look like once AOTY-22 and 24 land. Today the shell, `chart.json` and
`GET /api/health` exist; a missing chart shows a placeholder until AOTY-24. It reports the env and the deployed commit's SHA, read from the zip's `build.json`
(AOTY-35).

```mermaid
sequenceDiagram
  autonumber
  actor V as Visitor
  participant CF as CloudFront
  participant S3 as site bucket
  participant API as api Lambda
  participant U as users table
  participant Q as sync queue

  V->>CF: GET /toys/aoty/user/Alice
  CF->>S3: static shell (every page path serves index.html)
  S3-->>V: HTML, then hashed CSS and JS
  V->>CF: GET data/users/alice/chart.json (lowercased)
  CF->>S3: chart.json
  alt chart exists (return visit)
    S3-->>V: 200: paint the chart, URL rewritten to Last.fm's casing
  else first visit
    S3-->>V: 404: first-visit screen (AOTY-24)
  end
  V->>API: POST api/users/alice/sync (AOTY-22)
  API->>U: last sync less than 10 min ago?
  alt within the cooldown
    API-->>V: current state, nothing queued
  else
    API->>Q: {"username": "alice"}
    API-->>V: queued
  end
  loop every 2-5 s until idle (AOTY-22)
    V->>API: GET status
    API->>U: state, progress
    API-->>V: stage, weeks done, albums checked
  end
  V->>CF: GET chart.json again
  CF-->>V: re-render in place
```

## Background flow: sync, then the mb lane

What happens after a sync message is queued. The sync worker, the mb lane and the release
index and the chart builder are built (AOTY-18, 19, 20, 21).

```mermaid
sequenceDiagram
  autonumber
  participant Q as sync queue
  participant SW as sync Lambda
  participant LF as Last.fm
  participant ST as store bucket
  participant AT as albums table
  participant HQ as mb-hot / mb-cold
  participant MB as mb Lambda
  participant MBA as MusicBrainz
  participant CB as chart Lambda

  Q->>SW: {"username": "alice"}
  SW->>ST: load weeks.json.gz
  loop each unsettled chart week
    SW->>LF: user.getWeeklyAlbumChart
    SW->>ST: checkpoint every 8 weeks
  end
  SW->>LF: getRecentTracks (covers)
  SW->>AT: BatchGetItem: which albums are dated already?
  SW->>HQ: undated albums: top 150 to hot, the rest to cold
  SW-)MB: async invoke {}
  SW-)CB: {"username", "requested_at"}

  Note over MB: Reserved concurrency 1: a second invoke waits (throttled, retried by Lambda)
  MB->>ST: index/current.json (re-read at most hourly)
  loop until both queues are empty or time runs short
    MB->>HQ: receive up to 20 (hot first)
    MB->>AT: skip albums that already have an item (unless a message is a re-check)
    MB->>ST: index shards (cached while warm)
    Note over MB: A trusted hit (exact title, type Album) needs no year search
    MB->>MBA: one batched release-group search for the rest
    MB->>MBA: one search per miss
    MB->>MBA: release search for this year's albums (track counts; a re-check keeps the stored count)
    MB->>LF: album.getInfo when MusicBrainz has no count
    MB->>AT: write year, type, tracks (a confirmed miss = null year)
    MB->>HQ: delete the resolved messages
  end
  opt messages left when time ran short
    MB-)MB: async invoke itself (runs after this one exits)
  end
  MB-)CB: {"pending": true}: after the first batch that writes, then at most once a minute, and at the end
```

A failed request is never written. Its message reappears after the visibility timeout, and
after 5 receives it moves to the dead-letter queue.

## Background flow: schedules

```mermaid
sequenceDiagram
  autonumber
  participant SC as Scheduler
  participant MB as mb Lambda
  participant HQ as mb-hot / mb-cold
  participant IX as index build (Fargate Spot)
  participant DUMP as MusicBrainz dump
  participant ST as store bucket
  participant AT as albums table

  Note over SC,MB: Hourly backstop, in case a sync's poke was lost
  SC-)MB: invoke {}
  MB->>HQ: receive (long poll, both queues)
  alt both empty
    MB-->>SC: exit, no MusicBrainz request
  else work waiting
    MB->>MB: drain as in the flow above
  end

  Note over SC,AT: Daily at 06:15 UTC (a new dump comes out twice a week)
  SC-)IX: ecs:RunTask, FARGATE_SPOT
  IX->>ST: code/index.zip, then index/current.json
  IX->>DUMP: LATEST
  alt the manifest already names the latest dump
    IX-->>SC: exit in seconds
  else a new dump
    IX->>DUMP: download release-group.tar.xz (1.24 GB, SHA256-checked)
    IX->>IX: every dated release group, any year, into 4,096 shards by title
    IX->>ST: upload index/DUMP/*.json.gz
    IX->>ST: write index/current.json last (the build goes live)
    IX->>ST: delete builds older than the one replaced
    IX->>AT: Scan
    IX->>HQ: re-checks to mb-cold: answer changed, or a miss older than 30 days
  end
```

A Spot interruption kills a build before its manifest is written, so the old index stays live
and the next day's run starts over.

## Who reads and writes what

| Store | Written by | Read by |
|---|---|---|
| store bucket `users/*/YEAR/weeks.json.gz` | sync | chart builder |
| store bucket `users/*/YEAR/chart_pending.json` | chart builder | chart builder (pending pass) |
| store bucket `index/current.json`, `index/DUMP/*.json.gz` | index build | mb lane |
| store bucket `code/index.zip` | CD | index build (at start) |
| `albums` table | **mb lane only** | sync (what to queue), index build (re-check scan), chart builder |
| `users` table | api, sync (state, `display_name`), chart builder (`chart_built_at`, `chart_pending`) | api (status, cooldown), chart builder |
| site bucket `data/users/*/chart.json` | chart builder | browsers, through CloudFront |
| site bucket `index.html`, `assets/*` | CD (`scripts/build_shell.py`) | browsers, through CloudFront |
| mb-hot / mb-cold | sync; index build (re-checks, mb-cold only) | mb lane |

An album's year comes from the `albums` table if it has an item. If not, the mb lane tries the
index, then a live search. The lane writes the item whichever of the two found the year, and
looks up the track count itself, because the dump has none. So "has an `albums` item" stays
the one test for "resolved". A re-check is the only way an item is rewritten. The index build
asks for one when its answer has changed, and the chart builder will read the table, so the
correction reaches every chart.

The index covers every dated release group of any year, not only the target year's, so the
same index serves 2027 and M4's albums of any year. An index answer is trusted only for an
exact title of type Album. The dump lags days behind MusicBrainz, so last year's single "X"
mustn't stand in for this year's album "X" that the dump doesn't have yet.

## Chart builder (AOTY-21)

```mermaid
sequenceDiagram
  autonumber
  participant SW as sync Lambda
  participant MB as mb Lambda
  participant CB as chart Lambda
  participant U as users table
  participant ST as store bucket
  participant AT as albums table
  participant S3 as site bucket

  alt after a sync
    SW-)CB: {"username", "requested_at"}
    CB->>U: chart_built_at newer than requested_at?
    Note over CB: yes: skip (a repeated or retried event)
  else while albums resolve
    MB-)CB: {"pending": true}
    CB->>U: Scan chart_pending > 0
    CB->>ST: chart_pending.json per user
    CB->>AT: keys-only BatchGet: any pending album answered?
    Note over CB: no: skip that user
  end
  CB->>ST: weeks.json.gz
  CB->>AT: BatchGet every played album
  CB->>S3: data/users/u/chart.json (Cache-Control no-cache)
  CB->>ST: chart_pending.json
  CB->>U: chart_built_at, chart_pending
```

Reserved concurrency 1 runs builds one at a time, so a build that read older data can't
overwrite a newer chart. The year comes from the `albums` table at build time, so a re-check
that corrects an album reaches the chart at the user's next sync. The pending pass Scans the
users table, which is fine at M1's scale; past a few thousand users, a sparse GSI on
`chart_pending` replaces it.
