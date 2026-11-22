"""Build one deployment zip per Lambda (AOTY-17).

  .venv/bin/python scripts/package_lambdas.py            # -> dist/api.zip, dist/sync.zip, dist/mb.zip

Each zip holds lambdas/<fn>/ at its root, plus lambdas/common/ if present, plus the packages
from lambdas/requirements.txt. Those are installed as Linux arm64 wheels, because the functions
run on python3.13 arm64 (infra/modules/stack/functions.tf), not on the machine building them.

Zips are reproducible: sorted entries, a fixed timestamp and mode, no bytecode, so the same
source always gives the same bytes.
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FUNCTIONS = ("api", "sync", "mb")
FIXED_DATE = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip entry can carry
FILE_MODE = 0o100644
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def _files(root):
  for path in sorted(root.rglob("*")):
    rel = path.relative_to(root)
    if path.is_file() and not SKIP_PARTS & set(rel.parts) and path.suffix != ".pyc":
      yield rel.as_posix(), path


def write_zip(out, roots):
  """Zip the files under each (root, prefix) pair, deterministically.

  prefix is the folder the root lands in inside the zip ("" for the zip root). Later roots
  win on a name clash, so the function's own files beat shared code and dependencies.
  """
  entries = {}
  for root, prefix in roots:
    for name, path in _files(root):
      entries[f"{prefix}{name}"] = path
  out.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(entries):
      info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
      info.external_attr = FILE_MODE << 16
      info.compress_type = zipfile.ZIP_DEFLATED
      z.writestr(info, entries[name].read_bytes())
  return out


def _requirements(path):
  if not path.exists():
    return []
  lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
  return [line for line in lines if line]


def install_deps(requirements, target):
  """pip-install the requirements as Linux arm64 wheels into target; None if there are none."""
  if not _requirements(requirements):
    return None
  shutil.rmtree(target, ignore_errors=True)
  subprocess.run(
    [
      sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
      "--target", str(target),
      "--platform=manylinux2014_aarch64",
      "--implementation=cp",
      "--python-version=3.13",
      "--only-binary=:all:",
      "-r", str(requirements),
    ],
    check=True,
  )  # fmt: skip
  return target


def package_all(lambdas_dir, dist_dir, functions=FUNCTIONS, deps_dir=None):
  """Build dist_dir/<fn>.zip for each function; returns the zip paths."""
  deps = install_deps(lambdas_dir / "requirements.txt", deps_dir or dist_dir / ".deps")
  common = lambdas_dir / "common"
  built = []
  for fn in functions:
    src = lambdas_dir / fn
    if not (src / "handler.py").is_file():
      raise SystemExit(f"{src}/handler.py is missing: every function needs one")
    roots = [(deps, "")] if deps else []
    if common.is_dir():
      roots.append((common, "common/"))  # importable as `common.<module>`
    roots.append((src, ""))
    built.append(write_zip(dist_dir / f"{fn}.zip", roots))
  return built


def main():
  for path in package_all(REPO / "lambdas", REPO / "dist"):
    print(f"{path.relative_to(REPO)}: {path.stat().st_size} bytes")


if __name__ == "__main__":
  main()
