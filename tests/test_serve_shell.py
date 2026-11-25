"""The preview server's fake first visits (tools/serve_shell.py, AOTY-24) follow the real pipeline."""

import importlib.util
from pathlib import Path

import pytest

SERVE = Path(__file__).resolve().parent.parent / "tools" / "serve_shell.py"
T0 = 1_795_400_000


@pytest.fixture(scope="module")
def ss():
  spec = importlib.util.spec_from_file_location("serve_shell", SERVE)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def timeline(ss, kind, until=60):
  return [(e, ss.fake_first_status(kind, T0, T0 + e, f"{kind}-1")) for e in range(until)]


def stages(statuses):
  out = []
  for _, s in statuses:
    if not out or out[-1] != s["stage"]:
      out.append(s["stage"])
  return out


def test_a_newbie_walks_every_stage_and_the_wall_goes_once_the_chart_ranks(ss):
  statuses = timeline(ss, "newbie")
  assert stages(statuses) == ["queued", "reading", "building", "dating", "idle"]
  reading = [s for _, s in statuses if s["stage"] == "reading"]
  assert all(len(s["wall"]) == 60 for s in reading)
  assert "so_far" not in reading[0]  # nothing read yet
  assert reading[-1]["so_far"]["top"]["plays"] > reading[1]["so_far"]["top"]["plays"]
  first_dating = next(s for _, s in statuses if s["stage"] == "dating")
  assert first_dating["albums"]["hits"] == 0 and "wall" in first_dating
  assert "wall" not in statuses[-1][1] and statuses[-1][1]["albums"]["hits"] > 0


def test_a_newbie_chart_ranks_nothing_at_first_then_fills_in(ss):
  assert ss.fake_first_chart("newbie", T0, T0 + 5, "newbie-1") is None
  first = ss.fake_first_chart("newbie", T0, T0 + ss.FIRST_S["building"], "newbie-1")
  assert first["rankings"]["album"] == [] and first["username"] == "newbie-1"
  later = ss.fake_first_chart("newbie", T0, T0 + 50, "newbie-1")
  assert later["rankings"]["album"] and later["generated_at"] > first["generated_at"]


def test_nothing_ends_idle_with_every_album_checked_and_no_hits(ss):
  statuses = timeline(ss, "nothing")
  assert stages(statuses) == ["queued", "reading", "building", "dating", "idle"]
  end = statuses[-1][1]
  assert end["albums"] == {"checked": ss.FIRST_ALBUMS, "total": ss.FIRST_ALBUMS, "hits": 0}
  assert ss.fake_first_chart("nothing", T0, T0 + 50, "nothing-1")["rankings"]["album"] == []


def test_the_returning_preview_still_walks_its_stages(ss, tmp_path):
  chart = tmp_path / "alice" / "chart.json"
  seen = [ss.fake_status(T0, T0 + e, chart)["stage"] for e in range(15)]
  assert seen[0] == "queued" and "reading" in seen and "building" in seen and seen[-1] == "idle"
  assert ss.fake_status(T0, T0 + 10, chart)["new_plays"] == 42


def test_a_caller_waits_in_line_counting_down_then_goes_on_as_a_newbie(ss):
  statuses = timeline(ss, "caller", until=ss.CALLER_S + 60)
  queued = [s for e, s in statuses if e < ss.CALLER_S]
  assert all(s["stage"] == "queued" for s in queued)
  places = [s["queue"]["position"] for s in queued]
  assert places[0] == ss.CALLER_FROM and places[-1] == 1 and places == sorted(places, reverse=True)
  assert all(s["queue"]["wait_s"] > 0 for s in queued)
  assert stages(statuses) == ["queued", "reading", "building", "dating", "idle"]
  assert "queue" not in statuses[-1][1]
  assert ss.fake_first_chart("caller", T0, T0 + ss.CALLER_S, "caller-1") is None


def test_refusal_names_answer_like_the_api(ss):
  assert ss.fake_outcome("capacity-alice") == (503, {"error": "paused"})
  assert ss.fake_outcome("too-many") == (429, {"error": "too_many_new"})
  assert ss.fake_outcome("throttled")[0] == 429
  assert ss.first_visit_kind("crowded-1") == "crowded"
