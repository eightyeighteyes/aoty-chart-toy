"""One CloudWatch Embedded Metric Format line (AOTY-41): namespace kamp-aoty, dimension Environment.

  emf.emit(env, {"SyncsDone": (1, "Count")}, outcome="done")

metrics become CloudWatch metrics (each name costs about $0.30/month per env past the free 10,
so add few); fields are plain properties, searchable with Logs Insights but free. Never put a
username in a field: use the logs' hash. The older emitters (mb, chart, index, guardrails) build
the same shape by hand.
"""

import json
import time

NAMESPACE = "kamp-aoty"


def line(env, metrics, *, clock=time.time, **fields):
  """The line as a dict. metrics: {name: (value, unit)}."""
  return {
    "_aws": {
      "Timestamp": int(clock() * 1000),
      "CloudWatchMetrics": [
        {
          "Namespace": NAMESPACE,
          "Dimensions": [["Environment"]],
          "Metrics": [{"Name": n, "Unit": u} for n, (_, u) in metrics.items()],
        }
      ],
    },
    "Environment": env,
    **fields,
    **{n: v for n, (v, _) in metrics.items()},
  }


def emit(env, metrics, *, clock=time.time, **fields):
  print(json.dumps(line(env, metrics, clock=clock, **fields), separators=(",", ":")))
