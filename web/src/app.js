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
  const grp = globalThis.aotyGroup;  // web/src/group.js, loaded first (AOTY-51)
  const ge = globalThis.aotyGroupEdit;  // web/src/groupedit.js, loaded first (AOTY-52)
  const GROUP_REFRESH_MS = 60000;  // a group page re-reads its chart while members load
  const GROUP_WATCH_MS = 5000;  // and every 5 s after an edit, or while its first chart is pressed
  const API_TIMEOUT_MS = 10000;
  const USERNAME_RE = intro.USERNAME_RE;
  const LOOKUP_TIMEOUT_MS = 10000;
  const CHART_VERSION = 1;
  const MODES = {
    track: "ranked by plays",
    album: "ranked by album plays: track plays divided by the album's track count (singles left out)",
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

  function cover(a, cls) {
    if (a.art) return `<img class="${cls}" src="${esc(a.art)}" alt="${esc(a.album)} cover" loading="lazy" `
        + `data-fallbacks="${esc(a.fallbacks.join(" "))}" data-artist="${esc(a.artist)}">`;
    return `<div class="${cls} noart"><span style="--fs:${nameSize(a.artist)}">${esc(a.artist)}</span></div>`;
  }

  // The number this ranking sorts by, plus the plays behind it.
  function score(a, mode) {
    if (mode === "track") return `<b>${num(a.score)}</b> plays`;
    if (mode === "weighted") return `<b>${num(a.score, 2)}</b> album plays per listener · ${num(a.plays)} plays`;
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
    return `<div class="dots" title="${a.listeners} of ${groupTotal} listeners">${s}</div>`;
  }
  // M0's score words: for one member it's their own plays, not the group's.
  function groupScore(a, mode) {
    const by = groupWho ? ` by ${esc(groupWho)}` : "";
    if (mode === "track") return `<b>${num(a.score)}</b> plays${by}`;
    if (mode === "album" || groupWho) return `<b>${num(a.score, 1)}</b> album plays${by}`;
    return `<b>${num(a.score, 1)}</b> plays per listener`;
  }
  // The stats line: user pages as before; group pages lead with listeners, worded per place.
  function stats(a, mode, place) {
    if (!groupTotal) return score(a, mode);
    const t = groupTotal, n = `<b>${a.listeners}</b>`;
    const lead = place === "hero" ? `${n} of ${t} listeners` : place === "card" ? `${n} listeners`
        : place === "tile" ? `${n}/${t}` : `${n}/${t} listeners`;
    return `${lead} · ${groupScore(a, mode)}`;
  }
  const names = a => `<div class="album">${esc(a.album)}</div><div class="artist">${esc(a.artist)}</div>`;

  // One ranking's poster view and list view.
  function renderRanking(chart, rows, mode) {
    if (!rows.length) {
      const line = grp.checkingLine(chart);
      const wait = line ? ` ${line}` : "";
      const whose = chart.kind === "group" ? `the ${esc(chart.name)} group` : esc(chart.display_name);
      return `<p class="empty">No ${chart.year} albums in ${whose}'s scrobbles yet.${wait}</p>`;
    }
    const [top, rest, more] = [rows[0], rows.slice(1, chart.featured), rows.slice(chart.featured)];
    const bg = top.art ? `style="--hero-art:url(&quot;${esc(top.art)}&quot;)"` : "";
    const hero = `<section class="hero" ${bg}>${cover(top, "art")}<div>
        <div class="crown">#1</div>${names(top)}${dots(top)}
        <div class="stats">${stats(top, mode, "hero")}</div>
      </div></section>`;
    const cards = rest.map((a, i) => `<li class="card" style="--i:${i + 1}">
        <div class="art-wrap">${cover(a, "art")}<span class="rank">${a.rank}</span></div>
        <div class="meta">${names(a)}${dots(a)}<div class="stats">${stats(a, mode, "card")}</div></div>
      </li>`).join("");
    const tiles = more.map(a => `<li class="tile">
        <div class="art-wrap">${cover(a, "art")}<span class="tile-rank">${a.rank}</span></div>
        ${names(a)}<div class="stats">${stats(a, mode, "tile")}</div>
      </li>`).join("");
    const moreSection = more.length ? `<section class="more"><h2>${chart.featured + 1}–${rows.length}</h2>
        <ol class="more-grid">${tiles}</ol></section>` : "";
    const list = rows.map((a, i) => `<li class="row${i ? "" : " first"}" style="--i:${i}">
        <span class="row-rank">${a.rank}</span>${cover(a, "row-art")}
        <div class="row-meta">${names(a)}</div>
        <div class="row-numbers">${dots(a)}<div class="stats">${stats(a, mode, "row")}</div></div>
      </li>`).join("");
    return `<div class="view-poster">${hero}<ol>${cards}</ol>${moreSection}</div>
      <ol class="list view-list">${list}</ol>`;
  }

  function renderChart(chart) {
    if (chart.kind === "group") return renderGroup(chart);
    const mode = chart.rankings[poster.dataset.rank] ? poster.dataset.rank : "track";
    const rows = chart.rankings[mode].map(([i, s], k) => ({...chart.albums[i], score: s, rank: k + 1}));
    const who = chart.display_name;
    $("sub").textContent = `Top ${rows.length} albums released in ${chart.year} that ${who} played, ${MODES[mode]}.`;
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

  // Once per page, for user and group pages alike: a second setup would add every click
  // listener twice.
  function setupSwitchesOnce() {
    if (switchesReady) return;
    switchesReady = true;
    setupSwitch("view", "aoty-view");
    setupSwitch("rank", "aoty-rank", () => renderChart(current));
  }

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

  // Copy url; with no clipboard (an insecure context, or permission refused), select urlText
  // instead. Says which happened through say(text) (default: the button's own label).
  async function copyLink(button, urlText, url = pageUrl(), say = null) {
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

  function renderGroup(chart) {
    const mode = grp.MODES.includes(poster.dataset.rank) ? poster.dataset.rank : "track";
    const who = grp.memberIndex(chart, $("who").value);
    const rows = grp.rows(chart, mode, who);
    groupTotal = chart.members.length + (chart.waiting || 0) + (chart.unavailable || 0);
    groupWho = who < 0 ? "" : chart.members[who];
    const whose = who < 0 ? `${chart.name}'s members` : chart.members[who];
    $("sub").textContent = `Top ${rows.length} albums released in ${chart.year} that ${whose} played, ${MODES[mode]}.`;
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
    heading(NAME, "");
    if (!grp.ID_RE.test(id)) {
      state("there's no group here", "check the link you were given.", false);
      return;
    }
    groupId = id;
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
        if (!watch) startWatch(false);
      }
    }
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
    groupHeading(groupInfo && groupInfo.id === chart.id ? groupInfo.name : chart.name, chart.year);
    $("controls").hidden = false;
    $("rank-weighted").hidden = false;
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
      setupSwitchesOnce();
      $("who").addEventListener("change", () => {
        const url = new URL(location.href);
        if ($("who").value) url.searchParams.set("user", $("who").value); else url.searchParams.delete("user");
        try { history.replaceState(null, "", url); } catch (_) {}
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

  // Back from Last.fm with ?signin=failed: say so quietly, and take it out of the address.
  function signinResult() {
    const url = new URL(location.href);
    if (url.searchParams.get("signin") !== "failed") return;
    url.searchParams.delete("signin");
    try { history.replaceState(null, "", url); } catch (_) {}
    $("account-msg").textContent = "sign-in didn't go through. try again?";
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
    openDialog(d, opener, toCreate && canWrite() ? $("create-name") : $("mygroups-close"));
    const r = await apiCall("GET", "me/groups");
    if (r.status === 401) { me = null; renderAccount(); d.close(); return; }
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
    $("mygroups-empty").textContent = "no groups yet.";
    $("mygroups-empty").hidden = groups.length > 0;
  }
  $("mygroups-close").addEventListener("click", () => $("mygroups").close());
  $("intro-start").addEventListener("click", () => openMyGroups($("intro-start"), true));

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
        location.assign(`${BASE}group/${encodeURIComponent(out.id)}?edit=1`);
        return;
      }
      busy(button, false);
      if (out.signedOut) { me = null; renderAccount(); }
      formError(input, msg, out.message || ge.MESSAGES.generic);
    });
    window.addEventListener("pageshow", () => { if (isBusy(button)) busy(button, false); });
  })();

  // ---- the group page's share and edit controls -------------------------------------------------

  function hideGroupTools() {
    $("group-tools").hidden = true;
    $("edit").hidden = true;
  }

  // Share for everyone once the group exists; edit for its owner only (the API refuses everyone
  // else's writes anyway). first: open the panel if the address asks (?edit=1, after a create).
  function showGroupTools(first) {
    if (groupInfo === null || (!groupInfo && !current)) return hideGroupTools();
    $("group-tools").hidden = false;
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

  $("share").addEventListener("click", () => {
    const url = ge.shareUrl(location.origin, BASE, groupId);
    const span = $("share-url");
    span.textContent = url.replace(/^https?:\/\//, "");
    copyLink($("share"), span, url, text => {
      $("share-msg").textContent = text;
      span.hidden = !text.startsWith("couldn't");
    });
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
        const url = new URL(location.href);
        url.pathname = `${BASE}group/${encodeURIComponent(out.id)}`;
        try { history.replaceState(null, "", url); } catch (_) {}
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

  // ---- routing ---------------------------------------------------------------------------------

  signinResult();
  const meReady = loadMe();
  const routedGroup = routeGroup();
  const name = routedGroup === null ? routeName() : null;
  if (routedGroup !== null) {
    showGroup(routedGroup);
  } else if (name === null) {
    showIntro();
  } else {
    showUser(name);
  }
})();
