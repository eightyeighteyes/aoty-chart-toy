"""Fake sign-in and groups for tools/serve_shell.py (AOTY-52), so group editing can be previewed
with no AWS. It mirrors lambdas/api/handler.py's routes and answers (AOTY-47, AOTY-48), and serves
group chart.json files built by the real lambdas/chart/group_core.py.

Sign-in is a plain preview cookie, not the real signed session: <base>api/auth/start signs you in
as alice (or ?as=<name>, e.g. api/auth/start?as=bob&next=<base>group/full-house to try the
non-owner view) and sends you back to ?next=.

Member names with special answers on add:
  nobody*      404 unknown user          blocked*    409 cannot_add (opted out)
  too-many     429 too_many_new          paused*     503 paused (new users paused)
  busy-busy    503 busy                  lastfm-down 503 lastfm_unavailable
  later*       added, but its sync wasn't queued ("their chart will start loading later")
Seeded groups: full-house (alice's, 25 members, so any add is "full"), bobs-crew (bob's, with
alice in it: no edit controls for alice) and paused-crew (its visits answer as pause_syncs would).
A member is "loading" for MEMBER_READY_S after being added, and each change shows up in the chart
CHART_DELAY_S later, as the chart builder would. A group page's visit (AOTY-55) re-syncs every
member for RESYNC_S, then the chart rebuilds; reloading within the hour is the group cooldown.
"""

import time
import zlib

# serve_shell.py puts lambdas/, lambdas/chart and lambdas/api on sys.path first.
import group_core
from api_core import USERNAME_RE
from common import groups as grp
from common.albumkey import album_key, join_key

COOKIE = "aoty_preview_session"
MEMBER_READY_S = 20
CHART_DELAY_S = 4
RESYNC_S = 45  # a visit's member re-syncs (AOTY-55)
GROUP_COOLDOWN_S = 3600
YEAR = 2026
ADD_REFUSALS = [
  ("nobody", (404, {"error": "unknown user"})),
  ("blocked", (409, {"error": "cannot_add"})),
  ("too-many", (429, {"error": "too_many_new"})),
  ("paused", (503, {"error": "paused"})),
  ("busy-busy", (503, {"error": "busy"})),
  ("lastfm-down", (503, {"error": "lastfm_unavailable"})),
]


def _stamp(uts):
  return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(uts))


def _store(member, n=90):
  """A member's synthetic store: most of the same albums, their own play counts."""
  seed = zlib.crc32(member.encode())
  store = {"version": 1, "year": YEAR, "weeks": {"0": {"plays": {}}}, "albums": {}}
  for i in range(n):
    if (i + seed) % 4 == 0:
      continue
    artist, album = f"Sample Artist {i % 23}", f"Sample Album {i:03d}"
    k = join_key(album_key(artist, album))
    store["weeks"]["0"]["plays"][k] = 60 + (seed >> (i % 16)) % 200 - i
    store["albums"][k] = {"artist": artist, "album": album, "mbid": "", "image": ""}
  return store


def _items(n=90):
  items = {}
  for i in range(n):
    k = join_key(album_key(f"Sample Artist {i % 23}", f"Sample Album {i:03d}"))
    items[k] = {"year": YEAR, "tracks": 4 + i % 11, "tracks_type": "Single" if i % 9 == 4 else "Album",
                "rg_mbid": None}  # fmt: skip
  return items


class FakeGroups:
  def __init__(self, clock=time.time):
    self.clock = clock
    self.groups = {}  # slug -> {name, owner, members: {name: added_at}, changed_at}
    t = clock() - 3600
    self.groups["full-house"] = {"name": "Full House", "owner": "alice", "changed_at": t,
                                 "members": {"alice": t, **{f"member{i:02d}": t for i in range(24)}}}  # fmt: skip
    self.groups["bobs-crew"] = {"name": "Bob's Crew", "owner": "bob", "changed_at": t,
                                "members": {"bob": t, "alice": t, "carol": t}}  # fmt: skip
    self.groups["paused-crew"] = {"name": "Paused Crew", "owner": "bob", "changed_at": t,
                                  "members": {"bob": t, "carol": t}}  # fmt: skip
    self.items = _items()

  # ---- the API -------------------------------------------------------------------------------

  def api(self, method, rest, query, body, ctype, cookies, base):
    """(status, body or None, extra headers) for <base><rest>, or None if it isn't a route here."""
    path = rest.rstrip("/")
    user = cookies.get(COOKIE) or None
    if user and not USERNAME_RE.match(user):
      user = None
    if path == "api/auth/start" and method == "GET":
      name = (query.get("as") or "alice").lower()
      if not USERNAME_RE.match(name):
        return 400, {"error": "invalid username"}, []
      landing = query.get("next") or base
      if not landing.startswith(base):
        landing = base
      return 302, None, [("Location", landing), ("Set-Cookie", f"{COOKIE}={name}; Path={base}; Max-Age=2592000; SameSite=Lax")]
    if path == "api/auth/signout" and method == "POST":
      out = {"signed_out": True}
      if query.get("everywhere") == "1":  # AOTY-57; the fake cookie is just a name, so nothing to revoke
        if not user:
          return 401, {"error": "signed_out"}, []
        out["everywhere"] = True
      return 200, out, [("Set-Cookie", f"{COOKIE}=; Path={base}; Max-Age=0; SameSite=Lax")]
    if path == "api/me" and method == "GET":
      if not user:
        return 401, {"error": "signed_out", "group_writes": True}, []
      return 200, {"username": user, "group_writes": True}, []
    if path == "api/me/groups" and method == "GET":
      if not user:
        return 401, {"error": "signed_out"}, []
      mine = [{"id": s, "name": g["name"], "member_count": len(g["members"]), "updated_at": int(g["changed_at"])}
              for s, g in self.groups.items() if g["owner"] == user]  # fmt: skip
      return 200, {"groups": mine}, []
    if not path.startswith("api/groups"):
      return None
    parts = path.split("/")[2:]  # after api/groups
    if method == "GET" and len(parts) == 1:
      return (*self.get(parts[0]), [])
    if method == "POST" and parts[1:] == ["sync"]:  # public, like the real one (AOTY-55)
      if not (ctype or "").lower().startswith("application/json"):
        return 415, {"error": "send content-type: application/json"}, []
      return (*self.visit(parts[0]), [])
    if method not in ("POST", "PATCH", "DELETE"):
      return 405, {"error": "method not allowed"}, []
    if not (ctype or "").lower().startswith("application/json"):
      return 415, {"error": "send content-type: application/json"}, []
    if not user:
      return 401, {"error": "signed_out"}, []
    now = self.clock()
    if method == "POST" and not parts:
      return (*self.create(user, body, now), [])
    slug = parts[0] if parts else ""
    g = self.groups.get(slug)
    if not g:
      return 404, {"error": "no such group"}, []
    if g["owner"] != user:
      return 403, {"error": "not your group"}, []
    if method == "PATCH" and len(parts) == 1:
      return (*self.rename(slug, body, now), [])
    if method == "DELETE" and len(parts) == 1:
      del self.groups[slug]
      return 204, None, []
    if method == "POST" and parts[1:] == ["members"]:
      return (*self.add(g, user, body, now), [])
    if method == "DELETE" and len(parts) == 3 and parts[1] == "members":
      if g["members"].pop(parts[2].lower(), None) is not None:
        g["changed_at"] = now
      return 204, None, []
    return 404, {"error": "not found"}, []

  def _ready(self, g, now):
    return [m for m, at in g["members"].items() if now - at >= MEMBER_READY_S]

  def get(self, slug):
    g = self.groups.get(slug)
    if not g:
      return 404, {"error": "no such group"}
    members = sorted(g["members"], key=str.casefold)
    now = self.clock()
    syncing = len(members) if now < g.get("resync_until", 0) else 0
    return 200, {"id": slug, "name": g["name"], "owner": g["owner"], "members": members,
                 "ready": len(self._ready(g, now)), "total": len(members), "syncing": syncing}  # fmt: skip

  def visit(self, slug):
    """POST groups/<slug>/sync: every member re-syncs for RESYNC_S (ready members stay ready),
    then the chart rebuilds. A second visit within GROUP_COOLDOWN_S is a cooldown; paused-* groups
    answer as pause_syncs would."""
    g = self.groups.get(slug)
    if not g:
      return 404, {"error": "no such group"}
    if slug.startswith("paused"):
      return 200, {"queued": 0, "reason": "paused"}
    now = self.clock()
    if now - g.get("synced_at", 0) < GROUP_COOLDOWN_S:
      return 200, {"queued": 0, "reason": "cooldown"}
    g["synced_at"], g["resync_until"] = now, now + RESYNC_S
    n = len(g["members"])
    return 200, {"queued": n, "members": {"queued": n}}

  def create(self, user, body, now):
    name = grp.clean_name(body.get("name"))
    if name is None:
      return 400, {"error": "bad_name", "max": grp.NAME_MAX}
    slug = grp.slug_for(name, body.get("slug"))
    if slug is None:
      return 400, {"error": "needs_slug"}
    if slug in self.groups:
      return 409, {"error": "taken"}
    self.groups[slug] = {"name": name, "owner": user, "members": {user: now}, "changed_at": now}
    return 201, {"id": slug, "sync": "queued"}

  def rename(self, slug, body, now):
    name = grp.clean_name(body.get("name"))
    if name is None:
      return 400, {"error": "bad_name", "max": grp.NAME_MAX}
    new = grp.slug_for(name, body.get("slug"))
    if new is None:
      return 400, {"error": "needs_slug"}
    if new != slug and new in self.groups:
      return 409, {"error": "taken"}
    g = self.groups.pop(slug)
    g["name"], g["changed_at"] = name, now
    self.groups[new] = g
    return 200, {"id": new}

  def add(self, g, user, body, now):
    name = str(body.get("username") or "").strip().lower()
    if not USERNAME_RE.match(name):
      return 400, {"error": "invalid username"}
    if name in g["members"]:
      return 200, {"member": name, "added": False}
    if len(g["members"]) >= grp.MAX_MEMBERS:
      return 409, {"error": "full", "max": grp.MAX_MEMBERS}
    for prefix, refusal in ADD_REFUSALS:
      if name.startswith(prefix) and name != user:
        return refusal
    g["members"][name] = now
    g["changed_at"] = now
    sync = "not_queued" if name.startswith("later") else "queued"
    return 201, {"member": name, "added": True, "sync": sync, "status": {"stage": "queued"}}

  # ---- data/groups/<slug>/chart.json ----------------------------------------------------------

  def chart(self, slug):
    """The group's chart.json as the builder would have written it by now, or None (404)."""
    g = self.groups.get(slug)
    if not g:
      return None
    now = self.clock()
    # The builder's last run: the latest change or member becoming ready that it has caught up with.
    events = [g["changed_at"] + CHART_DELAY_S] + [at + MEMBER_READY_S for at in g["members"].values()]
    if g.get("resync_until"):
      events.append(g["resync_until"] + CHART_DELAY_S)  # the rebuild after a visit's re-syncs
    built = max((e for e in events if e <= now), default=None)
    if built is None:
      return None
    ready = [m for m, at in g["members"].items() if at + MEMBER_READY_S <= built]
    stores = {m: _store(m) for m in ready}
    chart, _ = group_core.build_group_chart(stores, self.items, group_id=slug, name=g["name"], year=YEAR,
                                            waiting=len(g["members"]) - len(ready), now=int(built))  # fmt: skip
    return chart
