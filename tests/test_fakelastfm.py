"""The load test's stand-in Last.fm (lambdas/common/fakelastfm.py, AOTY-26), through the real client."""

import urllib.request

import pytest

from common import fakelastfm, lastfm


@pytest.fixture
def fake(monkeypatch):
  monkeypatch.setattr(fakelastfm, "LATENCY_S", 0)
  monkeypatch.setenv("LASTFM_FAKE", "1")
  monkeypatch.setenv("AOTY_ENV", "sandbox")
  return lastfm.Client("KEY", min_interval=0, sleep=lambda s: None)


@pytest.mark.parametrize("env", ["staging", "prod", "", "Sandbox"])
def test_only_sandbox_can_switch_it_on(monkeypatch, env):
  monkeypatch.setenv("LASTFM_FAKE", "1")
  monkeypatch.setenv("AOTY_ENV", env)
  assert not fakelastfm.enabled()
  assert lastfm.Client("KEY")._urlopen is urllib.request.urlopen


def test_off_unless_asked(monkeypatch):
  monkeypatch.delenv("LASTFM_FAKE", raising=False)
  monkeypatch.setenv("AOTY_ENV", "sandbox")
  assert lastfm.Client("KEY")._urlopen is urllib.request.urlopen


def test_load_test_users_exist_and_nobody_else(fake):
  assert fake.user_info("loadtest-0042") == "loadtest-0042"
  assert fake.user_info("alice") is None


def test_charts_are_deterministic_per_user_and_week(fake):
  a = fake.weekly_album_chart("loadtest-1", 1000, 2000)
  assert a == fake.weekly_album_chart("loadtest-1", 1000, 2000)
  assert 1 <= len(a) <= 4 and all(int(r["playcount"]) > 0 for r in a)
  assert {r["name"] for r in a} <= {album for _, album in fakelastfm.ALBUMS}


def test_recent_tracks_are_one_dated_page(fake):
  tracks, pages = fake.recent_tracks("loadtest-1", 1000)
  assert pages == 1 and tracks and all(int(t["date"]["uts"]) >= 1000 for t in tracks)


def test_album_info_has_tracks_and_no_cover(fake):
  assert fake.album_track_count("Load Test Artist 1", "Load Test Album 01") == 10
  assert fake.album_image("Load Test Artist 1", "Load Test Album 01") == ""


def test_every_call_is_counted(fake, capsys):
  fake.user_info("loadtest-1")
  fake.weekly_album_chart("loadtest-1", 0, 1)
  out = capsys.readouterr().out
  assert out.count('"FakeLastfmCalls": 1') == 2 and '"Environment": "sandbox"' in out
