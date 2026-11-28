"""Build the static shell (AOTY-21) for one base path: web/src/ -> dist/web/.

  .venv/bin/python scripts/build_shell.py --base /toys/aoty-staging/   # CD passes the env's BASE_PATH

kamp.fm serves <base>index.html for every page path under the base (AOTY-15), so asset URLs
are absolute and include the base. CD uploads assets/ with a year's immutable cache, so their
names carry a hash of their content: a changed file gets a new name, and an old index.html
still finds the assets it was built with (CD doesn't delete old ones).

The kamp.fm CSP allows no inline script and no inline event handlers, so the build fails if
index.html has either (a JSON data block, type="application/json", is fine).

index.html placeholders: {{BASE}}, {{NAME}} (the placeholder product name), {{TITLE}}, {{YEAR}},
{{GA}} (the GA4 measurement id, --ga-id, empty unless prod: AOTY-38), {{SHORT}} (the share links'
short host, --short-host, empty unless prod: AOTY-84), {{BGMAP}} (the backgrounds' URLs as JSON,
AOTY-77), and one per asset in ASSETS. They're filled here, not by JavaScript, so nothing flashes.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "web" / "src"
OUT = REPO / "dist" / "web"
# Each hashed asset and the index.html placeholder that gets its URL. The PNG touch icon is
# for Safari, which skips SVG favicons.
ASSETS = {
  "app.css": "CSS",
  "app.js": "JS",
  "consent.js": "CONSENT",  # before kampstats.js, which reads globalThis.kampConsent (AOTY-28)
  "kampstats.js": "STATS",
  "intro.js": "INTRO",
  "lastfm.js": "LASTFM",
  "share.js": "SHARE",
  "sharecard.js": "SHARECARD",
  "carddraw.js": "CARDDRAW",
  "firstvisit.js": "FIRSTVISIT",
  "group.js": "GROUP",
  "groupedit.js": "GROUPEDIT",
  "theme.js": "THEME",
  "backgrounds.js": "BACKGROUNDS",  # what every first-visit background shares (AOTY-77)
  "favicon.svg": "ICON",
  "apple-touch-icon.png": "TOUCH",
}
# The first visit's backgrounds (AOTY-77): id -> file, the one list of them. Each is hashed and
# published like ASSETS but loaded only when picked: index.html's {{BGMAP}} block carries their URLs
# (backgrounds.js load()), and the page's ids are that map's keys. Each file registers its own id.
BACKGROUNDS = {"borealis": "borealis.js"}
HASH_LEN = 12
# The product name is undecided (AOTY-23, user 2026-11-21): a plain description, not a brand.
# Avoid "AOTY" alone (albumoftheyear.org) and "Wrapped".
NAME = "albums of the year"  # lambdas/common/config.py NAME; test_build_shell keeps them equal
TITLE = f"{NAME} · a kamp toy"
TARGET_YEAR = 2026  # lambdas/common/config.py TARGET_YEAR; test_build_shell keeps them equal

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{[A-Z]+\}\}")
# A GA4 measurement id (AOTY-38), or empty for no analytics. Checked because it lands in HTML.
_GA_ID = re.compile(r"(G-[A-Z0-9]{4,20})?")
# The share links' short host (AOTY-84): kamp-site's aoty.kamp.fm (AOTY-69), whose redirects land
# on prod's base alone, so it's accepted only with that base. An allowlist, because it lands in HTML.
SHORT_HOSTS = {"aoty.kamp.fm"}
SHORT_BASE = "/toys/aoty/"


class ShellError(SystemExit):
  pass


def hashed_name(name, data):
  stem, ext = name.rsplit(".", 1)
  return f"{stem}.{hashlib.sha256(data).hexdigest()[:HASH_LEN]}.{ext}"


def check_csp(html):
  """Raise if the page has inline script or inline event handlers."""
  for attrs, body in _SCRIPT.findall(html):
    if "src=" in attrs.lower():
      if body.strip():
        raise ShellError("a <script src> element must be empty")
      continue
    if 'type="application/json"' not in attrs.lower():
      raise ShellError("inline <script> is blocked by the CSP: move it into app.js")
  if _HANDLER.search(html):
    raise ShellError("inline event handlers (on*=) are blocked by the CSP: use addEventListener")


def build(base, src=SRC, out=OUT, ga_id="", short_host=""):
  """Write out/index.html and out/assets/<hashed names>; returns {asset: published path}.

  ga_id is kamp.fm's GA4 measurement id, given for prod only (deploy.yml). Without one,
  kampstats.js loads nothing and sends nothing. short_host is the share links' short host, also
  prod only; without one, share.js shares the page's own address.
  """
  if not (base.startswith("/") and base.endswith("/")):
    raise ShellError(f"--base must start and end with a slash, got {base!r}")
  if not _GA_ID.fullmatch(ga_id):
    raise ShellError(f"--ga-id must look like G-XXXXXXXXXX, got {ga_id!r}")
  if short_host and short_host not in SHORT_HOSTS:
    raise ShellError(f"--short-host must be one of {sorted(SHORT_HOSTS)}, got {short_host!r}")
  if short_host and base != SHORT_BASE:
    raise ShellError(f"--short-host redirects to {SHORT_BASE} only, not {base!r}")
  shutil.rmtree(out, ignore_errors=True)
  (out / "assets").mkdir(parents=True)
  published = {}
  values = {
    "BASE": base,
    "NAME": NAME,
    "TITLE": TITLE,
    "YEAR": str(TARGET_YEAR),
    "GA": ga_id,
    "SHORT": short_host,
  }
  for name, placeholder in ASSETS.items():
    data = (src / name).read_bytes()
    hashed = hashed_name(name, data)
    (out / "assets" / hashed).write_bytes(data)
    published[name] = f"assets/{hashed}"
    values[placeholder] = f"{base}{published[name]}"
  backgrounds = {}
  for bg_id, name in BACKGROUNDS.items():
    data = (src / name).read_bytes()
    hashed = hashed_name(name, data)
    (out / "assets" / hashed).write_bytes(data)
    published[name] = f"assets/{hashed}"
    backgrounds[bg_id] = f"{base}{published[name]}"
  # Inside a <script type="application/json">: "</" could close the element, so it's escaped.
  values["BGMAP"] = json.dumps(backgrounds, sort_keys=True).replace("</", "<\\/")
  html = (src / "index.html").read_text(encoding="utf-8")
  for key, value in values.items():
    html = html.replace(f"{{{{{key}}}}}", value)
  left = _PLACEHOLDER.findall(html)
  if left:
    raise ShellError(f"unfilled placeholders in index.html: {sorted(set(left))}")
  check_csp(html)
  (out / "index.html").write_text(html, encoding="utf-8")
  return published


def main():
  p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  p.add_argument("--base", required=True, help="the env's base path, e.g. /toys/aoty/")
  p.add_argument("--out", type=Path, default=OUT)
  p.add_argument("--ga-id", default="", help="GA4 measurement id (prod only); empty for no analytics")
  p.add_argument(
    "--short-host", default="", help="share links' short host (prod only, aoty.kamp.fm); empty for none"
  )
  args = p.parse_args()
  for name, path in build(args.base, out=args.out, ga_id=args.ga_id, short_host=args.short_host).items():
    print(f"{name} -> {args.base}{path}")
  print(f"wrote {args.out / 'index.html'}")


if __name__ == "__main__":
  main()
