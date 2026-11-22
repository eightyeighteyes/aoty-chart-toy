"""Plain Python values <-> DynamoDB attribute values, for the low-level boto3 client.

Only the types the albums table uses: strings, integers, floats, booleans and null.
"""


def to_item(item):
  """A plain item (str, int, float, bool, None values) as DynamoDB attribute values."""
  out = {}
  for k, v in item.items():
    if v is None:
      out[k] = {"NULL": True}
    elif isinstance(v, bool):
      out[k] = {"BOOL": v}
    elif isinstance(v, int | float):
      out[k] = {"N": str(v)}
    else:
      out[k] = {"S": str(v)}
  return out


def from_item(item):
  """DynamoDB attribute values as plain Python; integers stay int. Other types are dropped."""
  out = {}
  for k, v in item.items():
    if "S" in v:
      out[k] = v["S"]
    elif "N" in v:
      n = v["N"]
      out[k] = int(n) if n.lstrip("-").isdigit() else float(n)
    elif "BOOL" in v:
      out[k] = v["BOOL"]
    elif "NULL" in v:
      out[k] = None
  return out
