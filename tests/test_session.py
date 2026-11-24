"""Signed sessions, sign-in state and cookies (lambdas/common/session.py, AOTY-47)."""

from common import session as s

KEY = "k" * 40
OLD = "o" * 40
NOW = 1_800_000_000


def test_a_session_round_trips_and_lowercases_the_name():
  token = s.new_session("Alice", KEY, NOW)
  claims = s.verify(s.SESSION, token, [KEY], NOW + 1)
  assert claims == {"u": "alice", "iat": NOW, "exp": NOW + s.SESSION_TTL_S}


def test_expiry():
  token = s.new_session("alice", KEY, NOW)
  assert s.verify(s.SESSION, token, [KEY], NOW + s.SESSION_TTL_S - 1)
  assert s.verify(s.SESSION, token, [KEY], NOW + s.SESSION_TTL_S) is None


def test_tampering_is_refused():
  token = s.new_session("alice", KEY, NOW)
  kind, payload, sig = token.split(".")
  forged = s._b64(b'{"exp":9999999999,"iat":0,"u":"bob"}')
  assert s.verify(s.SESSION, f"{kind}.{forged}.{sig}", [KEY], NOW) is None
  flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
  assert s.verify(s.SESSION, f"{kind}.{payload}.{flipped}", [KEY], NOW) is None
  assert s.verify(s.SESSION, token, ["x" * 40], NOW) is None


def test_garbage_is_refused_without_raising():
  for token in ("", None, "v1", "v1.a", "v1.a.b.c", "v1.!!.!!", "v1..", "v1.e30.e30"):
    assert s.verify(s.SESSION, token, [KEY], NOW) is None


def test_a_signed_non_object_or_missing_exp_is_refused():
  for claims in ([1], {"u": "alice"}, {"u": "alice", "exp": "9999999999"}, {"u": "a", "exp": True}):
    assert s.verify(s.SESSION, s.sign(s.SESSION, claims, KEY), [KEY], NOW) is None


def test_rotation_accepts_the_previous_key():
  token = s.new_session("alice", OLD, NOW)
  assert s.verify(s.SESSION, token, [KEY, OLD], NOW)
  assert s.verify(s.SESSION, token, [KEY], NOW) is None


def test_state_and_session_tokens_cannot_stand_in_for_each_other():
  state = s.new_state("nonce", "/toys/aoty/", KEY, NOW)
  session = s.new_session("alice", KEY, NOW)
  assert s.verify(s.SESSION, state, [KEY], NOW) is None
  assert s.verify(s.STATE, session, [KEY], NOW) is None
  # Relabelling the kind breaks the signature, since the kind is signed.
  assert s.verify(s.SESSION, "v1" + state[2:], [KEY], NOW) is None


def test_state_expires_after_ten_minutes_and_checks_its_nonce():
  claims = s.verify(s.STATE, s.new_state("abc", "/toys/aoty/", KEY, NOW), [KEY], NOW + 599)
  assert claims["next"] == "/toys/aoty/"
  assert s.nonce_matches(claims, "abc")
  assert not s.nonce_matches(claims, "abd")
  assert not s.nonce_matches(claims, "")
  assert not s.nonce_matches(None, "abc")
  assert s.verify(s.STATE, s.new_state("abc", "/", KEY, NOW), [KEY], NOW + s.STATE_TTL_S) is None


def test_nonces_are_unique_and_url_safe():
  a, b = s.new_nonce(), s.new_nonce()
  assert a != b and len(a) >= 22
  assert all(c.isalnum() or c in "-_" for c in a)


def test_usable_keys_fail_closed_on_short_or_missing_keys():
  assert s.usable_keys({}) == []
  assert s.usable_keys({"session_key": "short"}) == []
  assert s.usable_keys({"session_key": "short", "session_key_previous": OLD}) == []
  assert s.usable_keys({"session_key": KEY}) == [KEY]
  assert s.usable_keys({"session_key": KEY, "session_key_previous": "short"}) == [KEY]
  assert s.usable_keys({"session_key": KEY, "session_key_previous": OLD}) == [KEY, OLD]


def test_parse_cookies():
  got = s.parse_cookies(["a=1", " b = two=2 ", "a=again", "junk", "=x"])
  assert got == {"a": "1", "b": "two=2"}
  assert s.parse_cookies(None) == {}


def test_cookie_attributes():
  c = s.set_cookie(s.SESSION_COOKIE, "tok", "/toys/aoty/", s.SESSION_TTL_S)
  assert c == "__Secure-kamp_aoty_session=tok; Path=/toys/aoty/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax"
  cleared = s.clear_cookie(s.STATE_COOKIE, "/toys/aoty/api/auth/")
  assert cleared.startswith("__Secure-kamp_aoty_state=; Path=/toys/aoty/api/auth/; Max-Age=0;")
  assert "Secure" in cleared and "HttpOnly" in cleared
