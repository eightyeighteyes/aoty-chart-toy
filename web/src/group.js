// The group page's pure rules (AOTY-51), ported from M0's build_page.py PAGE_SCRIPT. No DOM.
//
// A group chart.json (lambdas/chart/group_core.py) has everyone's three rankings: By Listener
// ("listener", the default), By Play ("album") and Weighted (AOTY-63: By Track is gone). Picking
// a member re-ranks here by their plays / tracks, singles out, whatever the mode: with one
// listener all three are the same. Ties go to the group's listeners, then the chart's own order
// (listeners, then plays).
(root => {
  "use strict";

  const MODES = ["listener", "album", "weighted"];
  const GROUP_VERSION = 1;
  const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/;  // the group's slug, as the chart builder's

  // The members, sorted as the chart lists them, and the index of `name` among them (any
  // casing), or -1 for Everyone / an unknown name.
  function memberIndex(chart, name) {
    if (!name) return -1;
    const lower = String(name).toLowerCase();
    return chart.members.findIndex(m => m.toLowerCase() === lower);
  }

  // [{...album, score, rank}] for one mode, for everyone (who < 0) or one member.
  function rows(chart, mode, who = -1) {
    if (!MODES.includes(mode)) mode = "listener";
    let pairs;
    if (who < 0) {
      // A chart built before AOTY-63 has no "listener" ranking until it's rebuilt: show By Play.
      pairs = chart.rankings[mode] || (mode === "listener" && chart.rankings.album) || [];
    } else {
      const key = String(who);
      const pool = [];
      chart.albums.forEach((a, i) => {
        const plays = (a.by || {})[key];
        if (plays && !a.single && a.tracks) pool.push([i, plays / a.tracks]);
      });
      pool.sort((x, y) => y[1] - x[1] || chart.albums[y[0]].listeners - chart.albums[x[0]].listeners || x[0] - y[0]);
      pairs = pool.slice(0, chart.top || 100);
    }
    return pairs.map(([i, s], k) => ({...chart.albums[i], score: s, rank: k + 1}));
  }

  // The #1 on screen for one mode and member (the share image's album, AOTY-71), or null when that
  // ranking is empty: Weighted's listener floor, a member with no plays from the year.
  function top(chart, mode, who = -1) {
    return tops(chart, mode, who, 1)[0] || null;
  }
  // The first n on screen, in order (AOTY-73's top 3): fewer when the ranking has fewer.
  function tops(chart, mode, who = -1, n = 1) {
    return rows(chart, mode, who).slice(0, n);
  }

  // "3 of 8 still loading" while members' first syncs run; "" once everyone is in.
  function loadingLine(chart) {
    const total = chart.members.length + (chart.waiting || 0);
    return chart.waiting ? `${chart.waiting} of ${total} still loading` : "";
  }

  // What the subtitle says about albums still being dated, for user and group charts alike (no
  // big numbers, user decision 2026-11-23). pending_top counts undated albums in the top 150 by
  // plays (a group: any member's), which can move the chart; the cold tail behind them only
  // fills the shared cache, except that on a group a lightly played album many members share
  // can still surface, hence the deep cuts.
  function checkingLine(chart) {
    if (chart.pending_top > 0) return chart.kind === "group" ? "still checking…" : "still checking your top albums…";
    if (chart.kind === "group" && chart.pending > 0) return "still counting deep cuts…";
    return "";
  }

  // After a visit queued its members' syncs (AOTY-55), the page keeps re-reading quietly: while
  // GET api/groups/<id> says members are syncing, plus SYNC_GRACE_MS after the last of them, for
  // the chart build that follows a finished sync; never past SYNC_WATCH_MAX_MS (a row stuck as
  // queued would otherwise keep it going). w: {since, lastBusy} (ms), null to start one at now.
  // Returns the next w, or null to stop.
  const SYNC_GRACE_MS = 90000;
  const SYNC_WATCH_MAX_MS = 15 * 60000;
  function syncWatch(w, info, now) {
    const cur = w || {since: now, lastBusy: now};
    if (now - cur.since >= SYNC_WATCH_MAX_MS) return null;
    const lastBusy = info && info.syncing > 0 ? now : cur.lastBusy;
    return now - lastBusy < SYNC_GRACE_MS ? {since: cur.since, lastBusy} : null;
  }

  // A chart.json this page can show.
  function valid(chart, id) {
    return !!chart && chart.version === GROUP_VERSION && chart.kind === "group" && chart.id === id
        && Array.isArray(chart.members) && Array.isArray(chart.albums) && !!chart.rankings;
  }

  const api = {MODES, ID_RE, memberIndex, rows, top, tops, loadingLine, checkingLine, valid, syncWatch,
               SYNC_GRACE_MS, SYNC_WATCH_MAX_MS};
  root.aotyGroup = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
