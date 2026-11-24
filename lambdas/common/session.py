"""Signed tokens and cookies for sign-in (AOTY-47). Pure: no I/O, no clock of its own.

A token is "<kind>.<payload>.<sig>": payload is base64url (unpadded) compact JSON, sig is
base64url HMAC-SHA256 over "<kind>.<payload>". The kind is signed too, so a state token can never
pass as a session and vice versa:

  v1  a session, {u, iat, exp}: the Last.fm username (lowercased), 30 days
  s1  a sign-in in progress, {n, next, exp}: the nonce that must come back in the callback path,
      and where to land afterwards, 10 minutes

Keys are SSM SecureStrings (session_key, plus session_key_previous during a rotation). Signing
always uses the current key; verifying tries current, then previous. A key shorter than
MIN_KEY_BYTES is treated as missing, so a placeholder value can't quietly sign sessions.

Revocation (AOTY-57): "sign out everywhere" stores a per-user cutoff, revoked_at (epoch seconds),
and a session whose signed iat is at or before it is revoked. A cutoff rather than a counter
signed into the cookie, so the cookie format is unchanged and sessions issued before this existed
are covered too. The cost is that a sign-in in the very second of the revoke is revoked as well:
it fails safe, and signing in again takes a Last.fm round trip.
"""

import base64
import hashlib
import hmac
import json
import secrets

SESSION, STATE = "v1", "s1"
SESSION_COOKIE = "__Secure-kamp_aoty_session"
STATE_COOKIE = "__Secure-kamp_aoty_state"
SESSION_TTL_S = 30 * 86400
STATE_TTL_S = 600
MIN_KEY_BYTES = 32
KEY_NAMES = ("session_key", "session_key_previous")


def _b64(raw):
  return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text):
  return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key, message):
  return hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()


def usable_keys(values):
  """[current, previous] from {name: value} (Secrets.get()), keeping only keys long enough.

  An empty list means sign-in can't work right now: callers answer 503, never 401, so an SSM
  outage doesn't look like being signed out. A short current key also empties the list: signing
  with the previous key alone would make a rotation that set a bad value look like it worked.
  """
  current = values.get(KEY_NAMES[0]) or ""
  if len(current.encode()) < MIN_KEY_BYTES:
    return []
  previous = values.get(KEY_NAMES[1]) or ""
  return [current] + ([previous] if len(previous.encode()) >= MIN_KEY_BYTES else [])


def sign(kind, claims, key):
  payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
  message = f"{kind}.{payload}"
  return f"{message}.{_b64(_mac(key, message))}"


def verify(kind, token, keys, now):
  """The claims of a token of this kind signed by one of keys and not expired, else None."""
  if not token or not keys:
    return None
  parts = token.split(".")
  if len(parts) != 3 or parts[0] != kind:
    return None
  message = f"{parts[0]}.{parts[1]}"
  # Compare the encoded text, not decoded bytes: base64's spare trailing bits would otherwise let
  # several spellings of one signature pass.
  given = parts[2].encode()
  if not any(hmac.compare_digest(given, _b64(_mac(k, message)).encode()) for k in keys):
    return None
  try:
    claims = json.loads(_unb64(parts[1]))
  except ValueError:
    return None
  if not isinstance(claims, dict):
    return None
  exp = claims.get("exp")
  if not isinstance(exp, int) or isinstance(exp, bool) or exp <= now:
    return None
  return claims


def is_revoked(claims, revoked_at):
  """Whether "sign out everywhere" has revoked this verified session.

  revoked_at: None when the user never revoked, else the stored cutoff. Anything that isn't a
  number (a hand-written runbook value of the wrong type, say) revokes: fail closed. So does a
  session without a usable iat.
  """
  if revoked_at is None:
    return False
  if isinstance(revoked_at, bool) or not isinstance(revoked_at, (int, float)):
    return True
  iat = (claims or {}).get("iat")
  if isinstance(iat, bool) or not isinstance(iat, int):
    return True
  return iat <= revoked_at


def new_session(username, key, now):
  return sign(SESSION, {"u": username.lower(), "iat": int(now), "exp": int(now) + SESSION_TTL_S}, key)


def new_nonce():
  return secrets.token_urlsafe(16)  # 128 bits


def new_state(nonce, next_path, key, now):
  return sign(STATE, {"n": nonce, "next": next_path, "exp": int(now) + STATE_TTL_S}, key)


def nonce_matches(claims, nonce):
  expected = (claims or {}).get("n")
  if not isinstance(expected, str) or not nonce:
    return False
  return hmac.compare_digest(expected.encode(), nonce.encode())


def parse_cookies(cookies):
  """{name: value} from payload v2's event["cookies"] ("name=value" strings). The first of a
  repeated name wins: browsers send the most specific path first."""
  out = {}
  for c in cookies or ():
    name, sep, value = c.partition("=")
    name = name.strip()
    if sep and name and name not in out:
      out[name] = value.strip()
  return out


def set_cookie(name, value, path, max_age):
  return f"{name}={value}; Path={path}; Max-Age={int(max_age)}; HttpOnly; Secure; SameSite=Lax"


def clear_cookie(name, path):
  """Clears only the cookie set with exactly this path."""
  return (
    f"{name}=; Path={path}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Secure; SameSite=Lax"
  )
