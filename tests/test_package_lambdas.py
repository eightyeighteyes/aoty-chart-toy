"""Lambda packaging (scripts/package_lambdas.py): what goes in each zip, and reproducibility."""

import json
import shutil
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


# ---- the card Lambda (AOTY-91): Node, packed by card_deps and card_zip --------------------------


@pytest.fixture
def card_tree(tree, tmp_path):
  """lambdas/card with a local test install, web/src with the page modules, and a fake npm tree."""
  card = tree / "card"
  (card / "fonts").mkdir(parents=True)
  (card / "handler.mjs").write_text("export const handler = async () => ({ok: true});\n")
  (card / "package.json").write_text('{"name": "card"}\n')
  (card / "package-lock.json").write_text("{}\n")
  (card / "fonts" / "Inter-500.ttf").write_bytes(b"font")
  (card / "node_modules" / "@napi-rs" / "canvas-darwin-arm64").mkdir(parents=True)  # a developer's own
  (card / "node_modules" / "@napi-rs" / "canvas-darwin-arm64" / "skia.node").write_bytes(b"mac")
  web = tmp_path / "web"
  web.mkdir()
  for name in pkg.CARD_WEB:
    (web / name).write_text(f"// {name}\n")
  modules = tmp_path / "linux" / "node_modules"
  for d in ("canvas", "canvas-linux-arm64-gnu"):
    (modules / "@napi-rs" / d).mkdir(parents=True)
    (modules / "@napi-rs" / d / "index.js").write_text("// x\n")
  (modules / "@napi-rs" / "canvas" / "node_modules" / "dep").mkdir(parents=True)  # nested deps go too
  (modules / "@napi-rs" / "canvas" / "node_modules" / "dep" / "a.js").write_text("// dep\n")
  return tree, web, modules


def test_the_card_zip_holds_the_handler_fonts_page_modules_and_the_linux_install(card_tree, tmp_path):
  tree, web, modules = card_tree
  out = pkg.card_zip(tree, web, tmp_path / "dist", modules, build={"sha": "abc"})
  got = names(out)
  assert {"handler.mjs", "package.json", "fonts/Inter-500.ttf", "build.json"} <= set(got)
  assert {f"web/{n}" for n in pkg.CARD_WEB} <= set(got)
  assert "node_modules/@napi-rs/canvas-linux-arm64-gnu/index.js" in got
  assert "node_modules/@napi-rs/canvas/node_modules/dep/a.js" in got
  assert not any("darwin" in n for n in got)  # the developer's own install never ships


def test_the_card_zips_page_modules_are_commonjs_whatever_the_cards_package_says(card_tree, tmp_path):
  # AOTY-104: the card's package.json is "type": "module", and in the zip it's the nearest one to
  # web/*.js, which made Node load the page's scripts as ES modules.
  tree, web, modules = card_tree
  out = pkg.card_zip(tree, web, tmp_path / "dist", modules)
  with zipfile.ZipFile(out) as z:
    assert json.loads(z.read("web/package.json")) == {"type": "commonjs"}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_packed_card_handler_runs_the_page_modules(tmp_path):
  # AOTY-104: the real handler and page modules, unpacked as Lambda sees them in /var/task, must
  # load and work. In the repo the handler reads web/src/, which no "type": "module" covers, so
  # web/test/card.test.mjs never saw the packed layout. Canvas is imported lazily, so none is needed.
  modules = tmp_path / "node_modules"
  modules.mkdir()
  out = pkg.card_zip(pkg.REPO / "lambdas", pkg.REPO / "web" / "src", tmp_path / "dist", modules)
  task = tmp_path / "task"
  with zipfile.ZipFile(out) as z:
    z.extractall(task)
  script = (
    "import {pathToFileURL} from 'node:url';"
    "const card = await import(pathToFileURL(process.argv[1]).href);"
    "const chart = {username: 'alice', display_name: 'alice', year: 2026, generated_at: 1,"
    " albums: [{album: 'Blue Lines', artist: 'Massive Attack', art: ''}], rankings: {album: [[0, 1]]}};"
    "const input = card.cardInput(chart, {origin: 'https://kamp.fm', base: '/toys/aoty/', short: ''});"
    "console.log(JSON.stringify(input.texts));"
  )
  run = subprocess.run(["node", "--input-type=module", "-e", script, str(task / "handler.mjs")],
                       capture_output=True, text=True, timeout=60)  # fmt: skip
  assert run.returncode == 0, run.stderr
  texts = json.loads(run.stdout)
  assert texts["albums"] == [{"title": "Blue Lines", "artist": "Massive Attack"}]
  assert texts["url"] == "https://kamp.fm/toys/aoty/user/alice"


def test_the_card_zip_is_reproducible(card_tree, tmp_path):
  tree, web, modules = card_tree
  a = pkg.card_zip(tree, web, tmp_path / "a", modules, build={"sha": "abc"}).read_bytes()
  b = pkg.card_zip(tree, web, tmp_path / "b", modules, build={"sha": "abc"}).read_bytes()
  assert a == b


def test_only_the_lambdas_canvas_binary_is_accepted(card_tree, tmp_path):
  _, _, modules = card_tree
  pkg.check_card_native(modules)
  (modules / "@napi-rs" / "canvas-linux-arm64-musl").mkdir()
  with pytest.raises(SystemExit, match="musl"):
    pkg.check_card_native(modules)
  for d in ("canvas-linux-arm64-musl", "canvas-linux-arm64-gnu"):
    (modules / "@napi-rs" / d / "index.js").unlink(missing_ok=True)
    (modules / "@napi-rs" / d).rmdir()
  with pytest.raises(SystemExit, match="not just"):
    pkg.check_card_native(modules)  # none at all


def test_card_deps_installs_for_linux_arm64_glibc(card_tree, tmp_path, monkeypatch):
  tree, _, modules = card_tree
  calls = []

  def fake_run(cmd, **k):
    calls.append(cmd)
    target = tmp_path / "deps" / "node_modules" / "@napi-rs" / "canvas-linux-arm64-gnu"
    target.mkdir(parents=True)

  monkeypatch.setattr(pkg.subprocess, "run", fake_run)
  out = pkg.card_deps(tree / "card", tmp_path / "deps")
  (cmd,) = calls
  assert cmd[:2] == ["npm", "ci"] and out == tmp_path / "deps" / "node_modules"
  for flag in ("--os=linux", "--cpu=arm64", "--libc=glibc", "--omit=dev", "--ignore-scripts"):
    assert flag in cmd


def test_the_card_doesnt_make_the_build_dirty_with_its_test_install(card_tree):
  # git_sha walks the same files the zips would carry: node_modules is never one of them.
  tree, _, _ = card_tree
  assert not any("node_modules" in n for n, _ in pkg._files(tree / "card"))


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
