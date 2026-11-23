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

  async function showUser(raw) {
    const lower = raw.toLowerCase();
    heading(raw, "");
    if (!USERNAME_RE.test(lower)) {
      state("That isn't a Last.fm username", "Check the spelling and try again.", true);
      return;
    }
    let res;
    try {
      res = await fetch(`${BASE}data/users/${lower}/chart.json`, {cache: "no-cache"});
    } catch (_) {
      state("Couldn't load this chart", "Check your connection and reload the page.", false);
      return;
    }
    if (res.status === 404) {
      // AOTY-24 replaces this with the first-visit flow (queue a sync, show progress).
      state("No chart yet", `There's no ${raw} chart here yet.`, true);
      return;
    }
    let chart = null;
    try { chart = res.ok ? await res.json() : null; } catch (_) {}
    if (!chart || chart.version !== CHART_VERSION || chart.username !== lower) {
      state("Couldn't load this chart", "Something went wrong on our side. Try again in a minute.", false);
      return;
    }
    // Last.fm's casing in the address bar, but only a name that is this user's.
    const display = typeof chart.display_name === "string" && chart.display_name.toLowerCase() === lower
        ? chart.display_name : lower;
    chart.display_name = display;
    if (display !== raw) {
      try { history.replaceState(null, "", `${BASE}user/${encodeURIComponent(display)}${location.search}`); } catch (_) {}
    }
    heading(display, chart.year);
    $("controls").hidden = false;
    const updated = new Date(chart.generated_at);
    if (!isNaN(updated)) {
      $("footer").textContent = `Data from Last.fm scrobbles · release years from MusicBrainz · updated `
          + updated.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
    }
    const render = () => renderChart(chart);
    setupSwitch("view", "aoty-view");
    setupSwitch("rank", "aoty-rank", render);
    render();
  }

  const name = routeName();
  if (name === null) {
    heading("Albums of the Year", "");
    state("Your albums of the year", "Type a Last.fm username to see the albums released this year they played most.", true);
  } else {
    showUser(name);
  }
})();
