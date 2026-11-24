"""A small Last.fm client for the Lambdas: standard library only, paced and retried.

Behaviour comes from the AOTY-11 spike (tools/lastfm_spike.py):
- No error-29 rate limits at 4 req/s from one client. The sync event source mapping runs at
  most 2 workers, so 0.5 s per call keeps the aggregate at or under 4 req/s.
- Error 6 is "User not found"; 17 is the expected code for a private account (unverified).
- getWeeklyAlbumChart takes arbitrary from/to (undocumented) and caps a response at about
  1,000 album rows; the caller splits ranges near the cap (sync/core.py).
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from common import covers

API = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "kamp-aoty/0.1 (+https://kamp.fm/toys/aoty/)"
MIN_INTERVAL = 0.5
MAX_ATTEMPTS = 5
TIMEOUT = 30
# Operation failed, service offline, temporarily unavailable, rate limited.
RETRY_CODES = {8, 11, 16, 29}
NOT_FOUND = 6
PRIVATE = 17
# Answers about the user or album, not failures: they never count as LastfmErrors.
EXPECTED_CODES = {NOT_FOUND, PRIVATE}
# auth.getSession's answers about the token itself: invalid (4), not authorized (14), expired (15).
# Anyone can send a callback a made-up token, so these must not count either, or they could trip
# the lastfm-errors alarm at will. 13 (bad signature) still counts: that's our secret.
TOKEN_CODES = {4, 14, 15}
NAMESPACE = "kamp-aoty"


class LastfmError(Exception):
  def __init__(self, code, message=""):
    super().__init__(f"Last.fm error {code}: {message}")
    self.code = code


def as_list(v):
  """Last.fm returns a single item as a bare object instead of a one-item list."""
  if v is None:
    return []
  return [v] if isinstance(v, dict) else list(v)


def _default_urlopen():
  """The network, or the load test's stand-in when it's switched on in sandbox (AOTY-26)."""
  from common import fakelastfm

  return fakelastfm.urlopen if fakelastfm.enabled() else urllib.request.urlopen


class Client:
  def __init__(self, api_key, min_interval=MIN_INTERVAL, max_attempts=MAX_ATTEMPTS, *,
               urlopen=None, sleep=time.sleep, clock=time.monotonic, timeout=TIMEOUT):  # fmt: skip
    self.api_key = api_key
    self.min_interval = min_interval
    self.max_attempts = max_attempts
    self.timeout = timeout  # per attempt; the API uses a short one inside API Gateway's 30 s
    self._urlopen, self._sleep, self._clock = urlopen or _default_urlopen(), sleep, clock
    self._last = None
    self.calls = 0  # HTTP attempts, for logging

  def _pace(self):
    if self._last is not None:
      wait = self._last + self.min_interval - self._clock()
      if wait > 0:
        self._sleep(wait)
    self._last = self._clock()

  def _get(self, params):
    """One attempt: (json or None, http status)."""
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    self.calls += 1
    try:
      with self._urlopen(req, timeout=self.timeout) as r:
        status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
      # Last.fm sends API errors (bad user, private) with a 4xx and a JSON body.
      status, body = e.code, e.read()
    try:
      return json.loads(body), status
    except ValueError:
      return None, status

  def call(self, method, **params):
    return self._call_counted(method, params, None, EXPECTED_CODES)

  def signed_call(self, method, secret, expected=EXPECTED_CODES, **params):
    """A call carrying api_sig, for the auth methods. Errors never include the params.
    expected: error codes that are answers, not failures (not counted as LastfmErrors)."""
    return self._call_counted(method, params, secret, expected)

  def _call_counted(self, method, params, secret, expected):
    try:
      return self._call(method, params, secret)
    except LastfmError as e:
      if e.code not in expected:
        _emit_error(method, e.code)
      raise

  def _call(self, method, params, secret=None):
    q = {"method": method, "api_key": self.api_key, **params}
    if secret is not None:
      q["api_sig"] = api_sig(q, secret)
    q["format"] = "json"
    error = None
    for attempt in range(1, self.max_attempts + 1):
      self._pace()
      try:
        data, status = self._get(q)
      except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        error = LastfmError("net", str(e))
      else:
        if isinstance(data, dict) and "error" in data:
          error = LastfmError(data["error"], data.get("message", ""))
          if data["error"] not in RETRY_CODES:
            raise error
        elif status >= 500 or data is None:
          error = LastfmError(status, "server error or unreadable body")
        elif status >= 400:
          raise LastfmError(status, "http error")
        else:
          return data
      if attempt < self.max_attempts:
        self._sleep(2 * attempt)
    raise error

  def weekly_album_chart(self, user, frm, to):
    """Album rows for [frm, to): artist (album artist), name, mbid, playcount."""
    data = self.call("user.getweeklyalbumchart", user=user, **{"from": frm, "to": to})
    return as_list(data.get("weeklyalbumchart", {}).get("album"))

  def recent_tracks(self, user, frm, page=1, limit=200, to=None):
    """(tracks, total_pages), newest first. Includes the now-playing row, which has no date."""
    window = {"from": frm} if to is None else {"from": frm, "to": to}
    data = self.call("user.getrecenttracks", user=user, page=page, limit=limit, **window)
    rt = data.get("recenttracks", {})
    total = int((rt.get("@attr") or {}).get("totalPages") or 0)
    return as_list(rt.get("track")), total

  def album_track_count(self, artist, album):
    """Length of Last.fm's tracklist, or None when it has none (often) or no such album.

    The mb lane's fallback when MusicBrainz has no matching release (AOTY-19), as in M0's
    build_page.lastfm_track_count. Unlike M0, a failed request raises instead of returning
    None, so the caller can tell "no count" (cached) from "try again later" (not cached).
    """
    try:
      data = self.call("album.getinfo", artist=artist, album=album, autocorrect=1)
    except LastfmError as e:
      if e.code == NOT_FOUND:  # "Album not found" shares the code with "User not found"
        return None
      raise
    tracks = (data.get("album") or {}).get("tracks") or {}
    tracks = tracks.get("track") if isinstance(tracks, dict) else None
    return len(as_list(tracks)) or None

  def album_image(self, artist, album):
    """album.getInfo's largest cover URL, or "" when Last.fm has none or no such album.

    The chart builder's fallback for albums whose scrobbles carried no cover (AOTY-21). A failed
    request raises, so the caller can tell "no cover" (cached) from "try again" (not cached).
    """
    try:
      data = self.call("album.getinfo", artist=artist, album=album, autocorrect=1)
    except LastfmError as e:
      if e.code == NOT_FOUND:
        return ""
      raise
    return covers.image_url((data.get("album") or {}).get("image"))

  def user_info(self, user):
    """The user's name in Last.fm's own casing, or None on a definite "User not found".

    Other errors propagate. The chart page redirects to this casing (AOTY-21).
    """
    try:
      data = self.call("user.getinfo", user=user)
    except LastfmError as e:
      if e.code == NOT_FOUND:
        return None
      raise
    return (data.get("user") or {}).get("name") or user

  def user_exists(self, user):
    """False only on a definite "User not found"; other errors propagate."""
    return self.user_info(user) is not None

  def auth_get_session(self, token, secret):
    """The lowercased username a web-auth token belongs to (sign-in, AOTY-47), or None when
    Last.fm's answer has no name. Last.fm's session key is dropped here: nothing stores it.
    A bad, used or expired token raises LastfmError (codes 4, 14, 15)."""
    data = self.signed_call("auth.getSession", secret, EXPECTED_CODES | TOKEN_CODES, token=token)
    name = (data.get("session") or {}).get("name")
    return name.lower() if isinstance(name, str) and name else None


def api_sig(params, secret):
  """Last.fm's request signature: md5 of every param's name and value, sorted by name, then the
  shared secret. format and callback aren't signed (Last.fm's auth spec)."""
  text = "".join(f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback"))
  return hashlib.md5((text + secret).encode("utf-8")).hexdigest()  # noqa: S324 (Last.fm's scheme)


def _emit_error(method, code):
  """One EMF line per failed call (after its retries), for the Last.fm error alarm (AOTY-27).

  Only inside a Lambda, so tests and laptop tools don't print metric lines. AOTY-41 adds a
  LastfmCalls count beside it for a true error rate.
  """
  if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    return
  print(json.dumps({
    "_aws": {
      "Timestamp": int(time.time() * 1000),
      "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [["Environment"]],
                             "Metrics": [{"Name": "LastfmErrors", "Unit": "Count"}]}],
    },
    "Environment": os.environ.get("AOTY_ENV", ""),
    "LastfmErrors": 1,
    "method": method,
    "code": str(code),
    "function": os.environ["AWS_LAMBDA_FUNCTION_NAME"],
  }))  # fmt: skip


_api_key = {}


def api_key_from_ssm(ssm, name):
  """The Last.fm key from SSM (SecureString), fetched once per warm container."""
  if name not in _api_key:
    _api_key[name] = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
  return _api_key[name]
