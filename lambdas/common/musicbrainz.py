"""A MusicBrainz web-service client for the mb Lambda: standard library only, paced and retried.

The same behaviour as M0's aoty.mb_get, which the AOTY-10 and AOTY-12 benchmarks tuned
(CLAUDE.md, "MusicBrainz — hard-won lessons"):
- The URL must name the entity (/ws/2/release-group/); a bare /ws/2/ redirects to a page with
  zero results.
- At most one request per MIN_INTERVAL. A 503 means throttled: wait its Retry-After (at least
  MIN_INTERVAL) plus up to JITTER, for up to MAX_ATTEMPTS tries. About half of all requests get
  a 503 whatever our pacing, so 8 tries are routine, not a sign of trouble.
- A failure returns None, never an empty result: callers must not cache it.

Unlike M0, every call takes a deadline (on the client's clock). An attempt that might not
finish before it is never started, so a Lambda can't run past its timeout mid-lookup. One
client per invocation, strictly sequential: this is the one MusicBrainz client (AOTY-19).
"""

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://musicbrainz.org/ws/2/"
MIN_INTERVAL = 1.1
MAX_ATTEMPTS = 8
JITTER = 0.5
TIMEOUT = 10  # seconds per attempt; MusicBrainz's server p95 is ~1.4 s


def user_agent(version, contact):
  """MusicBrainz asks for an application name, version and a way to reach the operator."""
  return f"kamp-aoty/{version} ( {contact or 'contact@example.com'} )"


class Client:
  def __init__(self, agent, *, min_interval=MIN_INTERVAL, max_attempts=MAX_ATTEMPTS, jitter=JITTER,
               timeout=TIMEOUT, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic,
               rand=random.uniform):  # fmt: skip
    self.agent = agent
    self.min_interval, self.max_attempts, self.jitter, self.timeout = min_interval, max_attempts, jitter, timeout
    self._urlopen, self._sleep, self._clock, self._rand = urlopen, sleep, clock, rand
    self._last = None
    self.stats = []  # one dict per attempt: status, slept, elapsed, retry_after

  def search(self, entity, query, limit, deadline=None):
    """The result list of one search (e.g. release groups), or None on failure."""
    body = self.get(entity, {"query": query, "limit": limit}, deadline)
    return None if body is None else body.get(entity + "s", [])

  def get(self, entity, params, deadline=None):
    """One web-service GET: the JSON body, or None on failure (out of time included)."""
    url = f"{API}{entity}/?{urllib.parse.urlencode({**params, 'fmt': 'json'})}"
    retry_after = self.min_interval
    for attempt in range(self.max_attempts):
      gap = self.min_interval if attempt == 0 else max(self.min_interval, retry_after) + self._rand(0, self.jitter)
      wait = 0.0 if self._last is None else max(self._last + gap - self._clock(), 0.0)
      if deadline is not None and self._clock() + wait + self.timeout > deadline:
        self.stats.append({"status": "deadline", "slept": 0.0, "elapsed": 0.0, "retry_after": 0.0})
        return None
      if wait:
        self._sleep(wait)
      self._last = self._clock()
      stat = {"status": "error", "slept": wait, "elapsed": 0.0, "retry_after": 0.0}
      self.stats.append(stat)
      status, headers, body = self._fetch(url)
      stat["elapsed"] = self._clock() - self._last
      stat["status"] = status
      if status == 503:
        retry_after = _seconds(headers.get("Retry-After"), self.min_interval)
        stat["retry_after"] = retry_after  # what MusicBrainz asked for (the gap floors it)
        continue
      if status is None:
        continue  # network error: retry like a 503
      if status != 200:
        return None
      try:
        return json.loads(body)
      except ValueError:
        return None
    return None

  def _fetch(self, url):
    """(status or None on a network error, headers, body)."""
    req = urllib.request.Request(url, headers={"User-Agent": self.agent, "Accept": "application/json"})
    try:
      with self._urlopen(req, timeout=self.timeout) as r:
        return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
      return e.code, e.headers or {}, b""
    except (urllib.error.URLError, TimeoutError, ConnectionError):
      return None, {}, b""

  def summary(self):
    """Totals for the run's metrics."""
    sent = [s for s in self.stats if s["status"] != "deadline"]
    return {
      "requests": len(sent),
      "throttled": sum(1 for s in sent if s["status"] == 503),
      "errors": sum(1 for s in sent if s["status"] not in (200, 503)),
      "retry_after_s": sum(s["retry_after"] for s in sent),
      "slept_s": sum(s["slept"] for s in sent),
      "server_s": sum(s["elapsed"] for s in sent),
      "deadline_stops": len(self.stats) - len(sent),
    }


def _seconds(value, default):
  """A Retry-After header in seconds; the HTTP-date form and junk fall back to the default."""
  try:
    return max(float(value), 0.0)
  except (TypeError, ValueError):
    return default
