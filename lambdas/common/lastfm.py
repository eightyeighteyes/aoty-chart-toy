"""A small Last.fm client for the Lambdas: standard library only, paced and retried.

Behaviour comes from the AOTY-11 spike (tools/lastfm_spike.py):
- No error-29 rate limits at 4 req/s from one client. The sync event source mapping runs at
  most 2 workers, so 0.5 s per call keeps the aggregate at or under 4 req/s.
- Error 6 is "User not found"; 17 is the expected code for a private account (unverified).
- getWeeklyAlbumChart takes arbitrary from/to (undocumented) and caps a response at about
  1,000 album rows; the caller splits ranges near the cap (sync/core.py).
"""

import json
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
    q = {"method": method, "api_key": self.api_key, "format": "json", **params}
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


_api_key = {}


def api_key_from_ssm(ssm, name):
  """The Last.fm key from SSM (SecureString), fetched once per warm container."""
  if name not in _api_key:
    _api_key[name] = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
  return _api_key[name]
