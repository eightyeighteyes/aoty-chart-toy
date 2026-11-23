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
  const fv = globalThis.aotyFirstVisit;  // web/src/firstvisit.js, loaded first
  const USERNAME_RE = intro.USERNAME_RE;
  const LOOKUP_TIMEOUT_MS = 10000;
  const CHART_VERSION = 1;
  const MODES = {
    track: "ranked by plays",
    album: "ranked by album plays: track plays divided by the album's track count (singles left out)",
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

  function cover(a, cls) {
    if (a.art) return `<img class="${cls}" src="${esc(a.art)}" alt="${esc(a.album)} cover" loading="lazy" `
        + `data-fallbacks="${esc(a.fallbacks.join(" "))}" data-artist="${esc(a.artist)}">`;
    return `<div class="${cls} noart"><span style="--fs:${nameSize(a.artist)}">${esc(a.artist)}</span></div>`;
  }

  // The number this ranking sorts by, plus the plays behind it.
  function score(a, mode) {
    if (mode === "track") return `<b>${num(a.score)}</b> plays`;
    return `<b>${num(a.score, 1)}</b> album plays · ${num(a.plays)} plays`;
  }
  const names = a => `<div class="album">${esc(a.album)}</div><div class="artist">${esc(a.artist)}</div>`;

  // One ranking's poster view and list view.
  function renderRanking(chart, rows, mode) {
    if (!rows.length) {
      const wait = chart.pending ? ` ${num(chart.pending)} of their albums are still being checked.` : "";
      return `<p class="empty">No ${chart.year} albums in ${esc(chart.display_name)}'s scrobbles yet.${wait}</p>`;
    }
    const [top, rest, more] = [rows[0], rows.slice(1, chart.featured), rows.slice(chart.featured)];
    const bg = top.art ? `style="--hero-art:url(&quot;${esc(top.art)}&quot;)"` : "";
    const hero = `<section class="hero" ${bg}>${cover(top, "art")}<div>
        <div class="crown">#1</div>${names(top)}
        <div class="stats">${score(top, mode)}</div>
      </div></section>`;
    const cards = rest.map((a, i) => `<li class="card" style="--i:${i + 1}">
        <div class="art-wrap">${cover(a, "art")}<span class="rank">${a.rank}</span></div>
        <div class="meta">${names(a)}<div class="stats">${score(a, mode)}</div></div>
      </li>`).join("");
    const tiles = more.map(a => `<li class="tile">
        <div class="art-wrap">${cover(a, "art")}<span class="tile-rank">${a.rank}</span></div>
        ${names(a)}<div class="stats">${score(a, mode)}</div>
      </li>`).join("");
    const moreSection = more.length ? `<section class="more"><h2>${chart.featured + 1}–${rows.length}</h2>
        <ol class="more-grid">${tiles}</ol></section>` : "";
    const list = rows.map((a, i) => `<li class="row${i ? "" : " first"}" style="--i:${i}">
        <span class="row-rank">${a.rank}</span>${cover(a, "row-art")}
        <div class="row-meta">${names(a)}</div>
        <div class="row-numbers"><div class="stats">${score(a, mode)}</div></div>
      </li>`).join("");
    return `<div class="view-poster">${hero}<ol>${cards}</ol>${moreSection}</div>
      <ol class="list view-list">${list}</ol>`;
  }

  function renderChart(chart) {
    const mode = chart.rankings[poster.dataset.rank] ? poster.dataset.rank : "track";
    const rows = chart.rankings[mode].map(([i, s], k) => ({...chart.albums[i], score: s, rank: k + 1}));
    const who = chart.display_name;
    $("sub").textContent = `Top ${rows.length} albums released in ${chart.year} that ${who} played, ${MODES[mode]}.`;
    if (chart.pending) {
      const p = $("sub").appendChild(document.createElement("span"));
      p.className = "pending";
      p.textContent = ` ${num(chart.pending)} album${chart.pending === 1 ? " is" : "s are"} still being checked.`;
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
    buttons.forEach(b => b.addEventListener("click", () => { set(b.dataset[attr]); if (onChange) onChange(); }));
    let saved = null;
    try { saved = localStorage.getItem(storageKey); } catch (_) {}
    if (values.includes(saved)) set(saved);
  }

  function heading(name, year) {
    $("eyebrow").textContent = NAME;
    const h1 = $("title");
    h1.textContent = name;
    if (year) {
      h1.appendChild(document.createElement("br"));
      const y = h1.appendChild(document.createElement("span"));
      y.className = "year";
      y.textContent = year;
    }
    document.title = year ? `${name}'s ${NAME} ${year} · a kamp toy` : TITLE;
  }

  // A message panel, optionally with the username box. The footer's profile line goes: the
  // panel may be saying this Last.fm user doesn't exist.
  function state(title, text, withLookup) {
    $("controls").hidden = true;
    $("sub").textContent = "";
    $("footer-user").hidden = true;
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
      if (clean.error) return fail(intro.MESSAGES[clean.error]);
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

  function paint(chart, raw, lower) {
    // Last.fm's casing in the address bar, but only a name that is this user's.
    const display = typeof chart.display_name === "string" && chart.display_name.toLowerCase() === lower
        ? chart.display_name : lower;
    chart.display_name = display;
    if (display !== raw) {
      try { history.replaceState(null, "", `${BASE}user/${encodeURIComponent(display)}${location.search}`); } catch (_) {}
    }
    const repaint = current !== null;
    current = chart;
    heading(display, chart.year);
    $("controls").hidden = false;
    const updated = new Date(chart.generated_at);
    profileLine(display, isNaN(updated) ? ""
        : updated.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}));
    if (!switchesReady) {  // once: a second setup would add every click listener twice
      switchesReady = true;
      setupSwitch("view", "aoty-view");
      setupSwitch("rank", "aoty-rank", () => renderChart(current));
    }
    if (repaint) {
      // In place: keep the scroll position, and don't replay the entrance animations.
      const y = window.scrollY;
      $("ranking").classList.add("no-anim");
      renderChart(chart);
      window.scrollTo(0, y);
    } else {
      renderChart(chart);
    }
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
    const wall = box.appendChild(document.createElement("ul"));
    wall.className = "wall";
    wall.setAttribute("aria-hidden", "true");  // decoration: the numbers above carry the meaning
    $("ranking").replaceChildren(box);
    firstView = {box, queue, sofar, note, wall, walled: false};
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
    copy.addEventListener("click", () => copyLink(copy, block.querySelector(".fv-url")));
    return block;
  }

  // The page's own address, in the casing the address bar shows.
  const pageUrl = () => `${location.origin}${location.pathname}`;

  async function copyLink(button, urlText) {
    try {
      await navigator.clipboard.writeText(pageUrl());
      button.textContent = "copied";
    } catch (_) {
      // No clipboard (an insecure context, or permission refused): select the address instead.
      if (urlText) {
        const range = document.createRange();
        range.selectNodeContents(urlText);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      button.textContent = "selected: copy it";
    }
    setTimeout(() => { button.textContent = "copy link"; }, 2500);
  }

  function updateQueue(position, waitSeconds) {
    const q = firstView.queue;
    q.hidden = !position;
    if (!position) return;
    const lines = fv.queueLines(position, pageUrl(), waitSeconds);
    q.querySelector(".fv-caller").textContent = lines.caller;
    const shown = pageUrl().replace(/^https?:\/\//, "");
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
      firstView.wall.innerHTML = s.wall
          .map((a, i) => `<li style="--i:${i}">${cover({...a, fallbacks: a.fallbacks || []}, "wall-art")}</li>`)
          .join("");
    }
  }

  // ---- sync on visit (AOTY-22) ------------------------------------------------------------
  //
  // Ask for a sync (the API answers a running sync or the 10-minute cooldown without queueing),
  // then poll the status: every 2 s, backing off to 5 s, every 30 s while release years are
  // checked, every 20-25 s while waiting in line (AOTY-26), pausing while the tab is hidden.
  // When the chart builder writes a newer chart, onNewChart repaints it in place. Nothing here
  // ever blocks the chart: errors only show a quiet line. A throttled or busy status (429/503)
  // is retried with a back-off, not given up on: a queued crowd is exactly when that happens.
  const POLL_FIRST_MS = 2000, POLL_MAX_MS = 5000, POLL_DATING_MS = 30000;
  const POLL_QUEUED_MS = 20000, POLL_QUEUED_JITTER_MS = 5000, POLL_RETRY_MAX_MS = 60000, RETRIES_MAX = 10;
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
    const queuedDelay = () => POLL_QUEUED_MS + Math.round(Math.random() * POLL_QUEUED_JITTER_MS);

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
      delay = inLine ? queuedDelay() : Math.min(Math.round(delay * 1.5), POLL_MAX_MS);
      schedule(delay);
      return true;
    }

    // The sync is over but there's still no chart to show (first visit): say so, don't wait on.
    function gaveUp(reason) {
      if (current) return;
      if (reason === "private") {
        state("this profile is private", "its last.fm listening isn't public, so there's nothing for us to read.", false);
      } else {
        state("couldn't build a chart this time", "something went wrong reading the scrobbles. try again in a few minutes.", false);
      }
    }

    async function handle(s) {
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
      } else if (s.stage === "queued") {
        delay = queuedDelay();
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

  const name = routeName();
  if (name === null) {
    showIntro();
  } else {
    showUser(name);
  }
})();
