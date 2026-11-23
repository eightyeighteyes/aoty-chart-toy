// The group page's pure rules (AOTY-51), ported from M0's build_page.py PAGE_SCRIPT. No DOM.
//
// A group chart.json (lambdas/chart/group_core.py) has everyone's three rankings. Picking a
// member re-ranks here, by M0's rules:
//   By Track   their plays
//   By Album   their plays / tracks, singles out
//   Weighted   the same as By Album: one listener
// Ties go to the group's listeners, then the chart's own order (By Track for everyone).
(root => {
  "use strict";

  const MODES = ["track", "album", "weighted"];
  const GROUP_VERSION = 1;
  const ID_RE = /^[a-z2-7]{26}$/;

  // The members, sorted as the chart lists them, and the index of `name` among them (any
  // casing), or -1 for Everyone / an unknown name.
  function memberIndex(chart, name) {
    if (!name) return -1;
    const lower = String(name).toLowerCase();
    return chart.members.findIndex(m => m.toLowerCase() === lower);
  }

  // [{...album, score, rank}] for one mode, for everyone (who < 0) or one member.
  function rows(chart, mode, who = -1) {
    if (!MODES.includes(mode)) mode = "track";
    let pairs;
    if (who < 0) {
      pairs = chart.rankings[mode] || [];
    } else {
      const key = String(who);
      const pool = [];
      chart.albums.forEach((a, i) => {
        const plays = (a.by || {})[key];
        if (!plays) return;
        if (mode === "track") pool.push([i, plays]);
        else if (!a.single && a.tracks) pool.push([i, plays / a.tracks]);
      });
      pool.sort((x, y) => y[1] - x[1] || chart.albums[y[0]].listeners - chart.albums[x[0]].listeners || x[0] - y[0]);
      pairs = pool.slice(0, chart.top || 100);
    }
    return pairs.map(([i, s], k) => ({...chart.albums[i], score: s, rank: k + 1}));
  }

  // "3 of 8 still loading" while members' first syncs run; "" once everyone is in.
  function loadingLine(chart) {
    const total = chart.members.length + (chart.waiting || 0);
    return chart.waiting ? `${chart.waiting} of ${total} still loading` : "";
  }

  // A chart.json this page can show.
  function valid(chart, id) {
    return !!chart && chart.version === GROUP_VERSION && chart.kind === "group" && chart.id === id
        && Array.isArray(chart.members) && Array.isArray(chart.albums) && !!chart.rankings;
  }

  const api = {MODES, ID_RE, memberIndex, rows, loadingLine, valid};
  root.aotyGroup = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
