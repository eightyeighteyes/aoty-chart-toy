"""The api's reads and writes of the groups table (AOTY-48). Layout: common/groups.py.

Every write that changes membership is one TransactWriteItems with the meta item in it, so the
member count, the owner check and the cap of MAX_MEMBERS hold under concurrent requests. Results
are short words the handler turns into answers:

  added | member (already one) | full | missing (no such group) | forbidden (not the owner)
  removed | absent (not a member) | taken (the slug is in use) | changed (lost a race twice)

A cancelled transaction says, per item, which condition failed (CancellationReasons, with the old
item thanks to ReturnValuesOnConditionCheckFailure). TransactionConflict, another transaction on
the same items at that moment, is retried a few times before it's raised.
"""

from common import dynamo
from common import groups as g

CONFLICT_RETRIES = 3


class Changed(Exception):
  """The group changed between the read and the write, twice in a row."""


def _table(deps):
  return deps.env["GROUPS_TABLE"]


def _key(slug, sk):
  return {"pk": {"S": g.pk(slug)}, "sk": {"S": sk}}


def _code(e):
  return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", ""))


def _reasons(e):
  """[(code, old item as plain values or None)] per transaction item, or None if e isn't a
  cancelled transaction."""
  if _code(e) != "TransactionCanceledException":
    return None
  raw = (getattr(e, "response", None) or {}).get("CancellationReasons") or []
  return [(r.get("Code") or "None", dynamo.from_item(r["Item"]) if r.get("Item") else None) for r in raw]


def _transact(deps, items):
  """TransactWriteItems, retrying TransactionConflict. Returns None on success, else the
  cancellation reasons (one per item) when a condition failed."""
  for attempt in range(CONFLICT_RETRIES + 1):
    try:
      deps.ddb.transact_write_items(TransactItems=items)
      return None
    except Exception as e:
      reasons = _reasons(e)
      if reasons is None:
        raise
      if any(code == "ConditionalCheckFailed" for code, _ in reasons):
        return reasons
      if attempt == CONFLICT_RETRIES or not any(code == "TransactionConflict" for code, _ in reasons):
        raise
      deps.sleep(0.05 * 2**attempt)
  raise AssertionError("unreachable")


def _failed(reasons, i):
  return reasons[i][0] == "ConditionalCheckFailed"


def _meta_verdict(old, owner):
  """Why a meta condition failed: missing, forbidden or full."""
  if not old:
    return "missing"
  if old.get("owner") != owner:
    return "forbidden"
  return "full"


# ---- reads ----------------------------------------------------------------------------------


def load(deps, slug):
  """(meta, [members]) for a group, consistent; (None, []) if there's no such group."""
  items, kwargs = [], {
    "TableName": _table(deps), "KeyConditionExpression": "pk = :pk",
    "ExpressionAttributeValues": {":pk": {"S": g.pk(slug)}}, "ConsistentRead": True,
  }  # fmt: skip
  while True:
    out = deps.ddb.query(**kwargs)
    items += [dynamo.from_item(it) for it in out.get("Items", [])]
    if not out.get("LastEvaluatedKey"):
      break
    kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]
  meta, members = g.split_items(items)
  return (meta, members) if meta else (None, [])


def owned_by(deps, owner):
  """[{id, name, member_count, updated_at}] for a person's groups, newest change first."""
  out_rows, kwargs = [], {
    "TableName": _table(deps), "IndexName": g.BY_OWNER, "KeyConditionExpression": "#o = :o",
    "ExpressionAttributeNames": {"#o": "owner"}, "ExpressionAttributeValues": {":o": {"S": owner}},
  }  # fmt: skip
  while True:
    out = deps.ddb.query(**kwargs)
    for it in out.get("Items", []):
      row = dynamo.from_item(it)
      slug = g.slug_of(row.get("pk"))
      if slug:
        out_rows.append({"id": slug, "name": row.get("name", ""), "member_count": row.get("member_count", 0),
                         "updated_at": row.get("updated_at")})  # fmt: skip
    if not out.get("LastEvaluatedKey"):
      break
    kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]
  return sorted(out_rows, key=lambda r: (-(r["updated_at"] or 0), r["id"]))


BATCH_GET_MAX = 100
BATCH_RETRIES = 4


def member_of(deps, username):
  """[{id, name, owner}] for the groups username is in, by name; groups they own are left out
  (owned_by lists those). AOTY-49's "groups you're in".

  by_member is keys only and eventually consistent, so the metas are read after it: a group deleted
  or moved since the index read has no meta and is dropped."""
  slugs, kwargs = [], {
    "TableName": _table(deps), "IndexName": g.BY_MEMBER, "KeyConditionExpression": "username = :u",
    "ExpressionAttributeValues": {":u": {"S": username}},
  }  # fmt: skip
  while True:
    out = deps.ddb.query(**kwargs)
    for it in out.get("Items", []):
      slug = g.slug_of(dynamo.from_item(it).get("pk"))
      if slug and slug not in slugs:
        slugs.append(slug)
    if not out.get("LastEvaluatedKey"):
      break
    kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]
  rows, table = [], _table(deps)
  wanted = {"ProjectionExpression": "pk, #n, #o", "ExpressionAttributeNames": {"#n": "name", "#o": "owner"}}
  for i in range(0, len(slugs), BATCH_GET_MAX):
    keys = [_key(s, g.META) for s in slugs[i : i + BATCH_GET_MAX]]
    for _ in range(BATCH_RETRIES + 1):
      out = deps.ddb.batch_get_item(RequestItems={table: {"Keys": keys, **wanted}})
      rows += [dynamo.from_item(it) for it in (out.get("Responses") or {}).get(table, [])]
      keys = ((out.get("UnprocessedKeys") or {}).get(table) or {}).get("Keys") or []
      if not keys:
        break
      deps.sleep(0.05)
    else:
      raise RuntimeError("batch_get_item left keys unprocessed")
  groups = [{"id": g.slug_of(r.get("pk")), "name": r.get("name", ""), "owner": r.get("owner")} for r in rows]
  groups = [x for x in groups if x["id"] and x["owner"] != username]
  return sorted(groups, key=lambda x: (x["name"].casefold(), x["id"]))


def is_member(deps, slug, username):
  out = deps.ddb.get_item(TableName=_table(deps), Key=_key(slug, g.member_sk(username)), ConsistentRead=True)
  return bool(out.get("Item"))


# ---- writes ---------------------------------------------------------------------------------


def _meta_put(slug, fields):
  return {"Put": {
    "TableName": None, "Item": dynamo.to_item({"pk": g.pk(slug), "sk": g.META, **fields}),
    "ConditionExpression": "attribute_not_exists(pk)", "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
  }}  # fmt: skip


def _member_put(slug, username, now, condition=True):
  put = {"TableName": None, "Item": dynamo.to_item({"pk": g.pk(slug), "sk": g.member_sk(username),
                                                     "username": username, "added_at": now})}  # fmt: skip
  if condition:
    put |= {
      "ConditionExpression": "attribute_not_exists(pk)",
      "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }
  return {"Put": put}


def _with_table(deps, items):
  for it in items:
    next(iter(it.values()))["TableName"] = _table(deps)
  return items


def create(deps, slug, name, owner, now, background=None):
  """created or taken. The owner is the first member. background: the new group's starting page
  background, a common.backgrounds engine (AOTY-96: random, picked by the caller)."""
  meta = {"name": name, "owner": owner, "member_count": 1, "created_at": now, "updated_at": now,
          "chart_requested_at": now}  # fmt: skip
  if background:
    meta["background"] = background
  reasons = _transact(deps, _with_table(deps, [_meta_put(slug, meta), _member_put(slug, owner, now)]))
  return "created" if reasons is None else "taken"


def add_member(deps, slug, owner, username, now):
  """added, member, full, missing or forbidden."""
  items = _with_table(deps, [
    {"Update": {
      "TableName": None, "Key": _key(slug, g.META),
      "UpdateExpression": "ADD member_count :one SET updated_at = :now, chart_requested_at = :now",
      "ConditionExpression": "attribute_exists(pk) AND #o = :me AND member_count < :max",
      "ExpressionAttributeNames": {"#o": "owner"},
      "ExpressionAttributeValues": dynamo.to_item({":one": 1, ":now": now, ":me": owner, ":max": g.MAX_MEMBERS}),
      "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }},
    _member_put(slug, username, now),
  ])  # fmt: skip
  reasons = _transact(deps, items)
  if reasons is None:
    return "added"
  meta_failed, member_failed = _failed(reasons, 0), _failed(reasons, 1)
  verdict = _meta_verdict(reasons[0][1], owner) if meta_failed else None
  if verdict in ("missing", "forbidden"):
    return verdict  # never say whether a stranger's group has someone in it
  return "member" if member_failed else verdict


def remove_member(deps, slug, caller, username, now):
  """removed, absent, missing or forbidden.

  The owner may remove anyone. Anyone else may remove only themselves: leaving (AOTY-49). Either
  way it's the same transaction, so the count, the cap and the rebuild stamp live in one place."""
  meta_update = {
    "TableName": None, "Key": _key(slug, g.META),
    "UpdateExpression": "ADD member_count :minus SET updated_at = :now, chart_requested_at = :now",
    "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
  }  # fmt: skip
  if caller == username:
    meta_update |= {"ConditionExpression": "attribute_exists(pk)",
                    "ExpressionAttributeValues": dynamo.to_item({":minus": -1, ":now": now})}  # fmt: skip
  else:
    meta_update |= {
      "ConditionExpression": "attribute_exists(pk) AND #o = :me", "ExpressionAttributeNames": {"#o": "owner"},
      "ExpressionAttributeValues": dynamo.to_item({":minus": -1, ":now": now, ":me": caller}),
    }  # fmt: skip
  items = _with_table(deps, [
    {"Update": meta_update},
    {"Delete": {"TableName": None, "Key": _key(slug, g.member_sk(username)),
                "ConditionExpression": "attribute_exists(pk)"}},
  ])  # fmt: skip
  reasons = _transact(deps, items)
  if reasons is None:
    return "removed"
  if _failed(reasons, 0):
    return "missing" if not reasons[0][1] else "forbidden"
  return "absent"


def _owned(deps, slug, owner):
  """(meta, members) of a group the caller owns, or the word for why not."""
  meta, members = load(deps, slug)
  if meta is None:
    return None, "missing"
  if meta.get("owner") != owner:
    return None, "forbidden"
  return (meta, members), None


def _meta_delete(slug, owner, count, theme_set_at=False, background_set_at=False):
  """Delete the meta only if nothing changed since it was read (the owner and the count).

  theme_set_at (a rename, AOTY-76): also only if no theme was saved since; None when the meta had
  none. A rename copies the meta it read to the new slug, so a theme saved in between would
  otherwise be lost. background_set_at (AOTY-96) is guarded the same way. The default (False)
  leaves that field out of the condition."""
  condition, values = "#o = :me AND member_count = :n", {":me": owner, ":n": count}
  for at, unset, same, ref in ((theme_set_at, THEME_UNSET, THEME_SAME, ":ts"),
                               (background_set_at, BACKGROUND_UNSET, BACKGROUND_SAME, ":bs")):  # fmt: skip
    if at is None:
      condition += unset
    elif at is not False:
      condition += same
      values[ref] = at
  return {"Delete": {
    "TableName": None, "Key": _key(slug, g.META),
    "ConditionExpression": condition,
    "ExpressionAttributeNames": {"#o": "owner"},
    "ExpressionAttributeValues": dynamo.to_item(values),
  }}  # fmt: skip


THEME_UNSET = " AND attribute_not_exists(theme_set_at)"
THEME_SAME = " AND theme_set_at = :ts"
BACKGROUND_UNSET = " AND attribute_not_exists(background_set_at)"
BACKGROUND_SAME = " AND background_set_at = :bs"


def set_style(deps, slug, owner, field, value, now):
  """set, missing or forbidden: one of the owner's page style fields on the group's meta, with
  field_set_at: "theme" (AOTY-76, a common.themes id) or "background" (AOTY-96, a
  common.backgrounds id, "none" included). The caller has checked the value."""
  try:
    deps.ddb.update_item(
      TableName=_table(deps), Key=_key(slug, g.META),
      UpdateExpression="SET #f = :t, #at = :now",
      ConditionExpression="attribute_exists(pk) AND #o = :me",
      ExpressionAttributeNames={"#o": "owner", "#f": field, "#at": f"{field}_set_at"},
      ExpressionAttributeValues=dynamo.to_item({":t": value, ":now": now, ":me": owner}),
      ReturnValuesOnConditionCheckFailure="ALL_OLD",
    )  # fmt: skip
  except Exception as e:
    if _code(e) != "ConditionalCheckFailedException":
      raise
    old = (getattr(e, "response", None) or {}).get("Item")
    return "forbidden" if old else "missing"
  return "set"


def delete(deps, slug, owner):
  """deleted, missing or forbidden. Raises Changed if members kept changing under it."""
  for _ in range(2):
    found, why = _owned(deps, slug, owner)
    if why:
      return why
    meta, members = found
    items = [_meta_delete(slug, owner, meta.get("member_count", 0))]
    items += [{"Delete": {"TableName": None, "Key": _key(slug, g.member_sk(m))}} for m in members]
    if _transact(deps, _with_table(deps, items)) is None:
      return "deleted"
  raise Changed(slug)


def rename(deps, slug, new_slug, name, owner, now):
  """(word, slug): renamed, taken, missing or forbidden, and the group's slug afterwards.

  The same slug changes only the name. A new slug moves the group: one transaction puts the meta
  and every member under the new key and deletes the old ones (at most 2 x 26 = 52 items, under
  DynamoDB's 100). The chart's requested and built stamps start over with the move.
  """
  if new_slug == slug:
    try:
      deps.ddb.update_item(
        TableName=_table(deps), Key=_key(slug, g.META),
        UpdateExpression="SET #n = :name, updated_at = :now, chart_requested_at = :now",
        ConditionExpression="attribute_exists(pk) AND #o = :me",
        ExpressionAttributeNames={"#n": "name", "#o": "owner"},
        ExpressionAttributeValues=dynamo.to_item({":name": name, ":now": now, ":me": owner}),
        ReturnValuesOnConditionCheckFailure="ALL_OLD",
      )  # fmt: skip
    except Exception as e:
      if _code(e) != "ConditionalCheckFailedException":
        raise
      old = (getattr(e, "response", None) or {}).get("Item")
      return ("forbidden" if old else "missing"), slug
    return "renamed", slug
  for _ in range(2):
    found, why = _owned(deps, slug, owner)
    if why:
      return why, slug
    meta, members = found
    # chart_read_at (AOTY-55) goes too: the new slug has no chart yet, so nothing may skip its build.
    # sync_requested_at stays: it's the same members, synced just as recently.
    fields = {k: v for k, v in meta.items() if k not in ("pk", "sk", "chart_built_at", "chart_read_at")}
    fields |= {"name": name, "updated_at": now, "chart_requested_at": now}
    items = [_meta_put(new_slug, fields),
             _meta_delete(slug, owner, meta.get("member_count", 0), meta.get("theme_set_at"),
                          meta.get("background_set_at"))]  # fmt: skip
    for m in members:
      items.append(_member_put(new_slug, m, now, condition=False))
      items.append({"Delete": {"TableName": None, "Key": _key(slug, g.member_sk(m))}})
    reasons = _transact(deps, _with_table(deps, items))
    if reasons is None:
      return "renamed", new_slug
    if _failed(reasons, 0):
      return "taken", slug
  raise Changed(slug)


# ---- a visit's member syncs (AOTY-55) -------------------------------------------------------
#
# The meta's sync_requested_at is the group's cooldown: claimed atomically, so a burst of visits
# to a shared link queues the members once.

CLAIM_SYNC = (
  "attribute_exists(pk) AND (attribute_not_exists(sync_requested_at) OR sync_requested_at <= :cool)"
)
RELEASE_SYNC = "sync_requested_at = :now"


def claim_sync(deps, slug, now, cooldown):
  """(True, previous sync_requested_at or 0) when this visit may sync the members, else
  (False, "cooldown") or (False, "missing") for no such group."""
  try:
    out = deps.ddb.update_item(
      TableName=_table(deps), Key=_key(slug, g.META),
      UpdateExpression="SET sync_requested_at = :now",
      ConditionExpression=CLAIM_SYNC,
      ExpressionAttributeValues=dynamo.to_item({":now": now, ":cool": now - cooldown}),
      ReturnValues="UPDATED_OLD", ReturnValuesOnConditionCheckFailure="ALL_OLD",
    )  # fmt: skip
  except Exception as e:
    if _code(e) != "ConditionalCheckFailedException":
      raise
    old = (getattr(e, "response", None) or {}).get("Item")
    return False, ("cooldown" if old else "missing")
  old = dynamo.from_item((out or {}).get("Attributes") or {})
  return True, old.get("sync_requested_at") or 0


def release_sync(deps, slug, now, previous):
  """Give the claim back after a visit that queued nobody because the sends failed, so the next
  visit tries again. Only our own claim: a later visit's is left alone. True if released."""
  try:
    deps.ddb.update_item(
      TableName=_table(deps), Key=_key(slug, g.META),
      UpdateExpression="SET sync_requested_at = :prev",
      ConditionExpression=RELEASE_SYNC,
      ExpressionAttributeValues=dynamo.to_item({":now": now, ":prev": previous or 0}),
    )  # fmt: skip
  except Exception as e:
    if _code(e) != "ConditionalCheckFailedException":
      raise
    return False
  return True


# ---- sign-in revocation (AOTY-57): session#<username> items, layout in common/groups.py ----------

REVOKE = "attribute_not_exists(revoked_at) OR revoked_at < :now"


def _session_key(username):
  return {"pk": {"S": g.session_pk(username)}, "sk": {"S": g.META}}


def revoked_at(deps, username, consistent=True):
  """username's revocation cutoff: None if they never revoked, a number, or the raw attribute when
  it isn't a number (session.is_revoked counts that as revoked). Raises when it can't be read."""
  out = deps.ddb.get_item(TableName=_table(deps), Key=_session_key(username), ConsistentRead=consistent)
  attr = (out.get("Item") or {}).get("revoked_at")
  if attr is None:
    return None
  try:
    return float(attr["N"])
  except (KeyError, TypeError, ValueError):
    return attr


def revoke(deps, username, now):
  """Revoke every session username signed in at or before now. The cutoff only moves forward."""
  try:
    deps.ddb.update_item(
      TableName=_table(deps), Key=_session_key(username),
      UpdateExpression="SET revoked_at = :now", ConditionExpression=REVOKE,
      ExpressionAttributeValues={":now": {"N": str(int(now))}},
    )  # fmt: skip
  except Exception as e:
    if _code(e) != "ConditionalCheckFailedException":  # already revoked at or after now: done
      raise
