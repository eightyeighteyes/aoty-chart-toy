"""The load test's pure parts (tools/load_test.py, AOTY-26)."""

import datetime as dt
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.groups import SLUG_RE
from common.username import USERNAME_RE

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


# ---- groups (AOTY-53) ---------------------------------------------------------------------


def test_the_group_plan_uses_names_the_fake_knows_one_group_per_owner(lt):
  plan = lt.group_plan(10, 25)
  assert len(plan) == 10 and len({owner for _, owner, _ in plan}) == 10
  slug, owner, members = plan[0]
  assert (slug, owner) == ("loadtest-g01", "loadtest-owner-01")
  assert len(members) == 25 and members[0] == "loadtest-g01-m01" and members[-1] == "loadtest-g01-m25"
  every = [owner for _, owner, _ in plan] + [m for _, _, ms in plan for m in ms]
  assert all(n.startswith("loadtest-") for n in every)  # common/fakelastfm.py only knows these
  assert all(USERNAME_RE.match(n) for n in every) and all(SLUG_RE.match(s) for s, _, _ in plan)


def test_the_group_summary_shows_coalescing(lt):
  t = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.UTC)
  minutes = {t: {"chart_duration_ms": 200_000, "card_duration_ms": 30_000}}
  builds = {"charts_built": "290", "groups_built": "40", "groups_coalesced": "210", "slowest_s": "6.5",
            "biggest": "25"}  # fmt: skip
  s = lt.group_summary({"create": {201: 10}, "add": {201: 250}}, builds, minutes)
  assert (s["group_builds"], s["coalesced"], s["coalesced_pct"], s["member_builds"]) == (40, 210, 84, 250)
  assert (s["slowest_group_s"], s["biggest_group"], s["chart_gb_s"], s["card_gb_s"]) == (6.5, 25, 100.0, 30.0)
  text = lt.group_report(SimpleNamespace(groups=10, members=25), s)
  assert "| Group builds / coalesced | 40 / 210 (84% saved) |" in text
  assert "| Member add answers | 201: 250 |" in text
  assert lt.group_report(SimpleNamespace(groups=0, members=25), s) == ""


def test_a_group_summary_with_no_logs_yet_is_zeros(lt):
  s = lt.group_summary({}, {}, {})
  assert (s["group_builds"], s["coalesced_pct"], s["chart_gb_s"]) == (0, 0, 0)
