"""Album identity shared by M0 (aoty.py) and the M1 Lambdas.

A key is (artist_key, title_key) = (norm(artist), norm(base_title(album))), the same pair the
`albums` table is keyed by (infra/modules/stack/tables.tf). Standard library only: the Lambdas
ship without `requests`.

Import it as `common.albumkey` everywhere (aoty.py and tests put lambdas/ on sys.path), so the
module has one name and one copy of its state.
"""

import re
import unicodedata

# Edition suffixes Last.fm often has but MusicBrainz release-group titles don't,
# e.g. "Album (Deluxe Edition)" or "Album [Remastered 2011]".
_EDITION_RE = re.compile(
  r"\s*[\(\[][^\)\]]*\b(deluxe|edition|remaster(ed)?|expanded|anniversary|bonus|"
  r"special|version|reissue)\b[^\)\]]*[\)\]]\s*$",
  re.IGNORECASE,
)

# Joins the pair into one string key for JSON objects; never appears in normalized text.
SEP = "␞"


def norm(s):
  """Loose comparison key: case, curly quotes, punctuation and spacing don't matter."""
  s = unicodedata.normalize("NFKC", s).casefold()
  s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
  s = re.sub(r"[^\w']+", " ", s)
  return " ".join(s.split())


def base_title(album):
  return _EDITION_RE.sub("", album).strip() or album


def _key_part(s):
  """norm(s), or for an all-punctuation name ("!!!", "( )", "...") the casefolded name itself.

  norm strips punctuation, so those would become "", which DynamoDB rejects as a key and which
  would merge unrelated albums. Returns "" only for a blank name.
  """
  return norm(s) or " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def album_key(artist, album):
  """The (artist_key, title_key) pair for an album artist and a Last.fm album title.

  Both halves are non-empty unless the name itself is blank; callers skip those.
  """
  return _key_part(artist), _key_part(base_title(album))


def is_complete(pair):
  return bool(pair[0]) and bool(pair[1])


def join_key(pair):
  return f"{pair[0]}{SEP}{pair[1]}"


def split_key(key):
  artist_key, title_key = key.split(SEP, 1)
  return artist_key, title_key
