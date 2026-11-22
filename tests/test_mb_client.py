"""The mb Lambda's MusicBrainz client (lambdas/common/musicbrainz.py), against a fake urlopen."""

import io
import json
import urllib.error
import urllib.parse

from common import musicbrainz


class Clock:
  """A fake monotonic clock that sleeping advances; each request takes `latency` seconds."""

  def __init__(self, latency=0.0):
    self.t, self.latency, self.sleeps = 0.0, latency, []

  def __call__(self):
    return self.t

  def sleep(self, s):
    self.sleeps.append(s)
    self.t += s


class FakeHTTP:
  """Replays (status, body, headers) responses; records each request."""

  def __init__(self, clock, *responses):
    self.clock, self.responses, self.requests = clock, list(responses), []

  def __call__(self, req, timeout):
    self.requests.append(req)
    self.clock.t += self.clock.latency
    status, body, headers = (*self.responses.pop(0), {})[:3]
    if isinstance(body, Exception):
      raise body
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    if status >= 400:
      raise urllib.error.HTTPError(req.full_url, status, "err", headers, io.BytesIO(raw))
    resp = io.BytesIO(raw)
    resp.status, resp.headers = status, headers
    return resp


def client(clock, http, **kw):
  return musicbrainz.Client("kamp-aoty/test ( x )", urlopen=http, sleep=clock.sleep, clock=clock,
                            rand=lambda lo, hi: hi, **kw)  # fmt: skip


def test_search_names_the_entity_and_returns_its_results():
  clock = Clock()
  http = FakeHTTP(clock, (200, {"release-groups": [{"title": "X"}], "count": 1}))
  assert client(clock, http).search("release-group", 'artist:"A"', 100) == [{"title": "X"}]
  url = urllib.parse.urlsplit(http.requests[0].full_url)
  assert url.path == "/ws/2/release-group/"
  assert urllib.parse.parse_qs(url.query) == {"query": ['artist:"A"'], "limit": ["100"], "fmt": ["json"]}
  assert http.requests[0].get_header("User-agent") == "kamp-aoty/test ( x )"


def test_user_agent_falls_back_to_a_placeholder_contact():
  assert musicbrainz.user_agent("1.2.3", "") == "kamp-aoty/1.2.3 ( contact@example.com )"
  assert musicbrainz.user_agent("1.2.3", "me@x.test") == "kamp-aoty/1.2.3 ( me@x.test )"


def test_503_waits_retry_after_plus_jitter_then_succeeds():
  clock = Clock()
  http = FakeHTTP(clock, (503, b"", {"Retry-After": "3"}), (200, {"releases": []}))
  c = client(clock, http)
  assert c.search("release", "q", 10) == []
  # The first request goes at once; the retry waits Retry-After (3) + the jitter maximum (0.5).
  assert clock.sleeps == [3.5]
  assert c.summary()["throttled"] == 1 and c.summary()["retry_after_s"] == 3.0


def test_retry_after_below_the_interval_is_floored():
  clock = Clock()
  http = FakeHTTP(clock, (503, b"", {"Retry-After": "0"}), (200, {"releases": []}))
  client(clock, http).search("release", "q", 10)
  assert clock.sleeps == [musicbrainz.MIN_INTERVAL + 0.5]


def test_requests_are_paced_at_the_minimum_interval():
  clock = Clock(latency=0.2)
  http = FakeHTTP(clock, (200, {"releases": []}), (200, {"releases": []}))
  c = client(clock, http)
  c.search("release", "a", 10)
  c.search("release", "b", 10)
  assert clock.sleeps == [musicbrainz.MIN_INTERVAL - 0.2]


def test_gives_up_after_max_attempts_and_returns_none():
  clock = Clock()
  http = FakeHTTP(clock, *[(503, b"", {"Retry-After": "1"})] * musicbrainz.MAX_ATTEMPTS)
  c = client(clock, http)
  assert c.search("release-group", "q", 100) is None
  assert len(http.requests) == musicbrainz.MAX_ATTEMPTS


def test_network_errors_are_retried():
  clock = Clock()
  http = FakeHTTP(clock, (200, urllib.error.URLError("reset")), (200, {"release-groups": []}))
  assert client(clock, http).search("release-group", "q", 100) == []
  assert len(http.requests) == 2


def test_other_http_errors_and_bad_json_fail_without_retrying():
  clock = Clock()
  assert client(clock, FakeHTTP(clock, (400, b"too far"))).search("release-group", "q", 100) is None
  assert client(clock, FakeHTTP(clock, (200, b"<html>"))).search("release-group", "q", 100) is None


def test_failure_is_none_not_an_empty_list():
  # Callers cache [] results as confirmed misses; a failure must never look like one.
  clock = Clock()
  assert client(clock, FakeHTTP(clock, (500, b""))).search("release-group", "q", 100) is None


def test_no_attempt_starts_unless_it_can_finish_before_the_deadline():
  clock = Clock()
  http = FakeHTTP(clock, (503, b"", {"Retry-After": "5"}), (200, {"release-groups": []}))
  c = client(clock, http, timeout=10)
  # First attempt: 0 + 0 + 10 <= 12 goes ahead. Retry: 0 + 5.5 + 10 > 12 never starts.
  assert c.search("release-group", "q", 100, deadline=12) is None
  assert len(http.requests) == 1
  assert c.summary()["deadline_stops"] == 1 and c.summary()["requests"] == 1


def test_past_deadline_makes_no_request_at_all():
  clock = Clock()
  http = FakeHTTP(clock)
  assert client(clock, http).search("release-group", "q", 100, deadline=5) is None
  assert http.requests == []
