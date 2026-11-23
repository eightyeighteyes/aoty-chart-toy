"""The uptime check (lambdas/uptime, AOTY-27): its rules, and the handler with fakes."""

import importlib.util
import json
from pathlib import Path

import pytest
import uptime_core

HANDLER = Path(__file__).resolve().parent.parent / "lambdas" / "uptime" / "handler.py"


@pytest.fixture
def mods():
  # Every Lambda's entry point is handler.py, so load this one under its own name.
  spec = importlib.util.spec_from_file_location("uptime_handler", HANDLER)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return uptime_core, module


BASE = "/toys/aoty-staging/"
SHELL = b'<html><head><base href="/toys/aoty-staging/"></head></html>'
HEALTH = json.dumps({"ok": True, "source": "lambdas/api"}).encode()
CHART = json.dumps({"version": 1, "rankings": {"track": [], "album": []}}).encode()


def test_urls_include_chart_only_with_a_user(mods):
  core, _ = mods
  assert core.urls("https://kamp.fm/", BASE, None) == {
    "shell": "https://kamp.fm/toys/aoty-staging/",
    "health": "https://kamp.fm/toys/aoty-staging/api/health",
  }
  assert core.urls("https://kamp.fm", BASE, "alice")["chart"] == (
    "https://kamp.fm/toys/aoty-staging/data/users/alice/chart.json"
  )


@pytest.mark.parametrize(
  "check,status,body,expected",
  [
    ("shell", 200, SHELL, None),
    ("shell", 200, b"<html>/toys/aoty/</html>", "not this env's shell"),
    ("shell", 503, SHELL, "status 503"),
    ("health", 200, HEALTH, None),
    ("health", 200, json.dumps({"source": "placeholder"}).encode(), "not the deployed api"),
    ("health", 200, b"<html>", "not a JSON object"),
    ("health", None, b"", "status None"),
    ("chart", 200, CHART, None),
    ("chart", 200, b"[]", "not a JSON object"),
    ("chart", 200, b"{}", "no rankings"),
    ("chart", 404, b"", "status 404"),
  ],
)
def test_judge(mods, check, status, body, expected):
  core, _ = mods
  assert core.judge(check, BASE, status, body) == expected


def _deps(handler, answers, user="alice"):
  fetched = []

  def fetch(url):
    fetched.append(url)
    for suffix, answer in answers.items():
      if url.endswith(suffix):
        return answer
    raise AssertionError(url)

  env = {"BASE_PATH": BASE, "AOTY_ENV": "staging"}
  return handler.Deps(env=env, fetch=fetch, user=lambda: user, clock=lambda: 1.0), fetched


def _emf(capsys):
  lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"_aws"' in x]
  assert len(lines) == 1
  return lines[0]


GOOD = {"aoty-staging/": (200, SHELL), "api/health": (200, HEALTH), "chart.json": (200, CHART)}


def test_all_up_emits_zero_failures(mods, capsys):
  _, handler = mods
  deps, fetched = _deps(handler, GOOD)
  out = handler.handler({}, None, deps)
  assert out["failures"] == 0 and len(fetched) == 3
  line = _emf(capsys)
  assert (line["UptimeChecks"], line["UptimeFailures"], line["Environment"]) == (1, 0, "staging")


def test_failed_checks_are_counted_and_the_user_is_never_logged(mods, capsys, caplog):
  _, handler = mods
  deps, _ = _deps(handler, {**GOOD, "chart.json": (404, b""), "api/health": (502, b"")})
  assert handler.handler({}, None, deps)["failures"] == 2
  assert _emf(capsys)["UptimeFailures"] == 2
  assert "alice" not in caplog.text


def test_no_user_skips_the_chart_without_failing(mods, capsys):
  _, handler = mods
  deps, fetched = _deps(handler, GOOD, user=None)
  out = handler.handler({}, None, deps)
  assert out["failures"] == 0 and out["results"]["chart"] == "skipped"
  assert not any(u.endswith("chart.json") for u in fetched)


def test_a_crash_still_emits_and_counts_as_a_failure(mods, capsys):
  _, handler = mods

  def boom():
    raise RuntimeError("ssm down")

  deps = handler.Deps(env={"BASE_PATH": BASE, "AOTY_ENV": "prod"}, fetch=None, user=boom, clock=lambda: 1.0)
  assert handler.handler({}, None, deps)["failures"] == 1
  line = _emf(capsys)
  assert (line["UptimeChecks"], line["UptimeFailures"]) == (1, 1)
