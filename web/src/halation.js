// "Halation": film grain, projector dust and warm light leaks, the gate of an old projector
// (AOTY-99). SPDX-License-Identifier: GPL-3.0-or-later
//
// Ported from kamp (github.com/eightyeighteyes/kamp, commit dfd8033) by its author, as plain JS
// with no build step:
//   kamp_ui/src/renderer/src/components/nowplaying/halation/halationSpec.ts   the shader and uniforms
//   kamp_ui/src/shared/halationTiming.ts                                      grain seeds, burn, hair
//   kamp_ui/src/shared/rareSchedule.ts                                        poissonGap, clampDt
// It runs on backgrounds.js runShader, at kamp's tuning: 0.5 scale, 20 fps (user decision,
// 2026-11-28: they're the lowest that still look right; grain blocks up below 0.5).
//
// What came across: pixel-locked grain with its variance-normalised seed crossfade, the drifting
// dust glints, the three light leaks, the gate weave, the burn (fired by a new palette rather than
// a track change) and the hair. kamp's art-hidden layout is the only one: there's no art pane, so
// the projection-screen wash and the stronger vignette always show.
// What didn't: the title mask, the cursor brightening glints, and the gate jolt (user decision
// 2026-11-28: a shake reads as the page stuttering). With no cursor, the hair needs no idle wait.
//
// Two rules of this port, not kamp's:
//   * The burn is paced: at most one every BURN_PACE_S, and none for the first palette after mount,
//     whenever it arrives (a chart's #1 cover comes from a CORS load after the first frames). One
//     burn lasts 5.8 s, so a first visit's wall bursts would otherwise keep the page amber.
//   * Light pages (AOTY-75): kamp mixed glints, the wash and the burn's core toward warm white,
//     which vanishes on white, and its vignette greys a white page's corners around the header's
//     solid --bg box. Here those aim at a deeper stain of their colour, the vignette is a quarter,
//     and the grain half, by smoothstep(0.35, 0.65, lum(--bg)), as Chapel does.
//
// Nothing here may strobe: grain is locked to the backing grid (the weave never moves it), every
// envelope moves on a bounded slope, and every colour layer is a mix() toward a target, never an
// add. Time: leaks, weave, drift and envelopes are computed here in float64; the shader's only clock,
// uGlintT, wraps at GLINT_PERIOD_S, and every glint frequency is a whole multiple of its inverse.
// The pure parts are tested by node (web/test/halation.test.mjs); the shader by hand.
(root => {
  "use strict";

  // web/src/backgrounds.js: loaded first in a page; required in node (its tests).
  const shared = root.aotyBg || (typeof require === "function" ? require("./backgrounds.js") : null);
  if (!shared) return;
  const {PALETTE_SIZE, PaletteCrossfade, accentPalette, packPalette, runShader, Pace} = shared;

  const TAU = Math.PI * 2;
  const smoothstep = t => {
    const x = Math.max(0, Math.min(1, t));
    return x * x * (3 - 2 * x);
  };
  const clampDt = (dt, max) => Math.min(Math.max(0, dt), max);
  // Seconds until the next rare moment: a floor plus an exponential tail, capped (rareSchedule.ts).
  const poissonGap = (u, floor, mean, max) => Math.min(max, floor - mean * Math.log(Math.max(1e-6, u)));

  // Deterministic 0..1 from an integer (the epoch index), stable across sessions.
  function hashUnit(i) {
    let h = Math.imul(i | 0, 0x9e3779b1) ^ 0x85ebca6b;
    h = Math.imul(h ^ (h >>> 16), 0x7feb352d);
    h = Math.imul(h ^ (h >>> 15), 0x846ca68b);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  // ---- the grain's seed schedule ----------------------------------------------------------------
  const SEED_PERIOD_S = [3, 4];
  const SEED_FADE_S = 0.5;
  // Seeds are offsets into the GLSL hash; large ones lose float32 precision and stripe.
  const SEED_WRAP = 97;
  const SEED_MID_S = (SEED_PERIOD_S[0] + SEED_PERIOD_S[1]) / 2;
  const SEED_JITTER_S = (SEED_PERIOD_S[1] - SEED_PERIOD_S[0]) / 4;  // ±, so a period spans lo..hi

  // Start of grain epoch k: a fixed grid with a per-epoch jitter, so any time's epoch is found in
  // O(1) and the schedule is a pure function of the clock.
  const seedBoundary = k => k * SEED_MID_S + (hashUnit(k) * 2 - 1) * SEED_JITTER_S;
  const seedOf = k => hashUnit(k + 0x5bd1e995) * SEED_WRAP;

  // {seedA, seedB, mix}: 0 shows seedA, 1 seedB. Outside a crossfade both seeds are equal.
  function seedAt(elapsed) {
    let k = Math.floor(elapsed / SEED_MID_S);
    if (elapsed < seedBoundary(k)) k -= 1;  // the jitter can put elapsed either side of the line
    else if (elapsed >= seedBoundary(k + 1)) k += 1;
    const into = elapsed - seedBoundary(k);
    const current = seedOf(k);
    if (into >= SEED_FADE_S) return {seedA: current, seedB: current, mix: 0};
    return {seedA: seedOf(k - 1), seedB: current, mix: smoothstep(into / SEED_FADE_S)};
  }

  // Crossfade two independent 0..1 noise samples without losing contrast: a plain lerp halves the
  // variance at the midpoint, so the grain would soften every few seconds. Mirrored in grain().
  function grainMix(a, b, m) {
    const wa = 1 - m;
    return (a * wa + b * m - 0.5) / Math.sqrt(wa * wa + m * m) + 0.5;
  }

  // ---- the burn ----------------------------------------------------------------------------------
  const BURN_EDGES = 3;  // 0 left, 1 right, 2 top (kamp kept the bottom for its title)

  class BurnEnvelope {
    constructor() {
      this.value = 0;
      this.edge = 0;
      this.from = 0;
      this.age = Infinity;
    }
    get active() { return this.value > 0 || this.age < BurnEnvelope.RISE_S + BurnEnvelope.HOLD_S; }
    // A new palette. The rise starts from wherever the burn is, and a burn already showing keeps
    // its edge: moving the gradient across the frame would read as a flash.
    trigger(edge) {
      if (this.value <= 0.02) this.edge = edge;
      this.from = this.value;
      this.age = 0;
    }
    tick(dt) {
      if (this.age === Infinity) return;
      this.age += clampDt(dt, BurnEnvelope.MAX_DT);
      this.value = this.at(this.age);
      if (this.value === 0 && this.age > BurnEnvelope.RISE_S) this.age = Infinity;
    }
    cancel() {
      this.value = 0;
      this.from = 0;
      this.age = Infinity;
    }
    at(age) {
      const {RISE_S, HOLD_S, DECAY_S, PEAK} = BurnEnvelope;
      if (age < RISE_S) return this.from + (PEAK - this.from) * smoothstep(age / RISE_S);
      if (age < RISE_S + HOLD_S) return PEAK;
      if (age < RISE_S + HOLD_S + DECAY_S) return PEAK * (1 - smoothstep((age - RISE_S - HOLD_S) / DECAY_S));
      return 0;
    }
  }
  BurnEnvelope.RISE_S = 2;
  BurnEnvelope.HOLD_S = 0.3;
  BurnEnvelope.DECAY_S = 3.5;
  BurnEnvelope.PEAK = 1;
  BurnEnvelope.MAX_RATE = (1.5 * BurnEnvelope.PEAK) / BurnEnvelope.RISE_S;  // smoothstep's steepest
  BurnEnvelope.MAX_DT = 0.1;

  // ---- the hair: kamp's RareEvents with the jolt taken out ------------------------------------
  class RareEvents {
    constructor(rng) {
      this.rng = rng;
      this.active = null;
      this.nextAt = this.gap();
    }
    gap() {
      return poissonGap(this.rng(), RareEvents.GAP_FLOOR_S, RareEvents.GAP_MEAN_S, RareEvents.GAP_MAX_S);
    }
    // Make the next hair due now (aotyHalation.pop(), and tests).
    forceNext() { this.nextAt = -Infinity; }
    // elapsed is the animation clock. No hair starts during a burn.
    tick(dt, elapsed, burning) {
      const step = clampDt(dt, RareEvents.MAX_DT);
      if (this.active) {
        this.active.age += step;
        if (this.active.age >= this.active.life) {
          this.active = null;
          this.nextAt = elapsed + this.gap();
        }
        return;
      }
      if (elapsed < this.nextAt || burning) return;
      const [lo, hi] = RareEvents.HAIR_LIFE_S;
      this.active = {age: 0, life: lo + (hi - lo) * this.rng(), seed: this.rng() * SEED_WRAP};
    }
    // Drop a hair in flight (a resume after a stop) and schedule the next.
    cancel(elapsed) {
      this.active = null;
      this.nextAt = elapsed + this.gap();
    }
    // 0..1 how far the hair has drifted across, or -1 with none.
    hairProgress() { return this.active ? this.active.age / this.active.life : -1; }
    // 0..1 opacity, with zero slope at both ends, so it never pops in or out.
    hairAlpha() {
      if (!this.active) return 0;
      const s = Math.sin(Math.PI * Math.min(1, this.active.age / this.active.life));
      return s * s;
    }
  }
  RareEvents.GAP_FLOOR_S = 60;
  RareEvents.GAP_MEAN_S = 90;
  RareEvents.GAP_MAX_S = 240;
  RareEvents.HAIR_LIFE_S = [7, 10];
  RareEvents.MAX_DT = 0.1;

  // ---- the shader -------------------------------------------------------------------------------
  const GLINT_PERIOD_S = 600;
  const STILL_SEED = 13;      // the reduced-motion still's grain
  const STILL_GLINT_T = 37;   // a glint phase with a few motes lit
  const STILL_LEAK = 0.7;
  const WEAVE = 0.0011;       // gate weave amplitude, fraction of the height
  const LEAK_PERIODS_S = [23, 31, 37];  // incommensurate, so the leaks never pulse together
  const CELL = 0.055;         // dust cell, height units (mirrored in the shader)
  const DUST_TILE = 64;       // the dust field repeats every 64 cells, so its drift can wrap
  // Dust drift, heights per second: a slow rise with a lazy sideways sway, ~2 min to cross.
  const DUST_RISE = 0.008, DUST_SLIDE = 0.003, DUST_SWAY = 0.025, DUST_SWAY_S = 41;

  // GLSL 100 (WebGL 1). kamp's header and dither inlined with only what's used: hash1 is dither's
  // alone, hashNS (sine-free, good at pixel coordinates in the thousands) does the grain and dust.
  // highp where there is one: under mediump the grain hash bands.
  const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;      // backing-store size in pixels
uniform float uTime;    // seconds of animation (frozen while stopped): dither only
uniform vec3 uBg;       // the page's --bg, 0..1: the canvas is opaque
uniform vec3 uPalette[${PALETTE_SIZE}];
uniform vec3 uGrain;    // seedA, seedB, mix
uniform vec2 uWeave;    // gate weave, height units, y up
uniform vec3 uLeak;     // per-leak intensity, 0..1
uniform vec2 uBurn;     // envelope 0..1, edge (0 left, 1 right, 2 top)
uniform vec3 uHair;     // progress 0..1, alpha 0..1, seed
uniform float uGlintT;  // seconds, wrapped at GLINT_PERIOD
uniform vec2 uDust;     // dust field drift, height units, y up, wrapped
uniform float uDim;     // 0..1, the page's strength for it (AOTY-96)

const float GLINT_PERIOD = ${GLINT_PERIOD_S.toFixed(1)};
const float GRAIN = 0.06;        // grain amplitude
const float LEAK = 0.42;         // peak leak opacity
const float BURN = 0.6;          // peak burn opacity
const float CELL = ${CELL.toFixed(3)};
const float DUST_DENSITY = 0.2;  // fraction of cells holding a mote
const float DUST_TILE = ${DUST_TILE.toFixed(1)};
// Per-mote meander, cell units: with motes in 0.4..0.6 of the cell and a 0.3-cell cutoff, 0.1 is
// the most that never clips at the cell edge.
const float MEANDER = 0.1;
const float VIGNETTE = 0.30;     // kamp's art-hidden strength
const vec3 AMBER = vec3(1.0, 0.56, 0.20);
const vec3 WARM_WHITE = vec3(1.0, 0.92, 0.80);

float hash1(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float hashNS(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

// Mirrors grainMix() in halation.js: constant contrast mid-fade.
float grain(vec2 px) {
  float a = hashNS(px + uGrain.x * vec2(37.0, 17.0));
  float b = hashNS(px + uGrain.y * vec2(37.0, 17.0));
  float m = uGrain.z;
  float wa = 1.0 - m;
  return (a * wa + b * m - 0.5) / sqrt(wa * wa + m * m) + 0.5;
}

// Dark, slow gradients band in 8-bit output without dither. Call last.
vec3 dither(vec3 col, float t) {
  return col + (hash1(gl_FragCoord.xy + fract(t) * 61.0) - 0.5) / 255.0;
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float aspect = uRes.x / uRes.y;
  vec2 q = vec2(uv.x * aspect, uv.y);   // height units
  vec2 qw = q + uWeave;                 // the gate weaves the light, not the grain
  vec2 uvw = vec2(qw.x / aspect, qw.y);
  float light = smoothstep(0.35, 0.65, lum(uBg));

  vec3 col = uBg;

  // A softly lit projection screen. On a light page it's a faint stain of the palette instead.
  vec2 sd = abs(uvw - 0.5);
  float screen = (1.0 - smoothstep(0.36, 0.45, sd.x)) * (1.0 - smoothstep(0.33, 0.43, sd.y));
  vec3 wash = mix(mix(WARM_WHITE, uPalette[0], 0.25), mix(uPalette[0], AMBER, 0.25), light);
  col = mix(col, wash, screen * 0.10 * mix(1.0, 0.6, light) * uDim);
  float vig = smoothstep(0.45, 0.95, length(uv - 0.5) * 1.414);
  col *= 1.0 - vig * VIGNETTE * mix(1.0, 0.25, light);

  // Light leaks breathing in from the edges, in album colours warmed toward amber.
  // (GLSL pow() is undefined for a negative base, so square by hand.)
  float y0 = (uvw.y - 0.62) / 0.30;
  float y1 = (uvw.y - 0.35) / 0.34;
  float a0 = (1.0 - smoothstep(0.0, 0.30, uvw.x)) * exp(-y0 * y0);
  float a1 = (1.0 - smoothstep(0.0, 0.26, 1.0 - uvw.x)) * exp(-y1 * y1);
  float a2 = 1.0 - smoothstep(0.0, 0.55, length((1.0 - uvw) * vec2(aspect, 1.0)));
  col = mix(col, mix(uPalette[0], AMBER, 0.35), a0 * a0 * uLeak.x * LEAK * uDim);
  col = mix(col, mix(uPalette[1], AMBER, 0.35), a1 * a1 * uLeak.y * LEAK * uDim);
  col = mix(col, mix(uPalette[2], AMBER, 0.45), a2 * a2 * uLeak.z * LEAK * uDim);

  // The burn: amber spreading in from one edge as the envelope rises. Its core lifts toward warm
  // white on a dark page and deepens toward amber on a light one.
  if (uBurn.x > 0.0) {
    float d = uBurn.y < 0.5 ? uvw.x * aspect : (uBurn.y < 1.5 ? (1.0 - uvw.x) * aspect : 1.0 - uvw.y);
    float reach = 0.10 + 0.45 * uBurn.x;
    vec3 core = mix(WARM_WHITE, AMBER * 0.8, light);
    vec3 bc = mix(mix(AMBER, uPalette[0], 0.2), core, exp(-d / (reach * 0.35)) * 0.5);
    col = mix(col, bc, uBurn.x * exp(-d / reach) * BURN * uDim);
  }

  // Grain, pixel-locked, scaled with the local light; half as strong on a light page, where it
  // would otherwise be at its strongest and read as dirt.
  float g = grain(floor(gl_FragCoord.xy)) - 0.5;
  col += g * GRAIN * (0.6 + 0.8 * lum(col)) * mix(1.0, 0.5, light) * uDim;

  // Dust glints: at most one mote per cell, kept clear of the cell edge so the single-cell lookup
  // never clips it. The field drifts (uDust) and tiles every DUST_TILE cells, so the drift wraps
  // with no jump; each mote meanders a little in its cell. Light on a dark page, a dark speck of
  // the palette on a light one.
  vec2 cq = (qw - uDust) / CELL;
  vec2 cell = floor(cq);
  vec2 ch = mod(cell, DUST_TILE);
  float h = hashNS(ch + 7.31);
  if (h > 1.0 - DUST_DENSITY) {
    // Whole-number kw and k: their periods divide GLINT_PERIOD, so the clock's wrap is seamless.
    float kw = 40.0 + floor(hashNS(ch + 61.9) * 35.0);
    float wt = 6.2831853 * uGlintT / GLINT_PERIOD;
    vec2 wander = MEANDER * vec2(sin(kw * wt + h * 40.0), sin((kw - 13.0) * wt + h * 97.0));
    vec2 pos = 0.4 + 0.2 * vec2(hashNS(ch + 19.7), hashNS(ch + 41.3)) + wander;
    float dist = length(cq - cell - pos) * CELL;
    float rad = max(0.0035, 2.0 / uRes.y);  // never sub-pixel, or it would crawl
    float spot = exp(-(dist * dist) / (rad * rad)) + 0.25 * exp(-dist / (rad * 2.0));
    spot *= 1.0 - smoothstep(0.7 * 0.3 * CELL, 0.3 * CELL, dist);
    float k = 40.0 + floor(hashNS(ch + 3.7) * 50.0);
    float s = sin(3.14159265 * k * uGlintT / GLINT_PERIOD + h * 6.2831853);
    float env = pow(s * s, 3.0);
    vec3 glintC = mix(mix(WARM_WHITE, uPalette[1], 0.2), mix(uPalette[1], AMBER, 0.3) * 0.55, light);
    col = mix(col, glintC, clamp(spot * env * 0.55 * uDim, 0.0, 0.9));
  }

  // A hair caught in the gate, drifting across. At least 1.5 backing px wide so it doesn't crawl;
  // light on dark, dark on light, so it reads on every theme.
  if (uHair.y > 0.001) {
    float seed = uHair.z;
    float x = qw.x - mix(-0.45, aspect + 0.05, uHair.x);
    float len = 0.25 + 0.15 * fract(seed * 0.37);
    float amp = 0.03 + 0.03 * fract(seed * 0.13);
    float f = 8.0 + 6.0 * fract(seed * 0.53);
    float y = 0.25 + 0.5 * fract(seed * 0.71) + amp * sin(x * f + seed);
    float slope = amp * f * cos(x * f + seed);
    float d = abs(qw.y - y) / sqrt(1.0 + slope * slope);
    float w = 1.5 / uRes.y;
    float along = smoothstep(0.0, 0.03, x) * (1.0 - smoothstep(len - 0.03, len, x));
    float hair = (1.0 - smoothstep(w * 0.5, w * 1.5, d)) * along * uHair.y;
    vec3 ink = mix(col + vec3(0.10, 0.085, 0.07), col * 0.35, smoothstep(0.25, 0.5, lum(col)));
    col = mix(col, ink, hair * 0.8 * uDim);
  }

  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`;
  // Every uniform FRAG declares: uRes, uTime and uBg by runShader, the rest by the scene's draw()
  // (web/test/halation.test.mjs checks both, statically and on a stand-in context).
  const UNIFORMS = ["uRes", "uTime", "uBg", "uPalette", "uGrain", "uWeave", "uLeak", "uBurn", "uHair", "uGlintT",
                    "uDust", "uDim"];

  // ---- the scene, on backgrounds.js runShader -------------------------------------------------
  const RES_SCALE = 0.5;   // grain upscales into blocks below this (kamp's TUNING.md)
  const FPS = 20;          // between re-seeds only the glints and leaks move
  const CROSSFADE_MS = 1000;
  const BURN_PACE_S = 30;  // at most one burn per window: one lasts 5.8 s

  let latest = null;  // the newest scene, for pop() (a by-hand check: hairs come every 1-4 min)

  function scene({accent: initialAccent, rng = Math.random} = {}) {
    let accent = initialAccent;
    let art = false;        // a palette from the covers has arrived
    let drawnLive = false;  // a live frame has drawn: before it, a palette lands
    let strength = 1, lastT = 0;
    const fade = new PaletteCrossfade(accentPalette(accent).colors, CROSSFADE_MS);
    const burn = new BurnEnvelope();
    const events = new RareEvents(rng);
    const pace = new Pace(BURN_PACE_S);
    const packed = new Float32Array(PALETTE_SIZE * 3);
    const fire = () => burn.trigger(Math.floor(rng() * BURN_EDGES));

    const s = {
      burn, events, pace,
      pop() { events.forceNext(); },
      // Nothing ticked while stopped: a half-finished burn or hair mustn't replay.
      onStart() {
        burn.cancel();
        events.cancel(lastT);
      },
      draw(gl, quad, frame) {
        const t = frame.elapsed;
        lastT = t;
        if (!frame.still) {
          drawnLive = true;
          fade.tick(frame.dt * 1000);
          if (pace.due(t)) fire();  // a palette that came inside the window gets its burn now
          burn.tick(frame.dt);
          events.tick(frame.dt, t, burn.active);
        }
        gl.uniform3fv(quad.u("uPalette[0]"), packPalette(fade.current(), packed));
        gl.uniform1f(quad.u("uDim"), strength);

        if (frame.atRest) {
          // The designed still: fixed grain, a few motes lit, no moments.
          gl.uniform3f(quad.u("uGrain"), STILL_SEED, STILL_SEED, 0);
          gl.uniform2f(quad.u("uWeave"), 0, 0);
          gl.uniform3f(quad.u("uLeak"), STILL_LEAK, STILL_LEAK, STILL_LEAK);
          gl.uniform2f(quad.u("uBurn"), 0, 0);
          gl.uniform3f(quad.u("uHair"), 0, 0, 0);
          gl.uniform1f(quad.u("uGlintT"), STILL_GLINT_T);
          gl.uniform2f(quad.u("uDust"), 0, 0);
          return;
        }
        const seeds = seedAt(t);
        gl.uniform3f(quad.u("uGrain"), seeds.seedA, seeds.seedB, seeds.mix);
        gl.uniform2f(quad.u("uWeave"), ...weaveAt(t));
        gl.uniform3f(quad.u("uLeak"), ...leaksAt(t));
        gl.uniform2f(quad.u("uBurn"), burn.value, burn.edge);
        gl.uniform3f(quad.u("uHair"), Math.max(0, events.hairProgress()), events.hairAlpha(),
                     events.active ? events.active.seed : 0);
        gl.uniform1f(quad.u("uGlintT"), t % GLINT_PERIOD_S);
        gl.uniform2f(quad.u("uDust"), ...dustAt(t));
      },
      // A palette from the covers. Before the first live frame it lands; after, it crossfades over
      // 1 s while running and lands when stopped. Only palettes after the first bring a burn, paced:
      // the first starts the window, so a chart page never burns on load.
      setPalette(colors, running) {
        const first = !art;
        art = true;
        fade.set(colors, running && drawnLive);
        if (first) {
          pace.request(lastT);
          return;
        }
        if (running && pace.request(lastT)) fire();
      },
      setColors({accent: nextAccent}, running) {
        if (nextAccent) accent = nextAccent;
        if (!art) fade.set(accentPalette(accent).colors, running && drawnLive);
      },
      setStrength(v) { strength = v; },
    };
    latest = s;
    return s;
  }

  // The gate weave at t, in height units: two incommensurate sines per axis.
  const weaveAt = t => [
    WEAVE * (0.6 * Math.sin((TAU * t) / 7.3) + 0.4 * Math.sin((TAU * t) / 3.1)),
    WEAVE * (0.6 * Math.sin((TAU * t) / 5.9 + 1.3) + 0.4 * Math.sin((TAU * t) / 2.7)),
  ];
  const leaksAt = t => LEAK_PERIODS_S.map((p, i) => 0.55 + 0.45 * Math.sin((TAU * t) / p + i * 2.1));
  // The dust field's drift, wrapped at its period, so the shader only ever sees small offsets and
  // the wrap lands on an identical field.
  function dustAt(t) {
    const span = DUST_TILE * CELL;
    const wrap = v => ((v % span) + span) % span;
    return [wrap(DUST_SLIDE * t + DUST_SWAY * Math.sin((TAU * t) / DUST_SWAY_S)), wrap(DUST_RISE * t)];
  }

  const SPEC = {frag: FRAG, fps: FPS, resScale: RES_SCALE, scene};
  // Run Halation on canvas: backgrounds.js's engine contract (runShader).
  const mount = (canvas, opts = {}) => runShader(canvas, opts, SPEC);
  // A hair now, on the newest Halation: aotyHalation.pop() in the console, for the by-hand check.
  const pop = () => { if (latest) latest.pop(); };

  const api = {SEED_PERIOD_S, SEED_FADE_S, SEED_WRAP, BURN_EDGES, BURN_PACE_S, GLINT_PERIOD_S, DUST_TILE, CELL,
               hashUnit, seedBoundary, seedAt, grainMix, poissonGap, BurnEnvelope, RareEvents, weaveAt, leaksAt,
               dustAt, FRAG, UNIFORMS, SPEC, mount, pop};
  root.aotyHalation = api;
  shared.register("halation", api);  // loaded on demand: the page's load("halation") waits for this
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
