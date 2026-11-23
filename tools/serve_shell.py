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
                                        broken (503s), private-* (a known private profile),
                                        capacity* (new users paused), too-many (the per-IP limit),
                                        throttled (API Gateway's 429). First visits (AOTY-24):
                                        newbie* (the cover wall, then charts that rank nothing,
                                        then fill in), nothing* (no albums from the year),
                                        caller* (a minute in line first, AOTY-26) and crowded*
                                        (throttled status polls that recover). Other names only
                                        get a chart if --data has one. With
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
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "lambdas"), str(REPO / "lambdas" / "chart"), str(REPO / "lambdas" / "api")]

import api_core  # noqa: E402
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
  # The guardrails' refusals (AOTY-26): the api's own, and API Gateway's throttle.
  "capacity": (503, {"error": "paused"}),
  "too-many": (429, {"error": "too_many_new"}),
  "throttled": (429, {"message": "Too Many Requests"}),
}


def fake_outcome(name):
  """(status, body) for a special preview name, else None. private-* is a known private profile;
  capacity* is new users paused (on the intro and on a chart page)."""
  if name.startswith("nobody"):
    return FAKE_OUTCOMES["nobody"]
  if name.startswith("capacity"):
    return FAKE_OUTCOMES["capacity"]
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
COOLDOWN = 600
# The fake status goes through the real api_core.status(), from users rows like the ones sync
# and the chart builder write, so the preview can't drift from the API.


def _stamp(uts):
  return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(uts))


def fake_status(started, now, chart_file):
  """The status API's answer for a returning user's pretend sync that began at `started` (AOTY-22).

  On reaching idle it stamps the chart's generated_at, so the page sees a newer chart and
  repaints it in place, as it would after a real sync.
  """
  e, s = now - started, int(started)
  q, r, b = FAKE_SYNC_S
  row = {"username": chart_file.parent.name, "sync_requested_at": s, "chart_built_at": s - 3600,
         "chart_plays": 1000, "chart_albums": 120, "chart_pending": 0, "chart_hits": 120}  # fmt: skip
  if e < q:
    row["state"] = "queued"
    return api_core.status(row, now, COOLDOWN, queue_depth=0)
  row.update(state="syncing", sync_started_at=s + q, last_synced_at=s + q)
  row["progress"] = {"weeks_done": min(4, int((e - q) / (r - q) * 4)), "weeks_total": 4, "plays": 1000}
  if e >= r:
    row.update(state="done", sync_finished_at=s + r)
    row["progress"] = {"weeks_done": 4, "weeks_total": 4, "plays": 1042}
  if e >= b:
    row["chart_built_at"], row["chart_plays"] = s + b, 1042
    if chart_file.is_file():
      chart = json.loads(chart_file.read_text(encoding="utf-8"))
      if chart.get("generated_at") != _stamp(s + b):
        chart["generated_at"] = _stamp(s + b)
        chart_file.write_text(json.dumps(chart), encoding="utf-8")
  return api_core.status(row, now, COOLDOWN)


# ---- first visits (AOTY-24) ---------------------------------------------------------------
#
# newbie*   a new user: queued, reading (the cover wall and running stats), building, then
#           release years checked with charts that rank nothing at first and fill in after.
# nothing*  the same, but none of their albums came out this year: it ends on that message.
# Both serve chart.json from the fake too (nothing is written under --data).
FIRST_S = {"queued": 2, "reading": 16, "building": 18, "dating": 40}  # each stage's end
FIRST_WEEKS, FIRST_ALBUMS, FIRST_PLAYS = 47, 640, 21_500
REBUILD_S = 4  # the chart builder's rebuilds while albums are dated


def first_visit_kind(name):
  return next((k for k in ("newbie", "nothing", "caller", "crowded") if name.startswith(k)), None)


# caller*   (AOTY-26) a new user who arrives with syncs backed up: CALLER_S seconds in line, the
#           place counting down from #37 with a measured wait, then the newbie flow.
# crowded*  the newbie flow, but the first CROWDED_429S status polls are throttled (API Gateway's
#           429), so the page's back-off can be seen not to give up.
CALLER_S, CALLER_FROM, CROWDED_429S = 60, 37, 2


def _as_newbie(kind, started):
  """A queue preview continues as newbie*, with its clock shifted past the time in line."""
  if kind == "caller":
    return "newbie", started + CALLER_S - FIRST_S["queued"]
  if kind == "crowded":
    return "newbie", started
  return kind, started


def fake_caller_status(started, now, name):
  """In line: a queued row with a ticket, and queue counters that move it up to #1."""
  e = now - started
  ticket = 1000
  row = {"username": name, "state": "queued", "sync_requested_at": int(started), "queue_ticket": ticket}
  counters = {"started": ticket - CALLER_FROM + int(e * CALLER_FROM / CALLER_S),
              "first_syncs": 25, "first_sync_seconds": 25 * 90}  # fmt: skip
  return api_core.status(row, now, COOLDOWN, queue_depth=CALLER_FROM + 3, queue=counters)


def fake_wall(n=60):
  """Covers for the wall: coloured squares as data: URIs (the CSP allows data:), a few with no
  cover (the artist-name tile) and a few broken ones (the fallback chain, then the tile)."""
  wall = []
  for i in range(n):
    artist, album = f"Sample Artist {i % 23}", f"Recent Album {i:02d}"
    art, fallbacks = "", []
    if i % 9 == 4:
      art = "https://lastfm.freetls.fastly.net/i/u/300x300/doesnotexist.jpg"
    elif i % 11 != 7:
      hue = (i * 47) % 360
      svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><defs><linearGradient id='g'>"
             f"<stop offset='0' stop-color='hsl({hue},60%,45%)'/><stop offset='1' stop-color='hsl({(hue + 60) % 360},70%,30%)'/>"
             "</linearGradient></defs><rect width='10' height='10' fill='url(#g)'/></svg>")  # fmt: skip
      art = "data:image/svg+xml," + urllib.parse.quote(svg)
    wall.append({"artist": artist, "album": album, "art": art, "fallbacks": fallbacks})
  return wall


def _first_hits(kind, e):
  """This-year albums found by e seconds in: none for nothing*, else growing from 4 s into dating."""
  into = e - FIRST_S["building"] - 4
  if kind == "nothing" or into < 0:
    return 0
  return min(120, 6 + int(into * 6))


def _first_built(e):
  """The latest chart build at e seconds: right after building, then every REBUILD_S."""
  b = FIRST_S["building"]
  if e < b:
    return None
  return b + min(int((e - b) // REBUILD_S) * REBUILD_S, FIRST_S["dating"] - b)


def fake_first_status(kind, started, now, name):
  """The status API's answer for a pretend first visit (AOTY-24)."""
  if kind == "caller" and now - started < CALLER_S:
    return fake_caller_status(started, now, name)
  kind, started = _as_newbie(kind, started)
  e, s = now - started, int(started)
  row = {"username": name, "sync_requested_at": s}
  if e < FIRST_S["queued"]:
    row["state"] = "queued"
    return api_core.status(row, now, COOLDOWN, queue_depth=3)
  read = min(1.0, (e - FIRST_S["queued"]) / (FIRST_S["reading"] - FIRST_S["queued"]))
  albums = int(FIRST_ALBUMS * read)
  row.update(state="syncing", sync_started_at=s + FIRST_S["queued"], last_synced_at=s + FIRST_S["queued"])
  row["progress"] = {"weeks_done": int(FIRST_WEEKS * read), "weeks_total": FIRST_WEEKS,
                     "plays": int(FIRST_PLAYS * read), "albums": albums, "top_artist": "Sample Artist 3",
                     "top_album": "Most Played So Far", "top_plays": int(212 * read)}  # fmt: skip
  row["preview"] = json.dumps({"wall": fake_wall()})
  if e >= FIRST_S["reading"]:
    row.update(state="done", sync_finished_at=s + FIRST_S["reading"])
  built = _first_built(e)
  if built is not None:
    at = built  # the chart as of its last build
    dated = min(1.0, (at - FIRST_S["building"]) / (FIRST_S["dating"] - FIRST_S["building"]))
    hits = _first_hits(kind, at)
    row.update(chart_built_at=s + built, chart_albums=FIRST_ALBUMS, chart_hits=hits,
               chart_pending=FIRST_ALBUMS - int(FIRST_ALBUMS * dated), chart_plays=FIRST_PLAYS)  # fmt: skip
    if hits:
      row.pop("preview")  # as the chart builder does once a chart ranks anything
  return api_core.status(row, now, COOLDOWN)


def fake_first_chart(kind, started, now, name):
  """chart.json for a pretend first visit: None (404) before the first build, then a chart
  stamped with its build time that ranks this year's albums found so far."""
  kind, started = _as_newbie(kind, started)
  built = _first_built(now - started)
  if built is None:
    return None
  chart = sample_chart(name, n=_first_hits(kind, built))
  chart["generated_at"] = _stamp(int(started) + built)
  chart["pending"] = FIRST_ALBUMS - int(FIRST_ALBUMS * min(1.0, (built - FIRST_S["building"]) / (FIRST_S["dating"] - FIRST_S["building"])))
  return chart


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
  polls = {}  # username -> status polls so far (crowded*'s throttled ones)

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
      def status_for(name):
        kind = first_visit_kind(name)
        if kind:
          return fake_first_status(kind, syncs[name], now, name)
        return fake_status(syncs[name], now, data / "users" / name / "chart.json")

      if action == "sync" and method == "POST":
        fresh = name not in syncs or now - syncs[name] > COOLDOWN
        if fresh:
          syncs[name] = now
        status = status_for(name)
        if fresh:
          body = {"queued": True, "status": status}
        else:
          body = {"queued": False, "reason": "in_progress" if status["stage"] != "idle" else "cooldown",
                  "status": status}  # fmt: skip
        return self.send(202 if fresh else 200, json.dumps(body).encode(), "application/json")
      if action == "status" and method == "GET":
        if name not in syncs:
          return self.send(404, b'{"error":"unknown user"}', "application/json")
        polls[name] = polls.get(name, 0) + 1
        if first_visit_kind(name) == "crowded" and polls[name] <= CROWDED_429S:
          code, body = FAKE_OUTCOMES["throttled"]
          return self.send(code, json.dumps(body).encode(), "application/json")
        return self.send(200, json.dumps(status_for(name)).encode(), "application/json")
      return self.send(405, b'{"error":"method not allowed"}', "application/json")

    def first_visit_chart(self, rest):
      """Serve chart.json for newbie* / nothing* from the fake: True if this path was one."""
      parts = rest.split("/")  # data, users, <name>, chart.json
      if len(parts) != 4 or parts[3] != "chart.json" or not first_visit_kind(parts[2].lower()):
        return False
      name = parts[2].lower()
      chart = fake_first_chart(first_visit_kind(name), syncs[name], time.time(), name) if name in syncs else None
      self.send(*((200, json.dumps(chart).encode()) if chart else (404, b"{}")), "application/json", "no-cache")
      return True

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
        if self.first_visit_chart(rest):
          return None
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
