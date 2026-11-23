"""Runtime guardrail settings from SSM (lambdas/common/guardrails.py, AOTY-26)."""

import pytest

from common import guardrails as g

PREFIX = "/kamp-aoty/test/"


class FakeSSM:
  def __init__(self, values=None, fail=False):
    self.values, self.fail, self.calls = dict(values or {}), fail, 0

  def get_parameters(self, Names):
    self.calls += 1
    if self.fail:
      raise RuntimeError("SSM down")
    return {
      "Parameters": [{"Name": n, "Value": self.values[n.removeprefix(PREFIX)]} for n in Names
                     if n.removeprefix(PREFIX) in self.values],
      "InvalidParameters": [n for n in Names if n.removeprefix(PREFIX) not in self.values],
    }  # fmt: skip


class Clock:
  def __init__(self):
    self.t = 1000.0

  def __call__(self):
    return self.t


def reader(ssm, clock=None):
  return g.Reader(ssm, PREFIX, clock=clock or Clock(), jitter=lambda a, b: 0)


def test_no_parameters_means_the_defaults_and_new_users_welcome():
  s = reader(FakeSSM()).get()
  assert s == g.DEFAULTS and not s.new_users_paused


def test_values_are_parsed():
  s = reader(FakeSSM({"pause_new_users": "True", "sync_cooldown_seconds": " 3600 ",
                      "max_queue_for_new": "50"})).get()  # fmt: skip
  assert s.pause_new_users and s.new_users_paused and not s.pause_syncs
  assert s.sync_cooldown_seconds == 3600 and s.max_queue_for_new == 50


def test_pausing_syncs_pauses_new_users_too():
  assert reader(FakeSSM({"pause_syncs": "true"})).get().new_users_paused


@pytest.mark.parametrize("name,raw", [("pause_new_users", "yes"), ("pause_syncs", "1"),
                                      ("sync_cooldown_seconds", "0"), ("new_users_per_ip_hour", "-3"),
                                      ("max_queue_for_new", "10.5")])  # fmt: skip
def test_malformed_values_are_rejected(name, raw):
  with pytest.raises(ValueError):
    g.parse(name, raw)


def test_a_typo_keeps_the_last_good_value(capsys):
  ssm, clock = FakeSSM({"pause_new_users": "true"}), Clock()
  r = reader(ssm, clock)
  assert r.get().pause_new_users
  ssm.values["pause_new_users"] = "flase"
  clock.t += g.TTL_S + 1
  assert r.get().pause_new_users  # still paused: a typo mustn't un-pause
  assert '"GuardrailBadValue": 1' in capsys.readouterr().out


def test_a_typo_on_a_first_read_means_the_default():
  s = reader(FakeSSM({"sync_cooldown_seconds": "ten"})).get()
  assert s.sync_cooldown_seconds == 600 and s.loaded


def test_a_deleted_parameter_goes_back_to_its_default():
  ssm, clock = FakeSSM({"pause_new_users": "true"}), Clock()
  r = reader(ssm, clock)
  r.get()
  del ssm.values["pause_new_users"]
  clock.t += g.TTL_S + 1
  assert not r.get().pause_new_users


def test_reads_are_cached():
  ssm, clock = FakeSSM(), Clock()
  r = reader(ssm, clock)
  r.get()
  clock.t += g.TTL_S - 1
  r.get()
  assert ssm.calls == 1
  clock.t += 2
  r.get()
  assert ssm.calls == 2


def test_ssm_down_on_a_cold_start_pauses_new_users_only():
  s = reader(FakeSSM(fail=True)).get()
  assert s.new_users_paused and not s.pause_syncs and s.sync_cooldown_seconds == 600


class SecretSSM(FakeSSM):
  def get_parameters(self, Names, WithDecryption=False):
    assert WithDecryption
    return super().get_parameters(Names)


def test_origin_secrets_are_read_cached_and_kept_through_a_blip():
  ssm, clock = SecretSSM({"origin_secret": "a", "origin_secret_previous": "b"}), Clock()
  r = g.OriginSecrets(ssm, PREFIX, clock=clock)
  assert r.get() == ("a", "b")
  clock.t += g.SECRET_TTL_S - 1
  r.get()
  assert ssm.calls == 1
  ssm.fail = True
  clock.t += 2
  assert r.get() == ("a", "b")


def test_no_origin_secret_means_none():
  assert g.OriginSecrets(SecretSSM(), PREFIX, clock=Clock()).get() == ()
  assert g.OriginSecrets(SecretSSM(fail=True), PREFIX, clock=Clock()).get() == ()


def test_origin_ok():
  assert g.origin_ok("a", ("x", "a"))
  assert not g.origin_ok("a", ())
  assert not g.origin_ok("", ("",))
  assert not g.origin_ok(None, ("a",))
  assert not g.origin_ok("ab", ("a",))


def test_ssm_down_later_keeps_the_last_good_settings():
  ssm, clock = FakeSSM({"sync_cooldown_seconds": "3600"}), Clock()
  r = reader(ssm, clock)
  r.get()
  ssm.fail = True
  clock.t += g.TTL_S + 1
  s = r.get()
  assert s.sync_cooldown_seconds == 3600 and not s.new_users_paused
  ssm.fail = False
  clock.t += 6  # a failed read retries after 5 s, not a full TTL
  r.get()
  assert ssm.calls == 3
