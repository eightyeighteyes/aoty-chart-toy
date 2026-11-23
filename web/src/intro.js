// The intro page's pure rules (AOTY-23): cleaning a typed or pasted name, and turning the API's
// answer to POST sync into "go to this chart" or a message. No DOM here, so node --test runs it
// (web/test/intro.test.mjs). A classic script: it sets globalThis.aotyIntro before app.js runs.
(function (root) {
  "use strict";

  // Last.fm names as the sync worker accepts them (lambdas/sync/handler.py USERNAME_RE).
  const USERNAME_RE = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
  // A pasted profile link: last.fm/user/<name>, with or without the scheme and www.
  const PROFILE_RE = /^(?:https?:\/\/)?(?:www\.)?last\.fm\/user\/([^/?#\s]+)/i;

  const MESSAGES = {
    empty: "type a last.fm username first.",
    format: "that doesn't look like a last.fm username. they're letters, numbers, dots, dashes and underscores.",
    unknown: "we couldn't find that last.fm user. check the spelling?",
    private: "that profile is private on last.fm, so there's nothing for us to read. make it public and try again.",
    lastfm: "last.fm isn't answering right now. try again in a minute.",
    busy: "lots of charts are pressing right now. try again in a few minutes.",
    generic: "something went wrong on our side. try again in a minute.",
  };

  // {name} (lowercased) or {error: key into MESSAGES}.
  function cleanName(raw) {
    let s = String(raw == null ? "" : raw).trim();
    const link = s.match(PROFILE_RE);
    if (link) {
      try { s = decodeURIComponent(link[1]); } catch (_) { s = link[1]; }
    }
    s = s.replace(/^@/, "").trim().toLowerCase();
    if (!s) return {error: "empty"};
    return USERNAME_RE.test(s) ? {name: s} : {error: "format"};
  }

  // The answer to POST <base>api/users/<name>/sync, as {go: name for the URL} or {message}.
  // status is the HTTP status (0 when the request failed or timed out); body is the parsed JSON,
  // or null when there was none.
  //
  // A 200 means the user is known and a sync is running or was just done (cooldown): go. A
  // private profile is only known here if a sync already found it within the cooldown;
  // otherwise the chart page says so when its sync hits Last.fm's error 17.
  function outcome(status, body, name) {
    if (status === 202 || status === 200) {
      const st = (body && body.status) || {};
      if (st.stage === "error" && st.reason === "private") return {message: MESSAGES.private};
      if (st.stage === "error" && st.reason === "not_found") return {message: MESSAGES.unknown};
      const shown = typeof st.display_name === "string" && st.display_name.toLowerCase() === name
          ? st.display_name : name;
      return {go: shown};
    }
    if (status === 400) return {message: MESSAGES.format};
    if (status === 404) return {message: MESSAGES.unknown};
    if (status === 503 && body && body.error === "lastfm_unavailable") return {message: MESSAGES.lastfm};
    if (status === 503 && body && body.error === "busy") return {message: MESSAGES.busy};
    return {message: MESSAGES.generic};
  }

  const api = {USERNAME_RE, MESSAGES, cleanName, outcome};
  root.aotyIntro = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
