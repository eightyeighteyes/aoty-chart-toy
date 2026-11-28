// The user page (AOTY-21), ported from M0's build_page.py PAGE_SCRIPT.
//
// kamp.fm serves this shell for every path under the base (AOTY-15), so routing happens here:
//   <base>user/<name>   fetch <base>data/users/<lowercased name>/chart.json and render it
//   anything else       a username box (AOTY-23 replaces it with the intro page)
// The CSP allows no inline script and no inline handlers, so everything lives in this file
// and the cover fallback is an event listener, not an onerror attribute.
(() => {
  "use strict";

  const BASE = document.querySelector('meta[name="aoty-base"]').content;
  // The placeholder product name (scripts/build_shell.py NAME) and the page's own title.
  const NAME = document.querySelector('meta[name="aoty-name"]').content;
  const TITLE = document.title;
  const YEAR = document.querySelector('meta[name="aoty-year"]').content;
  const intro = globalThis.aotyIntro;  // web/src/intro.js, loaded first
  const lf = globalThis.aotyLastfm;  // web/src/lastfm.js, loaded first (AOTY-43)
  const share = globalThis.aotyShare;  // web/src/share.js, loaded first (AOTY-84)
  // The share links' short host: aoty.kamp.fm on prod, empty elsewhere (build_shell --short-host).
  const SHORT = document.querySelector('meta[name="aoty-short"]').content;
  const fv = globalThis.aotyFirstVisit;  // web/src/firstvisit.js, loaded first
  const grp = globalThis.aotyGroup;  // web/src/group.js, loaded first (AOTY-51)
  const ge = globalThis.aotyGroupEdit;  // web/src/groupedit.js, loaded first (AOTY-52)
  const th = globalThis.aotyTheme;  // web/src/theme.js, loaded first (AOTY-65)
  // Analytics (web/src/kampstats.js, AOTY-38). An ad blocker may drop that file, and nothing it
  // does may ever break the page, so every call goes through here.
  const kampStats = globalThis.aotyStats;
  const track = (method, ...args) => { try { if (kampStats) kampStats[method](...args); } catch (_) {} };
  const GROUP_REFRESH_MS = 60000;  // a group page re-reads its chart while members load
  const GROUP_WATCH_MS = 5000;  // and every 5 s after an edit, or while its first chart is pressed
  const API_TIMEOUT_MS = 10000;
  const USERNAME_RE = intro.USERNAME_RE;
  const LOOKUP_TIMEOUT_MS = 10000;
  const CHART_VERSION = 1;
  // Rankings (user decision 2026-11-25, AOTY-63): By Track is gone. A user page, and a member's
  // view of a group, is By Play only (album plays); a group's everyone view picks By Listener
  // (the default), By Play or Weighted.
  const BY_PLAY = "ranked by album plays: track plays divided by the album's track count (singles left out)";
  const GROUP_MODES = {
    listener: "ranked by how many members played them, then album plays (singles left out)",
    album: BY_PLAY,
    weighted: "ranked by album plays per listener (albums with 3 or more listeners, singles left out)",
  };

  const poster = document.querySelector(".poster");
  const $ = id => document.getElementById(id);
  const esc = s => String(s).replace(/[&<>"']/g,
      c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
  const num = (x, digits = 0) => x.toLocaleString("en-US",
      {minimumFractionDigits: digits, maximumFractionDigits: digits});

  // Placeholder text size, in % of the tile's width: big for short names, smaller for
  // long ones, and never so big that the longest word overflows (~0.6em per character).
  function nameSize(name) {
    const longest = Math.max(1, ...String(name).split(/\s+/).map(w => w.length));
    const bySize = name.length <= 10 ? 16 : name.length <= 22 ? 12 : 9;
    return Math.min(bySize, 130 / longest).toFixed(1);
  }

  // A cover failed to load: try the next URL in data-fallbacks (other Last.fm sizes, then the
  // Cover Art Archive), and when none are left, swap in the artist-name tile.
  function coverFallback(img) {
    const rest = (img.dataset.fallbacks || "").split(" ").filter(Boolean);
    if (rest.length) {
      img.dataset.fallbacks = rest.slice(1).join(" ");
      img.src = rest[0];
      return;
    }
    const tile = document.createElement("div");
    tile.className = img.className + " noart";
    const name = tile.appendChild(document.createElement("span"));
    name.textContent = img.dataset.artist;
    name.style.setProperty("--fs", nameSize(img.dataset.artist));
    img.replaceWith(tile);
  }
  // Image errors don't bubble, so listen in the capture phase. Registered before any cover
  // exists: covers are only created by render(), after this script has run.
  document.addEventListener("error", e => {
    const t = e.target;
    if (t instanceof HTMLImageElement && "artist" in t.dataset) coverFallback(t);
  }, true);

  // alt="": every cover is inside its album's link, whose label already names it (AOTY-43).
  // A followed album link (AOTY-43), counted with nothing about which. Delegated, so it survives
  // every re-render's innerHTML. auxclick: a middle-click opens a tab without a click event.
  // (A context-menu "open in new tab" can't be seen.)
  const albumClick = e => {
    if (e.type === "auxclick" && e.button !== 1) return;
    if (e.target instanceof Element && e.target.closest("a.lf")) track("event", "album_click");
  };
  $("ranking").addEventListener("click", albumClick);
  $("ranking").addEventListener("auxclick", albumClick);

  function cover(a, cls) {
    if (a.art) return `<img class="${cls}" src="${esc(a.art)}" alt="" loading="lazy" `
        + `data-fallbacks="${esc(a.fallbacks.join(" "))}" data-artist="${esc(a.artist)}">`;
    return `<div class="${cls} noart"><span style="--fs:${nameSize(a.artist)}">${esc(a.artist)}</span></div>`;
  }

  // One cover, loaded off-screen (AOTY-66): the first of urls that loads, as the <img> itself (so
  // it's fetched once and can go straight on the page), or null when every URL failed. cors: a CORS
  // request, so its pixels can be read (AOTY-65's theme). Shared with AOTY-67's cover palette.
  function loadCover(urls, {cors = false} = {}) {
    return new Promise(resolve => {
      const next = i => {
        if (i >= urls.length) return resolve(null);
        const img = new Image();
        if (cors) img.crossOrigin = "anonymous";
        img.decoding = "async";
        img.onerror = () => next(i + 1);
        img.onload = async () => {
          img.onload = img.onerror = null;
          // decode() can reject for an image that loaded fine (Firefox): loaded is loaded.
          if (img.decode) { try { await img.decode(); } catch (_) { /* still shown */ } }
          if (img.naturalWidth > 0) resolve(img); else next(i + 1);
        };
        img.src = urls[i];
      };
      next(0);
    });
  }

  // A user page's one ranking (By Play): the album plays it sorts by, plus the plays behind them.
  function score(a) {
    return `<b>${num(a.score, 1)}</b> album plays · ${num(a.plays)} plays`;
  }

  // Group pages (AOTY-51), as M0's page: one dot per member, the first `listeners` filled (a
  // count, not who), above the stats; not on the small #11+ tiles. groupTotal is every member,
  // still-loading and unavailable ones included, as M0 counted the whole server.
  let groupTotal = 0, groupWho = "";
  function dots(a) {
    if (!groupTotal) return "";
    let s = "";
    for (let i = 0; i < groupTotal; i++) s += `<span class="dot${i < a.listeners ? " on" : ""}"></span>`;
    // Decoration inside the album's link: its label carries the count (AOTY-43).
    return `<div class="dots" aria-hidden="true">${s}</div>`;
  }
  // M0's score words: for one member it's their own album plays, not the group's. By Listener's
  // score is album plays too: the listeners it sorts by first lead the stats line.
  function groupScore(a, mode) {
    if (groupWho) return `<b>${num(a.score, 1)}</b> album plays by ${esc(groupWho)}`;
    if (mode === "weighted") return `<b>${num(a.score, 1)}</b> plays per listener`;
    return `<b>${num(a.score, 1)}</b> album plays`;
  }
  // The stats line: user pages as before; group pages lead with listeners, worded per place.
  function stats(a, mode, place) {
    if (!groupTotal) return score(a);
    const t = groupTotal, n = `<b>${a.listeners}</b>`;
    const lead = place === "hero" ? `${n} of ${t} listeners` : place === "card" ? `${n} listeners`
        : place === "tile" ? `${n}/${t}` : `${n}/${t} listeners`;
    return `${lead} · ${groupScore(a, mode)}`;
  }
  const names = a => `<div class="album">${esc(a.album)}</div><div class="artist">${esc(a.artist)}</div>`;

  // Each album is one link to its Last.fm page (AOTY-43; Last.fm's API terms, 2.7), the whole
  // card, tile or row (user, 2026-11-25). A new tab, with no referrer: the path names the user.
  // The label replaces the content for screen readers, so it carries the rank and the stats too
  // (the hero's wording, the fullest); aria-describedby is skipped on links by some of them.
  // "lastfm" is the album's Last.fm name; charts built before AOTY-43 have only the root title.
  function albumLink(a, cls, mode, extra = "") {
    const href = lf.albumUrl(a.artist, a.lastfm || a.album);
    const said = stats(a, mode, "hero").replace(/<[^>]*>/g, "");  // already escaped text
    const label = `${esc(`#${a.rank}, ${a.album} by ${a.artist} on last.fm`)}, ${said}, opens in a new tab`;
    return `<a class="lf ${cls}" href="${esc(href)}" target="_blank" rel="noopener noreferrer" `
        + `aria-label="${label}"${extra}>`;
  }

  // One ranking's poster view and list view.
  function renderRanking(chart, rows, mode) {
    if (!rows.length) {
      const line = grp.checkingLine(chart);
      const wait = line ? ` ${line}` : "";
      const whose = chart.kind === "group" ? `the ${esc(chart.name)} group` : esc(chart.display_name);
      return `<p class="empty">No ${chart.year} albums in ${whose}'s scrobbles yet.${wait}</p>`;
    }
    const [top, rest, more] = [rows[0], rows.slice(1, chart.featured), rows.slice(chart.featured)];
    const bg = top.art ? ` style="--hero-art:url(&quot;${esc(top.art)}&quot;)"` : "";
    const hero = `${albumLink(top, "hero", mode, bg)}${cover(top, "art")}<div>
        <div class="crown">#1</div>${names(top)}${dots(top)}
        <div class="stats">${stats(top, mode, "hero")}</div>
      </div></a>`;
    const cards = rest.map((a, i) => `<li>${albumLink(a, "card", mode, ` style="--i:${i + 1}"`)}
        <div class="art-wrap">${cover(a, "art")}<span class="rank">${a.rank}</span></div>
        <div class="meta">${names(a)}${dots(a)}<div class="stats">${stats(a, mode, "card")}</div></div>
      </a></li>`).join("");
    const tiles = more.map(a => `<li>${albumLink(a, "tile", mode)}
        <div class="art-wrap">${cover(a, "art")}<span class="tile-rank">${a.rank}</span></div>
        <div class="tile-meta">${names(a)}<div class="stats">${stats(a, mode, "tile")}</div></div>
      </a></li>`).join("");
    const moreSection = more.length ? `<section class="more"><h2>${chart.featured + 1}–${rows.length}</h2>
        <ol class="more-grid">${tiles}</ol></section>` : "";
    const list = rows.map((a, i) => `<li>${albumLink(a, `row${i ? "" : " first"}`, mode, ` style="--i:${i}"`)}
        <span class="row-rank">${a.rank}</span>${cover(a, "row-art")}
        <div class="row-meta">${names(a)}</div>
        <div class="row-numbers">${dots(a)}<div class="stats">${stats(a, mode, "row")}</div></div>
      </a></li>`).join("");
    return `<div class="view-poster">${hero}<ol>${cards}</ol>${moreSection}</div>
      <ol class="list view-list">${list}</ol>`;
  }

  function renderChart(chart) {
    if (chart.kind === "group") return renderGroup(chart);
    // One ranking, By Play: no switch (charts built before AOTY-63 also carry "track", unused).
    const mode = "album";
    $("rank-switch").hidden = true;
    const rows = (chart.rankings.album || []).map(([i, s], k) => ({...chart.albums[i], score: s, rank: k + 1}));
    const who = chart.display_name;
    $("sub").textContent = `Top ${rows.length} albums released in ${chart.year} that ${who} played, ${BY_PLAY}.`;
    const checking = grp.checkingLine(chart);
    if (checking) {
      const p = $("sub").appendChild(document.createElement("span"));
      p.className = "pending";
      p.textContent = ` ${checking}`;
    }
    $("ranking").innerHTML = renderRanking(chart, rows, mode);
  }

  // Two pill switches, view (data-view) and ranking (data-rank), each remembered per browser.
  // Storage can be blocked, so fall back to the defaults quietly.
  function setupSwitch(attr, storageKey, onChange) {
    const buttons = document.querySelectorAll(`.switch button[data-${attr}]`);
    const values = [...buttons].map(b => b.dataset[attr]);
    function set(value) {
      poster.dataset[attr] = value;
      buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
      try { localStorage.setItem(storageKey, value); } catch (_) {}
    }
    buttons.forEach(b => b.addEventListener("click", () => {
      set(b.dataset[attr]);
      track("event", "control_change", {control: attr, value: b.dataset[attr]});
      if (onChange) onChange();
    }));
    let saved = null;
    try { saved = localStorage.getItem(storageKey); } catch (_) {}
    if (values.includes(saved)) set(saved);
  }

  // user: a chart's username. Its name then links to their page here, and a small "[last.fm]"
  // to their Last.fm profile, as Last.fm's API terms (2.7) ask (AOTY-43). Built node by node.
  function heading(name, year, user) {
    $("eyebrow").textContent = NAME;
    const h1 = $("title");
    if (user) h1.replaceChildren(...userLinks(name, user, "title-lastfm"));
    else h1.textContent = name;
    if (year) {
      h1.appendChild(document.createElement("br"));
      const y = h1.appendChild(document.createElement("span"));
      y.className = "year";
      y.textContent = year;
    }
    document.title = year ? `${name}'s ${NAME} ${year} · a kamp toy` : TITLE;
  }

  // [the name, linked to user's page here; " "; "[last.fm]", linked to their Last.fm profile in a
  // new tab, with no referrer (the path names whoever's page this is)]. User names are
  // USERNAME_RE's letters, so the path needs no escaping beyond encodeURIComponent.
  function userLinks(name, user, cls) {
    const page = document.createElement("a");
    page.className = "user-link";
    page.href = `${BASE}user/${encodeURIComponent(name)}`;
    page.textContent = name;
    const wrap = document.createElement("span");
    wrap.className = cls;
    const profile = wrap.appendChild(document.createElement("a"));
    profile.href = intro.lastfmUserUrl(user);
    profile.target = "_blank";
    profile.rel = "noopener noreferrer";
    profile.textContent = "last.fm";
    profile.setAttribute("aria-label", `${name} on last.fm, opens in a new tab`);
    wrap.prepend("[");
    wrap.append("]");
    return [page, " ", wrap];
  }

  // A message panel, optionally with the username box. The footer's profile line goes: the
  // panel may be saying this Last.fm user doesn't exist.
  function state(title, text, withLookup) {
    stopAurora();
    clearTheme();
    $("controls").hidden = true;
    $("sub").textContent = "";
    $("footer-user").hidden = true;
    // A user page with no chart has nothing to share. Group pages decide in showGroupTools, which
    // runs after their states, so a passing error there never loses the share button.
    if (routeName() !== null) pageTools(null);
    const box = document.createElement("section");
    box.className = "state";
    box.appendChild(document.createElement("h2")).textContent = title;
    box.appendChild(document.createElement("p")).textContent = text;
    if (withLookup) box.appendChild(lookupForm());
    $("ranking").replaceChildren(box);
  }

  // The username form on chart pages' error states: the intro's form, built in script.
  let lookupCount = 0;
  function lookupForm() {
    const id = `lookup-${++lookupCount}`;
    const form = document.createElement("form");
    form.className = "lookup";
    form.noValidate = true;
    const label = form.appendChild(document.createElement("label"));
    label.className = "lookup-label";
    label.htmlFor = `${id}-name`;
    label.textContent = "last.fm username";
    const row = form.appendChild(document.createElement("div"));
    row.className = "lookup-row";
    const input = row.appendChild(document.createElement("input"));
    Object.assign(input, {id: `${id}-name`, name: "user", type: "text", maxLength: 128, autocomplete: "username",
                          spellcheck: false});
    for (const [k, v] of [["autocapitalize", "none"], ["autocorrect", "off"], ["enterkeyhint", "go"],
                          ["aria-describedby", `${id}-msg`]]) input.setAttribute(k, v);
    const button = row.appendChild(document.createElement("button"));
    button.type = "submit";
    button.textContent = "press my chart";
    const msg = form.appendChild(document.createElement("p"));
    Object.assign(msg, {className: "lookup-msg", id: `${id}-msg`});
    msg.setAttribute("role", "status");
    msg.setAttribute("aria-live", "polite");
    bindLookup(form);
    return form;
  }

  // Validate a name through the API (POST sync, which checks it with Last.fm and starts the
  // sync the visitor wants anyway), then go to its chart in Last.fm's casing. The chart page
  // POSTs again; that answers "in_progress" and queues nothing. intro.js has the rules.
  function bindLookup(form) {
    const input = form.querySelector("input");
    const button = form.querySelector("button");
    const msg = form.querySelector(".lookup-msg");
    const label = button.textContent;
    let busy = false;

    function setBusy(on) {
      busy = on;
      button.setAttribute("aria-busy", String(on));
      button.setAttribute("aria-disabled", String(on));  // not disabled: that would drop focus
      button.textContent = on ? "pressing…" : label;
    }
    function fail(text) {
      setBusy(false);
      msg.textContent = text;
      input.setAttribute("aria-invalid", "true");
      input.focus();
      input.select();
    }
    // Back from a chart page can restore this page from the bfcache mid-"pressing…".
    window.addEventListener("pageshow", () => setBusy(false));
    input.addEventListener("input", () => {
      input.removeAttribute("aria-invalid");
      msg.textContent = "";
    });

    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (busy) return;
      const clean = intro.cleanName(input.value);
      if (clean.error) {
        track("introSubmit", {message: intro.MESSAGES[clean.error]}, intro.MESSAGES);
        return fail(intro.MESSAGES[clean.error]);
      }
      setBusy(true);
      msg.textContent = "";
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), LOOKUP_TIMEOUT_MS);
      let status = 0, body = null;
      try {
        const res = await fetch(`${BASE}api/users/${clean.name}/sync`, {
          method: "POST", headers: {"content-type": "application/json"}, body: "{}",
          cache: "no-store", signal: ctl.signal,
        });
        status = res.status;
        try { body = await res.json(); } catch (_) { body = null; }  // e.g. a CloudFront 502 page
      } catch (_) {
        status = 0;  // offline, or the timeout
      } finally {
        clearTimeout(timer);
      }
      const result = intro.outcome(status, body, clean.name);
      track("introSubmit", result, intro.MESSAGES);
      if (result.go) {
        location.assign(`${BASE}user/${encodeURIComponent(result.go)}`);
        return;
      }
      fail(result.message);
    });
  }

  // The front door: the bare base path, and any path that isn't a chart (sent to the base).
  function showIntro() {
    if (location.pathname !== BASE) {
      try { history.replaceState(null, "", BASE); } catch (_) {}
    }
    document.title = TITLE;
    stopAurora();
    clearTheme();
    poster.hidden = true;
    $("intro").hidden = false;
    bindLookup($("intro-form"));
  }

  // The username in <base>user/<name>, or null for any other path.
  function routeName() {
    const path = location.pathname;
    if (!path.startsWith(BASE)) return null;
    const m = path.slice(BASE.length).match(/^user\/([^/]+)\/?$/);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (_) { return ""; }
  }

  // The user's chart.json: the chart, null when there's none yet (404), or throws.
  async function loadChart(lower) {
    const res = await fetch(`${BASE}data/users/${lower}/chart.json`, {cache: "no-cache"});
    if (res.status === 404) return null;
    const chart = res.ok ? await res.json() : null;
    if (!chart || chart.version !== CHART_VERSION || chart.username !== lower) throw new Error("bad chart");
    return chart;
  }

  // The page's one chart, painted on load and again in place when a sync brings a newer one.
  // current is the chart on screen (it ranks something); loadedAt is the generated_at of the
  // last chart.json fetched, shown or not (a first visit's charts rank nothing for a while).
  let current = null, loadedAt = null, switchesReady = false;

  // Once per page, for user and group pages alike: a second setup would add every click
  // listener twice.
  function setupSwitchesOnce() {
    if (switchesReady) return;
    switchesReady = true;
    setupSwitch("view", "aoty-view");
    setupSwitch("rank", "aoty-rank", () => renderChart(current));
    // The share image's #1 | top 3 (AOTY-73) and square | story (AOTY-72): remembered per viewer,
    // made ahead like the rest. AOTY-72's one switch saved "top1_story": that's the story shape.
    try {
      if (localStorage.getItem("aoty-card-shape") === null && localStorage.getItem("aoty-card-format") === "top1_story") {
        localStorage.setItem("aoty-card-shape", "story");
      }
      localStorage.removeItem("aoty-card-format");
    } catch (_) {}
    setupSwitch("content", "aoty-card-content", () => renderShare());
    setupSwitch("shape", "aoty-card-shape", () => renderShare());
  }

  function paint(chart, raw, lower) {
    // Last.fm's casing in the address bar, but only a name that is this user's.
    const display = typeof chart.display_name === "string" && chart.display_name.toLowerCase() === lower
        ? chart.display_name : lower;
    chart.display_name = display;
    if (display !== raw) {
      try { history.replaceState(null, "", `${BASE}user/${encodeURIComponent(display)}${location.search}`); } catch (_) {}
      track("setPage");
    }
    const repaint = current !== null;
    if (!repaint && firstView) track("event", "first_visit_end", {result: "painted"});
    stopAurora();  // the chart's here: the first visit's aurora fades out (AOTY-67)
    current = chart;
    heading(display, chart.year, lower);
    $("controls").hidden = false;
    pageTools({kind: "user", id: display, name: display});
    const updated = new Date(chart.generated_at);
    profileLine(display, isNaN(updated) ? ""
        : updated.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}));
    setupSwitchesOnce();
    if (repaint) {
      // In place: keep the scroll position, and don't replay the entrance animations.
      const y = window.scrollY;
      $("ranking").classList.add("no-anim");
      renderChart(chart);
      window.scrollTo(0, y);
    } else {
      renderChart(chart);
    }
    themeForChart(chart, lower, repaint);
  }

  // The footer's line about this page's user (AOTY-29): a link to their Last.fm profile, as
  // Last.fm's API terms ask of pages showing profile data, and when the chart was built. Built
  // node by node (never innerHTML) in a line of its own: the credits beside it are static HTML.
  function profileLine(name, updated) {
    const line = $("footer-user");
    const link = document.createElement("a");
    link.href = intro.lastfmUserUrl(name);
    link.textContent = name;
    line.replaceChildren(link, " on last.fm", ...(updated ? [` · updated ${updated}`] : []));
    line.hidden = false;
  }

  async function showUser(raw) {
    const lower = raw.toLowerCase();
    heading(raw, "");
    if (!USERNAME_RE.test(lower)) {
      state("that isn't a last.fm username", "they're letters, numbers, dots, dashes and underscores. try another?", true);
      return;
    }
    profileLine(raw, "");  // the URL's casing until a chart brings Last.fm's
    paintRememberedTheme({kind: "user", id: lower});  // no dark flash before a light page's chart arrives (AOTY-75)
    let chart;
    try {
      chart = await loadChart(lower);
    } catch (_) {
      state("couldn't load this chart", "check your connection, or try again in a minute.", false);
      return;
    }
    loadedAt = chart ? chart.generated_at : null;
    if (fv.isFirstVisit(chart)) {
      showFirstVisit(raw);
      themeForPage({kind: "user", id: lower}, chart);  // the owner can pick while the chart is pressed (AOTY-65)
    } else {
      paint(chart, raw, lower);
    }
    watchSync(lower, async () => {
      const fresh = await loadChart(lower);
      if (!fresh || fresh.generated_at === loadedAt) return;
      loadedAt = fresh.generated_at;
      // A chart that ranks nothing yet leaves the first-visit view up. The first one that ranks
      // something paints like any first paint, entrance animation included.
      if (!fv.isFirstVisit(fresh)) paint(fresh, raw, lower);
    });
  }

  // ---- first visit (AOTY-24) ----------------------------------------------------------------
  //
  // While a new user's first chart is pressed: a dim wall of the covers they played most
  // recently (sync's preview, from status), and what's been found so far. The progress line is
  // the banner, so the page keeps its one live region.
  let firstView = null;

  function showFirstVisit(name) {
    clearTheme();
    $("controls").hidden = true;
    $("sub").textContent = `pressing ${name}'s first chart. it takes a few minutes: we read the year's `
        + "scrobbles, then check every album's release year with musicbrainz.";
    const box = document.createElement("section");
    box.className = "firstvisit";
    const queue = box.appendChild(queueBlock());
    const sofar = box.appendChild(document.createElement("p"));
    sofar.className = "fv-sofar";
    const note = box.appendChild(document.createElement("p"));
    note.className = "fv-note";
    note.textContent = "counting every play this year, whatever year the album came out.";
    note.hidden = true;
    const wall = box.appendChild(document.createElement("div"));  // rows of covers (fillWall)
    wall.className = "wall";
    wall.setAttribute("aria-hidden", "true");  // decoration: the numbers above carry the meaning
    wall.hidden = true;  // until its first cover has loaded (fillWall)
    $("ranking").replaceChildren(box);
    firstView = {box, queue, sofar, note, wall, walled: false};
    startAurora();  // AOTY-67
  }

  // The request line (AOTY-26): the place in line, this page's address with a copy button, and
  // an estimated wait once there's a measured one. Hidden until the status has a position. Not
  // a live region: the banner announces the position, at most once a minute.
  function queueBlock() {
    const block = document.createElement("div");
    block.className = "fv-queue";
    block.hidden = true;
    const caller = block.appendChild(document.createElement("p"));
    caller.className = "fv-caller";
    const link = block.appendChild(document.createElement("p"));
    link.className = "fv-link";
    const wait = block.appendChild(document.createElement("p"));
    wait.className = "fv-wait";
    const copy = block.appendChild(document.createElement("button"));
    copy.type = "button";
    copy.className = "fv-copy";
    copy.textContent = "copy link";
    copy.addEventListener("click", () => {
      track("event", "share_copy");
      copyLink(copy, block.querySelector(".fv-url"), queueUrl());
    });
    return block;
  }

  // The link to a page (AOTY-84): {kind: "user" | "group", id}. Short on prod, else the page's
  // own address, never with a query.
  const linkFor = target => share.shareUrl({origin: location.origin, base: BASE, short: SHORT, ...target});
  // The first visit's queue line names the page's link, in the casing the address bar shows.
  const queueUrl = () => linkFor({kind: "user", id: routeName() || ""});

  // Copy url; with no clipboard (an insecure context, or permission refused), select urlText
  // instead. Says which happened through say(text) (default: the button's own label).
  async function copyLink(button, urlText, url, say = null) {
    const label = button.textContent;
    const tell = say || (t => { button.textContent = t; });
    try {
      await navigator.clipboard.writeText(url);
      tell(say ? "link copied." : "copied");
    } catch (_) {
      if (urlText) {
        const range = document.createRange();
        range.selectNodeContents(urlText);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      tell(say ? "couldn't copy it, so the link is selected: copy it from there." : "selected: copy it");
    }
    if (!say) setTimeout(() => { button.textContent = label; }, 2500);
  }

  function updateQueue(position, waitSeconds) {
    const q = firstView.queue;
    q.hidden = !position;
    if (!position) return;
    const lines = fv.queueLines(position, queueUrl(), waitSeconds);
    q.querySelector(".fv-caller").textContent = lines.caller;
    const shown = queueUrl().replace(/^https?:\/\//, "");
    const [before, after] = lines.link.split(shown);
    const link = q.querySelector(".fv-link");
    const url = document.createElement("span");
    url.className = "fv-url";
    url.textContent = shown;
    link.replaceChildren(before, url, after);
    const wait = q.querySelector(".fv-wait");
    wait.textContent = lines.wait;
    wait.hidden = !lines.wait;
  }

  function updateFirstVisit(s, position = null) {
    if (!firstView || !firstView.box.isConnected) return;
    updateQueue(position, s.queue && s.queue.wait_s);
    const text = fv.soFar(s);
    firstView.sofar.textContent = text;
    firstView.note.hidden = !text;
    if (!firstView.walled && Array.isArray(s.wall) && s.wall.length) {
      firstView.walled = true;
      fillWall(firstView.wall, s.wall);
    }
  }

  // Only covers that loaded go on the wall (AOTY-66, user decision 2026-11-25): each is added at
  // the end as it arrives, so the tiles already there never move, and fades in with wall-in. Covers
  // that fail every URL, and albums with no cover, never show: no blanks, no broken images, no
  // artist-name tiles. The wall stays hidden until its first cover lands. A burst of arrivals (a
  // warm cache) staggers by its place in the burst, as the chart's cards do.
  const WALL_BURST_MS = 400;
  // The first AURORA_COVERS load with CORS, once, and also colour the aurora (AOTY-67). A cover
  // whose CORS request fails still goes on the wall through a plain load; it just doesn't colour.
  const AURORA_COVERS = 6;
  async function wallCover(urls, forAurora) {
    if (!forAurora) return loadCover(urls);
    const img = await loadCover(urls, {cors: true});
    if (!img) return loadCover(urls);
    const px = coverPixels(img);
    if (px) auroraCover(px);
    return img;
  }
  function fillWall(wall, entries) {
    let burst = 0, last = -Infinity, loaded = 0;
    const sources = fv.wallSources(entries);
    // Rows that each drift at their own pace (AOTY-68). Loaded covers go round-robin, so the rows
    // grow evenly and a failed cover just shortens them all a little.
    const rows = Array.from({length: fv.wallRows(sources.length)}, (_, k) => {
      const row = wall.appendChild(document.createElement("ul"));
      const {seconds, reverse} = fv.drift(k);
      row.className = "wall-row";
      row.style.setProperty("--drift-s", `${seconds}s`);
      row.style.setProperty("--drift-dir", reverse ? "alternate-reverse" : "alternate");
      return row;
    });
    sources.forEach((urls, n) => {
      wallCover(urls, n < AURORA_COVERS).then(img => {
        if (!img || !wall.isConnected) return;  // a newer first-visit view, or the chart, replaced it
        const now = performance.now();
        if (now - last > WALL_BURST_MS) burst = 0;
        last = now;
        img.className = "wall-art";
        img.alt = "";  // decoration: the wall is aria-hidden
        const li = document.createElement("li");
        li.style.setProperty("--i", String(burst++));
        li.appendChild(img);
        rows[loaded++ % rows.length].appendChild(li);
        wall.hidden = false;
      });
    });
  }

  // A loaded cover's pixels, drawn small (32x32), or null when the browser won't let us read them
  // (a tainted canvas: the host didn't allow CORS after all). Shared by AOTY-65's "album of the
  // year" and the aurora.
  function coverPixels(img) {
    if (!img) return null;
    try {
      const c = document.createElement("canvas");
      c.width = c.height = 32;
      const ctx = c.getContext("2d", {willReadFrequently: true});
      ctx.drawImage(img, 0, 0, 32, 32);
      return ctx.getImageData(0, 0, 32, 32).data;
    } catch (_) {
      return null;
    }
  }

  // ---- the aurora (AOTY-67) -----------------------------------------------------------------
  //
  // kamp's Borealis (web/src/borealis.js) fills the viewport behind a first visit, coloured by the
  // first wall covers. User decisions (2026-11-25): first visits only, the whole page, the surge
  // as covers land. It mounts AURORA_DELAY_MS in, so a chart that's there at once never flashes
  // it, and it fades out when the chart paints or the page leaves the first visit.
  //
  // The canvas sits at z-index -1, under body's in-flow content but over the root's background,
  // and html.has-aurora makes body's own background transparent so it shows. That class stays until
  // the fade-out ends: removed earlier, body's background would hide the canvas at once.
  const bo = globalThis.aotyBorealis;  // web/src/borealis.js, loaded first
  const AURORA_DELAY_MS = 1500, AURORA_FADE_MS = 600;
  let aurora = null, auroraCanvas = null, auroraTimer = 0, auroraFeed = 0;
  let auroraPixels = [], auroraSent = null;

  function startAurora() {
    if (!bo || auroraCanvas || auroraTimer) return;
    auroraTimer = setTimeout(mountAurora, AURORA_DELAY_MS);
  }

  function pageColors() {
    const css = getComputedStyle(document.documentElement);
    return {bg: css.getPropertyValue("--bg").trim(), accent: css.getPropertyValue("--accent").trim()};
  }

  function mountAurora() {
    auroraTimer = 0;
    if (auroraCanvas || !firstView || !firstView.box.isConnected) return;
    const canvas = document.createElement("canvas");
    canvas.className = "aurora";
    canvas.setAttribute("aria-hidden", "true");
    document.body.prepend(canvas);
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const a = bo.mount(canvas, {...pageColors(), reducedMotion, onLost: () => stopAurora(true)});
    if (!a) { canvas.remove(); return; }  // no WebGL: the page stays as it always was
    aurora = a;
    auroraCanvas = canvas;
    document.documentElement.classList.add("has-aurora");
    requestAnimationFrame(() => canvas.classList.add("on"));  // fades in
    feedAurora();  // covers that arrived before it mounted
  }

  // A wall cover's pixels: the palette is recomputed once per burst of arrivals, and changes only
  // when it really does (borealis.paletteChanged), so a warm cache's six covers make one surge.
  function auroraCover(px) {
    if (!firstView || !firstView.box.isConnected) return;
    auroraPixels.push(px);
    clearTimeout(auroraFeed);
    auroraFeed = setTimeout(feedAurora, WALL_BURST_MS);
  }

  function feedAurora() {
    auroraFeed = 0;
    if (!aurora || !auroraPixels.length) return;
    const all = new Uint8ClampedArray(auroraPixels.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of auroraPixels) { all.set(p, at); at += p.length; }
    const {colors, source} = bo.extractPalette(all, pageColors().accent);
    if (source !== "art" || !bo.paletteChanged(auroraSent, colors)) return;
    auroraSent = colors;
    aurora.setPalette(colors);
  }

  // The page's colours changed (an owner's theme): the aurora paints its background itself.
  function auroraColors() {
    if (aurora) aurora.setColors(pageColors());
  }

  // Fade out, then take it down (and its WebGL context). lost: the context is gone already.
  function stopAurora(lost = false) {
    clearTimeout(auroraTimer);
    clearTimeout(auroraFeed);
    auroraTimer = auroraFeed = 0;
    auroraPixels = [];
    auroraSent = null;
    if (!auroraCanvas) return;
    const canvas = auroraCanvas, a = aurora;
    auroraCanvas = aurora = null;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      a.destroy();
      canvas.remove();
      if (!auroraCanvas) document.documentElement.classList.remove("has-aurora");
    };
    if (lost) return finish();
    canvas.addEventListener("transitionend", finish, {once: true});
    setTimeout(finish, AURORA_FADE_MS + 300);  // reduced motion has no transition to end
    canvas.classList.remove("on");
  }

  // ---- sync on visit (AOTY-22) ------------------------------------------------------------
  //
  // Ask for a sync (the API answers a running sync or the 10-minute cooldown without queueing),
  // then poll the status: every 2 s, backing off to 5 s, every 30 s while release years are
  // checked, and by place while waiting in line (AOTY-26; see queuedPollMs), pausing while the tab
  // is hidden.
  // When the chart builder writes a newer chart, onNewChart repaints it in place. Nothing here
  // ever blocks the chart: errors only show a quiet line. A throttled or busy status (429/503)
  // is retried with a back-off, not given up on: a queued crowd is exactly when that happens.
  const POLL_FIRST_MS = 2000, POLL_MAX_MS = 5000, POLL_DATING_MS = 30000;
  // While in line the pace goes by place (firstvisit.queuedPollMs): the normal back-off near the
  // front, 8-10 s in a short line, 20-25 s in a crowd. POLL_QUEUED_MS is a throttled poll's floor.
  const POLL_QUEUED_MS = 20000, POLL_RETRY_MAX_MS = 60000, RETRIES_MAX = 10;
  // The watch only counts once the sync has left the queue: a long wait in line isn't a stall.
  const WATCH_FOR_MS = 10 * 60 * 1000, DATING_FOR_MS = 2 * 60 * 1000;

  // The banner: #banner-msg is the page's one polite live region and changes only with the
  // stage; the counts beside it change on every poll and are hidden from screen readers.
  function say(msg, detail = "") {
    const box = $("banner"), live = $("banner-msg");
    if (live.textContent !== msg) live.textContent = msg;
    $("banner-detail").textContent = detail;
    box.classList.toggle("show", !!msg);
  }

  function watchSync(lower, onNewChart) {
    // A first visit keeps watching through the release-year checks, even once its chart shows,
    // because that's when most of it fills in.
    const firstSession = current === null;
    const progress = fv.makeProgress();
    const queue = fv.makeQueue();
    const baseTitle = document.title;
    let delay = POLL_FIRST_MS, timer = null, stopped = false, datingSince = null, watchFrom = null, retries = 0;
    const stop = msg => {
      stopped = true;
      clearTimeout(timer);
      timer = null;
      say(msg || "");
      if (!current) document.title = baseTitle;  // a painted chart set its own title
    };
    const quiet = () => stop("Couldn't update right now.");
    const schedule = ms => { if (!stopped && !document.hidden) timer = setTimeout(tick, ms); };
    const timedOut = () => watchFrom !== null && Date.now() - watchFrom > WATCH_FOR_MS;

    async function maybeRefresh(s) {
      const built = s.chart_built_at || 0;
      const have = loadedAt ? Date.parse(loadedAt) / 1000 : 0;
      if (built > have) {
        try { await onNewChart(); } catch (_) {}
      }
    }

    // Still nothing ranked: the wall, the progress line, and a clear end when there's no more
    // to wait for. True when it handled the status.
    function handleFirstVisit(s) {
      if (current) return false;
      const inLine = s.stage === "queued";
      const place = queue(s, Date.now());
      const position = inLine ? place.position : null;
      updateFirstVisit(s, position);
      const end = fv.firstVisitEnd(s, timedOut());
      if (end) {
        track("event", "first_visit_end", {result: end});
        const [title, text] = fv.endMessage(end, YEAR, s);
        state(title, text, false);
        stop("");
        return true;
      }
      if (s.stage === "idle") { gaveUp(); stop(""); return true; }
      const {live, detail, percent} = progress(s);
      // In line, the banner carries the place (announced at most once a minute); after, progress.
      if (position) say(place.live, ""); else say(live, detail);
      document.title = fv.tabTitle(baseTitle, position, percent);
      delay = fv.queuedPollMs(s) ?? Math.min(Math.round(delay * 1.5), POLL_MAX_MS);
      schedule(delay);
      return true;
    }

    // The sync is over but there's still no chart to show (first visit): say so, don't wait on.
    function gaveUp(reason) {
      if (current) return;
      if (firstView) track("event", "first_visit_end", {result: reason === "private" ? "private" : "error"});
      if (reason === "private") {
        state("this profile is private", "its last.fm listening isn't public, so there's nothing for us to read.", false);
      } else {
        state("couldn't build a chart this time", "something went wrong reading the scrobbles. try again in a few minutes.", false);
      }
    }

    async function handle(s) {
      themeFromStatus(s);
      if (watchFrom === null && s.stage !== "queued") watchFrom = Date.now();
      await maybeRefresh(s);
      if (s.stage === "error") { gaveUp(s.reason); return current ? quiet() : stop(""); }
      if (handleFirstVisit(s)) return;
      if (s.stage === "idle") return stop("");
      // A first session keeps its progress line (and its quarters) once the chart shows.
      const [msg, detail] = firstSession ? Object.values(progress(s)) : fv.describe(s);
      say(msg, detail);
      if (s.stage === "dating") {
        datingSince = datingSince || Date.now();
        if (!firstSession && Date.now() - datingSince > DATING_FOR_MS) return stop("");
        delay = POLL_DATING_MS;
      } else if (fv.queuedPollMs(s) !== null) {
        delay = fv.queuedPollMs(s);
      } else {
        delay = Math.min(Math.round(delay * 1.5), POLL_MAX_MS);
      }
      if (timedOut()) { gaveUp(); return stop(""); }
      schedule(delay);
    }

    async function tick() {
      timer = null;
      if (stopped) return;
      if (document.hidden) return;  // visibilitychange resumes it
      let res;
      try {
        res = await fetch(`${BASE}api/users/${lower}/status`, {cache: "no-store"});
      } catch (_) { return quiet(); }
      if (res.status === 404) return stop("");
      if (res.status === 429 || res.status === 503) {
        // Throttled or briefly unavailable: wait longer and ask again, without a word.
        if (++retries > RETRIES_MAX) return quiet();
        delay = Math.min(Math.max(delay * 2, POLL_QUEUED_MS), POLL_RETRY_MAX_MS);
        return schedule(delay);
      }
      if (!res.ok) return quiet();
      let s;
      try { s = await res.json(); } catch (_) { return quiet(); }
      retries = 0;
      await handle(s);
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !stopped && timer === null) schedule(0);
    });

    say(current ? "Checking for new scrobbles…" : progress({stage: "queued"}).live);
    (async () => {
      let res, body = null;
      try {
        res = await fetch(`${BASE}api/users/${lower}/sync`, {
          method: "POST", headers: {"content-type": "application/json"}, body: "{}", cache: "no-store",
        });
      } catch (_) { return quiet(); }
      try { body = await res.json(); } catch (_) { body = null; }
      if (res.status === 404) {
        if (!current) state("we couldn't find that last.fm user", "check the spelling and try again.", true);
        return stop("");
      }
      // At capacity (AOTY-26): a new user hears why and when to come back. A chart on screen
      // just stays as it is.
      const refused = fv.refusal(res.status, body);
      if (refused) {
        if (!current) state(refused[0], refused[1], false);
        return stop("");
      }
      if (!res.ok || !body || !body.status) return quiet();
      await handle(body.status);
    })();
  }

  // ---- group pages (AOTY-51) ---------------------------------------------------------------
  //   <base>group/<id>   fetch <base>data/groups/<id>/chart.json (lambdas/chart/group_core.py)
  // Everyone's rankings come from the chart; picking a member re-ranks in group.js and is kept
  // in ?user= (replaceState, matched in any casing, unknown = everyone), as in M0.

  function routeGroup() {
    const path = location.pathname;
    if (!path.startsWith(BASE)) return null;
    const m = path.slice(BASE.length).match(/^group\/([^/]+)\/?$/);
    return m ? m[1].toLowerCase() : null;
  }

  // The ranking the switch shows (the page's, so the share image follows it too).
  const groupMode = () => (grp.MODES.includes(poster.dataset.rank) ? poster.dataset.rank : "listener");

  function renderGroup(chart) {
    const mode = groupMode();
    const who = grp.memberIndex(chart, $("who").value);
    const rows = grp.rows(chart, mode, who);
    groupTotal = chart.members.length + (chart.waiting || 0) + (chart.unavailable || 0);
    groupWho = who < 0 ? "" : chart.members[who];
    renderShare();  // a member's view also offers the member's own page (AOTY-84)
    // A member's view has one listener, so every mode is By Play: no switch (AOTY-63).
    $("rank-switch").hidden = who >= 0;
    // A member's view names them with their links, as a user page's heading does (AOTY-43).
    const whose = who < 0 ? [`${chart.name}'s members`] : userLinks(chart.members[who], chart.members[who], "sub-lastfm");
    const how = who < 0 ? GROUP_MODES[mode] : BY_PLAY;
    $("sub").replaceChildren(`Top ${rows.length} albums released in ${chart.year} that `, ...whose, ` played, ${how}.`);
    const extra = [grp.loadingLine(chart), grp.checkingLine(chart)].filter(Boolean).join(" · ");
    if (extra) {
      const p = $("sub").appendChild(document.createElement("span"));
      p.className = "pending";
      p.textContent = ` ${extra}`;
    }
    $("ranking").innerHTML = renderRanking(chart, rows, mode);
  }

  function groupPicklist(chart) {
    const select = $("who");
    const want = new URLSearchParams(location.search).get("user") || select.value;
    const options = [new Option("everyone", ""), ...chart.members.map(m => new Option(m, m))];
    select.replaceChildren(...options);
    const i = grp.memberIndex(chart, want);
    select.value = i < 0 ? "" : chart.members[i];
    $("who-wrap").hidden = false;
  }

  async function loadGroup(id) {
    const res = await fetch(`${BASE}data/groups/${id}/chart.json`, {cache: "no-cache"});
    if (res.status === 404 || res.status === 403) return null;  // S3 behind CloudFront answers 403 for a missing key
    const chart = res.ok ? await res.json() : null;
    if (!grp.valid(chart, id)) throw new Error("bad chart");
    return chart;
  }

  // The group page's state (AOTY-52). One poller per page: every scheduled read carries the
  // generation it was made in, and a rename (which moves the group to a new id) or a delete bumps
  // it, so a read for the old id can never paint over the page.
  // groupInfo: GET api/groups/<id>'s answer; null once it said there's no such group; undefined
  // until it has been read.
  let groupId = null, groupInfo, groupGen = 0, groupTimer = null, groupWake = false, watch = null;
  let syncW = null;  // grp.syncWatch's state after this visit queued its members' syncs (AOTY-55)

  function groupHeading(name, year) {
    heading(name, year);
    document.title = `${name} · ${NAME} ${year} · a kamp toy`;
  }

  async function showGroup(id) {
    stopAurora();
    clearTheme();
    heading(NAME, "");
    if (!grp.ID_RE.test(id)) {
      state("there's no group here", "check the link you were given.", false);
      return;
    }
    groupId = id;
    paintRememberedTheme({kind: "group", id});  // no dark flash before a light group's chart (AOTY-76)
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && groupWake) { groupWake = false; refreshGroup(); }
    });
    await meReady;
    await refreshGroup({first: true});
    if (groupInfo !== null) visitSync();
  }

  // A visit syncs the group's members (AOTY-55): once per page load, quietly. The api decides
  // who's due (each member's cooldown, the group's own, pause_syncs); a refusal or a failure
  // changes nothing on the page. When it queued someone, keep re-reading while they sync.
  async function visitSync() {
    const gen = groupGen;
    const r = await apiCall("POST", `groups/${groupId}/sync`);
    if (gen !== groupGen) return;  // renamed or deleted meanwhile: that page's poller is in charge
    if (r.status !== 200 || !r.body || !(r.body.queued > 0)) return;
    syncW = grp.syncWatch(null, undefined, Date.now());
    if (!watch) scheduleGroup(GROUP_REFRESH_MS);  // an edit's watch already looks every 5 s
  }

  function scheduleGroup(ms) {
    clearTimeout(groupTimer);
    const gen = groupGen;
    groupTimer = setTimeout(() => {
      if (gen !== groupGen) return;
      if (document.hidden) { groupWake = true; return; }  // visibilitychange resumes it
      refreshGroup();
    }, ms);
  }

  // GET api/groups/<id>: {id, name, owner, members, ready, total}, null for no such group, or
  // undefined when it couldn't be read.
  async function loadGroupInfo(id) {
    const r = await apiCall("GET", `groups/${id}`);
    if (r.status === 200 && r.body && Array.isArray(r.body.members)) return r.body;
    return r.status === 404 ? null : undefined;
  }

  // Read the chart (and the group's own record when needed), paint, and decide when to look again.
  async function refreshGroup({first = false} = {}) {
    const gen = groupGen, id = groupId;
    clearTimeout(groupTimer);
    let chart, failed = false;
    try { chart = await loadGroup(id); } catch (_) { failed = true; }
    // The group's record: for the owner's edit controls, while watching an edit, and to tell a
    // group whose first chart isn't written yet from no group at all.
    const needInfo = !chart || watch || syncW || canWrite();
    const info = needInfo ? await loadGroupInfo(id) : groupInfo;
    if (gen !== groupGen) return;
    if (info !== undefined) groupInfo = info;
    if (groupInfo === null && !current) {
      state("there's no group here", "check the link you were given.", false);
      return hideGroupTools();
    }
    if (chart) {
      paintGroup(chart);
    } else if (!current) {
      // A chart is on screen through a rename (the new id's chart isn't written yet); otherwise
      // say what's happening.
      if (failed || !groupInfo) {
        state("we couldn't load this chart", "try again in a minute.", false);
      } else {
        groupHeading(groupInfo.name, YEAR);
        state("pressing this group's chart…", "it takes a few minutes for new members: we read each one's "
            + "year of scrobbles, then check every album's release year.", false);
        themeForPage({kind: "group", id: groupId}, null);  // the owner can pick before the first chart
        if (!watch) startWatch(false);
      }
    }
    // The group's record carries the owner (who gets the picker) and its stored theme.
    if (info && themeTarget) { themeFromStatus(info); renderThemeTools(); }
    showGroupTools(first);
    let next = null;
    if (watch) {
      const r = ge.rebuild(watch, groupInfo, chart || null, Date.now());
      if (r.state === "done" || r.state === "timeout") { watch = null; say(""); } else { say(r.line); next = GROUP_WATCH_MS; }
    }
    if (next === null && chart && (chart.waiting || chart.pending)) next = GROUP_REFRESH_MS;
    if (syncW) {  // members re-syncing after this visit: no banner, the chart just repaints
      syncW = grp.syncWatch(syncW, info === undefined ? undefined : groupInfo, Date.now());
      if (syncW && next === null) next = GROUP_REFRESH_MS;
    }
    if (next !== null) scheduleGroup(next);
  }

  // Watch the chart rebuild after an edit (or a new group's first chart), in the banner.
  function startWatch(soon = true) {
    watch = {since: Date.now(), before: current ? current.generated_at : null};
    say("rebuilding the chart…");
    if (soon) scheduleGroup(GROUP_WATCH_MS);
  }

  function paintGroup(chart) {
    const first = current === null;
    current = chart;
    // The group's theme, on every view of it: a member's (?user=) only re-ranks the page.
    themeForPage({kind: "group", id: groupId}, chart, !first);
    groupHeading(groupInfo && groupInfo.id === chart.id ? groupInfo.name : chart.name, chart.year);
    $("controls").hidden = false;
    groupPicklist(chart);
    // Each member's Last.fm profile, as Last.fm's API terms (2.7) ask; built node by node.
    const line = $("footer-user");
    const parts = [];
    chart.members.forEach((m, i) => {
      const a = document.createElement("a");
      a.href = intro.lastfmUserUrl(m);
      a.textContent = m;
      parts.push(...(i ? [", ", a] : [a]));
    });
    line.replaceChildren(...parts, " on last.fm");
    line.hidden = false;
    if (first) {
      track("event", "group_loaded",
            {group_size: chart.members.length + (chart.waiting || 0) + (chart.unavailable || 0)});
      setupSwitchesOnce();
      $("who").addEventListener("change", () => {
        const url = new URL(location.href);
        if ($("who").value) url.searchParams.set("user", $("who").value); else url.searchParams.delete("user");
        try { history.replaceState(null, "", url); } catch (_) {}
        // Never the member's name: whether someone was picked is all that's counted.
        track("event", "control_change", {control: "filter", value: $("who").value ? "member" : "everyone"});
        renderChart(current);
      });
      renderChart(chart);
    } else {
      const y = window.scrollY;  // in place: no entrance animations, same scroll position
      $("ranking").classList.add("no-anim");
      renderChart(chart);
      window.scrollTo(0, y);
    }
  }

  // ---- the API, signed in (AOTY-52) ----------------------------------------------------------
  //
  // Writes are JSON with the session cookie; the JSON content type is what keeps other sites'
  // forms out, so there's never a form-post fallback. {status, body}: status 0 when the request
  // failed or timed out, body null when there was none (a 204, or a CloudFront error page).
  async function apiCall(method, path, body) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), API_TIMEOUT_MS);
    const opts = {method, cache: "no-store", credentials: "same-origin", signal: ctl.signal};
    if (method !== "GET") {
      opts.headers = {"content-type": "application/json"};
      opts.body = JSON.stringify(body || {});
    }
    try {
      const res = await fetch(`${BASE}api/${path}`, opts);
      let parsed = null;
      try { parsed = await res.json(); } catch (_) { parsed = null; }
      return {status: res.status, body: parsed};
    } catch (_) {
      return {status: 0, body: null};
    } finally {
      clearTimeout(timer);
    }
  }

  // A button busy with a request: aria-busy, not disabled, so focus stays put (as bindLookup).
  function busy(button, on, text) {
    if (on) button.dataset.label = button.textContent;
    button.setAttribute("aria-busy", String(on));
    button.setAttribute("aria-disabled", String(on));
    button.textContent = on ? text : button.dataset.label;
  }
  const isBusy = button => button.getAttribute("aria-busy") === "true";

  function formError(input, msg, text) {
    msg.textContent = text;
    input.setAttribute("aria-invalid", "true");
    input.focus();
    input.select();
  }
  function clearOnInput(input, msg) {
    input.addEventListener("input", () => { input.removeAttribute("aria-invalid"); msg.textContent = ""; });
  }

  // ---- who's signed in --------------------------------------------------------------------------
  //   me: {username, group_writes} signed in; null signed out; undefined unknown (api/me failed,
  //   or a 503 because the session keys can't be read: that is not "signed out").
  //   writesOn: whether group writes are on in this env (api/me says so either way).
  let me, writesOn = false;
  const canWrite = () => !!me && writesOn;

  async function loadMe() {
    const r = await apiCall("GET", "me");
    if (r.status === 200 && r.body && typeof r.body.username === "string") {
      me = {username: r.body.username};
      writesOn = !!r.body.group_writes;
    } else if (r.status === 401) {
      me = null;
      writesOn = !!(r.body && r.body.group_writes);
    } else {
      me = undefined;
    }
    renderAccount();
  }

  // The row under the top bar. Hidden while api/me is unknown, and where group writes are off
  // (group_writes): signing in would offer nothing there.
  function renderAccount() {
    renderThemeTools();  // the theme picker is the page owner's (AOTY-65)
    const row = $("account-row");
    $("account").hidden = me === undefined || !writesOn;
    $("intro-groups").hidden = !canWrite();
    if (me === undefined) return row.replaceChildren();
    if (!me) {
      const a = document.createElement("a");
      a.className = "account-link";
      const here = new URL(location.href);
      here.searchParams.delete("signin");
      a.href = `${BASE}api/auth/start?next=${encodeURIComponent(here.pathname + here.search)}`;
      a.textContent = "sign in with last.fm";
      a.addEventListener("click", () => track("markSignIn"));  // counted when api/me says so
      return row.replaceChildren(a);
    }
    const name = document.createElement("a");
    name.className = "account-link";
    name.href = `${BASE}user/${encodeURIComponent(me.username)}`;
    name.textContent = me.username;
    const groups = document.createElement("button");
    groups.type = "button";
    groups.className = "link-button";
    groups.textContent = "your groups";
    groups.addEventListener("click", () => openMyGroups(groups, false));
    const out = document.createElement("button");
    out.type = "button";
    out.className = "link-button";
    out.textContent = "sign out";
    out.addEventListener("click", () => signOut(out, false));
    // AOTY-57: also revokes the sessions of every other browser or copied cookie.
    const all = document.createElement("button");
    all.type = "button";
    all.className = "link-button";
    all.textContent = "sign out everywhere";
    all.addEventListener("click", () => signOut(all, true));
    row.replaceChildren(name, " · ", groups, " · ", out, " · ", all);
  }

  async function signOut(button, everywhere) {
    if (isBusy(button)) return;
    busy(button, true, "signing out…");
    const r = await apiCall("POST", everywhere ? "auth/signout?everywhere=1" : "auth/signout");
    if (everywhere && r.status === 401) {  // this session was already revoked elsewhere
      me = null;
      renderAccount();
      showGroupTools(false);
      $("account-msg").textContent = "you were already signed out.";
      $("account-row").querySelector("a").focus();
      return;
    }
    if (r.status !== 200) {
      busy(button, false);
      $("account-msg").textContent = "couldn't sign out just now. try again?";
      return;
    }
    me = null;
    renderAccount();
    showGroupTools(false);
    $("account-msg").textContent = everywhere ? "signed out everywhere." : "signed out.";
    $("account-row").querySelector("a").focus();
  }

  // Back from Last.fm with ?signin=failed: say so quietly, and take it out of the address. True
  // when it failed.
  function signinResult() {
    const url = new URL(location.href);
    if (url.searchParams.get("signin") !== "failed") return false;
    url.searchParams.delete("signin");
    try { history.replaceState(null, "", url); } catch (_) {}
    $("account-msg").textContent = "sign-in didn't go through. try again?";
    return true;
  }

  // ---- dialogs ----------------------------------------------------------------------------------
  //
  // Native <dialog> with showModal: it traps focus and closes on Escape. Focus goes back to the
  // button that opened it, unless that button is gone (then the caller decides).
  function openDialog(dialog, opener, focusEl) {
    dialog.addEventListener("close", () => {
      if (opener && opener.isConnected && !opener.closest("[hidden]")) opener.focus();
    }, {once: true});
    dialog.showModal();
    if (focusEl) focusEl.focus();
  }

  // Resolves true when the visitor confirms. Cancel is focused first: the safe default.
  function confirmDialog(opener, title, text, yes) {
    const d = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-yes").textContent = yes;
    d.returnValue = "";
    return new Promise(resolve => {
      d.addEventListener("close", () => resolve(d.returnValue === "yes"), {once: true});
      openDialog(d, opener, $("confirm-no"));
    });
  }
  $("confirm-yes").addEventListener("click", () => $("confirm").close("yes"));
  $("confirm-no").addEventListener("click", () => $("confirm").close("no"));

  // ---- your groups ------------------------------------------------------------------------------

  async function openMyGroups(opener, toCreate) {
    const d = $("mygroups");
    const list = $("mygroups-list");
    $("create-form").hidden = !canWrite();
    list.replaceChildren();
    $("mygroups-empty").hidden = true;
    $("optout").hidden = true;
    $("optout-msg").textContent = "";
    openDialog(d, opener, toCreate && canWrite() ? $("create-name") : $("mygroups-close"));
    const [r, m] = await Promise.all([
      apiCall("GET", "me/groups"),
      canWrite() ? apiCall("GET", "me/memberships") : Promise.resolve(null),
    ]);
    if (r.status === 401 || (m && m.status === 401)) { signedOutInDialog(); return; }
    renderOptOut(m);
    const groups = r.status === 200 && r.body && Array.isArray(r.body.groups) ? r.body.groups : null;
    if (!groups) {
      $("mygroups-empty").textContent = "couldn't load your groups just now.";
      $("mygroups-empty").hidden = false;
      return;
    }
    list.replaceChildren(...groups.map(g => {
      const li = document.createElement("li");
      const a = li.appendChild(document.createElement("a"));
      a.href = `${BASE}group/${encodeURIComponent(g.id)}`;
      a.textContent = g.name;
      const n = Number(g.member_count) || 0;
      li.append(` · ${n} ${n === 1 ? "member" : "members"}`);
      return li;
    }));
    $("mygroups-empty").textContent = "you haven't started a group yet.";
    $("mygroups-empty").hidden = groups.length > 0;
  }
  $("mygroups-close").addEventListener("click", () => $("mygroups").close());
  $("intro-start").addEventListener("click", () => openMyGroups($("intro-start"), true));

  // ---- opting out (AOTY-49): leave others' groups, block future adds ----------------------------
  //
  // memberOf: the groups someone else owns that the visitor is in ({id, name, owner}), as last
  // listed and then kept by this page (by_member is eventually consistent, so a fresh list just
  // after a leave could still show the group).
  let memberOf = [];

  function signedOutInDialog() {
    me = null;
    renderAccount();
    showGroupTools(false);
    $("mygroups").close();
  }

  function renderOptOut(r) {
    const ok = r && r.status === 200 && r.body && Array.isArray(r.body.groups);
    if (!ok) {
      if (r) $("optout-msg").textContent = ge.MESSAGES.listFailed;
      return;
    }
    memberOf = r.body.groups.filter(g => g && typeof g.id === "string");
    $("block-adds").checked = r.body.block_group_adds === true;
    renderMemberOf();
    $("optout").hidden = false;
  }

  function renderMemberOf() {
    $("memberof-list").replaceChildren(...memberOf.map(g => {
      const li = document.createElement("li");
      const a = li.appendChild(document.createElement("a"));
      a.className = "member-name";
      a.href = `${BASE}group/${encodeURIComponent(g.id)}`;
      a.textContent = g.name || g.id;
      const b = li.appendChild(document.createElement("button"));
      b.type = "button";
      b.className = "quiet danger";
      b.textContent = "leave";
      b.setAttribute("aria-label", `leave ${g.name || g.id}`);
      b.addEventListener("click", () => leaveGroups(b, [g]));
      return li;
    }));
    $("memberof-empty").hidden = memberOf.length > 0;
    $("leave-all").hidden = memberOf.length < 2;
  }

  // Leave one group or several, one request at a time; a refusal stops nothing but itself.
  async function leaveGroups(button, groups) {
    if (isBusy(button) || !me) return;
    const [title, text, yes] = groups.length === 1 ? ge.leaveText(groups[0].name || groups[0].id)
      : ge.leaveAllText(groups.length);
    if (!await confirmDialog(button, title, text, yes)) return;
    busy(button, true, "leaving…");
    $("optout-msg").textContent = "";
    const left = [];
    let failed = null;
    for (const g of groups) {
      const r = await apiCall("DELETE", `groups/${encodeURIComponent(g.id)}/members/${encodeURIComponent(me.username)}`);
      const out = ge.answer("remove", r.status, r.body);
      if (out.signedOut) { signedOutInDialog(); return; }
      if (out.ok || out.gone) left.push(g.id);  // a group that's gone has no one in it
      else failed = out;
    }
    if (button.isConnected) busy(button, false);
    memberOf = memberOf.filter(g => !left.includes(g.id));
    renderMemberOf();
    $("memberof-title").focus();  // the rows and their buttons may be gone
    track("event", groups.length === 1 ? "group_leave" : "group_leave_all");
    const n = left.length;
    $("optout-msg").textContent = failed && !n ? failed.message
      : ge.leftLine(n, groups.length, groups.length === 1 ? groups[0].name : null);
    if (groupId && left.includes(groupId)) refreshGroup();  // the page behind is one they just left
  }
  $("leave-all").addEventListener("click", () => leaveGroups($("leave-all"), memberOf.slice()));

  (() => {
    const box = $("block-adds");
    let saving = false;
    box.addEventListener("change", async () => {
      if (saving) { box.checked = !box.checked; return; }
      saving = true;
      const on = box.checked;
      box.setAttribute("aria-busy", "true");
      $("optout-msg").textContent = "";
      const r = await apiCall("PUT", "me/settings", {block_group_adds: on});
      box.removeAttribute("aria-busy");
      saving = false;
      const out = ge.answer("settings", r.status, r.body);
      if (out.signedOut) { signedOutInDialog(); return; }
      if (!out.ok) {
        box.checked = !on;
        $("optout-msg").textContent = out.message === ge.MESSAGES.generic ? ge.MESSAGES.blockFailed : out.message;
        return;
      }
      track("event", "block_adds", {on: on ? "1" : "0"});
      $("optout-msg").textContent = on ? ge.MESSAGES.blockOn : ge.MESSAGES.blockOff;
    });
  })();

  // Create, then go to the new group's page with its edit panel open.
  (() => {
    const form = $("create-form"), input = $("create-name"), msg = $("create-msg");
    const button = form.querySelector("button");
    clearOnInput(input, msg);
    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (isBusy(button)) return;
      const clean = ge.checkName(input.value);
      if (clean.error) return formError(input, msg, ge.MESSAGES[clean.error]);
      busy(button, true, "starting…");
      msg.textContent = "";
      const r = await apiCall("POST", "groups", {name: clean.name});
      const out = ge.answer("create", r.status, r.body);
      if (out.ok && out.id) {
        track("event", "group_create");
        location.assign(`${BASE}group/${encodeURIComponent(out.id)}?edit=1`);
        return;
      }
      busy(button, false);
      if (out.signedOut) { me = null; renderAccount(); }
      formError(input, msg, out.message || ge.MESSAGES.generic);
    });
    window.addEventListener("pageshow", () => { if (isBusy(button)) busy(button, false); });
  })();

  // ---- sharing (AOTY-84) -------------------------------------------------------------------------

  // What the page's share button shares: {kind, id, name}, or null when there's nothing to share.
  // A member's view of a group (groupWho) adds a second button for the member's own page.
  let shareTarget = null, sharing = false;

  // The share row, for user and group pages alike. Edit controls are the group owner's alone, so
  // a user page always hides them.
  function pageTools(target) {
    shareTarget = target;
    $("page-tools").hidden = !target;
    if (!target || target.kind !== "group") {
      $("edit").hidden = true;
      $("edit-toggle").hidden = true;
    }
    renderShare();
  }

  const coarse = () => { try { return matchMedia("(pointer: coarse)").matches; } catch (_) { return false; } };
  const native = data => share.useNative(navigator, coarse(), data);
  const memberTarget = () => ({kind: "user", id: groupWho, name: groupWho});

  // The labels say what a tap does: "share" opens the share sheet (touch only), else "copy link".
  function renderShare() {
    const member = shareTarget && shareTarget.kind === "group" && groupWho ? groupWho : null;
    const labels = share.shareLabels(native({url: linkFor(shareTarget || {kind: "user", id: ""})}), member);
    $("share").textContent = labels.main;
    $("share-member").hidden = !member;
    if (member) $("share-member").textContent = labels.member;
    // The share image (AOTY-70, AOTY-71): any page whose ranking on screen has a #1.
    const image = $("share-image"), s = cardSubject();
    image.hidden = !s;
    $("shape-switch").hidden = !s;
    if (!image.hidden && !isBusy(image)) image.textContent = share.imageLabel(nativeFiles());
    // #1 | top 3 | top 10 (AOTY-73, AOTY-74): each only with that many on screen, and the switch
    // only once there's a choice (three). Hiding keeps the saved choice: a view with fewer draws the
    // biggest that fits, and the next view with enough is the saved one again. The pressed button
    // is the one the image will be; focus on a button that hides goes to the image button.
    const contents = $("content-switch"), count = s ? s.albums.length : 0;
    const focused = document.activeElement;
    contents.hidden = count < share.CONTENTS.top3;
    for (const b of contents.querySelectorAll("button[data-content]")) b.hidden = count < share.CONTENTS[b.dataset.content];
    if (contents.contains(focused) && (contents.hidden || focused.hidden) && !image.hidden) image.focus();
    const shown = cardContentFor(s);
    contents.querySelectorAll("button[data-content]")
        .forEach(b => b.setAttribute("aria-pressed", String(b.dataset.content === shown)));
    cardSoon();
  }

  // The share sheet where it applies, else copy. navigator.share must be the click's first await:
  // it needs the tap's user activation (and Safari uses it up, so a failed share can't copy to
  // the clipboard after it; the link is shown to copy by hand instead).
  async function shareOrCopy(button, target) {
    if (sharing || !target) return;  // a second tap while the sheet is open does nothing
    const url = linkFor(target);
    const span = $("share-url"), msg = $("share-msg");
    msg.textContent = "";
    span.textContent = url.replace(/^https?:\/\//, "");
    span.hidden = true;
    const year = current ? current.year : YEAR;
    const data = {url, title: share.shareTitle({kind: target.kind, name: target.name, year, product: NAME})};
    let shown = false;
    if (native(data)) {
      sharing = true;
      try {
        await navigator.share(data);
        track("event", "share_native");
        return;
      } catch (e) {
        // Cancelled, or a sheet already open: nothing to say.
        if (e && (e.name === "AbortError" || e.name === "InvalidStateError")) return;
        span.hidden = false;
        shown = true;
      } finally {
        sharing = false;
      }
    }
    track("event", "share_copy");
    copyLink(button, span, url, text => {
      msg.textContent = text;
      span.hidden = !shown && !text.startsWith("couldn't");
    });
  }

  $("share").addEventListener("click", () => shareOrCopy($("share"), shareTarget));
  $("share-member").addEventListener("click", () => shareOrCopy($("share-member"), groupWho ? memberTarget() : null));

  // ---- the share image (AOTY-70) ----------------------------------------------------------------
  //
  // The #1 or the top 3 (AOTY-73) as a PNG in the page's theme, 1080x1080 or a 1080x1920 story
  // (AOTY-72), drawn here on a canvas from sharecard.js's operations. Touch devices that can share
  // files get the share sheet with the PNG alone (the only way into Instagram from the web, and iOS
  // drops the image if text rides along); everything else downloads it.
  //
  // iOS refuses share() after slow work since the tap, so the image is made ahead: once the button
  // scrolls into view, or is hovered, focused or touched, never on every page load (it costs a CORS
  // fetch of each cover). A tap before it's ready makes it, then tries; if that share is refused,
  // the image is ready and the next tap shares it at once.
  const sc = globalThis.aotyShareCard;  // web/src/sharecard.js, loaded first
  // A cover still loading after this is drawn as its tile: a top 10 waits less, so ten covers on
  // a cold cache still come to an image within a few seconds (AOTY-74).
  const coverWaitMs = count => (count >= 10 ? 5000 : 8000);
  // What the image will show for subject s: the saved content when there are enough albums on
  // screen, else the #1 (data-content and data-shape on <main>, set by setupSwitch).
  const cardContentFor = s => share.cardContent(poster.dataset.content, s ? s.albums.length : 0);
  const cardFormat = s => share.formatId(cardContentFor(s), poster.dataset.shape);
  // The theme on screen, complete: what applyTheme last painted (the default's tokens for null).
  let shownTokens = th.complete(th.THEMES[th.DEFAULT]);
  // Per format, {key, promise, file}: the last image made or being made. One per format, so
  // flipping the switch back doesn't throw away a ready image (iOS wants it ready at the tap).
  const cards = {};
  let cardInView = false, cardTimer = 0;

  // What the image shows (share.imageSubject): the top of the ranking on screen, and for a member's
  // view of a group, the member's own (AOTY-71). groupWho and the switch are set by renderGroup
  // before it calls renderShare. Up to 3 albums, the most any content draws.
  function cardSubject() {
    if (!shareTarget || !current || !current.rankings) return null;
    const group = shareTarget.kind === "group" && current.kind === "group";
    if (shareTarget.kind === "group" && !group) return null;
    const tops = group ? grp.tops(current, groupMode(), grp.memberIndex(current, groupWho), 10)
        : share.userTops(current, 10);
    return share.imageSubject({target: shareTarget, chart: current, tops, member: group ? groupWho : ""});
  }

  // What the image shows now, and a key that changes whenever any of it does: the chart, the name,
  // the link, the format, every album drawn and the resolved colours ("album of the year" settles
  // after its cover loads).
  function cardInput() {
    const s = cardSubject();
    if (!s) return null;
    const format = cardFormat(s), shown = s.albums.slice(0, sc.FORMATS[format].count);
    const texts = share.cardTexts(s, current.year, linkFor(s.link), shown.length);  // as the card Lambda's (AOTY-91)
    // A group's file is named by its slug: its name may be all emoji or CJK, which fileName drops.
    const file = sc.fileName(s.kind === "group" ? s.link.id : s.name, current.year, format);
    const urls = shown.map(a => (a.art ? [a.art, ...(a.fallbacks || [])] : []));
    const tokens = shownTokens;
    const key = JSON.stringify([format, current.generated_at, texts, urls.map(u => u[0] || ""), tokens]);
    return {key, format, texts, file, urls, tokens};
  }

  // The image for the page as it is now: the one already made, or a promise of it (null on failure).
  function makeCard() {
    const input = cardInput();
    if (!input) return Promise.resolve(null);
    const had = cards[input.format];
    if (had && had.key === input.key) return had.promise;
    const mine = {key: input.key, file: null};
    mine.promise = renderCard(input).catch(() => null).then(file => (mine.file = file));
    cards[input.format] = mine;
    return mine.promise;
  }
  const readyCard = () => {
    const input = cardInput(), had = input && cards[input.format];
    return had && had.file && had.key === input.key ? had.file : null;
  };

  // Ahead of a tap: soon after the button is on screen, again after a theme or chart change.
  function cardSoon() {
    clearTimeout(cardTimer);
    if (!cardInView || $("share-image").hidden) return;
    cardTimer = setTimeout(() => {
      // Safari has no requestIdleCallback.
      if (window.requestIdleCallback) requestIdleCallback(() => makeCard(), {timeout: 2000});
      else makeCard();
    }, 300);
  }
  if (window.IntersectionObserver) {
    new IntersectionObserver(entries => {
      cardInView = entries.some(e => e.isIntersecting);
      cardSoon();
    }).observe($("share-image"));
  }
  for (const type of ["pointerenter", "focus", "touchstart"]) {
    $("share-image").addEventListener(type, () => { makeCard(); }, {passive: true});
  }

  async function renderCard({format, texts, file, urls, tokens}) {
    // With the real text, so the unicode-range subsets it needs load (é, Cyrillic, CJK).
    if (document.fonts && document.fonts.load) {
      await Promise.all(sc.fontLoads({texts, tokens, format})
          .map(([f, t]) => document.fonts.load(f, t).catch(() => null)));
    }
    // Every cover at once, each given up on after coverWaitMs, and each one that would taint the
    // canvas (it loaded without CORS after all) dropped on its own, so only its slot is a tile.
    const wait = coverWaitMs(sc.FORMATS[format].count);
    const imgs = await Promise.all(urls.map(u => (u.length ? withTimeout(loadCover(u, {cors: true}), wait)
        .then(img => (img && readable(img) ? img : null)) : null)));
    // A taint the check missed still makes toBlob throw, and a tainted canvas stays tainted: the
    // tiles go on a fresh one.
    const blob = (imgs.some(Boolean) && await drawCard(format, texts, tokens, imgs))
        || await drawCard(format, texts, tokens, imgs.map(() => null));
    return blob ? new File([blob], file, {type: "image/png"}) : null;
  }

  // p, or null once ms have passed.
  const withTimeout = (p, ms) => Promise.race([p, new Promise(resolve => setTimeout(() => resolve(null), ms))]);
  // Whether a canvas holding img can still be read back (getImageData throws on a tainted one).
  function readable(img) {
    try {
      const c = document.createElement("canvas");
      c.width = c.height = 1;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0, 1, 1);
      ctx.getImageData(0, 0, 1, 1);
      return true;
    } catch (_) {
      return false;
    }
  }

  // One attempt on a new canvas: the PNG, or null (tainted, or toBlob gave nothing, as iOS can
  // under memory pressure). imgs: per album, its cover or null for its tile.
  function drawCard(format, texts, tokens, imgs) {
    const {width, height} = sc.FORMATS[format];
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return Promise.resolve(null);
    const measure = (t, f) => { ctx.font = f; return ctx.measureText(t).width; };
    const covers = imgs.map(Boolean);
    globalThis.aotyCardDraw.drawCard(ctx, sc.compose({format, texts, tokens, covers, measure}), imgs);
    return new Promise(resolve => {
      try { canvas.toBlob(b => resolve(b && b.size ? b : null), "image/png"); } catch (_) { resolve(null); }
    });
  }

  // The ops are played by web/src/carddraw.js (drawOp), shared with the card Lambda (AOTY-91).

  // A file for the share sheet, or a download: one dummy PNG asks canShare whether files go.
  const nativeFiles = files => {
    try { return native({files: files || [new File([new Uint8Array(0)], "aoty.png", {type: "image/png"})]}); }
    catch (_) { return false; }
  };

  async function shareImage() {
    const button = $("share-image"), msg = $("share-msg");
    if (sharing || isBusy(button)) return;
    msg.textContent = "";
    $("share-url").hidden = true;
    const format = cardFormat(cardSubject());  // the tap's, even if a switch moves while it's made
    const ready = readyCard();
    if (ready) return deliver(ready, true, format);
    busy(button, true, "making…");
    const file = await makeCard();
    busy(button, false);
    if (!file) { msg.textContent = "couldn't make the image. try again in a minute."; return; }
    deliver(file, false, format);
  }

  // activated: share() is this tap's first await, so the share sheet may open.
  async function deliver(file, activated, format) {
    const msg = $("share-msg");
    if (nativeFiles([file])) {
      sharing = true;
      try {
        await navigator.share({files: [file]});
        track("event", "share_image", {format, via: "native"});
        return;
      } catch (e) {
        const why = e && e.name;
        if (why === "AbortError" || why === "InvalidStateError") return;  // cancelled, or a sheet open
        // The tap's activation ran out while the image was made: it's ready now.
        if (why === "NotAllowedError" && !activated) { msg.textContent = "the image is ready. tap again to share it."; return; }
      } finally {
        sharing = false;
      }
    }
    const url = URL.createObjectURL(file);
    const a = document.createElement("a");
    a.href = url;
    a.download = file.name;
    a.hidden = true;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);  // Firefox cancels a download revoked at once
    track("event", "share_image", {format, via: "download"});
    msg.textContent = "image saved.";
  }

  $("share-image").addEventListener("click", shareImage);

  // ---- the group page's share and edit controls -------------------------------------------------

  function hideGroupTools() {
    pageTools(null);
    $("edit").hidden = true;
  }

  // Share for everyone once the group exists; edit for its owner only (the API refuses everyone
  // else's writes anyway). first: open the panel if the address asks (?edit=1, after a create).
  function showGroupTools(first) {
    if (groupInfo === null || (!groupInfo && !current)) return hideGroupTools();
    const name = (groupInfo && groupInfo.name) || (current && current.name) || groupId;
    pageTools({kind: "group", id: groupId, name});
    const owner = !!groupInfo && canWrite() && ge.isOwner(me.username, groupInfo);
    const toggle = $("edit-toggle");
    toggle.hidden = !owner;
    if (!owner) {
      $("edit").hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      return;
    }
    const url = new URL(location.href);
    if (first && url.searchParams.has("edit")) {
      url.searchParams.delete("edit");
      try { history.replaceState(null, "", url); } catch (_) {}
      setEditOpen(true);
      $("add-name").focus();
    }
    renderMembers();
  }

  function setEditOpen(open) {
    $("edit").hidden = !open;
    $("edit-toggle").setAttribute("aria-expanded", String(open));
    if (open) {
      const input = $("rename-name");
      if (document.activeElement !== input) input.value = groupInfo.name;
      renderMembers();
    }
  }
  $("edit-toggle").addEventListener("click", () => {
    const open = $("edit").hidden;
    setEditOpen(open);
    if (open) $("edit-title").focus();
  });

  function renderMembers() {
    if (!groupInfo || $("edit").hidden) return;
    const list = $("members");
    const members = groupInfo.members;
    // Polls re-read the group every few seconds: rebuild the list only when it changed, or a
    // focused remove button would be replaced under the visitor.
    const key = JSON.stringify([groupInfo.owner, members]);
    if (list.dataset.key === key) return;
    list.dataset.key = key;
    $("members-title").textContent = `members · ${members.length} of ${ge.MAX_MEMBERS}`;
    list.replaceChildren(...members.map(m => {
      const li = document.createElement("li");
      const name = li.appendChild(document.createElement("span"));
      name.className = "member-name";
      name.textContent = m;
      if (m === groupInfo.owner) {
        const tag = li.appendChild(document.createElement("span"));
        tag.className = "member-tag";
        tag.textContent = "owner";
      }
      const b = li.appendChild(document.createElement("button"));
      b.type = "button";
      b.className = "quiet";
      b.textContent = "remove";
      b.setAttribute("aria-label", m === me.username ? "remove yourself" : `remove ${m}`);
      b.addEventListener("click", () => removeMember(b, m));
      return li;
    }));
    if (!members.length) {
      const li = list.appendChild(document.createElement("li"));
      li.className = "members-empty";
      li.textContent = "nobody yet. add someone below.";
    }
  }

  // After any write: re-read the group's record, show it, and watch the chart rebuild.
  async function afterWrite() {
    const gen = groupGen;
    const info = await loadGroupInfo(groupId);
    if (gen !== groupGen) return;
    if (info) groupInfo = info;
    if (info && themeTarget) { themeFromStatus(info); renderThemeTools(); }  // an owner change moves the picker
    renderMembers();
    startWatch();
  }

  // A write answered "signed out" or "gone": put the page right.
  function writeFailed(out) {
    if (out.signedOut) { me = null; renderAccount(); showGroupTools(false); }
    if (out.gone) {
      groupGen++;
      hideGroupTools();
      state("this group isn't here anymore", "it may have been renamed or deleted.", false);
    }
  }

  (() => {
    const form = $("add-form"), input = $("add-name"), msg = $("add-msg");
    const button = form.querySelector("button");
    clearOnInput(input, msg);
    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (isBusy(button)) return;
      const clean = intro.cleanName(input.value);
      if (clean.error) return formError(input, msg, intro.MESSAGES[clean.error]);
      busy(button, true, "adding…");
      msg.textContent = "";
      const r = await apiCall("POST", `groups/${groupId}/members`, {username: clean.name});
      busy(button, false);
      const out = ge.answer("add", r.status, r.body);
      if (!out.ok) {
        writeFailed(out);
        if (!out.signedOut && !out.gone) formError(input, msg, out.message);
        return;
      }
      track("event", "member_add");
      input.value = "";
      msg.textContent = out.note || `added ${clean.name}.`;
      input.focus();
      if (out.member) await afterWrite();
    });
  })();

  async function removeMember(button, name) {
    if (isBusy(button)) return;
    const [title, text] = ge.removeText(name, me.username);
    if (!await confirmDialog(button, title, text, name === me.username ? "take me out" : "remove")) return;
    busy(button, true, "removing…");
    const r = await apiCall("DELETE", `groups/${groupId}/members/${encodeURIComponent(name)}`);
    const out = ge.answer("remove", r.status, r.body);
    if (!out.ok) {
      busy(button, false);
      writeFailed(out);
      if (!out.signedOut && !out.gone) $("add-msg").textContent = out.message;
      return;
    }
    track("event", "member_remove");
    groupInfo = {...groupInfo, members: groupInfo.members.filter(m => m !== name)};
    renderMembers();
    $("members-title").focus();  // the row and its button are gone
    $("add-msg").textContent = name === me.username ? "you're out of the chart." : `removed ${name}.`;
    await afterWrite();
  }

  (() => {
    const form = $("rename-form"), input = $("rename-name"), msg = $("rename-msg");
    const button = form.querySelector("button");
    clearOnInput(input, msg);
    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (isBusy(button)) return;
      const clean = ge.checkName(input.value);
      if (clean.error) return formError(input, msg, ge.MESSAGES[clean.error]);
      if (clean.name === groupInfo.name) { msg.textContent = "that's its name already."; return; }
      busy(button, true, "renaming…");
      msg.textContent = "";
      const r = await apiCall("PATCH", `groups/${groupId}`, {name: clean.name});
      busy(button, false);
      const out = ge.answer("rename", r.status, r.body);
      if (!out.ok) {
        writeFailed(out);
        if (!out.signedOut && !out.gone) formError(input, msg, out.message);
        return;
      }
      const moved = !!out.id && out.id !== groupId;
      if (moved) {
        // The group moved: the old id's chart is deleted, so nothing may read it again.
        groupGen++;
        groupId = out.id;
        // The theme moved with it (the meta carries it): follow it, keeping a pick not yet saved.
        if (themeTarget && themeTarget.kind === "group") {
          rememberTheme(themeTarget, th.DEFAULT);
          themeTarget = {kind: "group", id: out.id};
          rememberTheme(themeTarget, savedTheme);
        }
        const url = new URL(location.href);
        url.pathname = `${BASE}group/${encodeURIComponent(out.id)}`;
        try { history.replaceState(null, "", url); } catch (_) {}
        track("setPage");  // later hits carry the new slug
      }
      groupInfo = {...groupInfo, id: groupId, name: clean.name};
      groupHeading(clean.name, current ? current.year : YEAR);
      input.value = clean.name;
      msg.textContent = moved ? "renamed. its link changed too, so share the new one." : "renamed.";
      await afterWrite();
    });
  })();

  $("delete-group").addEventListener("click", async () => {
    const button = $("delete-group");
    if (isBusy(button)) return;
    const ok = await confirmDialog(button, `delete ${groupInfo.name}?`,
        "the group and its chart go for good. everyone's own charts stay as they are.", "delete it");
    if (!ok) return;
    busy(button, true, "deleting…");
    const r = await apiCall("DELETE", `groups/${groupId}`);
    const out = ge.answer("delete", r.status, r.body);
    if (out.ok || out.gone) {
      groupGen++;
      location.assign(BASE);
      return;
    }
    busy(button, false);
    writeFailed(out);
    if (!out.signedOut) $("rename-msg").textContent = out.message;
  });

  // ---- page themes (AOTY-65) -------------------------------------------------------------------
  //
  // A user chart page shows its owner's theme (chart.json "theme", from the users row). Every other
  // view is the default (the poster look). "match1", shown as "album of the year", is derived here
  // from the chart's #1 cover: drawn small to a canvas (both cover hosts send CORS headers), then
  // theme.derive. The page fades into it once the cover has loaded; no cover, or one the browser
  // won't let us read, keeps the default.
  //
  // The owner (signed in as the page's user, where writes are on) gets a picker: a pick shows at
  // once and saves THEME_SAVE_MS later, so a run of clicks is one save. Once they've picked, their
  // pick wins over the chart.json this page fetches: the chart builder's restyle may land after a
  // repaint, and the page mustn't flick back.
  const THEME_SAVE_MS = 800;
  const THEME_FADE_MS = 450;
  // Whose page's theme is on screen (AOTY-76): {kind: "user", id: lowercased name} or
  // {kind: "group", id: slug}; null on any other view.
  let themeTarget = null;
  let themeChart = null;  // that page's chart, for the #1 cover
  const sameTarget = (a, b) => !!a && !!b && a.kind === b.kind && a.id === b.id;
  let shownTheme = th.DEFAULT, savedTheme = th.DEFAULT, pickedTheme = null;
  let themeGen = 0, themeSaveTimer = 0, fadeTimer = 0;

  function fade(on) {
    if (!on) return;
    document.documentElement.classList.add("theme-fade");
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(() => document.documentElement.classList.remove("theme-fade"), THEME_FADE_MS);
  }

  // The cover URLs to try for the #1 (the chart's art, then its fallbacks), or [] for none.
  function topCovers(chart) {
    const top = th.topRanking(chart)[0];
    const a = top ? chart.albums[top[0]] : null;
    return a && a.art ? [a.art, ...(a.fallbacks || [])] : [];
  }

  // The #1 cover's colours as a theme, or null. Its own CORS request: the chart's <img>s aren't
  // readable.
  async function coverTheme(urls) {
    const px = coverPixels(await loadCover(urls, {cors: true}));
    return px ? th.derive(px) : null;
  }

  // Paint a theme's tokens (null: the default), and tell the aurora (AOTY-67) its new colours.
  function applyTheme(tokens) {
    th.apply(tokens);
    auroraColors();
    // The share image (AOTY-70) draws from the same tokens, so its tests cover what's on screen.
    shownTokens = th.complete(tokens || th.THEMES[th.DEFAULT]);
    cardSoon();
  }

  let matchKey = "", matchTokens = null;  // the cover the "album of the year" colours came from
  async function showTheme(id, withFade) {
    const gen = ++themeGen;
    shownTheme = th.IDS.includes(id) ? id : th.DEFAULT;
    markSwatches();
    if (shownTheme !== th.MATCH) {
      fade(withFade);
      return applyTheme(th.tokens(shownTheme));
    }
    const urls = topCovers(themeChart);
    const key = urls[0] || "";
    if (key !== matchKey) {
      matchKey = key;
      matchTokens = key ? await coverTheme(urls) : null;
      if (gen !== themeGen) return;  // another pick, or another page, since
      withFade = true;  // a cover's colours arrive after the first paint: settle into them
    }
    fade(withFade);
    applyTheme(matchTokens);
  }

  // Off a themed page (intro, a message, "your groups"): the default, no picker.
  function clearTheme() {
    themeGen++;
    themeTarget = themeChart = pickedTheme = null;
    shownTheme = savedTheme = th.DEFAULT;
    clearTimeout(themeSaveTimer);
    applyTheme(null);
    renderThemeTools();
  }

  // A user chart was painted (first paint or in place).
  function themeForChart(chart, lower, repaint) {
    themeForPage({kind: "user", id: lower}, chart, repaint);
  }

  // A user's or a group's page, whatever its chart's state (user, 2026-11-25: a theme doesn't
  // wait for the chart). chart: the one on screen, or a first visit's (null, or one that ranks
  // nothing yet), which may still carry the saved theme; "album of the year" has no #1 to read
  // until a chart ranks something, and keeps the default until then. A group's member view
  // (?user=) only re-ranks the page: the theme and its #1 stay the group's.
  function themeForPage(target, chart, withFade = false) {
    if (!sameTarget(themeTarget, target)) pickedTheme = null;
    themeTarget = target;
    themeChart = chart && !(target.kind === "user" && fv.isFirstVisit(chart)) ? chart : null;
    savedTheme = chart && th.IDS.includes(chart.theme) ? chart.theme : th.DEFAULT;
    rememberTheme(target, savedTheme);
    showTheme(pickedTheme || savedTheme, withFade);
    renderThemeTools();
  }

  // The status API (a user) or GET api/groups/<id> (a group) carries the stored theme too, which
  // is newer than a chart.json the restyle hasn't reached, and the only source while there's no
  // chart at all.
  function themeFromStatus(s) {
    if (!themeTarget || pickedTheme || !s) return;
    const id = th.IDS.includes(s.theme) ? s.theme : th.DEFAULT;
    if (id === savedTheme) return;
    savedTheme = id;
    rememberTheme(themeTarget, id);
    showTheme(id, true);
  }

  // The theme a page last had, per viewer (AOTY-75): painted before chart.json arrives, so a
  // light page doesn't open dark and then flip. A convenience only: chart.json and the status
  // stay the truth, and replace it as soon as they arrive. "album of the year" isn't painted early
  // (its colours need the #1 cover). Storage can be blocked: then there's just no head start.
  function rememberTheme(target, id) {
    try {
      if (id === th.DEFAULT) localStorage.removeItem(th.themeKey(target));
      else localStorage.setItem(th.themeKey(target), id);
    } catch (_) { /* blocked storage: fine */ }
  }
  function paintRememberedTheme(target) {
    let id = null;
    try { id = localStorage.getItem(th.themeKey(target)); } catch (_) { id = null; }
    if (id && id !== th.MATCH && th.IDS.includes(id)) applyTheme(th.tokens(id));
  }

  // A group's owner is only known once GET api/groups/<id> answers; until then, no picker.
  const isThemeOwner = () => !!themeTarget && canWrite()
      && th.ownsTheme(themeTarget, me.username, themeTarget.kind === "group" && groupInfo ? groupInfo.owner : null);

  function renderThemeTools() {
    const owner = isThemeOwner();
    $("theme-tools").hidden = !owner;
    if (!owner) {
      $("theme-row").hidden = true;
      $("theme-toggle").setAttribute("aria-expanded", "false");
      return;
    }
    buildSwatches();
  }

  let swatchesBuilt = false;
  function buildSwatches() {
    const row = $("theme-row");
    if (!swatchesBuilt) {
      swatchesBuilt = true;
      for (const id of th.ORDER) {
        const b = row.appendChild(document.createElement("button"));
        b.type = "button";
        b.className = "pill swatch";
        b.dataset.theme = id;
        const dot = b.appendChild(document.createElement("span"));
        dot.className = id === th.MATCH ? "swatch-dot cover" : "swatch-dot";
        dot.setAttribute("aria-hidden", "true");
        const t = th.THEMES[id];
        if (t) {
          dot.style.setProperty("--sw-bg", t.bg);
          dot.style.setProperty("--sw-a", t.accent);
          dot.style.setProperty("--sw-b", t.accent2);
        }
        b.append(th.LABELS[id]);
        b.addEventListener("click", () => pickTheme(id));
      }
    }
    // "album of the year" shows the current #1 in its dot.
    const cover = row.querySelector(".swatch-dot.cover");
    const url = topCovers(themeChart)[0];
    if (url) cover.style.setProperty("--sw-img", `url("${url.replace(/["\\]/g, "")}")`);
    else cover.style.removeProperty("--sw-img");
    markSwatches();
  }

  function markSwatches() {
    for (const b of $("theme-row").querySelectorAll(".swatch")) {
      b.setAttribute("aria-pressed", String(b.dataset.theme === shownTheme));
    }
  }

  $("theme-toggle").addEventListener("click", () => {
    const row = $("theme-row");
    row.hidden = !row.hidden;
    $("theme-toggle").setAttribute("aria-expanded", String(!row.hidden));
    if (!row.hidden) (row.querySelector('[aria-pressed="true"]') || row.querySelector(".swatch")).focus();
  });

  function pickTheme(id) {
    if (!isThemeOwner()) return;
    pickedTheme = id;
    $("theme-msg").textContent = "";
    showTheme(id, true);
    clearTimeout(themeSaveTimer);
    // The target is read when the timer fires, not now: a group renamed in between has moved its
    // slug, and themeTarget follows it (rename).
    themeSaveTimer = setTimeout(() => saveTheme(id), THEME_SAVE_MS);
  }

  // By the answer's error word, not its status (as groupedit does): a 404 is also "no such route",
  // which an api without the theme route (an older deploy) answers.
  const THEME_ERRORS = {
    signed_out: "you're signed out. sign in again to change your theme.",
    not_owner: "only this page's owner can change its theme.",
    no_chart: "one moment: your page is still being set up. try again in a few seconds.",
    writes_off: "theme changes are switched off for now. your page keeps its theme.",
    // a group's (AOTY-76): the group routes' own words
    "not your group": "only this group's owner can change its theme.",
    "no such group": "this group isn't here any more.",
    groups_unavailable: "theme changes are switched off for now. the group keeps its theme.",
  };
  async function saveTheme(id) {
    const target = themeTarget;
    if (!target || id !== pickedTheme) return;
    const r = await apiCall("POST", th.savePath(target), {theme: id});
    // The page or the pick moved on. A rename keeps the page (same kind, new slug), so a group
    // matches on kind alone: themeTarget is the renamed one, which the theme moved with.
    const moved = !themeTarget || themeTarget.kind !== target.kind
        || (target.kind === "user" && themeTarget.id !== target.id);
    if (moved || id !== pickedTheme) return;
    if (r.status === 200) {
      savedTheme = id;
      rememberTheme(themeTarget, id);
      $("theme-msg").textContent = "saved. everyone sees it now.";
      track("event", "theme_set", {theme: id});
      return;
    }
    pickedTheme = null;
    showTheme(savedTheme, true);
    const word = r.body && typeof r.body.error === "string" ? r.body.error : "";
    $("theme-msg").textContent = (Object.hasOwn(THEME_ERRORS, word) && THEME_ERRORS[word])
        || "couldn't save your theme. try again in a minute.";
    if (word === "signed_out") { me = null; renderAccount(); }
  }

  // ---- routing ---------------------------------------------------------------------------------

  $("kamp-link").addEventListener("click", () => track("event", "kamp_click"));

  const signinFailed = signinResult();
  const meReady = loadMe();
  meReady.then(() => track("checkSignIn", me, signinFailed));
  const routedGroup = routeGroup();
  const name = routedGroup === null ? routeName() : null;
  if (routedGroup !== null) {
    showGroup(routedGroup);
  } else if (name === null) {
    showIntro();
  } else {
    showUser(name);
  }
  // One page view per load (AOTY-38), once routing has settled the path: showIntro has already
  // sent any other path to the base. Later replaceStates call setPage(), never a new page view.
  track("pageView");
})();
