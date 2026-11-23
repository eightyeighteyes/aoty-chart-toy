"""The static shell build (scripts/build_shell.py): hashed assets, absolute URLs, CSP checks."""

import re
from html.parser import HTMLParser

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
  # intro.js and firstvisit.js load before app.js, which reads their globals.
  assert html.index(published["intro.js"]) < html.index(published["app.js"])
  assert html.index(published["firstvisit.js"]) < html.index(published["app.js"])
  assert f'<meta name="aoty-year" content="{bs.TARGET_YEAR}">' in html


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


class _Links(HTMLParser):
  """Each <a href>, with the open elements (tag, class) around it, and every <footer>'s."""

  VOID = {"meta", "link", "input", "br", "img", "circle", "path"}

  def __init__(self):
    super().__init__()
    self.stack, self.links, self.footers = [], [], []

  def handle_starttag(self, tag, attrs):
    a = dict(attrs)
    if tag == "a":
      self.links.append((a.get("href"), [t for t, _ in self.stack], [c for _, c in self.stack]))
    if tag == "footer":
      self.footers.append([t for t, _ in self.stack])
    if tag not in self.VOID:
      self.stack.append((tag, a.get("class") or ""))

  def handle_startendtag(self, tag, attrs):
    self.handle_starttag(tag, attrs)
    if tag not in self.VOID:
      self.stack.pop()

  def handle_endtag(self, tag):
    if tag in self.VOID:
      return
    while self.stack:
      if self.stack.pop()[0] == tag:
        break


def _shell_links(tmp_path):
  bs.build(BASE, out=tmp_path / "web")
  p = _Links()
  p.feed((tmp_path / "web" / "index.html").read_text())
  return p


def test_one_footer_on_every_page_with_the_credits(tmp_path):
  # AOTY-29: outside main (hidden on the intro) and the intro's section, so every page has it.
  p = _shell_links(tmp_path)
  assert len(p.footers) == 1 and "main" not in p.footers[0] and "section" not in p.footers[0]
  in_footer = {href for href, tags, _ in p.links if "footer" in tags}
  assert {"https://www.last.fm/", "https://musicbrainz.org/", "https://coverartarchive.org/"} <= in_footer


def test_new_bands_for_old_heads_is_in_the_top_bar_apart_from_kamp(tmp_path):
  # A primary endorsement in the header (user, 2026-11-23), never inside kamp's own nav.
  p = _shell_links(tmp_path)
  ((tags, classes),) = [(t, c) for href, t, c in p.links if href == "https://www.newbandsforoldheads.com/"]
  assert "topbar" in classes and "kampbar" not in classes and "footer" not in tags and "main" not in tags


def test_nothing_overwrites_the_credits():
  # The footer has no id, and only #footer-user is filled from script (never innerHTML).
  html, js = (bs.SRC / "index.html").read_text(), (bs.SRC / "app.js").read_text()
  assert '<footer class="credits">' in html and '$("footer")' not in js
  assert (
    'id="footer-user"' in html and "innerHTML" not in js.split("function profileLine")[1].split("\n  }\n")[0]
  )


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
