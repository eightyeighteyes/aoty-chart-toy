"""Preview the user page (AOTY-21) locally, the way kamp.fm serves it.

  .venv/bin/python tools/serve_shell.py                       # http://127.0.0.1:8000/toys/aoty/
  .venv/bin/python tools/serve_shell.py --sample alice        # also writes a synthetic chart
  aws s3 cp s3://kamp-aoty-sandbox-site/toys/aoty-sandbox/data/users/<u>/chart.json \
    data/shell_preview/users/<u>/chart.json                   # or preview a real chart

It builds the shell (scripts/build_shell.py) for the base path, then serves:
  <base>assets/*, <base>index.html      from the build
  <base>data/users/<u>/chart.json       from --data (default data/shell_preview/), else 404
  any other path under <base>           index.html, as kamp.fm's shell function does
with kamp.fm's Content-Security-Policy, so CSP violations show in the browser console.
data/ is git-ignored, so real charts copied there stay local.
"""

import argparse
import calendar
import http.server
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "lambdas"), str(REPO / "lambdas" / "chart")]

import chart_core  # noqa: E402
from common.albumkey import album_key, join_key  # noqa: E402
from scripts import build_shell  # noqa: E402

# As served by kamp.fm (response headers policy kamp-aoty-<env>-headers, checked 2026-11-22).
CSP = (
  "default-src 'self'; script-src 'self' https://www.googletagmanager.com; "
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
  "img-src 'self' data: https://lastfm.freetls.fastly.net https://coverartarchive.org https://*.archive.org "
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


def handler_for(base, out, data):
  class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
      path = self.path.split("?", 1)[0]
      if not path.startswith(base):
        return self.send(404, b"not under the base path\n", "text/plain")
      rest = path[len(base) :]
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
