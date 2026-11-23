"""Pre-warm the albums table with the year's popular albums (AOTY-42).

  .venv/bin/python tools/prewarm.py --env staging --dry-run     # size it: candidates, already dated
  .venv/bin/python tools/prewarm.py --env prod                  # queue the undated ones to mb-cold

A first chart ranks only albums the shared albums table has dated. In a surge, every new user's
undated top albums land in the one mb-hot queue (AOTY-42's baseline). Dating the year's popular
albums ahead of time shrinks that: the table is shared, so anyone's dated album is free for all.

Candidates come from Last.fm, so they carry Last.fm's names, which is what albums items are keyed
by (sync's album_key):
  - tag.getTopAlbums for the year's tag ("2026"): listeners tag albums by release year, so this
    points almost straight at the target year.
  - chart.getTopArtists, then each artist's artist.getTopAlbums.
Every candidate without an albums item goes to **mb-cold**, in the lane's own message format: the
mb lane stays the only writer and the one MusicBrainz client, and cold never delays a waiting user.
No index check here: the lane does it, and an old album with a trusted index hit costs no
MusicBrainz request at all; only target-year albums pay for a track-count lookup.

Last.fm calls use .env's LASTFM_API_KEY (the laptop's key, not an environment's), paced like
the sync worker. Admin credentials (aws CLI) for the table and the queue.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lambdas"))

from common import lastfm  # noqa: E402
from common.albumkey import album_key, join_key, split_key  # noqa: E402
from common.config import TARGET_YEAR  # noqa: E402

REGION = "us-east-1"
BATCH_GET = 100
SEND_BATCH = 10


def aws(args):
  out = subprocess.run(
    ["aws", *args, "--region", REGION, "--output", "json"], check=True, capture_output=True, text=True
  ).stdout
  return json.loads(out) if out.strip() else None


def dotenv_key(path):
  for line in path.read_text().splitlines():
    key, _, value = line.partition("=")
    if key.strip() == "LASTFM_API_KEY":
      return value.strip().strip("'\"")
  raise SystemExit(f"no LASTFM_API_KEY in {path}")


def add(found, artist, album, mbid=""):
  """Keep the first spelling of each album key; skip blank names (as sync does)."""
  artist, album = (artist or "").strip(), (album or "").strip()
  if not artist or not album or album == "(null)":
    return
  a, t = album_key(artist, album)
  if a and t:
    found.setdefault(join_key((a, t)), {"artist": artist, "album": album, "mbid": mbid or ""})


def _artist(row):
  a = row.get("artist")
  return a.get("name", "") if isinstance(a, dict) else (a or "")


def gather(client, *, year, tag_pages, artist_pages, albums_per_artist, log=print):
  """{key: {"artist", "album", "mbid"}} from the year's tag and the top artists' top albums."""
  found = {}
  for page in range(1, tag_pages + 1):
    data = client.call("tag.gettopalbums", tag=str(year), page=page, limit=50)
    rows = lastfm.as_list((data.get("albums") or {}).get("album"))
    for r in rows:
      add(found, _artist(r), r.get("name"), r.get("mbid"))
    if not rows:
      break
  log(f"tag {year}: {len(found)} albums")
  artists = []
  for page in range(1, artist_pages + 1):
    data = client.call("chart.gettopartists", page=page, limit=50)
    artists += [a.get("name") for a in lastfm.as_list((data.get("artists") or {}).get("artist")) if a.get("name")]
  for i, name in enumerate(artists, 1):
    try:
      data = client.call("artist.gettopalbums", artist=name, limit=albums_per_artist, autocorrect=1)
    except lastfm.LastfmError as e:
      log(f"  skipped an artist: Last.fm {e.code}")
      continue
    for r in lastfm.as_list((data.get("topalbums") or {}).get("album")):
      add(found, _artist(r) or name, r.get("name"), r.get("mbid"))
    if i % 50 == 0:
      log(f"  {i}/{len(artists)} artists, {len(found)} albums")
  log(f"top artists ({len(artists)}): {len(found)} albums in all")
  return found


def message(key, meta):
  """The mb lane's message, as sync's _mb_message builds it."""
  artist_key, title_key = split_key(key)
  return {"artist_key": artist_key, "title_key": title_key, **meta}


def dated(env, keys):
  """The keys that already have an albums item (a year or a confirmed miss)."""
  table, have = f"kamp-aoty-{env}-albums", set()
  for i in range(0, len(keys), BATCH_GET):
    pending = {table: {
      "Keys": [{"artist_key": {"S": a}, "title_key": {"S": t}} for a, t in map(split_key, keys[i : i + BATCH_GET])],
      "ProjectionExpression": "artist_key, title_key",
    }}  # fmt: skip
    for attempt in range(5):
      out = aws(["dynamodb", "batch-get-item", "--request-items", json.dumps(pending)])
      for item in out.get("Responses", {}).get(table, []):
        have.add(join_key((item["artist_key"]["S"], item["title_key"]["S"])))
      pending = out.get("UnprocessedKeys") or {}
      if not pending:
        break
      time.sleep(0.2 * 2**attempt)
  return have


def send(env, messages, log=print):
  url = aws(["sqs", "get-queue-url", "--queue-name", f"kamp-aoty-{env}-mb-cold"])["QueueUrl"]
  sent = 0
  for i in range(0, len(messages), SEND_BATCH):
    entries = [{"Id": str(j), "MessageBody": json.dumps(m)} for j, m in enumerate(messages[i : i + SEND_BATCH])]
    out = aws(["sqs", "send-message-batch", "--queue-url", url, "--entries", json.dumps(entries)])
    sent += len(out.get("Successful", []))
    if (i // SEND_BATCH) % 50 == 0:
      log(f"  queued {sent}/{len(messages)}")
  return sent


def main(argv=None):
  ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  ap.add_argument("--env", choices=("staging", "prod", "sandbox"), required=True)
  ap.add_argument("--dry-run", action="store_true", help="count candidates and dated ones; queue nothing")
  ap.add_argument("--year", type=int, default=TARGET_YEAR)
  ap.add_argument("--tag-pages", type=int, default=10, help="tag.getTopAlbums pages of 50")
  ap.add_argument("--artist-pages", type=int, default=4, help="chart.getTopArtists pages of 50")
  ap.add_argument("--albums-per-artist", type=int, default=10)
  ap.add_argument("--dotenv", type=Path, default=REPO / ".env")
  args = ap.parse_args(argv)

  client = lastfm.Client(os.environ.get("LASTFM_API_KEY") or dotenv_key(args.dotenv))
  found = gather(client, year=args.year, tag_pages=args.tag_pages, artist_pages=args.artist_pages,
                 albums_per_artist=args.albums_per_artist)  # fmt: skip
  keys = sorted(found)
  have = dated(args.env, keys)
  todo = [message(k, found[k]) for k in keys if k not in have]
  print(f"{len(keys)} candidates, {len(have)} already in {args.env}'s albums table, {len(todo)} to queue "
        f"({client.calls} Last.fm calls)")  # fmt: skip
  if args.dry_run or not todo:
    return
  print(f"queued {send(args.env, todo)} to kamp-aoty-{args.env}-mb-cold; the mb lane dates them after any hot work")


if __name__ == "__main__":
  main()
