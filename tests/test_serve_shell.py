"""The preview server's fake first visits (tools/serve_shell.py, AOTY-24) follow the real pipeline."""

import importlib.util
import re
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


def test_the_preview_theme_route_answers_like_the_api(ss, tmp_path):
  import json

  f = tmp_path / "users" / "alice" / "chart.json"
  f.parent.mkdir(parents=True)
  f.write_text(json.dumps(ss.sample_chart("alice")))

  def theme(method, name, body, signed_in):
    return ss.fake_style(method, name, body, signed_in, tmp_path, "theme")

  ok = theme("POST", "alice", {"theme": "beach-house"}, "alice")
  assert ok[0] == 200 and json.loads(f.read_text())["theme"] == "beach-house"
  theme("POST", "alice", {"theme": "poster"}, "alice")
  assert "theme" not in json.loads(f.read_text())
  assert theme("POST", "alice", {"theme": "kamp"}, None)[0] == 401
  assert theme("POST", "alice", {"theme": "kamp"}, "bob")[0] == 403
  assert theme("POST", "alice", {"theme": "nope"}, "alice")[0] == 400
  assert theme("POST", "carol", {"theme": "kamp"}, "carol")[0] == 404
  assert theme("GET", "alice", {}, "alice")[0] == 405


def test_the_preview_background_route_answers_like_the_api(ss, tmp_path):
  # AOTY-96: the sample starts with borealis, as a new page does; none takes it away.
  import json

  f = tmp_path / "users" / "alice" / "chart.json"
  f.parent.mkdir(parents=True)
  f.write_text(json.dumps(ss.sample_chart("alice")))
  assert json.loads(f.read_text())["background"] == "borealis"
  ok = ss.fake_style("POST", "alice", {"background": "none"}, "alice", tmp_path, "background")
  assert ok[0] == 200 and "background" not in json.loads(f.read_text())
  assert ss.fake_style("POST", "alice", {"background": "nope"}, "alice", tmp_path, "background")[0] == 400
  assert ss.fake_style("POST", "alice", {"background": "none"}, "bob", tmp_path, "background")[0] == 403


def test_no_image_on_the_page_comes_from_a_blob_url_which_the_csp_refuses(ss):
  # AOTY-106: kamp.fm's img-src (mirrored here) allows data: but not blob:, so the share image's
  # preview showed its alt text. The one blob URL left is the download's <a href>, which img-src
  # doesn't cover. A new one needs a reason, or kamp-site's CSP changing first.
  img_src = next(p for p in ss.CSP.split(";") if p.strip().startswith("img-src")).split()
  assert "data:" in img_src and "blob:" not in img_src
  app = (Path(__file__).resolve().parent.parent / "web" / "src" / "app.js").read_text()
  uses = [m.start() for m in re.finditer(r"URL\.createObjectURL\(", app)]
  assert len(uses) == 1, "a new blob: URL in app.js: the CSP refuses it as an image"
  assert "a.download = file.name" in app[uses[0] : uses[0] + 300]  # the download's, nothing else


def test_the_preview_wall_mixes_ready_slow_broken_and_missing_covers(ss):
  wall = ss.fake_wall()
  arts = [w["art"] for w in wall]
  assert any(a.startswith("data:image/svg+xml") for a in arts)
  assert any(
    a.startswith("../preview/slow-cover?") for a in arts
  )  # resolves to <base>preview/... from user/<name>
  assert any("doesnotexist" in a for a in arts) and "" in arts
  # Sized SVGs: Firefox gives a viewBox-only SVG naturalWidth 0, which the wall's loader drops.
  assert all("width='300'" in ss.urllib.parse.unquote(a) for a in arts if a.startswith("data:"))


def test_slow_covers_are_capped_and_tolerate_junk(ss):
  assert ss.slow_cover("ms=1500&hue=40")[0] == 1.5
  assert ss.slow_cover("ms=999999")[0] == ss.SLOW_COVER_MAX_MS / 1000
  assert ss.slow_cover("ms=nope&hue=x")[0] == 2.0
  assert ss.slow_cover("")[1].startswith(b"<svg")
