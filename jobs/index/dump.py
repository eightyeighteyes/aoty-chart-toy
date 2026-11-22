"""Fetching and streaming the MusicBrainz JSON dump (AOTY-13, AOTY-20). Standard library only.

Shared by the index build (jobs/index/build.py) and the spike tool (tools/mb_index_dump.py).
The dump comes from data.metabrainz.org, not the rate-limited web service, so none of this
counts against the MusicBrainz API budget.
"""

import hashlib
import tarfile
import time
import urllib.request

DUMPS = "https://data.metabrainz.org/pub/musicbrainz/data/json-dumps"
RG_ARCHIVE = "release-group.tar.xz"
RG_MEMBER = "mbdump/release-group"


def http(url, user_agent, start=0, timeout=60):
  headers = {"User-Agent": user_agent, **({"Range": f"bytes={start}-"} if start else {})}
  return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def latest_dump(user_agent):
  """The newest dump's folder name, e.g. 20261121-001001."""
  with http(f"{DUMPS}/LATEST", user_agent) as r:
    return r.read().decode().strip()


def expected_sha256(dump, name, user_agent):
  with http(f"{DUMPS}/{dump}/SHA256SUMS", user_agent) as r:
    sums = r.read().decode()
  for line in sums.splitlines():
    parts = line.split()
    if parts and parts[-1].lstrip("*") == name:
      return parts[0]
  raise RuntimeError(f"{name} is not in {dump}/SHA256SUMS")


def download(dump, name, dest, user_agent, log=print):
  """Fetch DUMPS/dump/name to dest, resuming a .part file and checking SHA256. Returns stats."""
  want = expected_sha256(dump, name, user_agent)
  if dest.exists():
    log(f"{dest} already downloaded")
    return {"download_s": 0, "resumed_from": None, "sha256": want}
  part = dest.with_suffix(dest.suffix + ".part")
  start = part.stat().st_size if part.exists() else 0
  t0 = time.monotonic()
  done = start
  with http(f"{DUMPS}/{dump}/{name}", user_agent, start) as r, open(part, "ab") as f:
    total = start + int(r.headers.get("Content-Length", 0))
    last = 0.0
    while chunk := r.read(1 << 20):
      f.write(chunk)
      done += len(chunk)
      if time.monotonic() - last > 30:
        last = time.monotonic()
        mbps = (done - start) / 1e6 / max(last - t0, 1e-9)
        log(f"  {done / 1e6:,.0f} / {total / 1e6:,.0f} MB  {mbps:.1f} MB/s")
  seconds = time.monotonic() - t0
  h = hashlib.sha256()
  with open(part, "rb") as f:
    while chunk := f.read(1 << 24):
      h.update(chunk)
  if h.hexdigest() != want:
    part.unlink()
    raise RuntimeError(f"SHA256 mismatch for {name} in {dump}; deleted the download")
  part.rename(dest)
  return {
    "download_s": round(seconds, 1),
    "download_mbps": round((done - start) / 1e6 / max(seconds, 1e-9), 2),
    "resumed_from": start or None,
    "sha256": want,
  }


def dump_lines(fileobj, member_name):
  """Yield the JSON lines of one member of a streamed .tar.xz."""
  with tarfile.open(fileobj=fileobj, mode="r|xz") as tar:
    for member in tar:
      if member.name == member_name:
        yield from tar.extractfile(member)
        return
  raise RuntimeError(f"{member_name} not found in the archive")
