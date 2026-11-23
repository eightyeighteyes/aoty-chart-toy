// The sync banner's and the first visit's pure rules (AOTY-22, AOTY-24): what to say for a
// status, when to announce it, and when a first visit has run out of things to wait for. No DOM
// here, so node --test runs it (web/test/firstvisit.test.mjs). A classic script: it sets
// globalThis.aotyFirstVisit before app.js runs.
//
// A first visit is a chart page with nothing ranked yet: no chart.json, or one whose By Track
// ranking is empty. The first build runs before any album is dated, so that chart is the usual
// case, not an edge.
(function (root) {
  "use strict";

  const STAGES = ["queued", "reading", "building", "dating"];
  const QUARTERS = ["", "a quarter of the way", "halfway", "three quarters of the way"];
  const num = x => Number(x || 0).toLocaleString("en-US");
  const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;

  function isFirstVisit(chart) {
    const track = chart && chart.rankings && chart.rankings.track;
    return !Array.isArray(track) || track.length === 0;
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
      return {live, detail};
    };
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

  const api = {isFirstVisit, describe, makeProgress, soFar, firstVisitEnd, endMessage};
  root.aotyFirstVisit = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
