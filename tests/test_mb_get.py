"""mb_get / mb_search: paging body, 503 retry, failure is None (never an empty page)."""

import pytest

import aoty


class FakeResponse:
  def __init__(self, status, body=None, headers=None):
    self.status_code = status
    self._body = body
    self.headers = headers or {}

  def json(self):
    if self._body is None:
      raise ValueError("no JSON")
    return self._body


@pytest.fixture
def fake_mb(monkeypatch):
  """Replay a list of responses; record each request's params."""
  calls = []

  def install(*responses):
    queue = list(responses)

    def get(url, params, headers, timeout):
      calls.append({"url": url, **params})
      return queue.pop(0)

    monkeypatch.setattr(aoty.requests, "get", get)
    monkeypatch.setattr(aoty.time, "sleep", lambda s: None)
    aoty.reset_run_state()
    return calls

  return install


PAGE = {"count": 250, "offset": 100, "release-groups": [{"id": "a"}, {"id": "b"}]}


def test_mb_get_returns_whole_body_with_count_and_offset(fake_mb):
  calls = fake_mb(FakeResponse(200, PAGE))
  body = aoty.mb_get("release-group", {"query": "q", "limit": 100, "offset": 100})
  assert body == PAGE
  assert calls[0]["url"] == "https://musicbrainz.org/ws/2/release-group/"
  assert calls[0]["offset"] == 100 and calls[0]["fmt"] == "json"
  assert aoty.MB_STATS[-1]["results"] == 2


def test_mb_search_still_returns_the_result_list(fake_mb):
  fake_mb(FakeResponse(200, PAGE))
  assert aoty.mb_search("q", limit=100) == PAGE["release-groups"]


def test_503_is_retried_then_succeeds(fake_mb):
  calls = fake_mb(FakeResponse(503, headers={"Retry-After": "1"}), FakeResponse(200, PAGE))
  assert aoty.mb_get("release-group", {"query": "q", "limit": 100}) == PAGE
  assert len(calls) == 2
  assert [s["status"] for s in aoty.MB_STATS] == [503, 200]


def test_giving_up_is_none_not_an_empty_page(fake_mb, monkeypatch):
  monkeypatch.setattr(aoty, "MB_MAX_ATTEMPTS", 3)
  fake_mb(*[FakeResponse(503, headers={"Retry-After": "1"})] * 3)
  assert aoty.mb_get("release-group", {"query": "q", "limit": 100}) is None
  assert len(aoty.MB_STATS) == 3


def test_non_200_and_bad_json_are_none(fake_mb):
  fake_mb(FakeResponse(400, {}), FakeResponse(200, None))
  assert aoty.mb_get("release-group", {"query": "q"}) is None
  assert aoty.mb_search("q", limit=1) is None
