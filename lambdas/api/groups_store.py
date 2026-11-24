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


def create(deps, slug, name, owner, now):
  """created or taken. The owner is the first member."""
  meta = {"name": name, "owner": owner, "member_count": 1, "created_at": now, "updated_at": now,
          "chart_requested_at": now}  # fmt: skip
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


def remove_member(deps, slug, owner, username, now):
  """removed, absent, missing or forbidden."""
  items = _with_table(deps, [
    {"Update": {
      "TableName": None, "Key": _key(slug, g.META),
      "UpdateExpression": "ADD member_count :minus SET updated_at = :now, chart_requested_at = :now",
      "ConditionExpression": "attribute_exists(pk) AND #o = :me",
      "ExpressionAttributeNames": {"#o": "owner"},
      "ExpressionAttributeValues": dynamo.to_item({":minus": -1, ":now": now, ":me": owner}),
      "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
    }},
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


def _meta_delete(slug, owner, count):
  """Delete the meta only if nothing changed since it was read (the owner and the count)."""
  return {"Delete": {
    "TableName": None, "Key": _key(slug, g.META),
    "ConditionExpression": "#o = :me AND member_count = :n",
    "ExpressionAttributeNames": {"#o": "owner"},
    "ExpressionAttributeValues": dynamo.to_item({":me": owner, ":n": count}),
  }}  # fmt: skip


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
    fields = {k: v for k, v in meta.items() if k not in ("pk", "sk", "chart_built_at")}
    fields |= {"name": name, "updated_at": now, "chart_requested_at": now}
    items = [_meta_put(new_slug, fields), _meta_delete(slug, owner, meta.get("member_count", 0))]
    for m in members:
      items.append(_member_put(new_slug, m, now, condition=False))
      items.append({"Delete": {"TableName": None, "Key": _key(slug, g.member_sk(m))}})
    reasons = _transact(deps, _with_table(deps, items))
    if reasons is None:
      return "renamed", new_slug
    if _failed(reasons, 0):
      return "taken", slug
  raise Changed(slug)
