// Page themes (AOTY-65): an owner picks one for their chart page and every visitor sees it.
// Pure rules here (presets, contrast, the seam colour, colours from a cover), tested by node, plus
// apply/reset for the page, which set custom properties on <html> (app.css reads them).
//
// Presets are the poster look (the default) and kamp's dark palettes (kamp repo,
// kamp_ui/src/shared/theme.ts: bg/surface/text/textDim/accent/border). The poster uses two
// accents (its crown gradient), kamp one, so accent2 is picked per preset. Kamp's own values are
// kept wherever they pass the contrast rules; measured 2026-11-25, textDim reads under 4.5:1 on
// the panel in beach house (4.27), blackpink (4.46), deep purple (3.78) and golden smog (4.48),
// and deep purple's accent is 4.48, so those are lifted. "match1" (shown as "album of the year") derives
// a theme from the chart's #1 cover in the visitor's browser.
(root => {
  "use strict";

  const THEMES = {
    "poster": {bg: "#0d0c12", panel: "#17151f", ink: "#f4f1ea", muted: "#9c97a8", accent: "#ff6b4a",
               accent2: "#ffc24b", line: "#2c2937", onAccent: "#1a1014", tile: "#3a2a55"},
    "kamp": {bg: "#141414", panel: "#1e1e1e", ink: "#e0e0e0", muted: "#888888", accent: "#7c86e1",
             accent2: "#b8bef5", line: "#2e2e2e", onAccent: "#000000"},
    "strawberry-switchblade": {bg: "#180107", panel: "#1f030c", ink: "#f0dde3", muted: "#9a7a82",
                               accent: "#e8305a", accent2: "#ff9db2", line: "#3a2028", onAccent: "#1a0208"},
    "beach-house": {bg: "#0c1014", panel: "#131a20", ink: "#d4dde8", muted: "#8aa0b0", accent: "#5fb3c4",
                    accent2: "#a8e0e8", line: "#243040", onAccent: "#000000"},
    "blackpink": {bg: "#000000", panel: "#1a1018", ink: "#f5e6ef", muted: "#b08aa0", accent: "#f72585",
                  accent2: "#ff9ccd", line: "#38182c", onAccent: "#000000"},
    "deep-purple": {bg: "#0a0810", panel: "#13102a", ink: "#e8e0f5", muted: "#9d8fc4", accent: "#a875ec",
                    accent2: "#d2b8ff", line: "#2c2848", onAccent: "#0a0414"},
    "green-day": {bg: "#0d100a", panel: "#141910", ink: "#dce8d0", muted: "#8fa676", accent: "#78d42a",
                  accent2: "#c8f07a", line: "#2a3020", onAccent: "#000000"},
    "foxy-brown": {bg: "#100c08", panel: "#1c1510", ink: "#f0e2cc", muted: "#9a7a50", accent: "#c8882a",
                   accent2: "#f0c070", line: "#3a2a1e", onAccent: "#000000"},
    "golden-smog": {bg: "#0f0e09", panel: "#1a1910", ink: "#ede8cc", muted: "#a39878", accent: "#c8a832",
                    accent2: "#f0dc7a", line: "#353320", onAccent: "#000000"},
    // Light presets (AOTY-75, user 2026-11-25): named after bands and musicians, one per hue, like
    // the dark set. Accents are deep enough to be text on the light page; "a kamp toy"'s periwinkle
    // darkens to LIGHT_KAMP to stay legible on them.
    "white-stripes": {scheme: "light", bg: "#fbf7f3", panel: "#f1e9e2", ink: "#141010", muted: "#5e5450",
                      accent: "#b8122b", accent2: "#1c1414", line: "#e2d8cf", onAccent: "#ffffff"},
    "tangerine-dream": {scheme: "light", bg: "#fff6ec", panel: "#fbe7d2", ink: "#1f140a", muted: "#6a5442",
                        accent: "#a84500", accent2: "#7f3812", line: "#ecd6bd", onAccent: "#ffffff"},
    "yellow-magic-orchestra": {scheme: "light", bg: "#fffbe8", panel: "#f7efcc", ink: "#1c1a0e", muted: "#5c573a",
                               accent: "#6e5800", accent2: "#a8241b", line: "#e8dfb4", onAccent: "#ffffff"},
    "mint-royale": {scheme: "light", bg: "#f2faf5", panel: "#e1f1e7", ink: "#0f1a14", muted: "#4a5e52",
                    accent: "#146637", accent2: "#2b6200", line: "#cbe3d4", onAccent: "#ffffff"},
    "the-blue-nile": {scheme: "light", bg: "#f4f7fc", panel: "#e5ecf7", ink: "#0e1420", muted: "#4c586c",
                      accent: "#1d56a8", accent2: "#0a6573", line: "#d2dcec", onAccent: "#ffffff"},
    "lavender-diamond": {scheme: "light", bg: "#f8f5fd", panel: "#ece5f8", ink: "#16111f", muted: "#5a526c",
                         accent: "#6438ad", accent2: "#952870", line: "#ddd2ef", onAccent: "#ffffff"},
    // New Bands for Old Heads' RIYL Old Heads palette (white, #050505, pink #ec4899, soft pink
    // #fce7f3, #5f5f5f). Its #ec4899 is ~3.5:1 on white, too light for text, so the accents are its
    // deeper pinks and #ec4899 colours the artist-name tiles; headings are Arial (user, 2026-11-25).
    "gabbie": {scheme: "light", font: "display-arial", bg: "#ffffff", panel: "#fce7f3", ink: "#050505",
               muted: "#5f5f5f", accent: "#c0186a", accent2: "#8a1450", line: "#efefef", onAccent: "#ffffff",
               tile: "#ec4899", tileInk: "#050505"},
  };
  const LIGHT_KAMP = "#4c55b8";  // the endorsement's periwinkle on a light page (user, 2026-11-25)
  const DEFAULT = "poster";
  const MATCH = "match1";
  const IDS = [...Object.keys(THEMES), MATCH];  // lambdas/common/themes.py must match (a test checks)
  // The default's id stays "poster" (stored ids never change); it reads "oranger" (user, 2026-11-25).
  const LABELS = {
    "poster": "oranger", "kamp": "kamp", "strawberry-switchblade": "strawberry switchblade",
    "beach-house": "beach house", "blackpink": "blackpink", "deep-purple": "deep purple",
    "green-day": "green day", "foxy-brown": "foxy brown", "golden-smog": "golden smog",
    "white-stripes": "white stripes", "tangerine-dream": "tangerine dream",
    "yellow-magic-orchestra": "yellow magic orchestra", "mint-royale": "mint royale",
    "the-blue-nile": "the blue nile", "lavender-diamond": "lavender diamond", "gabbie": "gabbie",
    [MATCH]: "album of the year",  // user, 2026-11-25
  };
  const KAMP = "#7c86e1";  // app.css --kamp: the endorsement chrome (darker on light pages)
  const TILE_INK = "#f4f1ea";  // app.css --tile-ink: the artist-name tile's text on a dark page
  const TEXT_MIN = 4.5;  // WCAG AA for normal text

  // ---- colour arithmetic ------------------------------------------------------------------

  const hex2 = n => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, "0");
  const toHex = ([r, g, b]) => `#${hex2(r)}${hex2(g)}${hex2(b)}`;
  function rgb(hex) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) throw new Error(`not a colour: ${hex}`);
    const n = parseInt(m[1], 16);
    return [n >> 16, (n >> 8) & 255, n & 255];
  }
  function luminance(hex) {
    const [r, g, b] = rgb(hex).map(c => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  // The WCAG contrast ratio of two colours, 1 to 21.
  function contrast(a, b) {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  }
  function mix(a, b, t) {  // t of b into a
    const [x, y] = [rgb(a), rgb(b)];
    return toHex(x.map((c, i) => c + (y[i] - c) * t));
  }
  function hsl2hex(h, s, l) {
    const k = n => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    return toHex([f(0) * 255, f(8) * 255, f(4) * 255]);
  }
  function rgb2hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
    if (!d) return [0, 0, l];
    const s = d / (1 - Math.abs(2 * l - 1));
    const h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
  }

  // Seam fix 2 (app.css): body's gradient runs from --bg to a colour 1/255 away, so no Chrome tile
  // is ever one flat colour. Blue moves up a step, or down at 255, so it never wraps.
  function seamOf(bg) {
    const [r, g, b] = rgb(bg);
    return toHex([r, g, b < 255 ? b + 1 : b - 1]);
  }

  // The contrast rules every theme keeps, as [foreground, background, what]. app.css uses the
  // muted, accent and accent-2 tokens as text on both the page and the panels, and on-accent on
  // the accent gradient (switches, buttons).
  function checks(t) {
    return [
      [t.ink, t.bg, "ink on bg"], [t.ink, t.panel, "ink on panel"],
      [t.muted, t.bg, "muted on bg"], [t.muted, t.panel, "muted on panel"],
      [t.accent, t.bg, "accent on bg"], [t.accent, t.panel, "accent on panel"],
      [t.accent2, t.bg, "accent-2 on bg"], [t.accent2, t.panel, "accent-2 on panel"],
      [t.onAccent, t.accent, "on-accent on accent"], [t.onAccent, t.accent2, "on-accent on accent-2"],
      [t.kamp || KAMP, t.bg, "kamp on bg"],
      // The artist-name tile (.noart): its centred name over the gradient's start and middle.
      ...(t.tile ? [[t.tileInk || TILE_INK, t.tile, "tile-ink on tile"],
                    [t.tileInk || TILE_INK, mix(t.tile, t.tileEnd || t.accent, 0.5), "tile-ink on the tile's middle"]] : []),
    ];
  }
  // The rules a theme breaks: [] when it's legible everywhere.
  function failures(t) {
    return checks(t).filter(([fg, bg]) => contrast(fg, bg) < TEXT_MIN).map(([, , what]) => what);
  }

  // ---- a theme from a cover ---------------------------------------------------------------

  // rgba: the #1 cover's pixels (RGBA bytes, as getImageData gives). Tokens, or null when the
  // cover has too little colour (greyscale, mostly black or white) or no legible theme comes out.
  function derive(rgba) {
    const BUCKETS = 24, weights = new Array(BUCKETS).fill(0), hues = new Array(BUCKETS).fill(0);
    const sats = new Array(BUCKETS).fill(0);
    let seen = 0, vivid = 0;
    for (let i = 0; i + 3 < rgba.length; i += 4) {
      if (rgba[i + 3] < 128) continue;
      seen++;
      const [h, s, l] = rgb2hsl(rgba[i], rgba[i + 1], rgba[i + 2]);
      if (s < 0.25 || l < 0.12 || l > 0.88) continue;
      const w = s * (1 - Math.abs(l - 0.5) * 2);
      const b = Math.floor(h / (360 / BUCKETS)) % BUCKETS;
      weights[b] += w; hues[b] += h * w; sats[b] += s * w;
      vivid += w;
    }
    if (!seen || vivid < seen * 0.04) return null;
    const order = weights.map((w, b) => [w, b]).filter(([w]) => w > 0).sort((x, y) => y[0] - x[0]);
    const [w1, b1] = order[0];
    const h1 = hues[b1] / w1, s1 = sats[b1] / w1;
    // A second hue at least 45 degrees away carrying real weight, else a neighbour of the first.
    const far = order.find(([w, b]) => w >= w1 * 0.15 && Math.min(Math.abs(b - b1), BUCKETS - Math.abs(b - b1)) >= 3);
    const h2 = far ? hues[far[1]] / far[0] : (h1 + 30) % 360;
    const s2 = far ? sats[far[1]] / far[0] : s1;
    const sat = s => Math.min(0.9, Math.max(0.55, s));
    const t = {
      bg: hsl2hex(h1, 0.3, 0.06), panel: hsl2hex(h1, 0.25, 0.11), ink: hsl2hex(h1, 0.2, 0.94),
      muted: hsl2hex(h1, 0.14, 0.68), accent: hsl2hex(h1, sat(s1), 0.6), accent2: hsl2hex(h2, sat(s2), 0.72),
      line: hsl2hex(h1, 0.18, 0.2), onAccent: hsl2hex(h1, 0.5, 0.07),
    };
    // Lift any text colour that falls short on the panel (the lighter of the two backgrounds).
    const lift = (key, h, s, l) => {
      while (l < 0.92 && (contrast(t[key], t.panel) < TEXT_MIN || contrast(t[key], t.bg) < TEXT_MIN)) {
        l += 0.02;
        t[key] = hsl2hex(h, s, l);
      }
    };
    lift("muted", h1, 0.14, 0.68);
    lift("accent", h1, sat(s1), 0.6);
    lift("accent2", h2, sat(s2), 0.72);
    return failures(t).length ? null : t;
  }

  // ---- the page -----------------------------------------------------------------------------

  const PROPS = {bg: "--bg", panel: "--panel", ink: "--ink", muted: "--muted", accent: "--accent",
                 accent2: "--accent-2", line: "--line", onAccent: "--on-accent", tile: "--tile",
                 kamp: "--kamp", tileInk: "--tile-ink", tileEnd: "--tile-end", seam: "--bg-seam"};

  // The tokens a preset id stands for (with the derived ones filled in), or null for the
  // default, match1 (derived later) and anything unknown.
  function tokens(id) {
    const t = id !== DEFAULT && Object.prototype.hasOwnProperty.call(THEMES, id) ? THEMES[id] : null;
    return t ? complete(t) : null;
  }
  // Every token filled in: the tile from the panel and accent (fainter on a light page), the seam,
  // and for presets that don't set them (dark ones, derived ones) the dark-page defaults.
  function complete(t) {
    const light = t.scheme === "light";
    const tile = t.tile || mix(t.panel, t.accent, light ? 0.25 : 0.35);
    const tileInk = t.tileInk || (light ? t.ink : TILE_INK);
    // The tile's gradient runs to the accent, unless the name in its middle wouldn't read there: then
    // the end eases back toward the tile until it does (a light accent under light text, AOTY-75).
    let tileEnd = t.accent;
    for (let i = 0; i < 12 && contrast(tileInk, mix(tile, tileEnd, 0.5)) < TEXT_MIN; i++) tileEnd = mix(tileEnd, tile, 0.2);
    return {
      ...t,
      scheme: light ? "light" : "dark",
      kamp: t.kamp || (light ? LIGHT_KAMP : KAMP),
      tile, tileInk, tileEnd,
      font: t.font || "",
      seam: seamOf(t.bg),
    };
  }

  // The picker's order (user, 2026-11-25): "album of the year" first, then the presets in rainbow
  // order by their accent's hue, starting at red (the wheel is cut at RAINBOW_START, so a
  // red-pink like strawberry switchblade's 346 degrees leads and magenta-pinks close the row).
  // Dark presets first, then light (AOTY-75), each in rainbow order.
  const RAINBOW_START = 340;
  const hueOf = hex => rgb2hsl(...rgb(hex))[0];
  const byHue = (a, b) =>
    (hueOf(THEMES[a].accent) - RAINBOW_START + 360) % 360 - (hueOf(THEMES[b].accent) - RAINBOW_START + 360) % 360;
  const isLight = id => THEMES[id].scheme === "light";
  const ORDER = [MATCH, ...Object.keys(THEMES).filter(id => !isLight(id)).sort(byHue),
                 ...Object.keys(THEMES).filter(isLight).sort(byHue)];

  // Paint a theme's tokens onto <html>, or the default with null. A light theme also switches the
  // page's color-scheme (form controls, scrollbars) and sets data-scheme / data-font, which
  // app.css keys its light-page and gabbie's Arial rules on.
  function apply(t, el = root.document && root.document.documentElement) {
    if (!el) return;
    if (!t) return reset(el);
    const full = complete(t);
    for (const [key, prop] of Object.entries(PROPS)) el.style.setProperty(prop, full[key]);
    el.style.setProperty("color-scheme", full.scheme);
    el.setAttribute("data-scheme", full.scheme);
    if (full.font) el.setAttribute("data-font", full.font);
    else el.removeAttribute("data-font");
  }
  function reset(el = root.document && root.document.documentElement) {
    if (!el) return;
    for (const prop of [...Object.values(PROPS), "color-scheme"]) el.style.removeProperty(prop);
    el.removeAttribute("data-scheme");
    el.removeAttribute("data-font");
  }

  // ---- whose theme (AOTY-76) ----------------------------------------------------------------
  // A page theme belongs to a user's page or a group's: target = {kind: "user" | "group", id}.

  // The remembered-theme key (app.js, AOTY-75). Groups get their own prefix, so a group slug
  // that's also someone's username never shares a key with their page.
  function themeKey(target) {
    return target.kind === "group" ? `aoty-theme:group:${target.id}` : `aoty-theme:${target.id}`;
  }
  // Where the owner's pick is saved (the api, under <base>api/).
  function savePath(target) {
    return target.kind === "group" ? `groups/${target.id}/theme` : `users/${target.id}/theme`;
  }
  // Whether `me` (a signed-in username) owns the theme: their own user page, or a group whose
  // owner they are (group.owner, not membership: an owner who left still owns it).
  function ownsTheme(target, me, groupOwner) {
    if (!target || !me) return false;
    const lower = String(me).toLowerCase();
    if (target.kind === "group") return !!groupOwner && String(groupOwner).toLowerCase() === lower;
    return String(target.id).toLowerCase() === lower;
  }
  // The ranking "album of the year" takes its #1 from: a user chart's one ranking (By Play), a
  // group's default (By Listener; charts built before AOTY-63 have "track", older ones "album").
  // Never a member's re-rank: ?user= filters the page, and the theme stays the group's.
  function topRanking(chart) {
    const r = (chart && chart.rankings) || {};
    if (chart && chart.kind === "group") return r.listener || r.track || r.album || [];
    return r.album || [];
  }

  const api = {THEMES, IDS, ORDER, LABELS, DEFAULT, MATCH, KAMP, LIGHT_KAMP, TEXT_MIN, contrast, seamOf,
               failures, derive, tokens, complete, apply, reset, mix, themeKey, savePath, ownsTheme, topRanking};
  root.aotyTheme = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
