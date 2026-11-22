# M1 architecture

How the pieces of the single-user app (AOTY-2) fit together. Solid boxes are built; dashed
boxes and arrows are planned, labelled with the story that adds them. One copy of the stack
runs per environment (staging, prod, sandbox); names below drop the `kamp-aoty-<env>-` prefix.

Three actors drive everything:

- **Visitor**: a person opening `kamp.fm/toys/aoty/<username>`.
- **Sync worker**: reads one user's Last.fm history into the private store.
- **mb lane**: the one MusicBrainz client, which dates albums and counts their tracks.

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
    ssm[("SSM: Last.fm key,<br/>MB contact")]
    chart["chart builder<br/>(AOTY-21)"]
    index[("release index in store<br/>(AOTY-20)")]
    indexer["index build, twice weekly<br/>(AOTY-20)"]
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
  sync -.->|"AOTY-21"| chart
  chart -.-> storeb
  chart -.-> albums
  chart -.-> index
  chart -.->|"chart.json"| siteb
  indexer -.-> mbdump
  indexer -.-> index
  mb -.->|"AOTY-20: index first"| index
  ssm --- sync
  ssm --- mb

  classDef planned stroke-dasharray: 5 5
  class chart,index,indexer planned
```

## Visitor flow

What a visit will look like once AOTY-21, 22 and 23 land. Today only `GET /api/health`
exists.

```mermaid
sequenceDiagram
  autonumber
  actor V as Visitor
  participant CF as CloudFront
  participant S3 as site bucket
  participant API as api Lambda
  participant U as users table
  participant Q as sync queue

  V->>CF: GET /toys/aoty/alice
  CF->>S3: static shell
  S3-->>V: HTML, CSS, JS
  V->>CF: GET data/users/alice/chart.json
  CF->>S3: chart.json
  alt chart exists (return visit)
    S3-->>V: 200: paint the chart immediately
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

What happens after a sync message is queued. The sync worker and the mb lane are built (AOTY-18
and this story, AOTY-19); the chart builder hook is a no-op until AOTY-21.

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
  participant CB as chart builder (AOTY-21)

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
  SW-)CB: build chart (no-op until AOTY-21)

  Note over MB: Reserved concurrency 1: a second invoke waits (throttled, retried by Lambda)
  loop until both queues are empty or time runs short
    MB->>HQ: receive up to 20 (hot first)
    MB->>AT: skip albums that already have an item
    MB->>MBA: one batched release-group search (20 albums)
    MB->>MBA: one search per miss
    MB->>MBA: release search for this year's albums (track counts)
    MB->>LF: album.getInfo when MusicBrainz has no count
    MB->>AT: write year, type, tracks (a confirmed miss = null year)
    MB->>HQ: delete the resolved messages
  end
  opt messages left when time ran short
    MB-)MB: async invoke itself (runs after this one exits)
  end
  MB-)CB: rebuild charts with pending albums (AOTY-21)
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
  participant IX as index build (AOTY-20)
  participant DUMP as MusicBrainz dump
  participant ST as store bucket

  Note over SC,MB: Hourly backstop, in case a sync's poke was lost
  SC-)MB: invoke {}
  MB->>HQ: receive (long poll, both queues)
  alt both empty
    MB-->>SC: exit, no MusicBrainz request
  else work waiting
    MB->>MB: drain as in the flow above
  end

  Note over SC,ST: Twice weekly (AOTY-20, planned)
  SC-)IX: build the release index
  IX->>DUMP: download release-group.tar.xz
  IX->>ST: write the keymap (any year, titles that match this year)
  Note over IX,MB: Monthly: requeue misses older than 30 days to mb-cold
```

## Who reads and writes what

| Store | Written by | Read by |
|---|---|---|
| store bucket `users/*/YEAR/weeks.json.gz` | sync | chart builder (AOTY-21) |
| store bucket release index | index build (AOTY-20) | mb lane, chart builder |
| `albums` table | **mb lane only** | sync (what to queue), chart builder |
| `users` table | api, sync | api (status, cooldown) |
| site bucket `data/users/*/chart.json` | chart builder (AOTY-21) | browsers, through CloudFront |
| mb-hot / mb-cold | sync (AOTY-20: monthly re-check) | mb lane |

The lookup order for an album's year (AOTY-20) is the index, then the `albums` table, then a
live search in the mb lane. The lane writes the item whichever of those found the year, and
looks up the track count itself, because the dump has none. So "has an `albums` item" stays
the one test for "resolved".
