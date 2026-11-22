"""Build the static shell (AOTY-21) for one base path: web/src/ -> dist/web/.

  .venv/bin/python scripts/build_shell.py --base /toys/aoty-staging/   # CD passes the env's BASE_PATH

kamp.fm serves <base>index.html for every page path under the base (AOTY-15), so asset URLs
are absolute and include the base. CD uploads assets/ with a year's immutable cache, so their
names carry a hash of their content: a changed file gets a new name, and an old index.html
still finds the assets it was built with (CD doesn't delete old ones).

The kamp.fm CSP allows no inline script and no inline event handlers, so the build fails if
index.html has either (a JSON data block, type="application/json", is fine).
"""

import argparse
import hashlib
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "web" / "src"
OUT = REPO / "dist" / "web"
ASSETS = ("app.css", "app.js")
HASH_LEN = 12

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{[A-Z]+\}\}")


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


def build(base, src=SRC, out=OUT):
  """Write out/index.html and out/assets/<hashed names>; returns {asset: published path}."""
  if not (base.startswith("/") and base.endswith("/")):
    raise ShellError(f"--base must start and end with a slash, got {base!r}")
  shutil.rmtree(out, ignore_errors=True)
  (out / "assets").mkdir(parents=True)
  published = {}
  for name in ASSETS:
    data = (src / name).read_bytes()
    hashed = hashed_name(name, data)
    (out / "assets" / hashed).write_bytes(data)
    published[name] = f"assets/{hashed}"
  html = (src / "index.html").read_text(encoding="utf-8")
  html = (
    html.replace("{{BASE}}", base)
    .replace("{{CSS}}", f"{base}{published['app.css']}")
    .replace("{{JS}}", f"{base}{published['app.js']}")
  )
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
  args = p.parse_args()
  for name, path in build(args.base, out=args.out).items():
    print(f"{name} -> {args.base}{path}")
  print(f"wrote {args.out / 'index.html'}")


if __name__ == "__main__":
  main()
