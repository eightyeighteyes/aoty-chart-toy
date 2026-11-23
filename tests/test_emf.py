"""The shared EMF line (lambdas/common/emf.py, AOTY-41)."""

import json

from common import emf


def test_line_has_metrics_as_cloudwatch_metrics_and_fields_as_plain_properties():
  out = emf.line("prod", {"SyncsDone": (1, "Count"), "LastfmCalls": (7, "Count")}, clock=lambda: 2.5,
                 outcome="done", weeks=4)  # fmt: skip
  assert out["_aws"]["Timestamp"] == 2500
  (directive,) = out["_aws"]["CloudWatchMetrics"]
  assert directive == {
    "Namespace": "kamp-aoty",
    "Dimensions": [["Environment"]],
    "Metrics": [{"Name": "SyncsDone", "Unit": "Count"}, {"Name": "LastfmCalls", "Unit": "Count"}],
  }
  assert (out["Environment"], out["SyncsDone"], out["LastfmCalls"]) == ("prod", 1, 7)
  assert (out["outcome"], out["weeks"]) == ("done", 4)


def test_emit_prints_one_compact_json_line(capsys):
  emf.emit("staging", {"X": (1, "Count")}, clock=lambda: 0)
  (text,) = capsys.readouterr().out.splitlines()
  assert json.loads(text)["X"] == 1 and ", " not in text
