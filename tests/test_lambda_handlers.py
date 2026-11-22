"""The deployed Lambda handlers (lambdas/*/handler.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

LAMBDAS = Path(__file__).resolve().parent.parent / "lambdas"


def load(fn):
  spec = importlib.util.spec_from_file_location(f"lambda_{fn}", LAMBDAS / fn / "handler.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module.handler


@pytest.fixture
def api(monkeypatch):
  monkeypatch.setenv("BASE_PATH", "/toys/aoty-staging/")
  monkeypatch.setenv("AOTY_ENV", "staging")
  return load("api")


def test_health_answers_under_the_env_base_path(api):
  out = api({"rawPath": "/toys/aoty-staging/api/health"}, None)
  assert out["statusCode"] == 200
  assert json.loads(out["body"]) == {"ok": True, "source": "lambdas/api", "env": "staging"}


def test_api_responses_are_never_cached(api):
  for path in ("/toys/aoty-staging/api/health", "/toys/aoty-staging/api/nope"):
    assert api({"rawPath": path}, None)["headers"]["cache-control"] == "no-store"


def test_unknown_routes_and_other_base_paths_are_404(api):
  for path in ("/toys/aoty-staging/api/nope", "/toys/aoty/api/health", ""):
    assert api({"rawPath": path}, None)["statusCode"] == 404


@pytest.mark.parametrize("fn", ["mb"])  # sync: tests/test_sync_handler.py
def test_workers_are_no_ops_until_their_stories(fn):
  assert load(fn)({}, None) == {"ok": True, "source": f"lambdas/{fn}"}
