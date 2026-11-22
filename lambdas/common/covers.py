"""Cover URLs shared by M0 (build_page.py) and the chart builder (AOTY-21). Standard library only.

A cover is one URL to try first plus a chain of fallbacks the page walks on a load error,
ending in the artist-name tile (web/src/app.js).
"""

import re

_LASTFM_IMAGE = re.compile(r"(https://[^/]+/i/u/)(?:[^/]+/)?([^/]+)$")
CAA_SIZE = "front-250"
# getRecentTracks serves covers from lastfm-img.freetls.fastly.net; album.getInfo (M0) from
# lastfm.freetls.fastly.net, the only one kamp.fm's CSP allows. Same paths, same images.
LASTFM_HOST = "lastfm.freetls.fastly.net"
_LASTFM_ALIASES = ("lastfm-img.freetls.fastly.net",)
PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"  # Last.fm's default "no image" star


def image_url(images):
  """The largest URL in a Last.fm image list, or "" when missing or the placeholder star.

  Last.fm lists sizes small to mega; recent tracks and album.getInfo use the same shape.
  """
  url = ""
  for im in images or []:
    if im.get("#text"):
      url = im["#text"]
  return "" if not url or PLACEHOLDER in url else url


def lastfm_host(url):
  """url with a Last.fm image alias host replaced by LASTFM_HOST; other URLs unchanged."""
  for alias in _LASTFM_ALIASES:
    prefix = f"https://{alias}/"
    if (url or "").startswith(prefix):
      return f"https://{LASTFM_HOST}/{url[len(prefix) :]}"
  return url


def art_fallbacks(url):
  """Other sizes of the same Last.fm image, to try if the chosen one fails to load.

  The CDN resizes on first request and can cache a 404 for one size (seen 2026-11-20:
  JUNK POP's 300x300 404'd in the browser while every other size was fine).
  """
  m = _LASTFM_IMAGE.match(url or "")
  if not m:
    return []
  base, name = m.groups()
  return [f"{base}{size}{name}" for size in ("ar0/", "174s/") if f"{base}{size}{name}" != url]


def caa_url(rg_mbid):
  """The Cover Art Archive's front cover for a release group, or "" without an MBID.

  CAA has no art for some release groups and answers 404, so this is a fallback, never a
  guarantee. It redirects to archive.org, which the page's CSP allows (*.archive.org).
  """
  return f"https://coverartarchive.org/release-group/{rg_mbid}/{CAA_SIZE}" if rg_mbid else ""


def cover(image, rg_mbid):
  """(art, fallbacks) for an album: its Last.fm image and that image's other sizes, then CAA.

  With no Last.fm image, CAA is the art itself. ("", []) means the page shows the tile.
  Last.fm URLs are moved to the host the page's CSP allows (lastfm_host).
  """
  image = lastfm_host(image or "")
  chain = [u for u in [image, *art_fallbacks(image), caa_url(rg_mbid)] if u]
  return (chain[0], chain[1:]) if chain else ("", [])
