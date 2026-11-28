// "Bokeh": soft blurred orbs in the covers' colours, drifting slowly, now and then racking one or
// two small ones into focus (AOTY-102). SPDX-License-Identifier: GPL-3.0-or-later
//
// Ported from kamp (github.com/eightyeighteyes/kamp, commit d8ea676) by its author, as plain JS
// with no build step:
//   kamp_ui/src/renderer/src/components/nowplaying/bokehEngine.ts   the renderer
//   kamp_ui/src/shared/bokehField.ts                                the orb field and its density
//   kamp_ui/src/shared/bokehFocus.ts                                the rack focus
//   kamp_ui/src/shared/bokehSprites.ts, particleUtil.ts             sprite geometry
// It runs on backgrounds.js runCanvas (the first Canvas 2D port) at kamp's tuning: 0.25 scale,
// dpr capped at 2, 30 fps (user decision, 2026-11-28).
//
// What came across: the orb field, its breath, the rack focus, the sprites with their blur baked
// in (never a CSS blur, which cost kamp 66% GPU), the firefly (always due: there's no pointer to
// wait out, user decision 2026-11-28), and the palette bloom, fired by a new palette (a wall
// burst, a new #1), at most one per BLOOM_PACE_S. What didn't: the pointer parallax, the art pane
// (the rack's visibility and the density census see no art), the vignette, kamp's dev hooks.
//
// Two things are new here:
//   * Safari has no ctx.filter, so kamp's blur bake would give hard-edged orbs on every iPhone.
//     Where the filter is missing, an orb is a radial gradient whose stops are the same blur,
//     computed (blurredStops, tested against the filter by hand in Chrome).
//   * Light pages (AOTY-75): 'screen' and 'lighter' do nothing over white, so there everything
//     draws 'multiply', a stain of colour, and the firefly's warm-white core takes its own colour.
//
// The pure parts are tested by node (web/test/bokeh.test.mjs), the scene on a stand-in 2D context.
(root => {
  "use strict";

  // web/src/backgrounds.js: loaded first in a page; required in node (its tests).
  const shared = root.aotyBg || (typeof require === "function" ? require("./backgrounds.js") : null);
  if (!shared) return;
  const {PaletteCrossfade, accentPalette, runCanvas, spriteCanvas, DrunkWalk, Pace} = shared;

  const TAU = Math.PI * 2;
  const rand = (rng, min, max) => min + rng() * (max - min);
  const smoothstep = t => t * t * (3 - 2 * t);
  // Zero velocity and acceleration at both ends: no onset snap.
  const smootherstep = t => t * t * t * (t * (t * 6 - 15) + 10);
  const clampDt = (dt, max) => Math.min(Math.max(0, dt), max);

  // ---- the orb field (bokehField.ts) ----------------------------------------------------------
  // Each orb's base position wanders on a bounded, mean-reverting walk around the pane centre, one
  // per axis, so no orb ever wraps or pops at an edge. reach is how far a base may roam, as a
  // fraction of half the pane: heroes stay inside it, mids and accents (reach > 1) leave and come
  // back, so the count on screen varies. blur gives the field depth of field: small accents sit
  // nearest the focal plane, heroes furthest, and the ranges don't overlap.
  const TIERS = {
    hero: {size: [0.55, 0.75], alpha: [0.32, 0.4], reach: 0.8, rate: 0.35, blur: [1.0, 1.3]},
    mid: {size: [0.3, 0.45], alpha: [0.4, 0.5], reach: 1.1, rate: 0.35, blur: [0.7, 0.95]},
    accent: {size: [0.12, 0.22], alpha: [0.5, 0.56], reach: 1.5, rate: 0.35, blur: [0.35, 0.6]},
  };
  // DrunkWalk reflects hard at its bound; through tanh that bound sits where the curve is nearly
  // flat, so a reflection is a gentle slowdown. tanh(SOFT·u)/tanh(SOFT) still lands on ±1.
  const SOFT = 2;
  const softBound = u => Math.tanh(SOFT * u) / Math.tanh(SOFT);
  // Walk time simulated at creation, so orbs start spread out rather than all at the centre.
  const PREWARM_S = 600, STEP_S = 0.1;
  // 8 orbs, the most dominant palette slots on the big heroes; three accents in three colours, so
  // a rack focus has a real choice.
  const ORB_PLAN = [
    {tier: "hero", colorIndex: 0}, {tier: "hero", colorIndex: 1},
    {tier: "mid", colorIndex: 2}, {tier: "mid", colorIndex: 3}, {tier: "mid", colorIndex: 0},
    {tier: "accent", colorIndex: 4}, {tier: "accent", colorIndex: 2}, {tier: "accent", colorIndex: 1},
  ];
  const BLOOM_MAX_DELAY = 0.12;  // seconds: per-orb bloom onset jitter

  function makeOrb(plan, rng) {
    const t = TIERS[plan.tier];
    const orb = {
      tier: plan.tier, bx: 0.5, by: 0.5,
      walkX: new DrunkWalk(rng, 1), walkY: new DrunkWalk(rng, 1),
      reach: t.reach, rate: t.rate,
      sizePct: rand(rng, t.size[0], t.size[1]),  // diameter, fraction of S = min(w, h)
      ax: rand(rng, 0.05, 0.07), ay: rand(rng, 0.05, 0.07),  // wobble amplitude, fraction of S
      // incommensurate wobble periods (seconds): never a closed loop
      px1: rand(rng, 15, 19), px2: rand(rng, 27, 31), py1: rand(rng, 18, 22), py2: rand(rng, 29, 33),
      breathP: rand(rng, 12, 20), breathPhase: rand(rng, 0, TAU),
      baseAlpha: rand(rng, t.alpha[0], t.alpha[1]),
      colorIndex: plan.colorIndex,  // a stable palette slot, so a recolour never flickers
      bloomDelay: rand(rng, 0, BLOOM_MAX_DELAY),
      blurMul: rand(rng, t.blur[0], t.blur[1]),  // baked blur, a multiple of BOKEH_BLUR_CSS
    };
    for (let s = 0; s < PREWARM_S; s += STEP_S) advanceOrb(orb, STEP_S / orb.rate);
    return orb;
  }
  const makeField = rng => ORB_PLAN.map(p => makeOrb(p, rng));
  function advanceOrb(o, dt) {
    o.walkX.tick(dt * o.rate);
    o.walkY.tick(dt * o.rate);
    o.bx = 0.5 + 0.5 * o.reach * softBound(o.walkX.value);
    o.by = 0.5 + 0.5 * o.reach * softBound(o.walkY.value);
  }
  function advanceField(orbs, dt) { for (const o of orbs) advanceOrb(o, dt); }

  // Where an orb is drawn, in CSS px: drift plus wobble (kamp's pointer parallax is gone).
  function orbCenter(o, elapsed, w, h) {
    const s = Math.min(w, h);
    const ox = o.ax * (Math.sin(elapsed / o.px1) + Math.sin(elapsed / o.px2));
    const oy = o.ay * (Math.sin(elapsed / o.py1) + Math.sin(elapsed / o.py2));
    return {x: o.bx * w + ox * s, y: o.by * h + oy * s};
  }

  // ---- the density metric (bokehField.ts) ------------------------------------------------------
  // An orb counts as visible when at least VISIBLE_SHARE of its disc is in the pane, outside an
  // edge band (kamp's vignette; colour there reads as little even without one) and outside art
  // (a normalised rect; null here: there's no art pane, but kamp's tests keep it).
  const EDGE_BAND = 0.06, VISIBLE_SHARE = 0.25;
  const SAMPLES = [];
  for (const r of [0.3, 0.6, 0.9]) {
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * TAU;
      SAMPLES.push({dx: Math.cos(a) * r, dy: Math.sin(a) * r, w: r});  // area-weighted by radius
    }
  }
  const SAMPLE_WEIGHT = SAMPLES.reduce((acc, p) => acc + p.w, 0);
  function visibleShare(x, y, radius, w, h, art = null) {
    let seen = 0;
    for (const p of SAMPLES) {
      const nx = (x + p.dx * radius) / w, ny = (y + p.dy * radius) / h;
      if (nx < EDGE_BAND || nx > 1 - EDGE_BAND || ny < EDGE_BAND || ny > 1 - EDGE_BAND) continue;
      if (art && nx > art.x0 && nx < art.x1 && ny > art.y0 && ny < art.y1) continue;
      seen += p.w;
    }
    return seen / SAMPLE_WEIGHT;
  }
  function visibleOrbCount(orbs, elapsed, w, h, art = null) {
    const s = Math.min(w, h);
    let n = 0;
    for (const o of orbs) {
      const {x, y} = orbCenter(o, elapsed, w, h);
      if (visibleShare(x, y, o.sizePct * s * 0.5, w, h, art) >= VISIBLE_SHARE) n++;
    }
    return n;
  }

  // When the sprites need baking again: the geometry key changed, or the colours did. Colours are
  // compared by reference, PaletteCrossfade's contract: settled, current() is the same array every
  // call; mid-fade, a fresh blend. So a settled field never re-bakes, and a fade re-bakes each frame.
  class SpriteBakeGate {
    constructor() { this.key = ""; this.colors = null; }
    shouldBake(key, colors) {
      if (key === this.key && colors === this.colors) return false;
      this.key = key;
      this.colors = colors;
      return true;
    }
    invalidate() { this.key = ""; }
  }

  // ---- the rack focus (bokehFocus.ts) ---------------------------------------------------------
  // Every so often focus is racked to one or two of the small accents, which sharpen into defined
  // discs while the rest stay soft.
  const RACK_GAP = [20, 40], FIRST_RACK = [4, 10], PULL_S = 3, HIDDEN_RERACK_S = 5, RETRY_S = 2;
  const MAX_SHARP = 2, SOFT_DIM = 0.5;
  const randIn = (rng, [min, max]) => min + rng() * (max - min);

  class RackFocus {
    // count orbs; eligible are the ones that may come into focus (the accents).
    constructor(count, eligible, rng) {
      this.eligible = eligible;
      this.rng = rng;
      this.focus = new Array(count).fill(0);  // per orb, 0 soft only .. 1 fully in focus
      this.targets = new Set();
      this.from = new Array(count).fill(0);
      this.to = new Array(count).fill(0);
      this.progress = new Array(count).fill(1);
      this.untilRack = randIn(rng, FIRST_RACK);
      this.hiddenFor = 0;
    }
    // visible(i): can orb i be seen now; asked about eligible orbs only.
    tick(dt, visible) {
      for (let i = 0; i < this.focus.length; i++) {
        if (this.progress[i] >= 1) continue;
        this.progress[i] = Math.min(1, this.progress[i] + dt / PULL_S);
        // Snap on arrival: an ease that only approaches its target leaves an orb a hair off.
        this.focus[i] = this.progress[i] >= 1 ? this.to[i]
          : this.from[i] + (this.to[i] - this.from[i]) * smootherstep(this.progress[i]);
      }
      let anyHidden = false;
      for (const i of this.targets) if (!visible(i)) anyHidden = true;
      this.hiddenFor = anyHidden ? this.hiddenFor + dt : 0;
      this.untilRack -= dt;
      if (this.untilRack <= 0 || this.hiddenFor >= HIDDEN_RERACK_S) this.rack(visible);
    }
    // New targets now: 1 or 2 visible eligible orbs, preferring ones not already in focus. Every
    // pull starts from the orb's current focus, so a rack mid-pull never jumps.
    rack(visible) {
      this.hiddenFor = 0;
      const candidates = this.eligible.filter(visible);
      const next = new Set();
      if (candidates.length === 0) {
        this.untilRack = RETRY_S;
      } else {
        const want = Math.min(this.rng() < 0.5 ? 1 : 2, MAX_SHARP, candidates.length);
        const fresh = candidates.filter(i => !this.targets.has(i));
        const stale = candidates.filter(i => this.targets.has(i));
        while (next.size < want) {
          const pool = fresh.length > 0 ? fresh : stale;
          const [pick] = pool.splice(Math.floor(this.rng() * pool.length), 1);
          next.add(pick);
        }
        this.untilRack = randIn(this.rng, RACK_GAP);
      }
      this.targets.clear();
      for (const i of next) this.targets.add(i);
      for (let i = 0; i < this.focus.length; i++) {
        const to = next.has(i) ? 1 : 0;
        if (to === this.to[i]) continue;
        this.from[i] = this.focus[i];
        this.to[i] = to;
        this.progress[i] = 0;
      }
    }
  }

  // Layer weights for one orb at focus, soft under sharp: never a crossfade, which dips at its
  // midpoint. The soft layer dims as focus rises, by no more than keeps the centre from darkening
  // under 'screen': 1 - (1 - as·(1 - k·f))·(1 - ak·f) never falls while k <= ak·(1 - as)/as.
  function focusAlphas(focus, softCentre, sharpCentre) {
    const k = softCentre <= 0 ? SOFT_DIM : softCentre >= 1 ? 0
      : Math.min(SOFT_DIM, (sharpCentre * (1 - softCentre)) / softCentre);
    return {soft: 1 - k * focus, sharp: focus};
  }

  // ---- sprite geometry (bokehSprites.ts, particleUtil.ts) --------------------------------------
  const BOKEH_BLUR_CSS = 28;  // the softness kamp's CSS filter used: Gaussian sigma, CSS px
  // Padding that keeps a baked Gaussian from being clipped: three sigma.
  const blurPad = sigma => (sigma > 0 ? Math.ceil(3 * sigma) : 0);
  // One sprite's size, in backing px (a sprite drawn at another size scales its blur with it, so
  // sigma follows the scale too).
  function orbSprite(radiusCss, blurCss, scale) {
    const radius = radiusCss * scale, sigma = Math.max(0, blurCss) * scale;
    return {radius, sigma, half: Math.max(1, Math.ceil(radius + blurPad(sigma)))};
  }
  // Half-side to draw a sprite at, CSS px, for an orb whose radius is now radiusCss: breath and
  // bloom scale the baked sprite rather than re-baking it.
  function drawHalf(sp, radiusCss, scale) {
    const baseCss = sp.radius / scale;
    return (sp.half / scale) * (baseCss > 0 ? radiusCss / baseCss : 1);
  }
  // globalAlpha for a sprite baked at its peak alpha to show alpha (globalAlpha only dims).
  const alphaScale = (alpha, peak) => (peak <= 0 ? 0 : Math.max(0, Math.min(1, alpha / peak)));
  // A sprite set's geometry: the pane's short side in backing px, so sub-pixel jitter never re-bakes.
  const bakeKey = (paneShortCss, scale, blurCss) => `${Math.round(paneShortCss * scale)}|${scale}|${blurCss}`;

  // ---- the blur without ctx.filter (new here, for Safari) --------------------------------------
  // kamp bakes each sprite's blur with ctx.filter = blur(σ). Safari has no ctx.filter, so there the
  // blurred gradient is computed instead: a radial gradient's alpha (and colour) is piecewise
  // linear in radius, the 2D Gaussian blur of it is linear in those stop values, so per geometry
  // each stop's blurred "hat" is computed once (blurBasis) and any colours are a weighted sum. It's
  // done on premultiplied colour, so a two-colour gradient (the firefly's warm core) blurs right.
  // Rings of the unit 2D Gaussian, polar: radii in sigmas up to 3, weighted by the density times
  // the ring's circumference, normalised.
  const RING_K = 10, RING_M = 20;
  const RING = (() => {
    const pts = [];
    for (let k = 0; k < RING_K; k++) {
      const u = ((k + 0.5) * 3) / RING_K;
      const w = Math.exp(-u * u / 2) * u;
      for (let m = 0; m < RING_M; m++) {
        const a = ((m + (k % 2) * 0.5) / RING_M) * TAU;
        pts.push({x: u * Math.cos(a), y: u * Math.sin(a), w});
      }
    }
    const sum = pts.reduce((acc, p) => acc + p.w, 0);
    for (const p of pts) p.w /= sum;
    return pts;
  })();
  // The hat functions of stops at positions (0..1 of the radius, ascending, the first 0): weights
  // of each stop at t, the unblurred gradient's own interpolation. Past the last stop, nothing
  // (the sprite's corners hold the last stop, which is transparent).
  function hats(positions, t, out) {
    out.fill(0);
    const n = positions.length;
    if (t > positions[n - 1]) return out;
    for (let i = 0; i < n - 1; i++) {
      const a = positions[i], b = positions[i + 1];
      if (t >= a && t <= b) {
        const k = b > a ? (t - a) / (b - a) : 0;
        out[i] = 1 - k;
        out[i + 1] = k;
        return out;
      }
    }
    out[0] = 1;  // t before the first stop: its colour
    return out;
  }
  const BASIS_SAMPLES = 20, EDGE_SAMPLES = 14;
  const basisCache = new Map();
  // Where to sample the blurred gradient (radii, px), and each stop's weight there.
  function blurBasis(positions, radius, sigma) {
    const key = `${positions.join(",")}|${radius.toFixed(2)}|${sigma.toFixed(2)}`;
    if (basisCache.has(key)) return basisCache.get(key);
    const R = radius + 3 * sigma;
    const rho = [];
    for (let j = 0; j <= BASIS_SAMPLES; j++) rho.push((R * j) / BASIS_SAMPLES);
    // The edge, where the blur does most, gets more samples.
    const lo = Math.max(0, radius - 2 * sigma), hi = Math.min(R, radius + 2 * sigma);
    for (let j = 1; j < EDGE_SAMPLES; j++) rho.push(lo + ((hi - lo) * j) / EDGE_SAMPLES);
    rho.sort((a, b) => a - b);
    const at = rho.filter((v, i) => i === 0 || v - rho[i - 1] > 1e-6);
    const n = positions.length, h = new Array(n);
    const weights = at.map(d => {
      const w = new Array(n).fill(0);
      for (const p of RING) {
        const dx = d + p.x * sigma, dy = p.y * sigma;
        hats(positions, Math.hypot(dx, dy) / Math.max(radius, 1e-6), h);
        for (let i = 0; i < n; i++) w[i] += p.w * h[i];
      }
      return w;
    });
    const basis = {R, at, weights};
    if (basisCache.size > 256) basisCache.clear();
    basisCache.set(key, basis);
    return basis;
  }
  // A gradient's stops [{at, c: {r, g, b}, a}] (at 0..1 of radius) blurred by sigma (px): stops of
  // a gradient of radius R = radius + 3 sigma, as {R, stops}. No blur: the stops as they are.
  function blurredStops(stops, radius, sigma) {
    if (!(sigma > 0)) return {R: radius, stops: stops.map(s => ({at: s.at, c: s.c, a: s.a}))};
    const {R, at, weights} = blurBasis(stops.map(s => s.at), radius, sigma);
    const last = stops[stops.length - 1].c;
    return {
      R,
      stops: at.map((d, j) => {
        let pr = 0, pg = 0, pb = 0, pa = 0;
        weights[j].forEach((w, i) => {
          const s = stops[i], wa = w * s.a;
          pa += wa; pr += wa * s.c.r; pg += wa * s.c.g; pb += wa * s.c.b;
        });
        const c = pa > 1e-9 ? {r: pr / pa, g: pg / pa, b: pb / pa} : last;
        return {at: Math.min(1, d / R), c, a: pa};
      }),
    };
  }
  const rgba = (c, a) => `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${Math.max(0, Math.min(1, a)).toFixed(4)})`;
  // Whether a 2D context can blur (Chrome, Firefox; not Safari).
  const canFilter = ctx => !!ctx && "filter" in ctx;

  // ---- the renderer's tunables (bokehEngine.ts) -----------------------------------------------
  const RES_SCALE = 0.25, MAX_DPR = 2, FPS = 30;  // kamp's tuning: 0.25 measured 8% GPU busy
  const ORB_ALPHA_PEAK = 1 + 0.06 + 0.2;  // breath and bloom on top of the base: what a sprite holds
  const CROSSFADE_MS = 1000;
  const TICK_MAX_S = 0.1;  // kamp's per-rAF clamp, applied to each drawn frame here
  // The firefly: a rare transient mote. Poisson inter-arrival, so it never feels scheduled.
  const FIREFLY_GAP_FLOOR = 30, FIREFLY_GAP_MEAN = 60, FIREFLY_GAP_MAX = 120;
  const FIREFLY_LIFE = [10, 15], FIREFLY_SIZE = [0.08, 0.12];  // seconds; core radius, fraction of S
  const FIREFLY_TRAVEL = 0.07, FIREFLY_BOW = 0.012;  // drift and its bow, fractions of S
  const FIREFLY_ALPHA = 0.8, FIREFLY_PEAK = FIREFLY_ALPHA * 1.15;
  const FIREFLY_CORE = {r: 255, g: 246, b: 230};  // warm off-white: a glow, not a dot
  // The palette bloom: radius-led, so the field "inhales" rather than flashes; its tail outlasts
  // the crossfade, so the light settles after the new colours land.
  const BLOOM_DURATION = 1.5, BLOOM_ATTACK = 0.3, BLOOM_RADIUS = 0.16, BLOOM_ALPHA = 0.2;
  const BLOOM_DONE = BLOOM_DURATION + BLOOM_MAX_DELAY;  // no bloom active
  const BLOOM_PACE_S = 7;  // at most one bloom per this, so a first visit's wall bursts never strobe
  // Depth of field: an in-focus accent is a flat disc with a brighter lens-like rim, over its soft
  // sprite. Its edge blur is a floor in backing px against aliasing, not a look.
  const SHARP_SIGMA = 1.5, SHARP_RADIUS = 0.85, SHARP_GAIN = 1.25, RIM_GAIN = 1.25;
  const RIM_STOPS = [0.8, 0.93, 1];
  const FOCUSABLE = ORB_PLAN.flatMap((p, i) => (p.tier === "accent" ? [i] : []));
  const STATIC_ALPHA = 0.22;  // reduced motion's still: a gradient of the two dominant swatches
  // Light pages (AOTY-75) need no thicker paint, unlike Chapel's fields: measured in Chrome, one
  // orb multiplied onto white moves the page about as far as it screens onto the dark --bg (summed
  // RGB peak, five swatches: 127 against 121 on average; only a yellow is fainter on white, 93
  // against 154, as yellow on white is).

  function bloomEnvelope(age) {
    const x = age / BLOOM_DURATION;
    if (x <= 0 || x >= 1) return 0;
    if (x < BLOOM_ATTACK) return smootherstep(x / BLOOM_ATTACK);
    return 1 - smootherstep((x - BLOOM_ATTACK) / (1 - BLOOM_ATTACK));
  }

  // An orb's (or the firefly's) gradient, into a centred sprite: kamp's three stops, the centre in
  // core if given. With ctx.filter, kamp's bake; without, the computed blur.
  function bakeOrb(geom, c, peakAlpha, [mid, midAlpha], core) {
    const side = geom.half * 2;
    const {canvas, ctx} = spriteCanvas(side);
    if (!ctx) return null;
    const cx = geom.half, r = Math.max(0.5, geom.radius);
    const stops = [{at: 0, c: core || c, a: peakAlpha}, {at: mid, c, a: peakAlpha * midAlpha}, {at: 1, c, a: 0}];
    let R = r, out = stops;
    if (geom.sigma > 0 && canFilter(ctx)) ctx.filter = `blur(${geom.sigma}px)`;
    else ({R, stops: out} = blurredStops(stops, r, geom.sigma));
    const grad = ctx.createRadialGradient(cx, cx, 0, cx, cx, R);
    for (const s of out) grad.addColorStop(s.at, rgba(s.c, s.a));
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, side, side);
    return canvas;
  }
  // An in-focus accent: a flat disc with a brighter rim, baked at the gains over the orb's peak.
  // Without ctx.filter its 1.5 backing px edge blur is left out: it's an anti-alias floor only.
  function bakeSharp(geom, c, peakAlpha) {
    const side = geom.half * 2;
    const {canvas, ctx} = spriteCanvas(side);
    if (!ctx) return null;
    const cx = geom.half, r = Math.max(0.5, geom.radius);
    if (geom.sigma > 0 && canFilter(ctx)) ctx.filter = `blur(${geom.sigma}px)`;
    const inner = Math.min(1, peakAlpha * SHARP_GAIN), rim = Math.min(1, inner * RIM_GAIN);
    const grad = ctx.createRadialGradient(cx, cx, 0, cx, cx, r);
    grad.addColorStop(0, rgba(c, inner));
    grad.addColorStop(RIM_STOPS[0], rgba(c, inner));
    grad.addColorStop(RIM_STOPS[1], rgba(c, rim));
    grad.addColorStop(RIM_STOPS[2], rgba(c, 0));
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cx, r, 0, TAU);
    ctx.fill();
    return canvas;
  }

  // ---- the scene, on backgrounds.js runCanvas -------------------------------------------------
  let latest = null;  // the newest scene, for pop() (a by-hand check: a firefly comes every 30-120 s)

  function scene({accent: initialAccent, rng = Math.random} = {}) {
    let accent = initialAccent;
    let art = false;        // a palette from the covers has arrived
    let drawnLive = false;  // a live frame has drawn: before it, a palette lands (mount's)
    let strength = 1, elapsed = 0, cssW = 0, cssH = 0;
    const fade = new PaletteCrossfade(accentPalette(accent).colors, CROSSFADE_MS);
    const orbs = makeField(rng);
    const rack = new RackFocus(ORB_PLAN.length, FOCUSABLE, rng);
    const gate = new SpriteBakeGate();
    const pace = new Pace(BLOOM_PACE_S);
    let sprites = [], spriteGeom = [], sharpSprites = [], sharpGeom = [];
    let bloomAge = BLOOM_DONE;
    let firefly = null, lastCorner = -1;
    const fireflyGap = () => Math.min(FIREFLY_GAP_MAX, FIREFLY_GAP_FLOOR - FIREFLY_GAP_MEAN * Math.log(Math.max(1e-6, rng())));
    let nextFireflyAt = fireflyGap();

    const orbVisible = i => {
      const o = orbs[i];
      if (!o || cssW === 0) return false;
      const {x, y} = orbCenter(o, elapsed, cssW, cssH);
      return visibleShare(x, y, o.sizePct * Math.min(cssW, cssH) * 0.5, cssW, cssH) >= VISIBLE_SHARE;
    };

    function spawnFirefly() {
      const colors = fade.current();
      if (!colors.length) return;
      const color = {...colors[Math.floor(rng() * colors.length)]};  // a snapshot: never recoloured
      let corner = Math.floor(rng() * 4);
      if (corner === lastCorner) corner = (corner + 1) % 4;  // never the same corner twice running
      lastCorner = corner;
      const left = corner === 0 || corner === 3, top = corner < 2;
      const x0 = left ? rand(rng, 0.08, 0.24) : rand(rng, 0.76, 0.92);
      const y0 = top ? rand(rng, 0.08, 0.24) : rand(rng, 0.76, 0.92);
      // Drift outward, so the mote never walks the eye to the middle.
      const angle = Math.atan2(y0 < 0.5 ? -1 : 1, x0 < 0.5 ? -1 : 1) + rand(rng, -0.6, 0.6);
      const dirX = Math.cos(angle), dirY = Math.sin(angle);
      firefly = {x0, y0, dirX, dirY, perpX: -dirY, perpY: dirX, age: 0,
                 life: rand(rng, FIREFLY_LIFE[0], FIREFLY_LIFE[1]), size: rand(rng, FIREFLY_SIZE[0], FIREFLY_SIZE[1]),
                 color, breathPhase: rand(rng, 0, TAU), sprite: null, geom: null, key: ""};
    }

    function tick(dt) {
      fade.tick(dt * 1000);
      bloomAge = Math.min(BLOOM_DONE, bloomAge + dt);
      if (firefly) {
        firefly.age += dt;
        if (firefly.age >= firefly.life) {
          firefly = null;
          nextFireflyAt = elapsed + fireflyGap();
        }
      } else if (elapsed >= nextFireflyAt) spawnFirefly();  // no pointer: always idle
      advanceField(orbs, dt);
      rack.tick(dt, orbVisible);
    }

    function bakeSprites(s, scale, colors) {
      if (!gate.shouldBake(bakeKey(s, scale, BOKEH_BLUR_CSS), colors)) return;
      sharpSprites = orbs.map((o, i) => {
        const c = colors[o.colorIndex] || colors[0];
        if (!FOCUSABLE.includes(i) || !c) return null;
        sharpGeom[i] = orbSprite(o.sizePct * s * 0.5 * SHARP_RADIUS, SHARP_SIGMA / scale, scale);
        return bakeSharp(sharpGeom[i], c, o.baseAlpha * ORB_ALPHA_PEAK);
      });
      sprites = orbs.map((o, i) => {
        spriteGeom[i] = orbSprite(o.sizePct * s * 0.5, BOKEH_BLUR_CSS * o.blurMul, scale);
        const c = colors[o.colorIndex] || colors[0];
        return c ? bakeOrb(spriteGeom[i], c, Math.min(1, o.baseAlpha * ORB_ALPHA_PEAK), [0.4, 0.55]) : null;
      });
    }

    function drawOrbs(ctx, w, h, scale, blend) {
      const s = Math.min(w, h);
      bakeSprites(s, scale, fade.current());
      ctx.globalCompositeOperation = blend;
      orbs.forEach((o, i) => {
        const breath = Math.sin(elapsed / o.breathP + o.breathPhase);
        const bloom = bloomEnvelope(bloomAge - o.bloomDelay);
        const {x, y} = orbCenter(o, elapsed, w, h);
        const radius = o.sizePct * s * 0.5 * (1 + 0.08 * breath + BLOOM_RADIUS * bloom);
        const sprite = sprites[i], geom = spriteGeom[i];
        if (radius <= 0 || !sprite || !geom) return;
        const alpha = o.baseAlpha * (1 + 0.06 * breath + BLOOM_ALPHA * bloom);
        const peak = o.baseAlpha * ORB_ALPHA_PEAK, dim = alphaScale(alpha, peak);
        const focus = rack.focus[i] || 0;
        const sharp = focus > 0 ? sharpSprites[i] : null;
        const layers = focusAlphas(focus, Math.min(1, peak) * dim, Math.min(1, peak * SHARP_GAIN) * dim);
        const half = drawHalf(geom, radius, scale);
        ctx.globalAlpha = dim * (sharp ? layers.soft : 1) * strength;
        ctx.drawImage(sprite, x - half, y - half, half * 2, half * 2);
        if (sharp && sharpGeom[i]) {
          const sh = drawHalf(sharpGeom[i], radius * SHARP_RADIUS, scale);
          ctx.globalAlpha = dim * layers.sharp * strength;
          ctx.drawImage(sharp, x - sh, y - sh, sh * 2, sh * 2);
        }
      });
    }

    function drawFirefly(ctx, w, h, scale, light) {
      const f = firefly;
      if (!f) return;
      const s = Math.min(w, h), t = f.age / f.life;
      const env = smoothstep(Math.min(1, t / 0.3)) * smoothstep(Math.min(1, (1 - t) / 0.5))
                * (1 + 0.15 * Math.sin(t * Math.PI * 3 + f.breathPhase));
      if (env <= 0) return;
      const travel = FIREFLY_TRAVEL * smoothstep(t), bow = FIREFLY_BOW * Math.sin(t * Math.PI);
      const x = f.x0 * w + (f.dirX * travel + f.perpX * bow) * s;
      const y = f.y0 * h + (f.dirY * travel + f.perpY * bow) * s;
      const radius = f.size * s;
      // Baked once per spawn (and per geometry or page-lightness change). On a light page the core
      // takes the mote's own colour: warm white multiplied over white is nothing.
      const key = `${bakeKey(s, scale, BOKEH_BLUR_CSS)}|${light}`;
      if (f.key !== key) {
        f.geom = orbSprite(radius, BOKEH_BLUR_CSS, scale);
        f.sprite = bakeOrb(f.geom, f.color, FIREFLY_PEAK, [0.35, 0.7], light ? f.color : FIREFLY_CORE);
        f.key = key;
      }
      if (!f.sprite) return;
      const half = drawHalf(f.geom, radius, scale);
      ctx.globalCompositeOperation = light ? "multiply" : "lighter";
      ctx.globalAlpha = alphaScale(FIREFLY_ALPHA * env, FIREFLY_PEAK) * strength;
      ctx.drawImage(f.sprite, x - half, y - half, half * 2, half * 2);
    }

    // Reduced motion's rest frame: a soft gradient of the two dominant swatches (kamp's).
    function drawStatic(ctx, w, h, blend) {
      const colors = fade.current();
      const a = colors[0] || {r: 40, g: 40, b: 48}, b = colors[1] || a;
      const alpha = STATIC_ALPHA * strength;
      const grad = ctx.createLinearGradient(0, 0, w, h);
      grad.addColorStop(0, rgba(a, alpha));
      grad.addColorStop(1, rgba(b, alpha));
      ctx.globalCompositeOperation = blend;
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }

    const s = {
      orbs, rack, pace,
      get bloomAge() { return bloomAge; },
      get firefly() { return firefly; },
      pop() { firefly = null; spawnFirefly(); },
      onStart() { bloomAge = BLOOM_DONE; },  // a bloom is "this just happened": it never fires late
      draw(ctx, frame) {
        const {w, h, scale, light} = frame;
        cssW = w;
        cssH = h;
        elapsed = frame.elapsed;
        const blend = light ? "multiply" : "screen";
        if (frame.atRest) return drawStatic(ctx, w, h, blend);
        if (!frame.still) {
          drawnLive = true;
          tick(clampDt(frame.dt, TICK_MAX_S));
        }
        drawOrbs(ctx, w, h, scale, blend);
        drawFirefly(ctx, w, h, scale, light);
      },
      // A palette from the covers. Before the first live frame (a chart's, set right after mount)
      // it lands with no bloom; after, it crossfades in 1 s while running (and lands when stopped),
      // with a bloom at most once per BLOOM_PACE_S. Re-armed mid-bloom, it rises from the peak.
      setPalette(colors, running) {
        art = true;
        const live = running && drawnLive;
        fade.set(colors, live);
        if (!live || !pace.request(elapsed)) return;
        bloomAge = bloomAge < BLOOM_DONE ? BLOOM_ATTACK * BLOOM_DURATION : 0;
      },
      setColors({accent: nextAccent}, running) {
        if (nextAccent) accent = nextAccent;
        if (!art) fade.set(accentPalette(accent).colors, running && drawnLive);
      },
      setStrength(v) { strength = v; },
      dispose() {
        sprites = []; spriteGeom = []; sharpSprites = []; sharpGeom = [];
        firefly = null;
        if (latest === s) latest = null;
      },
    };
    latest = s;
    return s;
  }

  const SPEC = {fps: FPS, resScale: RES_SCALE, maxDpr: MAX_DPR, scene};
  // Run Bokeh on canvas: backgrounds.js's engine contract (runCanvas).
  const mount = (canvas, opts = {}) => runCanvas(canvas, opts, SPEC);
  // A firefly now, on the newest Bokeh: aotyBokeh.pop() in the console, for the by-hand check.
  const pop = () => { if (latest) latest.pop(); };

  const api = {TIERS, ORB_PLAN, PREWARM_S, BLOOM_MAX_DELAY, makeOrb, makeField, advanceField, orbCenter, EDGE_BAND,
               VISIBLE_SHARE, visibleShare, visibleOrbCount, SpriteBakeGate, RACK_GAP, FIRST_RACK, PULL_S,
               HIDDEN_RERACK_S, MAX_SHARP, SOFT_DIM, RackFocus, focusAlphas, BOKEH_BLUR_CSS, blurPad, orbSprite,
               drawHalf, alphaScale, bakeKey, blurBasis, blurredStops, canFilter, bakeOrb, bakeSharp, BLOOM_PACE_S,
               BLOOM_DONE, FOCUSABLE, SPEC, mount, pop};
  root.aotyBokeh = api;
  shared.register("bokeh", api);  // loaded on demand: the page's load("bokeh") waits for this
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
