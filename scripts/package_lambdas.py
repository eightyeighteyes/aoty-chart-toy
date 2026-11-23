"""Build one deployment zip per Lambda (AOTY-17) and per Fargate job (AOTY-20).

  .venv/bin/python scripts/package_lambdas.py   # -> dist/{api,sync,mb,chart,uptime}.zip, dist/index.zip

Each zip holds lambdas/<fn>/ (or jobs/<job>/) at its root, plus lambdas/common/ if present,
plus the packages from lambdas/requirements.txt. Those are installed as Linux arm64 wheels,
because the functions and the index task run on python3.13 arm64
(infra/modules/stack/functions.tf, index.tf), not on the machine building them. JOBS names
each job's entry script; CD uploads job zips to the store bucket's code/ prefix.

Zips are reproducible: sorted entries, a fixed timestamp and mode, no bytecode, so the same
source always gives the same bytes.

Every zip also gets a build.json at its root, {"sha": <git HEAD>} (AOTY-35), which the API's
/api/health reports and CD's smoke test checks. The SHA ends in "-dirty" when a packaged file
differs from HEAD or isn't tracked, so a zip never claims a commit it wasn't built from. The
same commit still gives the same bytes; a new commit changes every zip.
"""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FUNCTIONS = ("api", "sync", "mb", "chart", "uptime")
JOBS = {"index": "build.py"}  # jobs/<name>/ -> its entry script
FIXED_DATE = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip entry can carry
FILE_MODE = 0o100644
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
BUILD_FILE = "build.json"


def _files(root):
  for path in sorted(root.rglob("*")):
    rel = path.relative_to(root)
    if path.is_file() and not SKIP_PARTS & set(rel.parts) and path.suffix != ".pyc":
      yield rel.as_posix(), path


def build_json(build):
  """build.json's bytes: sorted keys and fixed separators, so equal dicts give equal bytes."""
  return (json.dumps(build, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_zip(out, roots, extra=None):
  """Zip the files under each (root, prefix) pair, deterministically.

  prefix is the folder the root lands in inside the zip ("" for the zip root). Later roots
  win on a name clash, so the function's own files beat shared code and dependencies.
  extra maps zip names to bytes the packager generates; a source file with one of those
  names is an error, because it would be silently replaced.
  """
  entries = {}
  for root, prefix in roots:
    for name, path in _files(root):
      entries[f"{prefix}{name}"] = path
  for name, data in (extra or {}).items():
    if name in entries:
      raise SystemExit(f"{entries[name]} clashes with the generated {name}: remove it from the source")
    entries[name] = data
  out.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(entries):
      info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
      info.external_attr = FILE_MODE << 16
      info.compress_type = zipfile.ZIP_DEFLATED
      data = entries[name]
      z.writestr(info, data if isinstance(data, bytes) else data.read_bytes())
  return out


def _git(repo, *args):
  return subprocess.run(
    ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
  ).stdout.strip()


def git_sha(repo, sources):
  """HEAD's SHA for the zips built from `sources` (dirs inside repo); "unknown" outside git.

  "-dirty" is added when a file under sources has uncommitted changes, or when a file the zips
  would carry isn't tracked at all. git status alone misses the second: it doesn't list
  ignored files, and _files would still zip them.
  """
  rels = [Path(s).resolve().relative_to(Path(repo).resolve()).as_posix() for s in sources]
  try:
    sha = _git(repo, "rev-parse", "HEAD")
    changed = _git(repo, "status", "--porcelain", "--", *rels)
    tracked = set(_git(repo, "ls-files", "-z", "--", *rels).split("\0"))
  except (OSError, subprocess.CalledProcessError):
    return "unknown"
  root = Path(repo).resolve()
  packaged = {p.resolve().relative_to(root).as_posix() for s in sources for _, p in _files(Path(s))}
  return f"{sha}-dirty" if changed or packaged - tracked else sha


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


def package_all(
  lambdas_dir, dist_dir, functions=FUNCTIONS, deps_dir=None, jobs_dir=None, jobs=None, build=None
):
  """Build dist_dir/<fn>.zip for each function and job; returns the zip paths.

  build, if given, is written into every zip as build.json.
  """
  deps = install_deps(lambdas_dir / "requirements.txt", deps_dir or dist_dir / ".deps")
  extra = {BUILD_FILE: build_json(build)} if build is not None else None
  common = lambdas_dir / "common"
  sources = [(fn, lambdas_dir / fn, "handler.py") for fn in functions]
  sources += [(job, jobs_dir / job, entry) for job, entry in (jobs or {}).items()]
  built = []
  for name, src, entry in sources:
    if not (src / entry).is_file():
      raise SystemExit(f"{src}/{entry} is missing: it's the entry point")
    roots = [(deps, "")] if deps else []
    if common.is_dir():
      roots.append((common, "common/"))  # importable as `common.<module>`
    roots.append((src, ""))
    built.append(write_zip(dist_dir / f"{name}.zip", roots, extra))
  return built


def main():
  lambdas, jobs = REPO / "lambdas", REPO / "jobs"
  sha = git_sha(REPO, [lambdas, jobs])
  print(f"build sha: {sha}")
  for path in package_all(lambdas, REPO / "dist", jobs_dir=jobs, jobs=JOBS, build={"sha": sha}):
    print(f"{path.relative_to(REPO)}: {path.stat().st_size} bytes")


if __name__ == "__main__":
  main()
