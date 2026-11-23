"""tools/prewarm.py (AOTY-42): candidates keyed as sync keys them, in the lane's message format."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import prewarm  # noqa: E402

from common.albumkey import album_key, join_key  # noqa: E402


class FakeClient:
  calls = 0

  def __init__(self, pages):
    self.pages = pages

  def call(self, method, **params):
    self.calls += 1
    return self.pages.get((method, params.get("page"), params.get("artist")), {})


def test_gather_keys_by_lastfm_names_and_keeps_the_first_spelling():
  pages = {
    ("tag.gettopalbums", 1, None): {"albums": {"album": [
      {"name": "Singin' to an Empty Chair (Deluxe)", "artist": {"name": "Ratboys"}, "mbid": "m1"},
      {"name": "(null)", "artist": {"name": "X"}},
    ]}},
    ("chart.gettopartists", 1, None): {"artists": {"artist": [{"name": "Ratboys"}]}},
    ("artist.gettopalbums", None, "Ratboys"): {"topalbums": {"album": {
      "name": "Singin' to an Empty Chair", "artist": {"name": "Ratboys"},
    }}},  # a single album comes back as a bare object
  }  # fmt: skip
  found = prewarm.gather(FakeClient(pages), year=2026, tag_pages=2, artist_pages=1, albums_per_artist=5,
                         log=lambda *a: None)  # fmt: skip
  key = join_key(album_key("Ratboys", "Singin' to an Empty Chair"))
  assert list(found) == [key]  # the deluxe edition and the plain title are one album; "(null)" is dropped
  assert found[key] == {"artist": "Ratboys", "album": "Singin' to an Empty Chair (Deluxe)", "mbid": "m1"}


def test_message_matches_the_sync_workers():
  key = join_key(album_key("Ratboys", "Singin' to an Empty Chair"))
  msg = prewarm.message(key, {"artist": "Ratboys", "album": "Singin' to an Empty Chair", "mbid": ""})
  assert set(msg) == {"artist_key", "title_key", "artist", "album", "mbid"}
  assert (msg["artist_key"], msg["title_key"]) == album_key("Ratboys", "Singin' to an Empty Chair")
