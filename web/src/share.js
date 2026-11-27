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
    // A link shares anywhere share() exists; files only where canShare can confirm them (AOTY-70).
    if (typeof nav.canShare !== "function") return !(data && data.files);
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

  // A user chart's #1 (By Play, its one ranking), or null before it ranks anything.
  function userTop(chart) {
    return userTops(chart, 1)[0] || null;
  }
  // Its first n, in order (AOTY-73): fewer when it ranks fewer.
  function userTops(chart, n = 1) {
    const r = chart && chart.rankings && chart.rankings.album;
    if (!Array.isArray(r)) return [];
    return r.slice(0, n).map(([i]) => chart.albums[i]).filter(Boolean);
  }

  // The image's two switches (AOTY-73): what it shows, and its shape. A content needs that many
  // albums on screen; with fewer the image is the #1 (the saved choice stays, for the next view).
  const CONTENTS = {top1: 1, top3: 3};
  const SHAPES = ["square", "story"];
  function cardContent(saved, count) {
    return Object.prototype.hasOwnProperty.call(CONTENTS, saved) && count >= CONTENTS[saved] ? saved : "top1";
  }
  // The sharecard format for a content and a shape: top3_story, top1_square, …
  function formatId(content, shape) {
    const c = Object.prototype.hasOwnProperty.call(CONTENTS, content) ? content : "top1";
    return `${c}_${SHAPES.includes(shape) ? shape : "square"}`;
  }

  // What the share image shows (AOTY-70, AOTY-71, AOTY-73), or null for no image: {kind, name,
  // link, album, albums, played}. Every visitor gets it, signed in or not. tops are the albums on
  // screen from the top (userTops, or group.tops for the ranking and member shown; top alone is
  // a #1); member is a group's ?user= member, or "".
  //   - a user page: its name and link.
  //   - a member's view of a group: the member's own image, linking to their user page, never the
  //     group's (user decision 2026-11-27).
  //   - a group: its name and link, and "7 of 9 members" once no member is still loading (a count
  //     during the first build would undercount, user decision 2026-11-27). The total counts every
  //     member, unavailable ones included, as the page's dots do.
  function imageSubject({target, chart, top, tops, member}) {
    const albums = (tops || (top ? [top] : [])).filter(Boolean);
    if (!target || !chart || !albums.length) return null;
    const first = albums[0];
    if (target.kind !== "group") {
      return {kind: "user", name: target.name, link: target, album: first, albums, played: null};
    }
    if (member) {
      return {kind: "user", name: member, link: {kind: "user", id: member, name: member}, album: first, albums,
              played: null};
    }
    const total = (chart.members || []).length + (chart.unavailable || 0);
    const counted = !chart.waiting && total > 0 && Number.isInteger(first.listeners);
    const played = counted ? `${first.listeners} of ${total} ${total === 1 ? "member" : "members"}` : null;
    return {kind: "group", name: target.name, link: target, album: first, albums, played};
  }
  // Its label says what a tap does: the share sheet (touch, where files can be shared), or a download.
  function imageLabel(native) {
    return native ? "share image" : "save image";
  }

  const api = {shareUrl, useNative, shareTitle, shareLabels, userTop, userTops, CONTENTS, SHAPES, cardContent, formatId,
               imageSubject, imageLabel};
  root.aotyShare = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
