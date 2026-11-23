"""Preview the user page (AOTY-21) locally, the way kamp.fm serves it.

  .venv/bin/python tools/serve_shell.py                       # http://127.0.0.1:8000/toys/aoty/
  .venv/bin/python tools/serve_shell.py --sample alice        # also writes a synthetic chart
  aws s3 cp s3://kamp-aoty-sandbox-site/toys/aoty-sandbox/data/users/<u>/chart.json \
    data/shell_preview/users/<u>/chart.json                   # or preview a real chart

It builds the shell (scripts/build_shell.py) for the base path, then serves:
  <base>assets/*, <base>index.html      from the build
  <base>data/users/<u>/chart.json       from --data (default data/shell_preview/), else 404
  <base>api/users/<u>/{sync,status}     a stand-in API: POST sync starts a pretend 12 s sync
                                        (queued, reading, building, then idle, which bumps the
                                        chart's generated_at so the page repaints in place);
                                        a second POST within 10 min answers "cooldown"
  any other path under <base>           index.html, as kamp.fm's shell function does
with kamp.fm's Content-Security-Policy, so CSP violations show in the browser console.
data/ is git-ignored, so real charts copied there stay local.
"""

import argparse
import calendar
import http.server
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "lambdas"), str(REPO / "lambdas" / "chart")]

import chart_core  # noqa: E402
from common.albumkey import album_key, join_key  # noqa: E402
from scripts import build_shell  # noqa: E402

# As served by kamp.fm (response headers policy kamp-aoty-<env>-headers in kamp-site's
# infra/aoty.tf). https://archive.org is CAA's first redirect hop (kamp-site PR #22).
CSP = (
  "default-src 'self'; script-src 'self' https://www.googletagmanager.com; "
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
  "img-src 'self' data: https://lastfm.freetls.fastly.net https://coverartarchive.org https://archive.org "
  "https://*.archive.org "
  "https://*.google-analytics.com https://*.googletagmanager.com; "
  "connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com "
  "https://*.googletagmanager.com; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)
OUT = REPO / "dist" / "web-preview"
DATA = REPO / "data" / "shell_preview"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
         ".json": "application/json"}  # fmt: skip


def sample_chart(username, n=120):
  """A synthetic chart: n albums, a few singles, a few without covers or with a broken one."""
  store = {"version": 1, "year": 2026, "weeks": {"0": {"plays": {}}}, "albums": {}}
  items = {}
  for i in range(n):
    artist, album = f"Sample Artist {i % 23}", f"Sample Album {i:03d}"
    k = join_key(album_key(artist, album))
    store["weeks"]["0"]["plays"][k] = 400 - 3 * i
    # Every 7th album has a Last.fm-style URL that 404s, to exercise the fallback chain.
    image = "https://lastfm.freetls.fastly.net/i/u/300x300/doesnotexist.jpg" if i % 7 == 0 else ""
    store["albums"][k] = {"artist": artist, "album": album, "mbid": "", "image": image}
    items[k] = {"year": 2026, "tracks": 4 + i % 11, "tracks_type": "Single" if i % 9 == 4 else "Album",
                "rg_mbid": None}  # fmt: skip
  chart, _ = chart_core.build_chart(store, items, username=username.lower(), display_name=username,
                                    now=calendar.timegm((2026, 11, 22, 12, 0, 0)))  # fmt: skip
  chart["pending"] = 3
  return chart


FAKE_SYNC_S = (2, 8, 12)  # queued until 2 s, reading until 8 s, building until 12 s, then idle


def fake_status(started, now, chart_file):
  """The status API's answer for a pretend sync that began at `started` (AOTY-22 preview).

  On reaching idle it stamps the chart's generated_at, so the page sees a newer chart and
  repaints it in place, as it would after a real sync.
  """
  e = now - started
  q, r, b = FAKE_SYNC_S
  base = {"display_name": None, "weeks": {"done": 0, "total": 4}, "scrobbles": 0, "new_plays": 0,
          "albums": {"checked": None, "total": None, "hits": None}, "last_synced_at": int(started),
          "chart_built_at": None, "cooldown_until": int(started) + 600}  # fmt: skip
  if e < q:
    return {**base, "stage": "queued", "queue_ahead_approx": 0}
  if e < r:
    return {**base, "stage": "reading", "weeks": {"done": min(4, int((e - q) / (r - q) * 4)), "total": 4}}
  if e < b:
    return {**base, "stage": "building", "weeks": {"done": 4, "total": 4}, "new_plays": 42}
  built = int(started + b)
  if chart_file.is_file():
    chart = json.loads(chart_file.read_text(encoding="utf-8"))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(built))
    if chart.get("generated_at") != stamp:
      chart["generated_at"] = stamp
      chart_file.write_text(json.dumps(chart), encoding="utf-8")
  return {**base, "stage": "idle", "weeks": {"done": 4, "total": 4}, "chart_built_at": built}


def handler_for(base, out, data):
  syncs = {}  # username -> when its pretend sync started

  class Handler(http.server.BaseHTTPRequestHandler):
    def api(self, method, rest):
      """A stand-in for the API (AOTY-22): POST sync starts a pretend sync, status follows it."""
      parts = rest.split("/")  # api, users, <name>, sync|status
      if len(parts) != 4 or parts[:2] != ["api", "users"] or parts[3] not in ("sync", "status"):
        return self.send(404, b'{"error":"not found"}', "application/json")
      name, action = parts[2].lower(), parts[3]
      now = time.time()
      if action == "sync" and method == "POST":
        fresh = name not in syncs or now - syncs[name] > 600
        if fresh:
          syncs[name] = now
        status = fake_status(syncs[name], now, data / "users" / name / "chart.json")
        body = {"queued": fresh, "status": status} if fresh else {"queued": False, "reason": "cooldown", "status": status}
        return self.send(202 if fresh else 200, json.dumps(body).encode(), "application/json")
      if action == "status" and method == "GET":
        if name not in syncs:
          return self.send(404, b'{"error":"unknown user"}', "application/json")
        status = fake_status(syncs[name], now, data / "users" / name / "chart.json")
        return self.send(200, json.dumps(status).encode(), "application/json")
      return self.send(405, b'{"error":"method not allowed"}', "application/json")

    def do_POST(self):
      path = self.path.split("?", 1)[0]
      self.rfile.read(int(self.headers.get("Content-Length") or 0))
      if path.startswith(f"{base}api/"):
        return self.api("POST", path[len(base) :])
      return self.send(405, b"", "text/plain")

    def do_GET(self):
      path = self.path.split("?", 1)[0]
      if not path.startswith(base):
        return self.send(404, b"not under the base path\n", "text/plain")
      rest = path[len(base) :]
      if rest.startswith("api/"):
        return self.api("GET", rest)
      if rest.startswith("data/"):
        f = data / rest[len("data/") :]
        return self.file(f, "no-cache") if f.is_file() else self.send(404, b"{}", "application/json")
      f = out / rest
      if rest and f.is_file():
        return self.file(f, "public, max-age=31536000, immutable")
      return self.file(out / "index.html", "no-cache")

    def file(self, f, cache):
      self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"), cache)

    def send(self, status, body, ctype, cache="no-store"):
      self.send_response(status)
      self.send_header("Content-Type", ctype)
      self.send_header("Content-Security-Policy", CSP)
      self.send_header("Cache-Control", cache)
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

  return Handler


def main():
  p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  p.add_argument("--base", default="/toys/aoty/")
  p.add_argument("--port", type=int, default=8000)
  p.add_argument("--data", type=Path, default=DATA, help="holds users/<u>/chart.json")
  p.add_argument("--sample", metavar="NAME", help="write a synthetic chart for NAME first")
  args = p.parse_args()
  build_shell.build(args.base, out=OUT)
  if args.sample:
    f = args.data / "users" / args.sample.lower() / "chart.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sample_chart(args.sample)), encoding="utf-8")
    print(f"sample chart: {f}")
  server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args.base, OUT, args.data))
  who = args.sample or "<name>"
  print(f"serving http://127.0.0.1:{args.port}{args.base}user/{who}  (ctrl-c to stop)")
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    pass


if __name__ == "__main__":
  main()
