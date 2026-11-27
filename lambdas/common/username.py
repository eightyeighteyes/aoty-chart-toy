"""The one rule for a Last.fm username, as the api, the sync worker and the chart builder accept it.

Lowercase letters, digits, "_", "." and "-", up to 64 characters, starting with a letter, a digit
or "_" (AOTY-80: Last.fm's signup now wants a letter first, but older accounts such as
"_korps_" exist). A leading "." or "-" stays refused, so a name is never a "." or ".." path
segment. It's also the path-safety rule for S3 keys and routes, so loosen it with care.

Copies that must change with it (tests/test_username.py checks the first and last):
  - web/src/intro.js USERNAME_RE (the page; app.js reuses it), the same pattern source.
  - kamp-site's infra/functions/aoty-short.js NAME_RE and web/src/share.js SHORT_NAME_RE: the
    short link's narrower rule (a letter or digit first, no "."). A name it refuses gets the
    full kamp.fm link instead.
  - The CD smoke test's name (.github/workflows/deploy.yml, "-smoke-") must stay *invalid*: its
    POST sync expects a 400 without any Last.fm call.
"""

import re

USERNAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_.-]{0,63}$")
