// The sync banner's and the first visit's pure rules (AOTY-22, AOTY-24): what to say for a
// status, when to announce it, and when a first visit has run out of things to wait for. No DOM
// here, so node --test runs it (web/test/firstvisit.test.mjs). A classic script: it sets
// globalThis.aotyFirstVisit before app.js runs.
//
// A first visit is a chart page with nothing ranked yet: no chart.json, or one whose By Play
// ranking ("album", its only one since AOTY-63) is empty. The first build runs before any album is dated, so that chart is the usual
// case, not an edge.
(function (root) {
  "use strict";

  const STAGES = ["queued", "reading", "building", "dating"];
  const QUARTERS = ["", "a quarter of the way", "halfway", "three quarters of the way"];
  const num = x => Number(x || 0).toLocaleString("en-US");
  const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;

  function isFirstVisit(chart) {
    const ranked = chart && chart.rankings && chart.rankings.album;  // By Play, the one ranking (AOTY-63)
    return !Array.isArray(ranked) || ranked.length === 0;
  }

  // The returning-user banner: [message, detail] for a status ("" when there's nothing to say).
  function describe(s) {
    if (s.stage === "queued" || s.stage === "reading") {
      const w = s.weeks || {};
      return ["Checking for new scrobbles…", w.total ? `${num(w.done)} of ${num(w.total)} weeks` : ""];
    }
    if (s.stage === "building") {
      return [s.new_plays ? `+${num(s.new_plays)} new plays · updating chart` : "Updating chart…", ""];
    }
    if (s.stage === "dating") {
      const a = s.albums || {};
      return ["Checking release years…", a.total ? `${num(a.checked)} of ${num(a.total)} albums` : ""];
    }
    return ["", ""];
  }

  // The first visit's progress, as {live, detail}: live is the polite live region's text and
  // changes only on a new stage or a new quarter; detail has the exact numbers (not announced).
  //
  // Everything only moves forward. A retried or resumed sync recounts weeks_total as the weeks
  // still to read and restarts weeks_done at 0; a stage can read as an earlier one for a poll.
  // Neither may make the page look like it's going backwards.
  function makeProgress() {
    let stage = -1, best = null, quarter = 0, plays = 0;

    function fraction(s, name) {
      if (name === "reading") {
        const w = s.weeks || {};
        return w.total ? {done: w.done || 0, total: w.total} : null;
      }
      if (name === "dating") {
        const a = s.albums || {};
        return a.total ? {done: a.checked || 0, total: a.total} : null;
      }
      return null;
    }

    return function update(s) {
      const i = STAGES.indexOf(s.stage);
      if (i > stage) {
        stage = i;
        best = null;
        quarter = 0;
      }
      const name = STAGES[stage];
      if (name === undefined) return {live: "", detail: ""};
      const f = fraction(s, name);
      if (f && (!best || f.done / f.total >= best.done / best.total)) best = f;
      if (best) quarter = Math.max(quarter, Math.min(3, Math.floor((best.done / best.total) * 4)));
      plays = Math.max(plays, s.scrobbles || 0);

      const head = {
        queued: "waiting for a turn to read your scrobbles",
        reading: "reading your scrobbles",
        building: "sorting your albums",
        dating: "checking release years",
      }[name];
      const live = quarter ? `${head} · ${QUARTERS[quarter]}` : head;
      let detail = "";
      if (name === "reading") {
        const parts = best ? [`${num(best.done)} of ${num(best.total)} weeks`] : [];
        if (plays) parts.push(plural(plays, "play", "plays"));
        parts.push("about a minute in all");
        detail = parts.join(" · ");
      } else if (name === "dating" && best) {
        detail = `${num(best.done)} of ${num(best.total)} albums`;
      }
      // Weeks read, as a whole percent, for the tab title (forward-only, like the rest).
      const percent = name === "reading" && best ? Math.floor((best.done / best.total) * 100) : null;
      return {live, detail, percent};
    };
  }

  // ---- the queue (AOTY-26) ------------------------------------------------------------------
  //
  // A new user who arrives while syncs are backed up waits in line: the status carries
  // queue.position (and queue.wait_s once there's a measured average) while the stage is queued.

  const ANNOUNCE_EVERY_MS = 60 * 1000;

  // The place in line as the page shows it: only ever going down (positions are approximate,
  // since the queue doesn't strictly keep order between users), and announced to screen readers
  // at most once a minute. update(s, nowMs) returns {position, live}: position is the number to
  // show (null when the status has none); live is the polite live region's text, which changes
  // at most once a minute.
  function makeQueue() {
    let best = null, live = "", announcedAt = -Infinity, announced = null;
    return function update(s, nowMs) {
      const p = s && s.stage === "queued" && s.queue && Number(s.queue.position);
      if (p > 0) best = best === null ? p : Math.min(best, p);
      if (best !== null && best !== announced && nowMs - announcedAt >= ANNOUNCE_EVERY_MS) {
        announced = best;
        announcedAt = nowMs;
        live = `you're caller #${num(best)} in line`;
      }
      return {position: best, live};
    };
  }

  // How long to wait before the next status poll while queued (AOTY-26), by place in line; null
  // means the page's normal 2-5 s back-off. The slow, jittered pace is for a real crowd, which is
  // when polls pile up. Near the front, or with no place at all (counters unreadable), the sync is
  // seconds away, and a 20-25 s poll left a lone user staring at "caller #1" (user, 2026-11-25).
  // rand: Math.random, for the jitter.
  const NEAR_FRONT = 2, SHORT_LINE = 10;
  function queuedPollMs(s, rand = Math.random) {
    const p = s && s.stage === "queued" && s.queue ? Number(s.queue.position) : 0;
    if (!(p > NEAR_FRONT)) return null;
    return p <= SHORT_LINE ? 8000 + Math.round(rand() * 2000) : 20000 + Math.round(rand() * 5000);
  }

  // "about 12 minutes" for a wait in seconds, or "" when there's no estimate.
  function waitText(seconds) {
    if (!(seconds > 0)) return "";
    const m = Math.ceil(seconds / 60);
    return m <= 1 ? "about a minute" : `about ${num(m)} minutes`;
  }

  // The request line's words for a place in line. url is the page's own address, shown without
  // its scheme (kamp.fm/toys/aoty/user/alice).
  function queueLines(position, url, waitSeconds) {
    return {
      caller: `you're caller #${num(position)}. the dj is spinning other people's years first.`,
      link: `this page is yours: ${String(url).replace(/^https?:\/\//, "")}. close the tab and come back. `
          + "it'll be pressed and waiting.",
      wait: waitText(waitSeconds),
    };
  }

  // The tab's title: "(#12) …" while in line, "(64%) …" while the year is read, else plain.
  function tabTitle(base, position, percent) {
    if (position) return `(#${position}) ${base}`;
    if (percent !== null && percent !== undefined) return `(${percent}%) ${base}`;
    return base;
  }

  // [title, text] for a sync request the API turned away for capacity or a Last.fm failure, or null. status is the
  // HTTP status; body its JSON (null if none).
  function refusal(status, body) {
    const error = body && body.error;
    if (status === 503 && error === "paused") {
      return ["we're at capacity right now", "come back soon. charts already pressed still work."];
    }
    if (status === 503 && error === "busy") {
      return ["lots of charts are pressing right now", "try again in a few minutes."];
    }
    // A new user is checked with Last.fm first (AOTY-81): it timed out or failed.
    if (status === 503 && error === "lastfm_unavailable") {
      return ["last.fm isn't answering right now", "try again in a minute."];
    }
    if (status === 429 && error === "too_many_new") {
      return ["that's a lot of new names from here", "try again in an hour."];
    }
    if (status === 429) {
      return ["lots of people are pressing charts right now", "try again in a minute."];
    }
    return null;
  }

  // "albums found so far: 1,204 · most played: LP by Band (61 plays)", or "".
  function soFar(s) {
    const f = s && s.so_far;
    if (!f || !f.top || !f.top.plays) return "";
    return `albums found so far: ${num(f.albums)} · most played: ${f.top.album} by ${f.top.artist} `
        + `(${plural(f.top.plays, "play", "plays")})`;
  }

  // Why a first visit stops waiting, or null to keep going. timedOut: the page's watch ran out.
  //   none    every album is checked and none came out this year
  //   stalled  the chart stopped rebuilding with albums still unchecked, or the watch ran out
  function firstVisitEnd(s, timedOut) {
    const a = (s && s.albums) || {};
    if (s && s.stage === "idle" && a.total != null) {
      if ((a.checked || 0) >= a.total && !a.hits) return "none";
      if ((a.checked || 0) < a.total) return "stalled";
    }
    return timedOut ? "stalled" : null;
  }

  // [title, text] for a firstVisitEnd kind. year is the chart's year.
  function endMessage(kind, year, s) {
    if (kind === "none") {
      const plays = (s && s.scrobbles) || 0;
      return ["no " + year + " albums here yet",
              `we read ${plural(plays, "play", "plays")} and checked every album: none came out in ${year}. `
              + "play a few of this year's records and your chart fills in on your next visit."];
    }
    return ["still checking release years",
            "this one's taking longer than usual. come back in a few minutes: your chart keeps pressing while you're away."];
  }

  // The cover wall's sources (AOTY-66): for each wall entry with art, the URLs to try in order (its
  // art, then its fallbacks), each URL once across the whole wall. Entries with no art are left out:
  // the wall shows only covers that loaded, never the artist-name tile (user, 2026-11-25).
  function wallSources(wall) {
    const seen = new Set(), out = [];
    for (const entry of Array.isArray(wall) ? wall : []) {
      if (!entry || typeof entry.art !== "string" || !entry.art || seen.has(entry.art)) continue;
      const urls = [entry.art, ...(Array.isArray(entry.fallbacks) ? entry.fallbacks : [])]
          .filter(u => typeof u === "string" && u && !seen.has(u));
      const unique = [...new Set(urls)];
      unique.forEach(u => seen.add(u));
      out.push(unique);
    }
    return out;
  }

  // The wall's drift (AOTY-68, user decision 2026-11-25): a few rows of covers, each swaying slowly
  // at its own pace, neighbours opposite ways, so the rows read as depth. wallRows: how many rows a
  // wall of `count` covers gets (one for a handful, up to four, about 15 covers a row, so a full
  // wall overfills any screen's width). drift(k): row k's period and direction; the periods share
  // no small factor, so the rows never fall into step.
  const WALL_ROWS_MAX = 4, WALL_PER_ROW = 15, WALL_ONE_ROW = 8;
  const DRIFT_PERIODS_S = [46, 59, 72, 85];
  function wallRows(count) {
    const n = Math.max(0, Math.floor(Number(count) || 0));
    if (n <= WALL_ONE_ROW) return 1;
    return Math.min(WALL_ROWS_MAX, Math.ceil(n / WALL_PER_ROW));
  }
  function drift(k) {
    const i = Math.max(0, Math.floor(Number(k) || 0));
    return {seconds: DRIFT_PERIODS_S[i % DRIFT_PERIODS_S.length], reverse: i % 2 === 1};
  }

  const api = {isFirstVisit, describe, makeProgress, soFar, firstVisitEnd, endMessage, makeQueue, waitText,
               queueLines, tabTitle, refusal, wallSources, queuedPollMs, wallRows, drift};
  root.aotyFirstVisit = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
