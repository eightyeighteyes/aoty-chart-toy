"""Link preview pages (AOTY-85): a chart.json -> the tags-only og.html crawlers read.

Pure. The chart builder writes one beside every chart.json it publishes
(data/users/<u>/og.html, data/groups/<slug>/og.html), and kamp-site's shell function sends known
link-preview bots (Discord, iMessage, Slack, ...) there instead of the SPA, which they can't run.
People never see it, but its body links to the page in case one does.

User decisions (2026-11-27): a bot-only tag page, not pre-rendered shells or Lambda@Edge, and
og:image is the #1's cover, not a rendered card.

Rules:
  - Titles match share.shareTitle: "alice's albums of the year 2026", "Full House · albums of
    the year 2026".
  - The description names the #1 "so far", or, before anything ranks, says what the chart is.
    Never a progress word: Discord and others cache an embed for days.
  - og:url is always the canonical kamp.fm page, in the display name's casing, whatever was pasted
    (a short link, other casing).
  - og:image only when the #1 has an https cover (a Last.fm or Cover Art Archive URL, 250 px or
    more); otherwise the card is text-only.
  - Every value is HTML-escaped: group names are user-written.
"""

from html import escape
from urllib.parse import quote

from common.config import NAME

SITE_NAME = "a kamp toy"
DESCRIPTION_MAX = 200
GROUP_MODES = ("listener", "album")  # a group's #1: By Listener, else By Play (charts before AOTY-63)


CARD_SIZE = 1080  # the card Lambda's top1_square (AOTY-91)


# card: the rendered preview card's URL (AOTY-91, lambdas/card), or None for the #1's bare cover.
def user_page(chart, origin, base, card=None):
  name = chart.get("display_name") or chart.get("username") or ""
  year = chart.get("year")
  title = f"{name}'s {NAME} {year}"
  url = f"{origin}{base}user/{quote(name, safe='')}"
  top = _top(chart, ("album",))
  empty = f"the {year} albums {name} actually lived with, pressed into a chart. {SITE_NAME}."
  return _page(title, _description(top, empty), url, top, card)


def group_page(chart, origin, base, card=None):
  slug = chart.get("id") or ""
  name = chart.get("name") or slug
  year = chart.get("year")
  title = f"{name} · {NAME} {year}"
  url = f"{origin}{base}group/{quote(slug, safe='')}"
  top = _top(chart, GROUP_MODES)
  empty = f"the {year} albums {name} played together, pressed into a chart. {SITE_NAME}."
  return _page(title, _description(top, empty), url, top, card)


def _top(chart, modes):
  """The #1 album dict of the first mode that ranks anything, or None."""
  albums = chart.get("albums") or []
  for mode in modes:
    ranking = (chart.get("rankings") or {}).get(mode) or []
    if ranking and 0 <= ranking[0][0] < len(albums):
      return albums[ranking[0][0]]
  return None


def _description(top, empty):
  if top is None:
    return _cut(empty)
  return _cut(f"#1 so far: {top.get('album', '')} by {top.get('artist', '')}")


def _cut(text, limit=DESCRIPTION_MAX):
  """text, cut at a word boundary with an ellipsis when it's longer than limit characters."""
  if len(text) <= limit:
    return text
  head = text[: limit - 1]
  space = head.rfind(" ")
  return (head[:space] if space > limit // 2 else head).rstrip() + "…"


def _page(title, description, url, top, card=None):
  art = (top or {}).get("art") or ""
  e = lambda s: escape(str(s), quote=True)  # noqa: E731
  tags = [
    ("og:type", "website"),
    ("og:site_name", SITE_NAME),
    ("og:title", title),
    ("og:description", description),
    ("og:url", url),
  ]
  if card and card.startswith("https://"):
    # The share card, drawn large (user decision 2026-11-27: X, Facebook and LinkedIn crop a square
    # to about 1.91:1, accepted; Discord and iMessage show it whole).
    tags += [("og:image", card), ("og:image:type", "image/png"), ("og:image:width", CARD_SIZE),
             ("og:image:height", CARD_SIZE)]  # fmt: skip
  elif art.startswith("https://"):
    tags.append(("og:image", art))
  twitter = "summary_large_image" if card and card.startswith("https://") else "summary"
  meta = "\n".join(f'<meta property="{p}" content="{e(v)}">' for p, v in tags)
  return (
    "<!doctype html>\n"
    '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    f"<title>{e(title)} · {e(SITE_NAME)}</title>\n"
    f'<meta name="description" content="{e(description)}">\n'
    f"{meta}\n"
    f'<meta name="twitter:card" content="{twitter}">\n'
    f'<link rel="canonical" href="{e(url)}">\n'
    "</head>\n<body>\n"
    f'<p><a href="{e(url)}">{e(title)}</a></p>\n'
    "</body>\n</html>\n"
  )
