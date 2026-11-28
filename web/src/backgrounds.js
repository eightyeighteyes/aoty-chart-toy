// The first visit's backgrounds (AOTY-77): what every ported kamp background shares, loaded on every
// page before app.js. SPDX-License-Identifier: GPL-3.0-or-later
//
// The palette code and the WebGL program setup were Borealis's (AOTY-67) and came from kamp
// (github.com/eightyeighteyes/kamp, commit dfd8033), by its author:
//   kamp_ui/src/renderer/src/components/nowplaying/palette.ts                 extractPalette
//   kamp_ui/src/renderer/src/components/nowplaying/webgl/glQuad.ts            createQuad
//   kamp_ui/src/shared/{paletteCrossfade,paletteUniforms}.ts
//
// The pure parts are tested by node (web/test/backgrounds.test.mjs).
(root => {
  "use strict";

  const smoothstep = t => {
    const x = Math.max(0, Math.min(1, t));
    return x * x * (3 - 2 * x);
  };

  // ---- palette (palette.ts) -------------------------------------------------------------------
  const PALETTE_SIZE = 5, SAT_FLOOR = 0.45, LIGHT_MIN = 0.45, LIGHT_MAX = 0.68, HUE_BINS = 16;
  const MONO_SAT_THRESHOLD = 0.12;

  function rgbToHsl({r, g, b}) {
    const rn = r / 255, gn = g / 255, bn = b / 255;
    const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn), l = (max + min) / 2, d = max - min;
    if (d === 0) return {h: 0, s: 0, l};
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const h = max === rn ? ((gn - bn) / d + (gn < bn ? 6 : 0)) * 60
        : max === gn ? ((bn - rn) / d + 2) * 60 : ((rn - gn) / d + 4) * 60;
    return {h, s, l};
  }
  function hslToRgb({h, s, l}) {
    const c = (1 - Math.abs(2 * l - 1)) * s, hp = (((h % 360) + 360) % 360) / 60;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    const [r, g, b] = hp < 1 ? [c, x, 0] : hp < 2 ? [x, c, 0] : hp < 3 ? [0, c, x]
        : hp < 4 ? [0, x, c] : hp < 5 ? [x, 0, c] : [c, 0, x];
    const m = l - c / 2;
    return {r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255)};
  }
  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return {r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255};
  }
  // Keep the cover's hue; fix its tone into a pleasant band.
  function clampSwatch(rgb) {
    const hsl = rgbToHsl(rgb);
    return hslToRgb({h: hsl.h, s: Math.max(SAT_FLOOR, hsl.s), l: Math.min(LIGHT_MAX, Math.max(LIGHT_MIN, hsl.l))});
  }
  // Pad to a fixed length by hue-rotating the swatches, so a shader never branches on length.
  function padPalette(colors) {
    if (!colors.length) return [];
    const out = colors.slice(0, PALETTE_SIZE), rotations = [30, -30, 60, -60, 90];
    for (let i = 0; out.length < PALETTE_SIZE; i++) {
      const base = rgbToHsl(colors[i % colors.length]);
      out.push(hslToRgb({...base, h: base.h + rotations[i % rotations.length]}));
    }
    return out;
  }
  // The page accent's palette: for a grey cover, no covers yet, or a failed read.
  function accentPalette(accentHex) {
    const hsl = rgbToHsl(hexToRgb(accentHex) || {r: 255, g: 107, b: 74});
    const colors = [0, 28, -28, 56, -56].map(dh => clampSwatch(hslToRgb({...hsl, h: hsl.h + dh})));
    return {colors, source: "accent"};
  }
  // A small, clamped palette from RGBA pixels: binned by hue, weighted by saturation x mid-lightness,
  // so a mostly black cover surfaces its one neon accent rather than the black.
  function extractPalette(data, accentHex) {
    const w8 = new Float64Array(HUE_BINS), rs = new Float64Array(HUE_BINS);
    const gs = new Float64Array(HUE_BINS), bs = new Float64Array(HUE_BINS);
    for (let i = 0; i + 3 < data.length; i += 4) {
      if (data[i + 3] < 128) continue;
      const rgb = {r: data[i], g: data[i + 1], b: data[i + 2]};
      const {h, s, l} = rgbToHsl(rgb);
      const w = s * (1 - Math.abs(l - 0.5) * 2);
      if (w <= 0) continue;
      const bin = Math.min(HUE_BINS - 1, Math.floor((h / 360) * HUE_BINS));
      w8[bin] += w; rs[bin] += rgb.r * w; gs[bin] += rgb.g * w; bs[bin] += rgb.b * w;
    }
    const ranked = [...Array(HUE_BINS).keys()].filter(i => w8[i] > 0).sort((a, b) => w8[b] - w8[a]);
    if (!ranked.length) return accentPalette(accentHex);
    const avgs = ranked.map(i => ({r: Math.round(rs[i] / w8[i]), g: Math.round(gs[i] / w8[i]),
                                   b: Math.round(bs[i] / w8[i])}));
    if (rgbToHsl(avgs[0]).s < MONO_SAT_THRESHOLD) return accentPalette(accentHex);  // effectively grey
    return {colors: padPalette(avgs.slice(0, PALETTE_SIZE).map(clampSwatch)), source: "art"};
  }

  // Whether two palettes differ enough to be worth a crossfade and a surge: near-identical ones
  // (another cover of the same colours) change nothing on screen.
  const CHANGE_MIN = 36;  // summed RGB distance in one slot, 0..765
  function paletteChanged(a, b) {
    if (!a || !b || a.length !== b.length) return true;
    return a.some((c, i) => Math.abs(c.r - b[i].r) + Math.abs(c.g - b[i].g) + Math.abs(c.b - b[i].b) > CHANGE_MIN);
  }

  // ---- the crossfade (paletteCrossfade.ts) ---------------------------------------------------
  // kamp's one rule (KAMP-779): with no loop running nothing advances the fade, so a change must
  // land directly, or a reduced-motion still keeps the old colours for good.
  class PaletteCrossfade {
    constructor(initial, durationMs = 1000) {
      this.durationMs = durationMs;
      this.from = this.to = initial;
      this.t = 1;
    }
    get settled() { return this.t >= 1; }
    set(next, animate) {
      this.from = this.current();
      this.to = next;
      this.t = animate && this.durationMs > 0 ? 0 : 1;
    }
    tick(dtMs) {
      if (this.t < 1) this.t = Math.min(1, this.t + dtMs / this.durationMs);
    }
    current() {
      if (this.t >= 1) return this.to;
      const k = smoothstep(this.t), lerp = (a, b) => a + (b - a) * k;
      return this.to.map((c, i) => {
        const f = this.from[i] || c;
        return {r: lerp(f.r, c.r), g: lerp(f.g, c.g), b: lerp(f.b, c.b)};
      });
    }
  }

  // The vec3[5] uniform (paletteUniforms.ts): short palettes repeat, an empty one is a neutral grey.
  function packPalette(colors, out = new Float32Array(PALETTE_SIZE * 3)) {
    const src = colors && colors.length ? colors : [{r: 64, g: 64, b: 72}];
    for (let i = 0; i < PALETTE_SIZE; i++) {
      const c = src[i % src.length];
      out[i * 3] = c.r / 255; out[i * 3 + 1] = c.g / 255; out[i * 3 + 2] = c.b / 255;
    }
    return out;
  }

  // ---- WebGL (glQuad.ts) ----------------------------------------------------------------------
  // WebGL 1, low power, and refused on a blocklisted GPU: a background is never worth a slow page.
  const CONTEXT_ATTRS = {alpha: false, antialias: false, depth: false, stencil: false,
                         preserveDrawingBuffer: false, powerPreference: "low-power",
                         failIfMajorPerformanceCaveat: true};
  const VERT = "attribute vec2 aPos;\nvoid main() { gl_Position = vec4(aPos, 0.0, 1.0); }\n";

  // One program for frag and one fullscreen triangle, or null on any failure (the caller falls back).
  function createQuad(gl, frag) {
    const compile = (type, src) => {
      const sh = gl.createShader(type);
      if (!sh) return null;
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (gl.getShaderParameter(sh, gl.COMPILE_STATUS)) return sh;
      gl.deleteShader(sh);
      return null;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT), fs = compile(gl.FRAGMENT_SHADER, frag);
    const program = vs && fs ? gl.createProgram() : null;
    if (!program) { gl.deleteShader(vs); gl.deleteShader(fs); return null; }
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.bindAttribLocation(program, 0, "aPos");
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { gl.deleteProgram(program); return null; }
    const buf = gl.createBuffer();
    if (!buf) { gl.deleteProgram(program); return null; }
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const locs = new Map();
    return {
      u(name) {
        if (!locs.has(name)) locs.set(name, gl.getUniformLocation(program, name));
        return locs.get(name);
      },
      use() { gl.useProgram(program); },  // before setting uniforms: they go to the bound program
      draw() {
        gl.useProgram(program);
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.enableVertexAttribArray(0);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      },
      dispose() { gl.deleteBuffer(buf); gl.deleteProgram(program); },
    };
  }

  // ---- the registry (AOTY-77, kamp's registry.ts) --------------------------------------------
  //
  // The engine contract. An engine file calls register(id, engine) when it loads, and
  //   engine.mount(canvas, {bg, accent, reducedMotion, onLost})
  // returns {setPalette(colors), setColors({bg, accent}), destroy()}, or null when it can't run
  // here (no WebGL, a blocklisted GPU): the page then stays as it was. bg and accent are the page's
  // --bg and --accent (hex); colors is extractPalette's. While mounted, the engine owns its loop:
  // it runs only while the tab is visible, draws a still frame under reduced motion, paints every
  // pixel itself (its canvas is opaque, starting with --bg), and calls onLost if its context goes.
  // destroy() frees everything, WebGL contexts included (browsers cap them at about 16).
  //
  // The ids come from build_shell.BACKGROUNDS, through the page's #aoty-backgrounds JSON block
  // ({id: hashed URL}), so a new background is one entry there and its file.
  const engines = new Map(), loads = new Map();

  function register(id, engine) {
    if (typeof id === "string" && engine && typeof engine.mount === "function") engines.set(id, engine);
  }
  const registered = id => engines.get(id) || null;

  // The map's text, parsed: {id: same-origin path}. Anything else (missing, garbled, a full URL)
  // is left out, never thrown.
  function parseMap(text) {
    let m = null;
    try { m = JSON.parse(text); } catch (_) { return {}; }
    if (!m || typeof m !== "object" || Array.isArray(m)) return {};
    const out = {};
    for (const [id, url] of Object.entries(m)) {
      if (typeof url === "string" && url.startsWith("/") && !url.startsWith("//")) out[id] = url;
    }
    return out;
  }
  function readMap(doc = root.document) {
    const el = doc && doc.getElementById && doc.getElementById("aoty-backgrounds");
    return el ? parseMap(el.textContent) : {};
  }
  const ids = (map = readMap()) => Object.keys(map).sort();

  // Which background a first visit shows: a preview's ?bg= when it names one, then the page's own
  // (AOTY-96 stores it), else one at random. Null with none to pick from.
  function pick({ids: from, override, saved, random = Math.random}) {
    if (!from || !from.length) return null;
    if (from.includes(override)) return override;
    if (from.includes(saved)) return saved;
    return from[Math.min(from.length - 1, Math.floor(random() * from.length))];
  }

  // The engine for id: at once if its file has run, else after adding its <script src> (once; the
  // CSP allows same-origin scripts). Null for an id the map doesn't have, or a script that fails
  // to load or registers nothing; a failure isn't retried in this page session.
  function load(id, doc = root.document) {
    if (engines.has(id)) return Promise.resolve(engines.get(id));
    if (loads.has(id)) return loads.get(id);
    const url = readMap(doc)[id];
    if (!url || !doc || !doc.createElement) return Promise.resolve(null);
    const p = new Promise(resolve => {
      const s = doc.createElement("script");
      s.src = url;
      s.async = true;
      s.addEventListener("load", () => resolve(registered(id)));
      s.addEventListener("error", () => resolve(null));
      doc.head.appendChild(s);
    });
    loads.set(id, p);
    return p;
  }

  const api = {PALETTE_SIZE, extractPalette, accentPalette, paletteChanged, hexToRgb, PaletteCrossfade,
               packPalette, CONTEXT_ATTRS, VERT, createQuad, register, registered, parseMap, readMap, ids, pick,
               load};
  root.aotyBg = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
