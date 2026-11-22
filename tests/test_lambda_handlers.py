"""The deployed Lambda handlers (lambdas/*/handler.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

LAMBDAS = Path(__file__).resolve().parent.parent / "lambdas"


def load_module(fn):
  spec = importlib.util.spec_from_file_location(f"lambda_{fn}", LAMBDAS / fn / "handler.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def load(fn):
  return load_module(fn).handler


@pytest.fixture
def api(monkeypatch):
  monkeypatch.setenv("BASE_PATH", "/toys/aoty-staging/")
  monkeypatch.setenv("AOTY_ENV", "staging")
  return load("api")


def test_health_answers_under_the_env_base_path(api):
  out = api({"rawPath": "/toys/aoty-staging/api/health"}, None)
  assert out["statusCode"] == 200
  # A checkout has no build.json (only zips do), so the SHA is "unknown" here.
  assert json.loads(out["body"]) == {"ok": True, "source": "lambdas/api", "env": "staging", "sha": "unknown"}


def test_health_reports_the_stamped_sha(api, monkeypatch):
  module = load_module("api")
  monkeypatch.setattr(module, "BUILD_SHA", "abc123")
  body = json.loads(module.handler({"rawPath": "/toys/aoty-staging/api/health"}, None)["body"])
  assert body["sha"] == "abc123"


def test_build_sha_reads_build_json(tmp_path):
  build_sha = load_module("api")._build_sha
  (tmp_path / "build.json").write_text('{"sha":"abc123"}\n')
  assert build_sha(tmp_path / "build.json") == "abc123"


@pytest.mark.parametrize("content", [None, "", "not json", "[]", "{}", '{"sha": 5}', '{"sha": ""}'])
def test_build_sha_is_unknown_without_a_good_stamp(tmp_path, content):
  path = tmp_path / "build.json"
  if content is not None:
    path.write_text(content)
  assert load_module("api")._build_sha(path) == "unknown"


def test_the_packaged_stamp_is_what_the_handler_reads(tmp_path):
  from scripts import package_lambdas as pkg

  (tmp_path / "build.json").write_bytes(pkg.build_json({"sha": "abc123"}))
  assert load_module("api")._build_sha(tmp_path / "build.json") == "abc123"


def test_api_responses_are_never_cached(api):
  for path in ("/toys/aoty-staging/api/health", "/toys/aoty-staging/api/nope"):
    assert api({"rawPath": path}, None)["headers"]["cache-control"] == "no-store"


def test_unknown_routes_and_other_base_paths_are_404(api):
  for path in ("/toys/aoty-staging/api/nope", "/toys/aoty/api/health", ""):
    assert api({"rawPath": path}, None)["statusCode"] == 404


def test_terraforms_placeholder_never_acknowledges_sqs_messages():
  path = LAMBDAS.parent / "infra" / "modules" / "stack" / "placeholder" / "handler.py"
  spec = importlib.util.spec_from_file_location("placeholder", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  with pytest.raises(RuntimeError):
    module.handler({"Records": [{"messageId": "m1", "body": "{}"}]}, None)
  assert module.handler({}, None) == {"ok": True, "placeholder": "unknown"}


# The workers have their own tests: tests/test_sync_handler.py, tests/test_mb_handler.py.
