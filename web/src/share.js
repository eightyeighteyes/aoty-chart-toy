// Share links and the share button's rules (AOTY-84). Pure: tested by web/test/share.test.mjs.
//
// On prod the link is the short host's (aoty.kamp.fm, kamp-site's AOTY-69 redirect), which
// build_shell.py writes into <meta name="aoty-short"> for prod alone; elsewhere it's the page's
// own address. Either way it has no query: ?user=, ?edit and ?signin stay behind.
(function (root) {
  "use strict";

  // What aoty.kamp.fm redirects: a name of 2+ characters (one-letter first segments are reserved
  // for short routes like /g) with no "." (USERNAME_RE allows one, the redirect doesn't), starting
  // with a letter, digit or "_" (AOTY-89: _korps_), and a group slug as group.js ID_RE. Anything
  // else gets the full URL, which always works. kamp-site's aoty-short.js NAME_RE must take it all.
  const SHORT_NAME_RE = /^[A-Za-z0-9_][A-Za-z0-9_-]{1,63}$/;
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

  // The link buttons' labels: "share" whether a tap opens the share sheet or copies (AOTY-98). A
  // member's view of a group (?user=) offers the group and the member's own page (user decision
  // 2026-11-27); member is that member's name, or null.
  function shareLabels(member) {
    if (!member) return {main: "share link", member: null};
    return {main: "share group link", member: `share ${member}'s link`};
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
  // A saved choice too big for what's on screen falls back to the biggest that fits, never past
  // the saved one: a top 10 with 5 on screen is a top 3 (AOTY-74).
  const CONTENTS = {top1: 1, top3: 3, top10: 10};
  const SHAPES = ["square", "story"];
  function cardContent(saved, count) {
    const want = Object.prototype.hasOwnProperty.call(CONTENTS, saved) ? CONTENTS[saved] : 1;
    let best = "top1";
    for (const [id, n] of Object.entries(CONTENTS)) if (n <= want && n <= count && n >= CONTENTS[best]) best = id;
    return best;
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

  // The texts sharecard.compose draws for a subject (imageSubject's): its albums' titles and
  // artists, its name, the year, the link and a group's member count. The page's share image and
  // the card Lambda's link preview card (AOTY-91) both come from here, so they say the same.
  function cardTexts(subject, year, url, count = 1) {
    const albums = subject.albums.slice(0, count).map(a => ({title: a.album, artist: a.artist}));
    return {albums, name: subject.name, year, url, kind: subject.kind, played: subject.played};
  }

  // The preview's alt text (AOTY-98), from cardTexts': whose it is, then every album drawn, in
  // order. "alice's top 3: A by X, B by Y, C by Z"; a group names itself as its caption does.
  function previewAlt(texts) {
    const albums = (texts && texts.albums) || [];
    const what = albums.length > 1 ? `top ${albums.length}` : "#1";
    const whose = texts && texts.kind === "group" ? `${texts.name} · ${what}` : `${texts ? texts.name : ""}'s ${what}`;
    return `${whose}: ${albums.map(a => `${a.title} by ${a.artist}`).join(", ")}`;
  }

  // A link preview card's subject (AOTY-91): the page as a visitor first sees it, Everyone's view.
  // A user page by its display name; a group by its name, with top its By Listener #1 (group.top,
  // which the caller passes: this file doesn't load group.js). Null when nothing ranks yet.
  function previewSubject(chart, top) {
    if (!chart) return null;
    const target = chart.kind === "group"
      ? {kind: "group", id: chart.id, name: chart.name || chart.id}
      : {kind: "user", id: chart.display_name || chart.username, name: chart.display_name || chart.username};
    return imageSubject({target, chart, top, member: ""});
  }

  const api = {shareUrl, useNative, shareTitle, shareLabels, userTop, userTops, CONTENTS, SHAPES, cardContent, formatId,
               imageSubject, imageLabel, cardTexts, previewAlt, previewSubject};
  root.aotyShare = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
