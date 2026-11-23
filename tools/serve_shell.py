"""Preview the user page (AOTY-21) locally, the way kamp.fm serves it.

  .venv/bin/python tools/serve_shell.py                       # http://127.0.0.1:8000/toys/aoty/
  .venv/bin/python tools/serve_shell.py --sample alice        # also writes a synthetic chart
  .venv/bin/python tools/serve_shell.py --proxy staging       # real users: api/ and data/ from kamp.fm
  .venv/bin/python tools/serve_shell.py --proxy sandbox       # a branch's API in sandbox (aws creds)
  aws s3 cp s3://kamp-aoty-sandbox-site/toys/aoty-sandbox/data/users/<u>/chart.json \
    data/shell_preview/users/<u>/chart.json                   # or preview a real chart

It builds the shell (scripts/build_shell.py) for the base path, then serves:
  <base>assets/*, <base>index.html      from the build
  <base>data/users/<u>/chart.json       from --data (default data/shell_preview/), else 404
  <base>api/users/<u>/{sync,status}     a stand-in API: POST sync starts a pretend 12 s sync
                                        (queued, reading, building, then idle, which bumps the
                                        chart's generated_at so the page repaints in place);
                                        a second POST within 10 min answers "cooldown". The
                                        intro's errors: nobody* (404), lastfm-down, busy-busy,
                                        broken (503s), private-* (a known private profile). Only
                                        names with a chart under --data ever get one. With
                                        --proxy, api/ and data/ go to that env instead: kamp.fm
                                        for staging and prod; for sandbox, its execute-api URL
                                        and its site bucket (it has no CloudFront path).
  any other path under <base>           index.html, as kamp.fm's shell function does
with kamp.fm's Content-Security-Policy, so CSP violations show in the browser console.
data/ is git-ignored, so real charts copied there stay local.
"""

import argparse
import calendar
import subprocess
import http.server
import json
import sys
import time
import urllib.error
import urllib.request
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
         ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png"}  # fmt: skip
# Names the fake API answers specially, so every intro message can be previewed (AOTY-23).
FAKE_OUTCOMES = {
  "nobody": (404, {"error": "unknown user"}),
  "lastfm-down": (503, {"error": "lastfm_unavailable"}),
  "busy-busy": (503, {"error": "busy"}),
  "broken": (503, {"error": "unavailable"}),
}


def fake_outcome(name):
  """(status, body) for a special preview name, else None. private-* is a known private profile."""
  if name.startswith("nobody"):
    return FAKE_OUTCOMES["nobody"]
  if name.startswith("private-"):
    return 200, {"queued": False, "reason": "cooldown", "status": {"stage": "error", "reason": "private"}}
  return FAKE_OUTCOMES.get(name)


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


KAMP = {"prod": "https://kamp.fm/toys/aoty/", "staging": "https://kamp.fm/toys/aoty-staging/"}
PROXIES = ("prod", "sandbox", "staging")


class KampProxy:
  """staging / prod: api/ and data/ both come from kamp.fm."""

  def __init__(self, base_url):
    self.base_url, self.label = base_url, base_url

  def api(self, method, rest, body, ctype):
    return forward(self.base_url, method, rest, body, ctype)

  def data(self, rest):
    return forward(self.base_url, "GET", rest)


class SandboxProxy:
  """sandbox isn't on kamp.fm: api/ goes to its execute-api URL, data/ is read from its site
  bucket with the aws CLI (your credentials), as CloudFront would serve it."""

  def __init__(self, stack):
    self.api_base = f"{stack['api_endpoint']}{stack['base_path']}"
    self.bucket, self.prefix = stack["site_bucket_name"], stack["s3_key_prefix"]
    self.label = f"{self.api_base} and s3://{self.bucket}/{self.prefix}"

  @classmethod
  def from_terraform(cls):
    out = subprocess.run(["terraform", f"-chdir={REPO / 'infra' / 'envs' / 'sandbox'}", "output", "-json", "stack"],
                         capture_output=True, text=True)  # fmt: skip
    if out.returncode != 0:
      raise SystemExit(f"can't read sandbox's stack outputs (terraform init there first?):\n{out.stderr}")
    return cls(json.loads(out.stdout))

  def api(self, method, rest, body, ctype):
    return forward(self.api_base, method, rest, body, ctype)

  def data(self, rest):
    out = subprocess.run(["aws", "s3", "cp", f"s3://{self.bucket}/{self.prefix}{rest}", "-", "--only-show-errors"],
                         capture_output=True)  # fmt: skip
    if out.returncode == 0:
      return 200, out.stdout, "application/json"
    err = out.stderr.decode(errors="replace")
    if "404" in err or "NoSuchKey" in err or "Not Found" in err:
      return 404, b"{}", "application/json"
    return 502, json.dumps({"error": err.strip()[:300]}).encode(), "application/json"


def make_proxy(name):
  if name is None:
    return None
  return SandboxProxy.from_terraform() if name == "sandbox" else KampProxy(KAMP[name])


def forward(target, method, rest, body=b"", ctype=None):
  """(status, body, content type) from the real site for <target><rest>: errors pass through."""
  req = urllib.request.Request(f"{target}{rest}", data=body if method == "POST" else None, method=method,
                               headers={"User-Agent": "aoty-serve-shell/1", **({"Content-Type": ctype} if ctype else {})})  # fmt: skip
  try:
    with urllib.request.urlopen(req, timeout=30) as r:
      return r.status, r.read(), r.headers.get("Content-Type", "application/json")
  except urllib.error.HTTPError as e:
    return e.code, e.read(), e.headers.get("Content-Type", "application/json")


def handler_for(base, out, data, proxy=None):
  syncs = {}  # username -> when its pretend sync started

  class Handler(http.server.BaseHTTPRequestHandler):
    def proxied(self, method, rest, body=b""):
      if rest.startswith("data/"):
        status, payload, ctype = proxy.data(rest)
        return self.send(status, payload, ctype, "no-cache")
      status, payload, ctype = proxy.api(method, rest, body, self.headers.get("Content-Type"))
      return self.send(status, payload, ctype, "no-store")

    def api(self, method, rest):
      """A stand-in for the API (AOTY-22): POST sync starts a pretend sync, status follows it."""
      parts = rest.split("/")  # api, users, <name>, sync|status
      if len(parts) != 4 or parts[:2] != ["api", "users"] or parts[3] not in ("sync", "status"):
        return self.send(404, b'{"error":"not found"}', "application/json")
      name, action = parts[2].lower(), parts[3]
      now = time.time()
      special = fake_outcome(name) if action == "sync" and method == "POST" else None
      if special:
        return self.send(special[0], json.dumps(special[1]).encode(), "application/json")
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
      body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
      if path.startswith(f"{base}api/"):
        rest = path[len(base) :]
        return self.proxied("POST", rest, body) if proxy else self.api("POST", rest)
      return self.send(405, b"", "text/plain")

    def do_GET(self):
      path = self.path.split("?", 1)[0]
      if not path.startswith(base):
        return self.send(404, b"not under the base path\n", "text/plain")
      rest = path[len(base) :]
      if proxy and (rest.startswith("api/") or rest.startswith("data/")):
        return self.proxied("GET", rest)
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
  p.add_argument("--proxy", choices=sorted(PROXIES),
                 help="send api/ and data/ to that environment (real users, real syncs); sandbox "
                      "reads data/ from its bucket with your AWS credentials")  # fmt: skip
  args = p.parse_args()
  build_shell.build(args.base, out=OUT)
  if args.sample:
    f = args.data / "users" / args.sample.lower() / "chart.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sample_chart(args.sample)), encoding="utf-8")
    print(f"sample chart: {f}")
  proxy = make_proxy(args.proxy)
  if proxy:
    print(f"api/ and data/ go to {proxy.label}: syncs are real (the API's cooldown still applies)")
  server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args.base, OUT, args.data, proxy))
  who = args.sample or "<name>"
  print(f"serving http://127.0.0.1:{args.port}{args.base}user/{who}  (ctrl-c to stop)")
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    pass


if __name__ == "__main__":
  main()
