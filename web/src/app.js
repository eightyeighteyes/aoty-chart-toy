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
  // Last.fm names as the sync worker accepts them (lambdas/sync/handler.py USERNAME_RE).
  const USERNAME_RE = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
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
    $("eyebrow").textContent = "Albums of the Year";
    const h1 = $("title");
    h1.textContent = name;
    if (year) {
      h1.appendChild(document.createElement("br"));
      const y = h1.appendChild(document.createElement("span"));
      y.className = "year";
      y.textContent = year;
    }
    document.title = year ? `${name}'s Albums of the Year ${year}` : "Albums of the Year";
  }

  // A message panel, optionally with the username box.
  function state(title, text, withLookup) {
    $("controls").hidden = true;
    $("sub").textContent = "";
    const box = document.createElement("section");
    box.className = "state";
    box.appendChild(document.createElement("h2")).textContent = title;
    box.appendChild(document.createElement("p")).textContent = text;
    if (withLookup) box.appendChild(lookupForm());
    $("ranking").replaceChildren(box);
  }

  function lookupForm() {
    const form = document.createElement("form");
    form.className = "lookup";
    const input = form.appendChild(document.createElement("input"));
    Object.assign(input, {name: "user", placeholder: "Last.fm username", autocomplete: "off",
                          spellcheck: false, required: true});
    input.setAttribute("aria-label", "Last.fm username");
    form.appendChild(document.createElement("button")).textContent = "Show chart";
    form.addEventListener("submit", e => {
      e.preventDefault();
      const name = input.value.trim();
      if (name) location.assign(`${BASE}user/${encodeURIComponent(name)}`);
    });
    return form;
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
  let current = null, switchesReady = false;

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
    if (!isNaN(updated)) {
      $("footer").textContent = `Data from Last.fm scrobbles · release years from MusicBrainz · updated `
          + updated.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
    }
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

  async function showUser(raw) {
    const lower = raw.toLowerCase();
    heading(raw, "");
    if (!USERNAME_RE.test(lower)) {
      state("That isn't a Last.fm username", "Check the spelling and try again.", true);
      return;
    }
    let chart;
    try {
      chart = await loadChart(lower);
    } catch (_) {
      state("Couldn't load this chart", "Check your connection, or try again in a minute.", false);
      return;
    }
    if (chart) {
      paint(chart, raw, lower);
    } else {
      // AOTY-24 replaces this with the real first-visit flow; until then the sync below fills it.
      state("No chart yet", `Reading ${raw}'s scrobbles. The chart appears here when it's ready.`, false);
    }
    watchSync(lower, async () => {
      const fresh = await loadChart(lower);
      if (fresh && (!current || fresh.generated_at !== current.generated_at)) paint(fresh, raw, lower);
    });
  }

  // ---- sync on visit (AOTY-22) ------------------------------------------------------------
  //
  // Ask for a sync (the API answers a running sync or the 10-minute cooldown without queueing),
  // then poll the status: every 2 s, backing off to 5 s, every 30 s while release years are
  // checked, pausing while the tab is hidden. When the chart builder writes a newer chart,
  // onNewChart repaints it in place. Nothing here ever blocks the chart: errors only show a
  // quiet line.
  const POLL_FIRST_MS = 2000, POLL_MAX_MS = 5000, POLL_DATING_MS = 30000;
  const WATCH_FOR_MS = 10 * 60 * 1000, DATING_FOR_MS = 2 * 60 * 1000;

  // The banner: #banner-msg is the page's one polite live region and changes only with the
  // stage; the counts beside it change on every poll and are hidden from screen readers.
  function say(msg, detail = "") {
    const box = $("banner"), live = $("banner-msg");
    if (live.textContent !== msg) live.textContent = msg;
    $("banner-detail").textContent = detail;
    box.classList.toggle("show", !!msg);
  }

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

  function watchSync(lower, onNewChart) {
    const started = Date.now();
    let delay = POLL_FIRST_MS, timer = null, stopped = false, datingSince = null;
    const stop = msg => { stopped = true; clearTimeout(timer); timer = null; say(msg || ""); };
    const quiet = () => stop("Couldn't update right now.");
    const schedule = ms => { if (!stopped && !document.hidden) timer = setTimeout(tick, ms); };

    async function maybeRefresh(s) {
      const built = s.chart_built_at || 0;
      const have = current ? Date.parse(current.generated_at) / 1000 : 0;
      if (built > have) {
        try { await onNewChart(); } catch (_) {}
      }
    }

    async function handle(s) {
      await maybeRefresh(s);
      if (s.stage === "idle") return stop("");
      if (s.stage === "error") return quiet();
      const [msg, detail] = describe(s);
      say(msg, detail);
      if (s.stage === "dating") {
        datingSince = datingSince || Date.now();
        if (Date.now() - datingSince > DATING_FOR_MS) return stop("");
        delay = POLL_DATING_MS;
      } else {
        delay = Math.min(Math.round(delay * 1.5), POLL_MAX_MS);
      }
      if (Date.now() - started > WATCH_FOR_MS) return stop("");
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
      if (!res.ok) return quiet();
      let s;
      try { s = await res.json(); } catch (_) { return quiet(); }
      await handle(s);
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !stopped && timer === null) schedule(0);
    });

    say("Checking for new scrobbles…");
    (async () => {
      let res, body = null;
      try {
        res = await fetch(`${BASE}api/users/${lower}/sync`, {
          method: "POST", headers: {"content-type": "application/json"}, body: "{}", cache: "no-store",
        });
        body = await res.json();
      } catch (_) { return quiet(); }
      if (res.status === 404) {
        if (!current) state("No such Last.fm user", "Check the spelling and try again.", true);
        return stop("");
      }
      if (!res.ok || !body || !body.status) return quiet();
      await handle(body.status);
    })();
  }

  const name = routeName();
  if (name === null) {
    heading("Albums of the Year", "");
    state("Your albums of the year", "Type a Last.fm username to see the albums released this year they played most.", true);
  } else {
    showUser(name);
  }
})();
