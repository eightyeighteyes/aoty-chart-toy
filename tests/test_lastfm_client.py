"""The Lambdas' Last.fm client (lambdas/common/lastfm.py), against a fake urlopen."""

import io
import json
import urllib.error
import urllib.parse

import pytest

from common import lastfm


class FakeHTTP:
  """Replays (status, body) responses; records each request's query."""

  def __init__(self, *responses):
    self.responses = list(responses)
    self.queries = []

  def __call__(self, req, timeout):
    self.queries.append(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query)))
    status, body = self.responses.pop(0)
    if isinstance(body, Exception):
      raise body
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    if status >= 400:
      raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(raw))
    resp = io.BytesIO(raw)
    resp.status = status
    return resp


def client(http, sleeps=None):
  sleeps = [] if sleeps is None else sleeps
  return lastfm.Client("KEY", urlopen=http, sleep=sleeps.append, clock=lambda: 0.0)


def test_single_album_object_is_wrapped_in_a_list():
  http = FakeHTTP((200, {"weeklyalbumchart": {"album": {"name": "Solo", "playcount": "3"}}}))
  assert client(http).weekly_album_chart("alice", 10, 20) == [{"name": "Solo", "playcount": "3"}]
  assert http.queries[0]["from"] == "10" and http.queries[0]["to"] == "20"
  assert http.queries[0]["method"] == "user.getweeklyalbumchart"


def test_retryable_codes_server_errors_and_network_errors_are_retried():
  ok = {"recenttracks": {"track": [], "@attr": {"totalPages": "4"}}}
  http = FakeHTTP(
    (200, {"error": 29, "message": "rate limited"}),
    (503, b"<html>busy</html>"),
    (200, urllib.error.URLError("reset")),
    (200, ok),
  )
  sleeps = []
  assert client(http, sleeps).recent_tracks("alice", 0) == ([], 4)
  assert len(http.queries) == 4
  assert [s for s in sleeps if s >= 2] == [2, 4, 6]  # back-off between attempts


def test_user_errors_raise_at_once_with_their_code():
  http = FakeHTTP((404, {"error": 6, "message": "User not found"}))
  with pytest.raises(lastfm.LastfmError) as e:
    client(http).weekly_album_chart("nobody", 0, 1)
  assert e.value.code == lastfm.NOT_FOUND
  assert len(http.queries) == 1


def test_giving_up_raises_the_last_error():
  http = FakeHTTP(*[(200, {"error": 16, "message": "temporary"})] * lastfm.MAX_ATTEMPTS)
  with pytest.raises(lastfm.LastfmError) as e:
    client(http).recent_tracks("alice", 0)
  assert e.value.code == 16


def test_user_exists_is_false_only_for_not_found():
  assert client(FakeHTTP((200, {"user": {"name": "alice"}}))).user_exists("alice")
  assert not client(FakeHTTP((404, {"error": 6, "message": "User not found"}))).user_exists("x")
  with pytest.raises(lastfm.LastfmError):
    client(FakeHTTP((403, {"error": 17, "message": "private"}))).user_exists("x")


def test_user_info_returns_lastfms_casing():
  assert client(FakeHTTP((200, {"user": {"name": "AliCe"}}))).user_info("alice") == "AliCe"
  assert client(FakeHTTP((200, {"user": {}}))).user_info("alice") == "alice"
  assert client(FakeHTTP((404, {"error": 6, "message": "User not found"}))).user_info("x") is None


def test_album_image_takes_the_largest_real_cover():
  sizes = [{"size": "small", "#text": "https://i/s.jpg"}, {"size": "extralarge", "#text": "https://i/xl.jpg"},
           {"size": "mega", "#text": ""}]  # fmt: skip
  assert client(FakeHTTP((200, {"album": {"image": sizes}}))).album_image("A", "X") == "https://i/xl.jpg"
  star = [{"size": "large", "#text": "https://i/2a96cbd8b46e442fc41c2b86b821562f.png"}]
  assert client(FakeHTTP((200, {"album": {"image": star}}))).album_image("A", "X") == ""
  assert client(FakeHTTP((200, {"album": {}}))).album_image("A", "X") == ""
  assert client(FakeHTTP((404, {"error": 6, "message": "Album not found"}))).album_image("A", "X") == ""
  with pytest.raises(lastfm.LastfmError):
    client(FakeHTTP((403, {"error": 10, "message": "bad key"}))).album_image("A", "X")


def test_album_track_count_handles_lists_bare_objects_and_empty_tracklists():
  def count(body):
    return client(FakeHTTP((200, body))).album_track_count("A", "X")

  assert count({"album": {"tracks": {"track": [{"name": "1"}, {"name": "2"}]}}}) == 2
  assert count({"album": {"tracks": {"track": {"name": "only"}}}}) == 1  # one track: a bare object
  assert count({"album": {"tracks": ""}}) is None
  assert count({"album": {}}) is None


def test_album_track_count_not_found_is_none_but_other_errors_raise():
  assert (
    client(FakeHTTP((200, {"error": 6, "message": "Album not found"}))).album_track_count("A", "X") is None
  )
  with pytest.raises(lastfm.LastfmError):
    client(FakeHTTP((200, {"error": 10, "message": "bad key"}))).album_track_count("A", "X")


def test_calls_are_paced():
  t = [0.0]
  sleeps = []

  def sleep(s):
    sleeps.append(s)
    t[0] += s

  http = FakeHTTP((200, {"user": {}}), (200, {"user": {}}))
  c = lastfm.Client("KEY", min_interval=0.5, urlopen=http, sleep=sleep, clock=lambda: t[0])
  c.user_exists("a")
  c.user_exists("b")
  assert sleeps == [0.5]
