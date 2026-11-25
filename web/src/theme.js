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
  };
  const DEFAULT = "poster";
  const MATCH = "match1";
  const IDS = [...Object.keys(THEMES), MATCH];  // lambdas/common/themes.py must match (a test checks)
  // The default's id stays "poster" (stored ids never change); it reads "oranger" (user, 2026-11-25).
  const LABELS = {
    "poster": "oranger", "kamp": "kamp", "strawberry-switchblade": "strawberry switchblade",
    "beach-house": "beach house", "blackpink": "blackpink", "deep-purple": "deep purple",
    "green-day": "green day", "foxy-brown": "foxy brown", "golden-smog": "golden smog",
    [MATCH]: "album of the year",  // user, 2026-11-25
  };
  const KAMP = "#7c86e1";  // app.css --kamp: the endorsement chrome keeps it in every theme
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
      [KAMP, t.bg, "kamp on bg"],
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
                 accent2: "--accent-2", line: "--line", onAccent: "--on-accent", tile: "--tile"};

  // The tokens a preset id stands for (with the derived ones filled in), or null for the
  // default, match1 (derived later) and anything unknown.
  function tokens(id) {
    const t = id !== DEFAULT && Object.prototype.hasOwnProperty.call(THEMES, id) ? THEMES[id] : null;
    return t ? complete(t) : null;
  }
  function complete(t) {
    return {...t, tile: t.tile || mix(t.panel, t.accent, 0.35), seam: seamOf(t.bg)};
  }

  // The picker's order (user, 2026-11-25): "album of the year" first, then the presets in rainbow
  // order by their accent's hue, starting at red (the wheel is cut at RAINBOW_START, so a
  // red-pink like strawberry switchblade's 346 degrees leads and magenta-pinks close the row).
  const RAINBOW_START = 340;
  const hueOf = hex => rgb2hsl(...rgb(hex))[0];
  const ORDER = [MATCH, ...Object.keys(THEMES).sort((a, b) =>
    (hueOf(THEMES[a].accent) - RAINBOW_START + 360) % 360 - (hueOf(THEMES[b].accent) - RAINBOW_START + 360) % 360)];

  // Paint a theme's tokens onto <html>, or the default with null.
  function apply(t, el = root.document && root.document.documentElement) {
    if (!el) return;
    if (!t) return reset(el);
    const full = complete(t);
    for (const [key, prop] of Object.entries(PROPS)) el.style.setProperty(prop, full[key]);
    el.style.setProperty("--bg-seam", full.seam);
  }
  function reset(el = root.document && root.document.documentElement) {
    if (!el) return;
    for (const prop of [...Object.values(PROPS), "--bg-seam"]) el.style.removeProperty(prop);
  }

  const api = {THEMES, IDS, ORDER, LABELS, DEFAULT, MATCH, KAMP, TEXT_MIN, contrast, seamOf, failures,
               derive, tokens, apply, reset, mix};
  root.aotyTheme = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
