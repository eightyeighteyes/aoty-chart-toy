"""Groups (AOTY-48): the table's layout and the pure rules shared by the api, the chart builder and
tools/beta_group.py.

A group is addressed by a readable slug made from its name (user decision 2026-11-23: guessable is
fine, it's public Last.fm data), and renaming it moves it to the new name's slug (2026-11-24).

The groups table (kamp-aoty-<env>-groups), one partition per group:
  pk = group#<slug>, sk = meta              {name, owner, member_count, created_at, updated_at,
                                             chart_requested_at, chart_built_at}
  pk = group#<slug>, sk = member#<username> {username, added_at}
  GSI by_member: username -> pk (keys only): which groups a user is in
  GSI by_owner:  owner -> pk (sparse: only meta items have owner), with name, member_count and
                 updated_at: a person's own groups

Members are their own items, not a list on the meta item, because DynamoDB can't index into a
list: the reverse lookup ("which groups is alice in?") needs one item per membership. The meta's
member_count is kept in the same transaction as each member write, and its condition enforces
the cap of MAX_MEMBERS, so concurrent adds can't pass it.
"""

import re
import unicodedata

MAX_MEMBERS = 25
NAME_MAX = 60
SLUG_MAX = 50
# Lowercase letters, digits and inner dashes, 1-50: what the page's route and the S3 key accept.
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")
META = "meta"
MEMBER_PREFIX = "member#"
BY_MEMBER = "by_member"
BY_OWNER = "by_owner"
ACTIVE = frozenset({"queued", "syncing", "retrying"})  # as api_core.ACTIVE
UNAVAILABLE = frozenset({"not_found", "private"})


def slugify(name):
  """The URL form of a name: "New Bands for Old Heads!" -> "new-bands-for-old-heads". "" when
  nothing is left (a name with no ASCII letters or digits, like "日本"): the caller asks for a slug."""
  text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
  return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:SLUG_MAX].strip("-")


def clean_name(raw):
  """The group's display name, trimmed with inner whitespace collapsed; None unless 1-60
  characters, or if it holds control characters."""
  if not isinstance(raw, str):
    return None
  name = " ".join(raw.split())
  if not name or len(name) > NAME_MAX or any(unicodedata.category(c) == "Cc" for c in name):
    return None
  return name


def slug_for(name, requested=None):
  """The slug for a group called name, or the one the caller asked for. None if it isn't usable."""
  slug = requested.strip().lower() if isinstance(requested, str) and requested.strip() else slugify(name)
  return slug if SLUG_RE.match(slug) else None


def pk(slug):
  return f"group#{slug}"


def member_sk(username):
  return f"{MEMBER_PREFIX}{username}"


def slug_of(pk_value):
  """group#<slug> -> slug; None for anything else."""
  if isinstance(pk_value, str) and pk_value.startswith("group#"):
    slug = pk_value[len("group#") :]
    return slug if SLUG_RE.match(slug) else None
  return None


def split_items(items):
  """(meta or None, [member usernames, sorted]) from a group partition's plain items."""
  meta, members = None, []
  for it in items:
    sk = it.get("sk", "")
    if sk == META:
      meta = it
    elif sk.startswith(MEMBER_PREFIX):
      members.append(sk[len(MEMBER_PREFIX) :])
  return meta, sorted(members, key=str.casefold)


def member_state(row):
  """ready, waiting or unavailable, for one member's users row (the chart builder's rule, so the
  api's ready count agrees with the chart). A re-sync of a member who already has a chart keeps
  them ready; a first sync still running is waiting."""
  state = row.get("state")
  if state in UNAVAILABLE:
    return "unavailable"
  if not row.get("sync_finished_at"):
    return "waiting"
  if state in ACTIVE and not row.get("chart_built_at"):
    return "waiting"
  return "ready"
