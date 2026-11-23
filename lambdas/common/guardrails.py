"""Runtime guardrail settings (AOTY-26): SSM parameters that change behaviour without a deploy.

  /kamp-aoty/<env>/pause_new_users        true|false  new users get "we're at capacity"
  /kamp-aoty/<env>/pause_syncs            true|false  no syncs are queued at all
  /kamp-aoty/<env>/sync_cooldown_seconds  int > 0     per-user cooldown between page-visit syncs
  /kamp-aoty/<env>/new_users_per_ip_hour  int > 0     distinct new usernames one IP may try per hour
  /kamp-aoty/<env>/max_queue_for_new      int > 0     new users are refused while the sync queue is deeper

Values are set by hand (`aws ssm put-parameter --type String --overwrite`); Terraform only knows the
names, as for the other parameters (infra/modules/stack/ssm.tf). A missing parameter means its
default. Reads are cached for TTL_S plus jitter per warm container, so a change lands within about
half a minute.

Failure rules:
- A malformed value is ignored: the last good value (else the default) stays, with an error log and
  a GuardrailBadValue metric, so a typo can't silently un-pause anything.
- If SSM can't be read, the last good settings stay. A container that has never read them treats
  new users as paused (Settings.new_users_paused): during a surge, a cold start mustn't ignore an
  active pause.
"""

import json
import logging
import random
import time
from dataclasses import dataclass, replace

log = logging.getLogger()

TTL_S = 30
JITTER_S = 5
NAMESPACE = "kamp-aoty"


@dataclass(frozen=True)
class Settings:
  pause_new_users: bool = False
  pause_syncs: bool = False
  sync_cooldown_seconds: int = 600
  new_users_per_ip_hour: int = 10
  max_queue_for_new: int = 500
  loaded: bool = True  # False until a read of SSM has succeeded once

  @property
  def new_users_paused(self):
    return self.pause_new_users or self.pause_syncs or not self.loaded


DEFAULTS = Settings()
NAMES = (
  "pause_new_users",
  "pause_syncs",
  "sync_cooldown_seconds",
  "new_users_per_ip_hour",
  "max_queue_for_new",
)


def parse(name, raw):
  """The typed value of one parameter, or raise ValueError."""
  value = raw.strip().lower()
  if isinstance(getattr(DEFAULTS, name), bool):
    if value in ("true", "false"):
      return value == "true"
    raise ValueError(f"{name} must be true or false")
  if value.isdigit() and int(value) > 0:
    return int(value)
  raise ValueError(f"{name} must be a positive integer")


def merge(previous, values):
  """Settings from {name: raw string} over previous; returns (settings, names that were malformed).

  A name absent from values goes back to its default (the parameter was deleted).
  """
  out, bad = {}, []
  for name in NAMES:
    if name not in values:
      out[name] = getattr(DEFAULTS, name)
      continue
    try:
      out[name] = parse(name, values[name])
    except ValueError:
      bad.append(name)
      out[name] = getattr(previous, name) if previous.loaded else getattr(DEFAULTS, name)
  return replace(DEFAULTS, **out, loaded=True), bad


class Reader:
  """Cached settings for one environment's prefix. get() never raises."""

  def __init__(self, ssm, prefix, *, env_name="", clock=time.monotonic, jitter=random.uniform):
    self.ssm, self.prefix, self.env_name = ssm, prefix, env_name
    self._clock, self._jitter = clock, jitter
    self._settings = replace(DEFAULTS, loaded=False)
    self._expires = None

  def get(self):
    now = self._clock()
    if self._expires is not None and now < self._expires:
      return self._settings
    try:
      out = self.ssm.get_parameters(Names=[self.prefix + n for n in NAMES])
      values = {p["Name"].removeprefix(self.prefix): p["Value"] for p in out.get("Parameters", [])}
    except Exception:
      log.exception("could not read guardrail settings; keeping the last good ones")
      self._expires = now + 5  # retry soon, but don't hammer SSM from every request
      return self._settings
    self._settings, bad = merge(self._settings, values)
    for name in bad:
      log.error("guardrail %s has a malformed value; ignored", name)
    if bad:
      _emit_bad(self.env_name, len(bad))
    self._expires = now + TTL_S + self._jitter(0, JITTER_S)
    return self._settings


def _emit_bad(env_name, count):
  print(json.dumps({
    "_aws": {
      "Timestamp": int(time.time() * 1000),
      "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [["Environment"]],
                             "Metrics": [{"Name": "GuardrailBadValue", "Unit": "Count"}]}],
    },
    "Environment": env_name,
    "GuardrailBadValue": count,
  }))  # fmt: skip
