// Share links and the share button's rules (AOTY-84). Pure: tested by web/test/share.test.mjs.
//
// On prod the link is the short host's (aoty.kamp.fm, kamp-site's AOTY-69 redirect), which
// build_shell.py writes into <meta name="aoty-short"> for prod alone; elsewhere it's the page's
// own address. Either way it has no query: ?user=, ?edit and ?signin stay behind.
(function (root) {
  "use strict";

  // What aoty.kamp.fm redirects: a name of 2+ characters (one-letter first segments are reserved
  // for short routes like /g) with no "." (USERNAME_RE allows one, the redirect doesn't), and a
  // group slug as group.js ID_RE. Anything else gets the full URL, which always works.
  const SHORT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$/;
  const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/;

  // The link for a user page (kind "user", id = the name in its display casing) or a group
  // (kind "group", id = the slug).
  function shareUrl({origin, base, short, kind, id}) {
    const name = String(id ?? "");
    if (short) {
      if (kind === "user" && SHORT_NAME_RE.test(name)) return `https://${short}/${name}`;
      if (kind === "group" && SLUG_RE.test(name)) return `https://${short}/g/${name}`;
    }
    return `${origin}${base}${kind === "group" ? "group" : "user"}/${encodeURIComponent(name)}`;
  }

  // The system share sheet, on touch devices only (user decision 2026-11-27): desktop browsers
  // that have navigator.share still copy, which is what people expect there.
  function useNative(nav, coarse, data) {
    if (!coarse || !nav || typeof nav.share !== "function") return false;
    if (typeof nav.canShare !== "function") return true;
    try { return !!nav.canShare(data); } catch (_) { return false; }
  }

  // The shared title, from the page's name and year. Never document.title: during a first visit's
  // queue it reads "(#12) …".
  function shareTitle({kind, name, year, product}) {
    return kind === "group" ? `${name} · ${product} ${year}` : `${name}'s ${product} ${year}`;
  }

  // The buttons' labels. A member's view of a group (?user=) offers the group and the member's
  // own page (user decision 2026-11-27); member is that member's name, or null.
  function shareLabels(native, member) {
    if (!member) return {main: native ? "share" : "copy link", member: null};
    return native
      ? {main: "share group", member: `share ${member}`}
      : {main: "copy group link", member: `copy ${member}'s link`};
  }

  const api = {shareUrl, useNative, shareTitle, shareLabels};
  root.aotyShare = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
