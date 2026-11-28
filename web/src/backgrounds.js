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

  // ---- slow motion the backgrounds share ------------------------------------------------------
  //
  // DrunkWalk (kamp's drunkWalk.ts): a bounded, smooth drunkard's walk. It kicks the velocity,
  // damps it, and pulls the value gently back toward the centre, so the path is smooth, never
  // periodic, and stays near the centre more than chance would; a hard reflection at the bound
  // catches excursions. Chapel's hues and Bokeh's orbs wander on it (moved here in AOTY-102).
  const WALK_VEL_TAU_S = 4, WALK_PULL_S = 20, WALK_SIGMA = 0.032, WALK_DT_MAX = 0.1;
  const clampDt = (dt, max) => Math.min(Math.max(0, dt), max);
  function gauss(rng) {
    const u = Math.max(1e-9, rng());
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng());
  }
  class DrunkWalk {
    constructor(rng, max) {
      this.rng = rng;
      this.max = max;
      this.u = 0;  // value / max
      this.v = 0;  // velocity, in bounds per second
    }
    get value() { return this.u * this.max; }
    tick(rawDt) {
      const dt = clampDt(rawDt, WALK_DT_MAX);
      if (dt <= 0) return;
      const pull = this.u / (WALK_PULL_S * WALK_PULL_S);
      this.v += (-this.v / WALK_VEL_TAU_S - pull) * dt + WALK_SIGMA * Math.sqrt(dt) * gauss(this.rng);
      this.u += this.v * dt;
      if (Math.abs(this.u) > 1) {
        this.u = Math.sign(this.u);
        this.v = -0.5 * this.v;
      }
    }
  }

  // At most one of something per windowS on an animation clock (AOTY-103's re-proportion pace,
  // shared since AOTY-102): the first goes at once, and any asked for inside the window fold into
  // one more when it ends. A first visit's wall bursts (every 400 ms while covers stream in) would
  // otherwise fire a palette effect on every one.
  class Pace {
    constructor(windowS) {
      this.windowS = windowS;
      this.lastAt = -Infinity;
      this.pending = false;
    }
    // A request at clock now: true to act now, false if it waits for the window to end.
    request(now) {
      if (now - this.lastAt >= this.windowS) {
        this.lastAt = now;
        this.pending = false;
        return true;
      }
      this.pending = true;
      return false;
    }
    // Every live frame: true once a waiting request's window has ended.
    due(now) {
      if (!this.pending || now - this.lastAt < this.windowS) return false;
      this.lastAt = now;
      this.pending = false;
      return true;
    }
  }

  // ---- the loop (shaderEngine.ts and kamp's canvas engines, trimmed; AOTY-103, AOTY-102) ------
  //
  // What every background does the same way, so each engine file is only its scene. There are two
  // ways to draw on it, runShader (WebGL 1) and runCanvas (Canvas 2D); both take a spec
  //   {fps, resScale, maxDpr?, scene({accent, reducedMotion}) -> scene}   (runShader: frag too)
  // and a scene is
  //   draw(target, frame)          paint, ticking its own state first when !frame.still
  //   setPalette(colors, running)  a new palette from the covers
  //   setColors({accent}, running)?, setStrength(s)?, onStart()? (the loop just started), dispose()?
  // frame has elapsed, dt, still and atRest: dt is the raw seconds since the last drawn frame (0 on
  // a still), elapsed the animation clock (a stalled tab adds at most MAX_STEP_S), still any frame
  // drawn outside the loop (mount, a hidden tab, a stopped repaint), atRest only reduced motion's
  // designed rest frame (kamp's rule: a theme repaint is still but not at rest).
  //
  // The loop's rules, all kamp's: 30 fps unless the spec says, with FRAME_MS - 1 of slack (rAF
  // timestamps jitter, and a 30 fps cap without it runs at 20); only while the tab is visible; a
  // still frame at once, so the canvas is never blank; under reduced motion nothing but stills; a
  // resize clears the buffer (and a 2D context's whole state), so a same-size one (a phone's URL
  // bar, constantly) is skipped and a real one redrawn. kamp's canvas engines clamp dt to 0.1 s on
  // every rAF; here it's per drawn frame, so scenes clamp their own ticks.
  const DEFAULT_FPS = 30, DEFAULT_RES_SCALE = 0.25, DEFAULT_MAX_DPR = 1.5, MAX_STEP_S = 0.25;
  const DEFAULT_BG = {r: 13, g: 12, b: 18};

  // surface: {lostEvent, resize(w, h, scale), paint(scene, frame), dispose()}, one per drawing API.
  function runLoop(canvas, opts, spec, surface) {
    const doc = root.document;
    const reduced = !!opts.reducedMotion;
    const scene = spec.scene({accent: opts.accent, reducedMotion: reduced});
    const frameMs = 1000 / (spec.fps || DEFAULT_FPS), resScale = spec.resScale || DEFAULT_RES_SCALE;
    const maxDpr = spec.maxDpr || DEFAULT_MAX_DPR;
    let bg = hexToRgb(opts.bg) || DEFAULT_BG;
    let running = false, destroyed = false, raf = 0, lastDraw = 0, elapsed = 0, w = 0, h = 0, scale = 1;

    // Backing size = CSS size x dpr (capped) x resScale.
    function resize() {
      const dpr = Math.min(root.devicePixelRatio || 1, maxDpr);
      scale = dpr * resScale;
      const cw = Math.max(1, Math.round(canvas.clientWidth * scale));
      const ch = Math.max(1, Math.round(canvas.clientHeight * scale));
      if (cw === w && ch === h) return false;
      w = canvas.width = cw;
      h = canvas.height = ch;
      surface.resize(cw, ch, scale);
      return true;
    }

    function draw(still, dt) {
      surface.paint(scene, {w, h, scale, elapsed, dt, still, atRest: still && reduced, bg});
    }

    function frame(now) {
      raf = 0;
      if (!running) return;
      raf = root.requestAnimationFrame(frame);
      if (lastDraw && now - lastDraw < frameMs - 1) return;
      const dt = lastDraw ? (now - lastDraw) / 1000 : 0;
      lastDraw = now;
      elapsed += Math.min(dt, MAX_STEP_S);  // a stalled tab mustn't jump the animation
      resize();
      draw(false, dt);
    }
    function start() {
      if (running || reduced || destroyed || (doc && doc.hidden)) return;
      running = true;
      lastDraw = 0;
      if (scene.onStart) scene.onStart();  // nothing ticked while stopped: nothing stale may replay
      raf = root.requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      if (raf) root.cancelAnimationFrame(raf);
      raf = 0;
    }
    function still() {
      if (destroyed) return;
      resize();
      draw(true, 0);
    }

    const onVisibility = () => { if (doc.hidden) stop(); else if (reduced) still(); else start(); };
    const onResize = () => { if (resize() && !running) still(); };
    const onLost = e => {
      if (e && e.preventDefault) e.preventDefault();
      api.destroy();
      if (opts.onLost) opts.onLost();
    };
    canvas.addEventListener(surface.lostEvent, onLost);
    if (doc) doc.addEventListener("visibilitychange", onVisibility);
    if (root.addEventListener) root.addEventListener("resize", onResize);

    const api = {
      // A new palette from the covers: the scene animates it while running; stopped (reduced
      // motion, a hidden tab), it lands, and the still shows it at once.
      setPalette(colors) {
        if (destroyed || !colors || !colors.length) return;
        scene.setPalette(colors, running);
        if (!running) still();
      },
      // The page's colours changed (an owner's theme, AOTY-65).
      setColors({bg: nextBg, accent} = {}) {
        if (destroyed) return;
        bg = hexToRgb(nextBg) || bg;
        if (scene.setColors) scene.setColors({accent}, running);
        if (!running) still();
      },
      // 0..1 of the engine's own strength (AOTY-96): 1 on a first visit, less behind a built chart.
      setStrength(s) {
        if (destroyed || !Number.isFinite(s) || !scene.setStrength) return;
        scene.setStrength(Math.max(0, Math.min(1, s)));
        if (!running) still();
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        stop();
        canvas.removeEventListener(surface.lostEvent, onLost);
        if (doc) doc.removeEventListener("visibilitychange", onVisibility);
        if (root.removeEventListener) root.removeEventListener("resize", onResize);
        if (scene.dispose) scene.dispose();
        try { surface.dispose(); } catch (_) { /* the context may already be gone */ }
      },
    };

    still();
    start();
    return api;
  }

  function loseContext(gl) {
    const lose = gl.getExtension && gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
  }

  // Run a WebGL spec on canvas. opts: {bg, accent, reducedMotion, onLost()}. Returns the engine
  // contract's {setPalette, setColors, setStrength, destroy}, or null when WebGL isn't there (no
  // context, a blocklisted GPU, a shader that won't compile). The scene's draw(gl, quad, frame)
  // gets frame {w, h} in backing px and sets its own uniforms; the loop sets uRes, uTime and uBg.
  // destroy() calls WEBGL_lose_context, because browsers cap live contexts (~16).
  function runShader(canvas, opts = {}, spec) {
    let gl = null;
    try { gl = canvas.getContext("webgl", CONTEXT_ATTRS); } catch (_) { gl = null; }
    if (!gl) return null;
    const quad = createQuad(gl, spec.frag);
    if (!quad) { loseContext(gl); return null; }
    return runLoop(canvas, opts, spec, {
      lostEvent: "webglcontextlost",
      resize(cw, ch) { gl.viewport(0, 0, cw, ch); },
      paint(scene, f) {
        quad.use();  // uniforms go to the bound program
        gl.uniform2f(quad.u("uRes"), f.w, f.h);
        gl.uniform1f(quad.u("uTime"), f.elapsed % 1000);
        gl.uniform3f(quad.u("uBg"), f.bg.r / 255, f.bg.g / 255, f.bg.b / 255);
        scene.draw(gl, quad, {w: f.w, h: f.h, elapsed: f.elapsed, dt: f.dt, still: f.still, atRest: f.atRest});
        quad.draw();
      },
      dispose() {
        quad.dispose();
        loseContext(gl);
      },
    });
  }

  // ---- Canvas 2D (AOTY-102, the first canvas port; Sparks and Wintermute reuse it) ----------
  //
  // A light page (AOTY-75) by its --bg. kamp drew its canvas engines in 'screen' and 'lighter',
  // which do nothing over white; a canvas engine draws 'multiply' (a stain of colour) there
  // instead. Blend modes can't be mixed by degrees, so this is the midpoint of the shaders'
  // smoothstep(0.35, 0.65, lum).
  const isLight = rgb => !!rgb && (0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b) / 255 > 0.5;
  const cssRgb = c => `rgb(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)})`;

  // A square canvas for a baked sprite (kamp's makeCanvas): OffscreenCanvas where it has a 2D
  // context (Safari 16.4+), else a detached <canvas>.
  function spriteCanvas(side, doc = root.document) {
    if (typeof root.OffscreenCanvas === "function") {
      try {
        const c = new root.OffscreenCanvas(side, side);
        const ctx = c.getContext("2d");
        if (ctx) return {canvas: c, ctx};
      } catch (_) { /* fall through */ }
    }
    if (!doc || !doc.createElement) return {canvas: null, ctx: null};
    const c = doc.createElement("canvas");
    c.width = c.height = side;
    return {canvas: c, ctx: c.getContext("2d")};
  }

  // Run a Canvas 2D spec on canvas: the same contract as runShader. The canvas is opaque: every
  // frame starts with --bg, at identity alpha and source-over, in CSS px (a resize resets a 2D
  // context's whole state, so all of it is set every frame). The scene's draw(ctx, frame) gets
  // frame {w, h} in CSS px, scale (backing px per CSS px), bg (rgb) and light (isLight). A lost
  // context (contextlost, under memory pressure) ends it like a lost WebGL one.
  function runCanvas(canvas, opts = {}, spec) {
    let ctx = null;
    try { ctx = canvas.getContext("2d", {alpha: false}); } catch (_) { ctx = null; }
    if (!ctx) return null;
    return runLoop(canvas, opts, spec, {
      lostEvent: "contextlost",
      resize() {},
      paint(scene, f) {
        const w = f.w / f.scale, h = f.h / f.scale;
        ctx.setTransform(f.scale, 0, 0, f.scale, 0, 0);
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "source-over";
        ctx.fillStyle = cssRgb(f.bg);
        ctx.fillRect(0, 0, w, h);
        scene.draw(ctx, {w, h, scale: f.scale, elapsed: f.elapsed, dt: f.dt, still: f.still, atRest: f.atRest,
                         bg: f.bg, light: isLight(f.bg)});
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "source-over";
      },
      dispose() { canvas.width = canvas.height = 0; },  // frees the backing store at once
    });
  }

  // ---- the registry (AOTY-77, kamp's registry.ts) --------------------------------------------
  //
  // The engine contract. An engine file calls register(id, engine) when it loads, and
  //   engine.mount(canvas, {bg, accent, reducedMotion, onLost})
  // returns {setPalette(colors), setColors({bg, accent}), setStrength(s)?, destroy()}, or null when
  // it can't run here (no WebGL, a blocklisted GPU): the page then stays as it was. bg and accent
  // are the page's --bg and --accent (hex); colors is extractPalette's; setStrength (optional,
  // AOTY-96) scales the engine's own strength, 1 on a first visit and calmer behind a built chart.
  // While mounted, the engine owns its loop:
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

  // Which background a page shows: a preview's ?bg= when it names one, else the page's own (AOTY-96:
  // stored server-side, a new page's random start included), else none (null). "none" is never an
  // id here, so a saved "none" is null too.
  const NONE = "none";
  function pick({ids: from, override, saved}) {
    if (!from || !from.length) return null;
    if (from.includes(override)) return override;
    if (from.includes(saved)) return saved;
    return null;
  }

  // The owner's picker (AOTY-96, user decision 2026-11-28): "none" first, as in kamp, then every
  // background alphabetically. Each label is its id, lowercase like kamp's.
  const pickerOrder = from => [NONE, ...[...(from || [])].filter(id => id !== NONE).sort()];
  // Where the owner's pick is saved: the api's POST users/<name>/background or groups/<id>/background.
  const savePath = target => `${target.kind === "group" ? "groups" : "users"}/${target.id}/background`;

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
               packPalette, CONTEXT_ATTRS, VERT, createQuad, runShader, runCanvas, isLight, spriteCanvas,
               DrunkWalk, Pace, register, registered, parseMap, readMap, ids, pick,
               load, NONE, pickerOrder, savePath};
  root.aotyBg = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
