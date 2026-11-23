"""Lambda packaging (scripts/package_lambdas.py): what goes in each zip, and reproducibility."""

import json
import subprocess
import zipfile

import pytest

from scripts import package_lambdas as pkg


@pytest.fixture
def tree(tmp_path):
  """A lambdas/ tree like the repo's: one dir per function, an optional shared common/."""
  root = tmp_path / "lambdas"
  for fn in ("api", "sync"):
    (root / fn).mkdir(parents=True)
    (root / fn / "handler.py").write_text(f"def handler(e, c):\n  return '{fn}'\n")
  (root / "api" / "__pycache__").mkdir()
  (root / "api" / "__pycache__" / "handler.cpython-313.pyc").write_bytes(b"junk")
  (root / "common").mkdir()
  (root / "common" / "util.py").write_text("X = 1\n")
  (root / "requirements.txt").write_text("# nothing yet\n")
  return root


def names(path):
  with zipfile.ZipFile(path) as z:
    return z.namelist()


def test_each_function_zip_has_its_handler_and_the_shared_code_at_the_root(tree, tmp_path):
  dist = tmp_path / "dist"
  built = pkg.package_all(tree, dist, functions=("api", "sync"))
  assert sorted(p.name for p in built) == ["api.zip", "sync.zip"]
  assert names(dist / "api.zip") == ["common/util.py", "handler.py"]
  with zipfile.ZipFile(dist / "sync.zip") as z:
    assert z.read("handler.py").decode().endswith("'sync'\n")


def test_a_job_zip_has_its_scripts_and_the_shared_code(tree, tmp_path):
  jobs = tmp_path / "jobs"
  (jobs / "index").mkdir(parents=True)
  (jobs / "index" / "build.py").write_text("print('build')\n")
  (jobs / "index" / "dump.py").write_text("X = 2\n")
  built = pkg.package_all(tree, tmp_path / "dist", functions=(), jobs_dir=jobs, jobs={"index": "build.py"})
  assert [p.name for p in built] == ["index.zip"]
  assert names(built[0]) == ["build.py", "common/util.py", "dump.py"]


def test_a_job_without_its_entry_script_is_an_error(tree, tmp_path):
  (tmp_path / "jobs" / "index").mkdir(parents=True)
  with pytest.raises(SystemExit, match="build.py"):
    pkg.package_all(
      tree, tmp_path / "dist", functions=(), jobs_dir=tmp_path / "jobs", jobs={"index": "build.py"}
    )


def test_the_repo_packages_every_function_terraform_creates():
  assert pkg.FUNCTIONS == ("api", "sync", "mb", "chart", "uptime")
  for fn in pkg.FUNCTIONS:
    assert (pkg.REPO / "lambdas" / fn / "handler.py").is_file()
  tf = (pkg.REPO / "infra" / "modules" / "stack" / "functions.tf").read_text()
  for fn in pkg.FUNCTIONS:
    assert f"\n    {fn} " in tf or f"\n    {fn}  " in tf  # a key in local.functions


def test_the_repo_packages_the_index_job():
  assert pkg.JOBS == {"index": "build.py"}
  assert (pkg.REPO / "jobs" / "index" / "build.py").is_file()


def test_bytecode_caches_are_left_out(tree, tmp_path):
  pkg.package_all(tree, tmp_path / "dist", functions=("api",))
  assert not any("__pycache__" in n or n.endswith(".pyc") for n in names(tmp_path / "dist" / "api.zip"))


def test_rebuilding_gives_byte_identical_zips(tree, tmp_path):
  first = pkg.package_all(tree, tmp_path / "a", functions=("api",))[0].read_bytes()
  # Touch the source: a new mtime must not change the archive.
  src = tree / "api" / "handler.py"
  src.write_text(src.read_text())
  second = pkg.package_all(tree, tmp_path / "b", functions=("api",))[0].read_bytes()
  assert first == second


def test_entries_use_a_fixed_timestamp_and_mode(tree, tmp_path):
  with zipfile.ZipFile(pkg.package_all(tree, tmp_path / "dist", functions=("api",))[0]) as z:
    for info in z.infolist():
      assert info.date_time == pkg.FIXED_DATE
      assert info.external_attr >> 16 == 0o100644


SHA = "0123456789abcdef0123456789abcdef01234567"


def test_every_zip_carries_build_json_with_the_sha(tree, tmp_path):
  jobs = tmp_path / "jobs"
  (jobs / "index").mkdir(parents=True)
  (jobs / "index" / "build.py").write_text("print('build')\n")
  built = pkg.package_all(
    tree, tmp_path / "dist", functions=("api", "sync"), jobs_dir=jobs, jobs={"index": "build.py"},
    build={"sha": SHA},
  )  # fmt: skip
  assert len(built) == 3
  for path in built:
    with zipfile.ZipFile(path) as z:
      assert json.loads(z.read("build.json")) == {"sha": SHA}
      assert z.getinfo("build.json").date_time == pkg.FIXED_DATE


def test_same_sha_same_bytes_new_sha_new_bytes(tree, tmp_path):
  def build(out, sha):
    return pkg.package_all(tree, tmp_path / out, functions=("api",), build={"sha": sha})[0].read_bytes()

  assert build("a", SHA) == build("b", SHA)
  assert build("c", SHA) != build("d", SHA[::-1])


def test_a_build_json_in_the_source_is_an_error(tree, tmp_path):
  (tree / "api" / "build.json").write_text("{}")
  with pytest.raises(SystemExit, match="build.json"):
    pkg.package_all(tree, tmp_path / "dist", functions=("api",), build={"sha": SHA})


def test_the_repo_has_no_source_build_json():
  for root in (pkg.REPO / "lambdas", pkg.REPO / "jobs"):
    assert not any(name.endswith("build.json") for name, _ in pkg._files(root))


def git(repo, *args):
  subprocess.run(
    ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
     "-c", "commit.gpgsign=false", *args],
    check=True, capture_output=True,
  )  # fmt: skip


@pytest.fixture
def repo(tree):
  """tree, committed in its own git repo, with __pycache__ ignored as in the real repo."""
  root = tree.parent
  (root / ".gitignore").write_text("__pycache__/\n")
  git(root, "init", "-q")
  git(root, "add", ".")
  git(root, "commit", "-q", "-m", "init")
  head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
  return root, head.stdout.strip()


def test_git_sha_is_head_for_a_clean_tree(repo):
  root, head = repo
  assert pkg.git_sha(root, [root / "lambdas"]) == head


def test_git_sha_is_dirty_for_an_edited_file(repo):
  root, head = repo
  (root / "lambdas" / "api" / "handler.py").write_text("changed\n")
  assert pkg.git_sha(root, [root / "lambdas"]) == f"{head}-dirty"


def test_git_sha_is_dirty_for_an_ignored_file_that_would_be_zipped(repo):
  root, head = repo
  (root / ".gitignore").write_text("__pycache__/\nsecret.py\n")
  git(root, "commit", "-qam", "ignore")
  head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
  (root / "lambdas" / "api" / "secret.py").write_text("X = 1\n")
  assert pkg.git_sha(root, [root / "lambdas"]) == f"{head.stdout.strip()}-dirty"


def test_git_sha_ignores_changes_outside_the_sources(repo):
  root, head = repo
  (root / "notes.txt").write_text("not packaged\n")
  assert pkg.git_sha(root, [root / "lambdas"]) == head


def test_git_sha_is_unknown_outside_git(tree):
  assert pkg.git_sha(tree.parent, [tree]) == "unknown"


def test_missing_function_dir_is_an_error(tree, tmp_path):
  with pytest.raises(SystemExit, match="mb"):
    pkg.package_all(tree, tmp_path / "dist", functions=("mb",))


def test_no_requirements_means_no_pip(tree, tmp_path, monkeypatch):
  calls = []
  monkeypatch.setattr(pkg.subprocess, "run", lambda *a, **k: calls.append(a))
  assert pkg.install_deps(tree / "requirements.txt", tmp_path / "deps") is None
  assert calls == []


def test_requirements_install_linux_arm64_wheels(tree, tmp_path, monkeypatch):
  (tree / "requirements.txt").write_text("requests==2.34.2\n")
  calls = []
  monkeypatch.setattr(pkg.subprocess, "run", lambda cmd, **k: calls.append(cmd))
  target = pkg.install_deps(tree / "requirements.txt", tmp_path / "deps")
  assert target == tmp_path / "deps"
  (cmd,) = calls
  for flag in (
    "--platform=manylinux2014_aarch64",
    "--implementation=cp",
    "--python-version=3.13",
    "--only-binary=:all:",
  ):
    assert flag in cmd
  assert cmd[cmd.index("--target") + 1] == str(tmp_path / "deps")
