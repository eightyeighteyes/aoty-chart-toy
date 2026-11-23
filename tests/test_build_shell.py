"""The static shell build (scripts/build_shell.py): hashed assets, absolute URLs, CSP checks."""

import re

import pytest

from scripts import build_shell as bs

BASE = "/toys/aoty-test/"


@pytest.fixture
def src(tmp_path):
  d = tmp_path / "src"
  d.mkdir()
  for name in bs.ASSETS:
    (d / name).write_bytes((bs.SRC / name).read_bytes())
  (d / "index.html").write_text((bs.SRC / "index.html").read_text())
  return d


def test_the_real_shell_builds_with_absolute_hashed_urls(tmp_path):
  out = tmp_path / "web"
  published = bs.build(BASE, out=out)
  html = (out / "index.html").read_text()
  for name, path in published.items():
    assert re.fullmatch(rf"assets/{name.split('.')[0]}\.[0-9a-f]{{12}}\.{name.split('.')[1]}", path)
    assert (out / path).read_bytes() == (bs.SRC / name).read_bytes()
    assert f'"{BASE}{path}"' in html
  assert f'<meta name="aoty-base" content="{BASE}">' in html
  assert "{{" not in html


def test_the_same_source_gives_the_same_names_and_a_change_gives_new_ones(src, tmp_path):
  first = bs.build(BASE, src=src, out=tmp_path / "a")
  assert bs.build(BASE, src=src, out=tmp_path / "b") == first
  (src / "app.js").write_text((src / "app.js").read_text() + "\n// changed\n")
  second = bs.build(BASE, src=src, out=tmp_path / "c")
  assert second["app.js"] != first["app.js"] and second["app.css"] == first["app.css"]


def test_a_rebuild_leaves_no_stale_assets(src, tmp_path):
  out = tmp_path / "web"
  bs.build(BASE, src=src, out=out)
  (src / "app.css").write_text("body{}")
  bs.build(BASE, src=src, out=out)
  assert len(list((out / "assets").iterdir())) == len(bs.ASSETS)


@pytest.mark.parametrize("base", ["toys/aoty/", "/toys/aoty", ""])
def test_the_base_must_be_slash_wrapped(src, tmp_path, base):
  with pytest.raises(SystemExit, match="slash"):
    bs.build(base, src=src, out=tmp_path / "web")


@pytest.mark.parametrize(
  "snippet, message",
  [
    ("<script>alert(1)</script>", "inline <script>"),
    ('<img src="x" onerror="coverFallback(this)">', "event handlers"),
    ('<button ONCLICK = "go()">x</button>', "event handlers"),
    ('<script src="/x.js">alert(1)</script>', "must be empty"),
  ],
)
def test_csp_violations_fail_the_build(src, tmp_path, snippet, message):
  html = (src / "index.html").read_text().replace("</body>", f"{snippet}\n</body>")
  (src / "index.html").write_text(html)
  with pytest.raises(SystemExit, match=message):
    bs.build(BASE, src=src, out=tmp_path / "web")


def test_a_json_data_block_is_allowed():
  bs.check_csp('<script type="application/json" id="d">{"a": 1}</script>')


def test_unfilled_placeholders_fail_the_build(src, tmp_path):
  (src / "index.html").write_text((src / "index.html").read_text() + "{{OOPS}}")
  with pytest.raises(SystemExit, match="OOPS"):
    bs.build(BASE, src=src, out=tmp_path / "web")


def test_app_js_has_no_inline_handlers_or_eval():
  js = (bs.SRC / "app.js").read_text()
  assert "onerror=" not in js and "onclick=" not in js
  assert "eval(" not in js and "new Function" not in js
  assert 'addEventListener("error"' in js


def test_the_shell_has_one_polite_live_region_per_page(tmp_path):
  # The sync banner (chart pages) and the intro form's message (the intro page): never both shown.
  bs.build(BASE, out=tmp_path / "web")
  html = (tmp_path / "web" / "index.html").read_text()
  assert html.count("aria-live=") == 2
  assert re.search(r'id="banner-msg" role="status" aria-live="polite"', html)
  assert re.search(r'id="intro-msg" role="status" aria-live="polite"', html)
  assert 'id="banner-detail" aria-hidden="true"' in html


def test_placeholders_name_year_title_and_icon_are_filled(tmp_path):
  published = bs.build(BASE, out=tmp_path / "web")
  html = (tmp_path / "web" / "index.html").read_text()
  assert f"<title>{bs.NAME} · a kamp toy</title>" in html
  assert f"your {bs.TARGET_YEAR}, " in html
  assert f'<meta name="aoty-name" content="{bs.NAME}">' in html
  assert f'<link rel="icon" type="image/svg+xml" href="{BASE}{published["favicon.svg"]}">' in html
  # intro.js loads before app.js, which reads globalThis.aotyIntro.
  assert html.index(published["intro.js"]) < html.index(published["app.js"])


def test_the_year_matches_the_lambdas():
  from common.config import TARGET_YEAR

  assert bs.TARGET_YEAR == TARGET_YEAR


def test_the_placeholder_name_avoids_other_brands():
  assert bs.NAME == bs.NAME.lower()
  assert "aoty" not in bs.NAME.lower().split() and "wrapped" not in bs.NAME.lower()


def test_dm_sans_and_the_kamp_bar_are_in_the_shell(tmp_path):
  bs.build(BASE, out=tmp_path / "web")
  html = (tmp_path / "web" / "index.html").read_text()
  assert "family=DM+Sans" in html
  assert 'class="kampbar"' in html and 'href="https://kamp.fm/"' in html and "a kamp toy" in html
  bs.check_csp(html)  # the inline <svg> mark is fine


def test_app_js_sets_up_the_switches_once_and_polls_politely():
  js = (bs.SRC / "app.js").read_text()
  assert js.count('setupSwitch("rank"') == 1 and "switchesReady" in js  # no doubled listeners
  assert "visibilitychange" in js and "document.hidden" in js
  assert '"content-type": "application/json"' in js  # the API refuses anything else (415)


def test_both_seam_fixes_stay():
  # M0: the root paints --bg too. AOTY-23: body never paints as one flat colour (Chrome renders flat
  # tiles and content tiles' near-black differently, so short pages showed a darker strip).
  css = (bs.SRC / "app.css").read_text()
  assert "html { background: var(--bg)" in css
  assert "background-image: linear-gradient(var(--bg), #0d0c13)" in css
