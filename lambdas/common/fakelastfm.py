"""A stand-in Last.fm for the AOTY-26 load test (tools/load_test.py). Sandbox only.

lastfm.Client uses this module's urlopen instead of the network when enabled(): LASTFM_FAKE=1 *and*
AOTY_ENV=sandbox, both checked here, so a stray variable in staging or prod does nothing. The
load test sets LASTFM_FAKE on sandbox's functions for the run and removes it after.

Because it replaces only urlopen, the real client's pacing (0.5 s between calls) and retries
still run. Each call sleeps LATENCY_S, about a real page's time (AOTY-11), so syncs take about
as long as real ones, and emits one EMF line per call (FakeLastfmCalls), so the test can chart
the aggregate request rate across functions minute by minute. It isn't LastfmCalls: the
Lambdas count their own client's calls into that once per run (AOTY-41), which would count
these twice.

What it answers, deterministically per user:
  user.getinfo             loadtest-* users exist, in their own casing; anyone else is error 6
  user.getweeklyalbumchart a few of ALBUMS per week
  user.getrecenttracks     one page of scrobbles from the same albums, without covers
  album.getinfo            ten tracks and no image
The albums are made up, so the mb lane looks each one up once (cached as misses) and the charts
rank nothing: the load test measures the queue, not the charts.
"""

import hashlib
import io
import json
import os
import time
import urllib.parse

LATENCY_S = 0.8
NAMESPACE = "kamp-aoty"
ALBUMS = [(f"Load Test Artist {i % 7}", f"Load Test Album {i:02d}") for i in range(30)]


def enabled():
  return os.environ.get("LASTFM_FAKE") == "1" and os.environ.get("AOTY_ENV") == "sandbox"


def _seed(*parts):
  return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _picks(user, key, n):
  """n album indexes for (user, key), the same on every call."""
  s = _seed(user, key)
  return sorted({(s >> (i * 5)) % len(ALBUMS) for i in range(n)})


def answer(params):
  """The JSON body Last.fm would send for one request's query parameters."""
  method, user = params.get("method", ""), params.get("user", "")
  if method == "user.getinfo":
    if not user.startswith("loadtest-"):
      return {"error": 6, "message": "User not found"}
    return {"user": {"name": user}}
  if method == "user.getweeklyalbumchart":
    frm = int(params.get("from") or 0)
    rows = [{"artist": {"#text": ALBUMS[i][0]}, "name": ALBUMS[i][1], "mbid": "",
             "playcount": str(1 + _seed(user, frm, i) % 9)} for i in _picks(user, frm, 4)]  # fmt: skip
    return {"weeklyalbumchart": {"album": rows}}
  if method == "user.getrecenttracks":
    frm = int(params.get("from") or 0)
    to = int(params.get("to") or time.time())
    tracks = []
    for n, i in enumerate(_picks(user, "recent", 6) * 10):
      uts = max(frm, to - 3600 * (n + 1))
      tracks.append({"artist": {"#text": ALBUMS[i][0]}, "album": {"#text": ALBUMS[i][1], "mbid": ""},
                     "name": f"Track {n}", "image": [], "date": {"uts": str(uts)}})  # fmt: skip
    return {"recenttracks": {"track": tracks, "@attr": {"totalPages": "1", "page": "1"}}}
  if method == "album.getinfo":
    return {"album": {"tracks": {"track": [{"name": f"Track {n}"} for n in range(10)]}, "image": []}}
  return {"error": 3, "message": "Invalid method"}


class _Response(io.BytesIO):
  status = 200

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    self.close()


def urlopen(req, timeout=None, *, sleep=time.sleep):
  """urllib.request.urlopen's shape: a context manager with .status and .read()."""
  params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
  sleep(LATENCY_S)
  _emit(params.get("method", ""))
  return _Response(json.dumps(answer(params)).encode())


def _emit(method):
  print(json.dumps({
    "_aws": {
      "Timestamp": int(time.time() * 1000),
      "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [["Environment"]],
                             "Metrics": [{"Name": "FakeLastfmCalls", "Unit": "Count"}]}],
    },
    "Environment": os.environ.get("AOTY_ENV", ""),
    "FakeLastfmCalls": 1,
    "method": method,
    "function": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", ""),
  }))  # fmt: skip
