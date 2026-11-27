// Analytics (AOTY-38): page views and a few events to kamp.fm's GA4 property, content group
// "aoty". A classic script that sets globalThis.aotyStats before app.js runs; the pure rules are
// exported for node --test (web/test/kampstats.test.mjs).
//
// What is sent comes only from the templates and allowlists below, never from location or
// document.title: no username ever reaches GA (user decision 2026-11-24). Paths are templated
// (/toys/aoty/user/:name); a group's slug is sent on purpose, as group_id. Every replaceState that
// changes the path is followed by setPage(), so later and automatic hits carry the templates too.
//
// The measurement id comes from <meta name="aoty-ga"> (scripts/build_shell.py --ga-id, prod
// only). Empty means nothing loads and every call does nothing. Anywhere but kamp.fm, hits are
// sent with debug_mode, so a local preview shows in DebugView and stays out of the reports.
//
// Named kampstats, not analytics: filter lists block same-origin paths containing "analytics.".
// app.js still guards against this file never loading.
(function (root) {
  "use strict";

  const PROD_HOST = "kamp.fm";
  const GTAG_URL = "https://www.googletagmanager.com/gtag/js?id=";
  const SIGNIN_KEY = "aoty-signin-pending";
  // The group slug's shape (group.js ID_RE): anything else is sent as no group at all.
  const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/;
  const TITLES = {intro: "intro · a kamp toy", user: "user chart · a kamp toy", group: "group chart · a kamp toy"};

  // The intro's answers (intro.js MESSAGES keys), plus "ok" for a name that leads to a chart.
  const OUTCOMES = ["ok", "empty", "format", "unknown", "private", "lastfm", "busy", "paused", "tooMany",
                    "throttled", "generic"];
  // Each event's allowed params: a list of values, or "count" for a small whole number. An
  // unknown event is dropped, and so is any param or value not listed.
  const EVENTS = {
    intro_submit: {outcome: OUTCOMES},
    first_visit_end: {result: ["painted", "none", "stalled", "private", "error"]},
    group_loaded: {group_size: "count"},
    // "content" and "shape" are the share image's #1 | top 3 | top 10 (AOTY-73, AOTY-74) and
    // square | story (AOTY-72).
    control_change: {control: ["view", "rank", "filter", "content", "shape"],
                     value: ["poster", "list", "listener", "album", "weighted", "member", "everyone",
                             "top1", "top3", "top10", "square", "story"]},
    share_copy: {},
    // The system share sheet shared a page's link (AOTY-84): which page is never sent.
    share_native: {},
    // A share image went out (AOTY-70): its format and how, never whose page or which album.
    share_image: {format: ["top1_square", "top1_story", "top3_square", "top3_story", "top10_square", "top10_story"],
                  via: ["native", "download"]},
    // An owner saved their page's theme (AOTY-65): the theme id only, never whose page.
    theme_set: {theme: ["poster", "kamp", "strawberry-switchblade", "beach-house", "blackpink", "deep-purple",
                        "green-day", "foxy-brown", "golden-smog", "white-stripes", "tangerine-dream",
                        "yellow-magic-orchestra", "mint-royale", "the-blue-nile", "lavender-diamond", "gabbie",
                        "match1"]},
    sign_in: {},
    group_create: {},
    member_add: {},
    member_remove: {},
    kamp_click: {},
    // An album's Last.fm link was followed (AOTY-43): no album, no user, nothing else.
    album_click: {},
  };

  // {type, path, group_id} for a pathname. Anything that isn't a chart is the intro, because
  // app.js sends every other path there.
  function template(pathname, base) {
    const rest = String(pathname || "").startsWith(base) ? pathname.slice(base.length) : null;
    if (rest !== null && /^user\/[^/]+\/?$/.test(rest)) return {type: "user", path: `${base}user/:name`};
    const g = rest === null ? null : rest.match(/^group\/([^/]+)\/?$/);
    if (g) {
      const slug = g[1].toLowerCase();
      if (SLUG_RE.test(slug)) return {type: "group", path: `${base}group/${slug}`, group_id: slug};
      return {type: "group", path: `${base}group/:id`};
    }
    return {type: "intro", path: base};
  }

  // The referrer, without anything that could name someone: another site's is cut to its origin
  // (browsers mostly do that already), one of the toy's pages is templated, and any query string
  // is dropped.
  function templateReferrer(ref, origin, base) {
    if (!ref) return "";
    let url;
    try { url = new URL(ref); } catch (_) { return ""; }
    if (url.origin !== origin) return `${url.origin}/`;
    if (url.pathname.startsWith(base)) return `${origin}${template(url.pathname, base).path}`;
    return `${origin}${url.pathname}`;
  }

  // The page-level fields, applied with gtag('set') so every hit carries them.
  function pageFields(pathname, origin, base, referrer) {
    const t = template(pathname, base);
    const fields = {
      page_location: `${origin}${t.path}`,
      page_title: TITLES[t.type],
      page_referrer: referrer,
      content_group: "aoty",
      page_type: t.type,
    };
    // Set even when empty, so a group page that moves to a bad slug doesn't keep the old one.
    fields.group_id = t.group_id || "";
    return fields;
  }

  // The event's params with anything unlisted removed, or null for an event that isn't listed.
  function clean(name, params) {
    if (!Object.prototype.hasOwnProperty.call(EVENTS, name)) return null;
    const spec = EVENTS[name], out = {};
    for (const [k, v] of Object.entries(params || {})) {
      const allowed = spec[k];
      if (allowed === "count") {
        if (Number.isInteger(v) && v >= 0 && v <= 1000) out[k] = v;
      } else if (Array.isArray(allowed) && allowed.includes(v)) {
        out[k] = v;
      }
    }
    return out;
  }

  // The intro's result (intro.outcome) as an OUTCOMES code.
  function outcomeCode(result, messages) {
    if (result && result.go) return "ok";
    const key = Object.keys(messages || {}).find(k => messages[k] === (result && result.message));
    return OUTCOMES.includes(key) ? key : "generic";
  }

  // A sign-in finished: this browser left for Last.fm (the pending mark) and api/me now names
  // someone. A page that comes back with ?signin=failed clears the mark without counting.
  const signInDone = (pending, me) => !!pending && !!me && typeof me.username === "string";

  const NOOP = {pageView() {}, setPage() {}, event() {}, introSubmit() {}, markSignIn() {}, checkSignIn() {}};

  // The live wrapper. deps: {id, base, win, loc, referrer, storage, inject}; tests pass stubs.
  function create(deps) {
    const {id, base, win, loc, storage} = deps;
    if (!id) return NOOP;
    win.dataLayer = win.dataLayer || [];
    // gtag.js reads Arguments objects off the dataLayer, not arrays.
    function gtag() { win.dataLayer.push(arguments); }
    const referrer = templateReferrer(deps.referrer, loc.origin, base);
    const fields = () => pageFields(loc.pathname, loc.origin, base, referrer);
    let viewed = false;

    gtag("js", new Date());
    gtag("set", fields());
    const config = {send_page_view: false};
    if (loc.hostname !== PROD_HOST) config.debug_mode = true;
    gtag("config", id, config);
    deps.inject(GTAG_URL + encodeURIComponent(id));

    const store = (fn) => { try { return fn(); } catch (_) { return null; } };
    function event(name, params) {
      const p = clean(name, params);
      if (p) gtag("event", name, p);
    }
    return {
      // Once per page load, after routing.
      pageView() {
        if (viewed) return;
        viewed = true;
        gtag("set", fields());
        gtag("event", "page_view");
      },
      // After a replaceState that changes the path: a rename's new slug, a name's casing.
      setPage() { gtag("set", fields()); },
      event,
      // The intro form's answer (intro.outcome's result, or a cleanName refusal as {message}).
      introSubmit(result, messages) { event("intro_submit", {outcome: outcomeCode(result, messages)}); },
      markSignIn() { store(() => storage.setItem(SIGNIN_KEY, "1")); },
      // After api/me answers: counts a sign-in this browser started, once.
      checkSignIn(me, failed) {
        const pending = store(() => storage.getItem(SIGNIN_KEY));
        if (!pending) return;
        if (failed || signInDone(pending, me)) store(() => storage.removeItem(SIGNIN_KEY));
        if (!failed && signInDone(pending, me)) event("sign_in");
      },
    };
  }

  const api = {TITLES, OUTCOMES, EVENTS, NOOP, template, templateReferrer, pageFields, clean, outcomeCode,
               signInDone, create};
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (typeof document !== "undefined") {
    const meta = name => {
      const m = document.querySelector(`meta[name="${name}"]`);
      return m ? m.content : "";
    };
    root.aotyStats = create({
      id: meta("aoty-ga"),
      base: meta("aoty-base"),
      win: root,
      loc: root.location,
      referrer: document.referrer,
      storage: (() => { try { return root.sessionStorage; } catch (_) { return null; } })(),
      inject(src) {
        const s = document.createElement("script");
        s.async = true;
        s.src = src;
        document.head.appendChild(s);
      },
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
