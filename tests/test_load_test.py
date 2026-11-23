"""The load test's pure parts (tools/load_test.py, AOTY-26)."""

import datetime as dt
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "load_test.py"


@pytest.fixture(scope="module")
def lt():
  spec = importlib.util.spec_from_file_location("load_test", TOOL)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_the_fake_switch_keeps_every_other_variable(lt):
  env = {"AOTY_ENV": "sandbox", "BASE_PATH": "/x/"}
  on = lt.with_fake(env, True)
  assert on == {**env, "LASTFM_FAKE": "1"}
  assert lt.with_fake(on, False) == env
  assert lt.with_fake(None, False) == {}


def test_restoring_deletes_what_wasnt_there(lt):
  assert lt.restore_plan({"max_queue_for_new": "50"}) == [
    ("new_users_per_ip_hour", None),
    ("max_queue_for_new", "50"),
  ]


def test_usernames_are_load_test_names(lt):
  assert lt.usernames(3) == ["loadtest-0001", "loadtest-0002", "loadtest-0003"]


def test_summary_and_report(lt):
  t = dt.datetime(2026, 11, 23, 12, 0, tzinfo=dt.UTC)
  minutes = {
    t: {"sync_concurrency": 2, "lastfm_calls": 240, "sync_invocations": 3, "sync_duration_ms": 120_000},
    t + dt.timedelta(minutes=1): {"sync_concurrency": 2, "lastfm_calls": 180, "oldest_age_s": 400},
  }
  s = lt.summarize(minutes)
  assert s["peak_sync_concurrency"] == 2 and s["peak_lastfm_per_s"] == 4.0 and s["mean_lastfm_per_s"] == 3.5
  assert s["sync_gb_s"] == 60.0 and s["peak_oldest_message_s"] == 400
  text = lt.report({"environment": "sandbox"}, SimpleNamespace(users=2, rate=1.5),
                   {"loadtest-0001": 202, "loadtest-0002": 202}, [(t, 2)], [(t, "loadtest-0001", "queued", 2)],
                   minutes, t, t + dt.timedelta(minutes=5))  # fmt: skip
  assert "| POST answers | 202: 2 |" in text and "| Peak sync queue depth | 2 |" in text
  assert "| 12:00:00 | loadtest-0001 | queued | 2 |" in text


def test_an_empty_run_still_summarizes(lt):
  assert lt.summarize({})["peak_lastfm_per_s"] == 0
