// "Chapel": three stacked, feathered fields of cover colour that breathe slowly, with dry-brush
// texture and worked edges, a Rothko chapel (AOTY-103). SPDX-License-Identifier: GPL-3.0-or-later
//
// Ported from kamp (github.com/eightyeighteyes/kamp, commit 10fd636) by its author, as plain JS
// with no build step:
//   kamp_ui/src/renderer/src/components/nowplaying/chapel/chapelSpec.ts   the shader and uniforms
//   kamp_ui/src/shared/chapelLayout.ts                                    layout, breath, bloom
//   kamp_ui/src/shared/drunkWalk.ts                                       the hue wander
// It runs on backgrounds.js runShader, at kamp's tuning: 0.33 scale, 15 fps (user decision,
// 2026-11-28: they're the lowest that still look right).
//
// What came across: the fields with their breath, softness wander and hue wander; the seam glows;
// the rare seam bloom; the dither; the re-proportion (7 s) and the 4 s palette crossfade, fired by a
// new palette (a wall burst, a new #1) rather than a track change.
// What didn't: the pointer warming a seam or a field, the art skip, and the --bg band behind the
// title. kamp's seam glows came only from the pointer, so here each seam breathes a faint glow of
// its own (user decision 2026-11-28). Light pages (AOTY-75) get a deeper tone and darkening glows,
// because screen() over white does nothing.
//
// The pure parts (layout, timing) are tested by node (web/test/chapel.test.mjs); the scene is
// checked against a stand-in context there, and the shader by hand. Nothing here may jump or
// strobe: every value moves on a bounded slope, as in kamp.
(root => {
  "use strict";

  // web/src/backgrounds.js: loaded first in a page; required in node (its tests).
  const shared = root.aotyBg || (typeof require === "function" ? require("./backgrounds.js") : null);
  if (!shared) return;
  const {PaletteCrossfade, accentPalette, runShader, DrunkWalk, Pace} = shared;

  const TAU = Math.PI * 2;
  const smoothstep = t => {
    const x = Math.max(0, Math.min(1, t));
    return x * x * (3 - 2 * x);
  };
  const clampDt = (dt, max) => Math.min(Math.max(0, dt), max);

  // ---- layout (chapelLayout.ts) ---------------------------------------------------------------
  const FIELD_COUNT = 3;
  // Fractions of the pane. Rothko-like insets: the fields float on --bg rather than bleeding off
  // the edges, which is what keeps them reading as rectangles.
  const MARGIN_X = 0.06;      // of width, outside the widest possible stagger
  const OFFSET_MAX = 0.08;    // the most a field is offset sideways, of width
  const MARGIN_TOP = 0.07, MARGIN_BOTTOM = 0.07;  // of height
  // Seam gap, of height. The vertical feather equals it: each edge fades over one feather centred
  // on the edge, so neighbouring fades just meet in the seam instead of overlapping into mud.
  const GAP = 0.045;
  const MIN_PANE_PX = 16;     // below this the pane is a sliver: draw nothing

  const BREATH_PERIOD_S = [20, 40];
  const HEIGHT_BREATH = 0.08;  // ± fraction of a field's weight
  const ALPHA_BREATH = 0.15;   // ± fraction of FIELD_ALPHA
  // Field opacity over --bg. Swatches are clamped to L 0.45-0.68 and S >= 0.45
  // (backgrounds.js clampSwatch), so at full opacity they would be louder than the art.
  const FIELD_ALPHA = 0.5;
  const PROPORTION_RANGE = [0.75, 1.35];  // a field's weight relative to the others

  const EMPTY = {fields: [], seams: [], seamX: [], feather: 0, featherX: 0};

  // Stack one field per weight, top to bottom. The total field height is fixed by the pane, so
  // weights only redistribute it: breathing and re-proportioning can never push a field into its
  // neighbour or off the pane. offsets shift each field sideways by a fraction of the width, held
  // to ±OFFSET_MAX; fields are narrow enough that the extreme still leaves MARGIN_X for the feather.
  function layoutFields(w, h, weights, offsets = []) {
    const n = weights.length;
    if (n === 0 || w < MIN_PANE_PX || h < MIN_PANE_PX) return EMPTY;
    const gap = GAP * h;
    const avail = h * (1 - MARGIN_TOP - MARGIN_BOTTOM) - gap * (n - 1);
    const total = weights.reduce((a, b) => a + b, 0);
    if (avail <= 0 || !(total > 0)) return EMPTY;
    const marginX = MARGIN_X * w;
    const fw = w * (1 - 2 * (MARGIN_X + OFFSET_MAX));
    const centredX = (w - fw) / 2;
    const fields = [], seams = [];
    let y = MARGIN_TOP * h;
    for (let i = 0; i < n; i++) {
      const fh = (avail * weights[i]) / total;
      const off = Math.max(-OFFSET_MAX, Math.min(OFFSET_MAX, offsets[i] || 0));
      fields.push({x: centredX + off * w, y, w: fw, h: fh});
      y += fh;
      if (i < n - 1) {
        seams.push(y + gap / 2);
        y += gap;
      }
    }
    const seamX = seams.map((_, i) => {
      const a = fields[i], b = fields[i + 1];
      return {x0: Math.min(a.x, b.x), x1: Math.max(a.x + a.w, b.x + b.w)};
    });
    // The side feather has to fit inside the margin, or it would eat the canvas edge.
    return {fields, seams, seamX, feather: gap, featherX: Math.min(2 * gap, marginX)};
  }

  // ---- breathing ------------------------------------------------------------------------------
  function makeBreaths(rng, n = FIELD_COUNT) {
    const [lo, hi] = BREATH_PERIOD_S;
    return Array.from({length: n}, () => ({period: lo + (hi - lo) * rng(), phase: rng() * TAU}));
  }
  // -1..1: where a field is in its breath at time t.
  const breathAt = (b, t) => Math.sin((t / b.period) * TAU + b.phase);
  // Weights with each field's breath applied; breaths null is the composition at rest.
  function breathedWeights(base, breaths, t) {
    if (!breaths) return base.slice();
    return base.map((w, i) => w * (1 + HEIGHT_BREATH * (breaths[i] ? breathAt(breaths[i], t) : 0)));
  }

  // ---- proportions and offsets ----------------------------------------------------------------
  function randomProportions(rng, n = FIELD_COUNT) {
    const [lo, hi] = PROPORTION_RANGE;
    return Array.from({length: n}, () => lo + (hi - lo) * rng());
  }
  // A fresh sideways offset per field, for tension: each at least 40% of OFFSET_MAX, and never all
  // to one side, so the stack always visibly staggers.
  function randomOffsets(rng, n = FIELD_COUNT) {
    const o = Array.from({length: n}, () => {
      const m = (0.4 + 0.6 * rng()) * OFFSET_MAX;
      return rng() < 0.5 ? -m : m;
    });
    if (n > 1 && (o.every(x => x > 0) || o.every(x => x < 0))) o[1] = -o[1];
    return o;
  }

  // Eases numbers toward a new target, PaletteCrossfade's counterpart, with the same rule (kamp's
  // KAMP-779): nothing ticks a stopped loop, so a retarget while stopped lands at once.
  class ProportionTween {
    constructor(initial, durationMs) {
      this.durationMs = durationMs;
      this.from = this.to = initial;
      this.t = 1;
    }
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
      const k = smoothstep(this.t);
      return this.to.map((v, i) => {
        const f = this.from[i] === undefined ? v : this.from[i];
        return f + (v - f) * k;
      });
    }
  }

  // ---- the re-proportion's pace (new here: kamp re-proportions once per track) -----------------
  // A new palette asks for one; a first visit's wall bursts would keep the fields moving all the
  // time. So at most one per RETARGET_MS, through backgrounds.js Pace. The hue wander is
  // backgrounds.js DrunkWalk (kamp's drunkWalk.ts).
  const RETARGET_MS = 7000;
  class RetargetPace extends Pace {
    constructor(windowS = RETARGET_MS / 1000) { super(windowS); }
  }

  // ---- the rare seam bloom (chapelLayout.ts SeamBloom) ----------------------------------------
  // A luminous warming along one seam. Poisson inter-arrival (a floor plus an exponential draw,
  // soft-capped), so it never feels scheduled. kamp waited for the pointer to be idle; with no
  // pointer here it always is.
  const BLOOM_GAP_FLOOR_S = 90, BLOOM_GAP_MEAN_S = 120, BLOOM_GAP_MAX_S = 300;
  const BLOOM_LIFE_S = [12, 16];
  class SeamBloom {
    constructor(rng) {
      this.rng = rng;
      this.active = null;
      this.nextAt = this.gap();
    }
    gap() {
      const u = Math.max(1e-6, this.rng());
      return Math.min(BLOOM_GAP_MAX_S, BLOOM_GAP_FLOOR_S - BLOOM_GAP_MEAN_S * Math.log(u));
    }
    // elapsed is the animation clock.
    tick(dt, elapsed, seamCount) {
      if (this.active) {
        this.active.age += dt;
        if (this.active.age >= this.active.life) {
          this.active = null;
          this.nextAt = elapsed + this.gap();
        }
        return;
      }
      if (seamCount <= 0 || elapsed < this.nextAt) return;
      const [lo, hi] = BLOOM_LIFE_S;
      this.active = {
        seam: Math.min(seamCount - 1, Math.floor(this.rng() * seamCount)),
        x: 0.25 + 0.5 * this.rng(),  // where along the seam it starts, 0..1 of its span
        age: 0,
        life: lo + (hi - lo) * this.rng(),
      };
    }
    // 0..1 intensity: a slow rise and recession with no onset snap.
    envelope() {
      const a = this.active;
      if (!a) return 0;
      const s = Math.sin(Math.PI * Math.min(1, a.age / a.life));
      return s * s;
    }
    // 0..1 how far it has spread along the seam.
    spread() {
      const a = this.active;
      return a ? smoothstep(Math.min(1, a.age / (a.life * 0.6))) : 0;
    }
    // Drop one in flight (a resume after a stop) and schedule the next.
    cancel(elapsed) {
      if (!this.active) return;
      this.active = null;
      this.nextAt = elapsed + this.gap();
    }
  }

  // ---- the seams' own glow (new here) ---------------------------------------------------------
  // kamp lit a seam only where the pointer warmed it. Here each seam breathes on its own slow
  // cycle between a quarter and the whole of SEAM_AMBIENT, and holds the middle at rest.
  const GLOW_ALPHA = 0.32;    // kamp's peak seam-glow opacity
  const SEAM_AMBIENT = 0.45;  // of GLOW_ALPHA, at the top of its breath
  const seamGlow = breath => GLOW_ALPHA * SEAM_AMBIENT * (0.625 + 0.375 * breath);

  // ---- colours --------------------------------------------------------------------------------
  const luminance = c => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  // One colour per field: the dominant swatches, lightest at the top. Sorted once per palette, not
  // per frame, so the crossfade runs per field and never sweeps a field through an unrelated hue.
  function fieldColors(colors, n = FIELD_COUNT) {
    if (!colors || !colors.length) return [];
    const picked = Array.from({length: n}, (_, i) => colors[i % Math.min(n, colors.length)]);
    return picked.sort((a, b) => luminance(b) - luminance(a));
  }
  const mixRgb = (a, b, t) => ({r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t});

  // ---- the shader (glsl.ts + chapelSpec.ts) ---------------------------------------------------
  const CROSSFADE_MS = 4000;  // slow, so the new palette arrives as the fields re-proportion
  const BLOOM_ALPHA = 0.42;   // peak opacity of the rare bloom
  const GLOW_HALF_H = 1.4;    // glow band half-height, in feathers
  const WARM = {r: 255, g: 222, b: 184};  // glows lean toward warm white
  // Wandering softness: each field's edges drift between these multiples of the feather.
  const SOFT_MIN = 0.35, SOFT_MAX = 1.0;
  const HUE_WANDER_DEG = 10;  // each field's hue drifts this far either side, on its own walk
  const f4 = n => n.toFixed(4);
  const LAST = FIELD_COUNT - 1;

  // GLSL 100 (WebGL 1). kamp's header, noise and dither, inlined with only what's used. Rules kept
  // from kamp: texture coordinates are pane-height units, moved only by a field's x offset, so the
  // breath never re-samples noise frame to frame (nothing but dither() reads uTime); the texture
  // only thins the paint; every feather is floored in backing pixels, so nothing stairs upscaled.
  const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;                      // backing-store size in pixels
uniform float uTime;                    // seconds of animation (frozen while stopped)
uniform vec3 uBg;                       // the page's --bg, 0..1: the canvas is opaque
uniform vec4 uField[${FIELD_COUNT}];    // x0, y0, x1, y1 in backing px, GL y-up
uniform vec3 uFieldCol[${FIELD_COUNT}];
uniform vec2 uFieldA[${FIELD_COUNT}];   // breath -1..1, x offset (px)
uniform float uFieldSoft[${FIELD_COUNT}]; // edge softness, a multiple of the feather
uniform float uFieldHue[${FIELD_COUNT}];  // the field's wandering hue offset, radians
uniform vec2 uFeather;                  // vertical, horizontal (px)
uniform vec4 uSeam[${LAST}];            // y, x0, x1 (px), glow alpha
uniform vec3 uSeamCol[${LAST}];
uniform vec4 uBloom;                    // cx, cy, half-width (px), alpha
uniform float uBloomRy;
uniform vec3 uBloomCol;
uniform float uDim;                     // 0..1, the page's strength for it (AOTY-96)

float hash1(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
// 2D value noise, 0..1, smooth (Hermite-interpolated lattice).
float valueNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash1(i);
  float b = hash1(i + vec2(1.0, 0.0));
  float c = hash1(i + vec2(0.0, 1.0));
  float d = hash1(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
// Dark, slow gradients band in 8-bit output without dither. Call last.
vec3 dither(vec3 col, float t) {
  return col + (hash1(gl_FragCoord.xy + fract(t) * 61.0) - 0.5) / 255.0;
}

const float FIELD_ALPHA = ${f4(FIELD_ALPHA)};
const float ALPHA_BREATH = ${f4(ALPHA_BREATH)};
const vec3 WARM = vec3(${f4(WARM.r / 255)}, ${f4(WARM.g / 255)}, ${f4(WARM.b / 255)});

// Texture, per pane height. Rothko, not ribbons: the outline stays nearly rectangular, the edge's
// irregularity is in its opacity (scumble), and the life is inside the field.
const float TEX_FLOOR = 0.4;       // the thinnest the paint gets (density)
const float WASH_SCALE = 1.3;      // large clouds: layered washes
const vec2 WASH2_SCALE = vec2(2.6, 3.4);
const vec2 STREAK_SCALE = vec2(1.6, 14.0); // dry-brush: long in x, fine in y
const vec2 EDGE_SCALE = vec2(4.0, 6.0); // the outline's slow waver
const float EDGE_AMP = 0.15;       // waver, in feathers
const float SCUMBLE = 0.75;        // how far the streaks break up the feathered edge
const float LIFT = 0.12;           // inner light: the dense middle lifts toward white
// The bleed: each field's colour carries past its edge at low opacity, so it sits in its ground
// instead of on bare --bg (without it the eye invents a dark Mach band at every edge).
const float BLEED = 2.5;           // its width, in feathers
const float BLEED_ALPHA = 0.22;    // its strength, of the field's own opacity
const float GROUND = 0.06;         // --bg tinted this far toward the deepest field
const float SEAM_STREAK = 0.6;     // how far brush streaks break up a seam's glow
const float HUE_SPATIAL = ${f4((4 * Math.PI) / 180)}; // static hue shift across a field
const vec2 SOFT_SCALE = vec2(2.5, 3.0);
const float SOFT_ALONG_MIN = 0.6;
const float SOFT_ALONG_MAX = 1.4;
const float SOFT_REACH = ${f4(SOFT_MAX)} * SOFT_ALONG_MAX; // the widest a feather gets
// Light pages (AOTY-75): a field at half opacity over white reads as pastel, so the paint goes on
// a little thicker and a tone deeper there.
const float LIGHT_ALPHA = 1.25;
const float LIGHT_TONE = 0.8;

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

// Rotate a colour's hue around the grey axis (Rodrigues).
vec3 hueRotate(vec3 c, float a) {
  const vec3 k = vec3(0.57735027);
  float ca = cos(a);
  return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);
}

// Smooth 0..1 across [e - w/2, e + w/2], width floored in backing pixels.
float ramp(float d, float w) {
  float ww = max(w, 3.0);
  return smoothstep(-0.5 * ww, 0.5 * ww, d);
}

// A glow: screen on a dark page (light added); on a light page, where screen() over white does
// nothing, a multiply toward the glow's colour (warm light reads as a warm stain).
vec3 glow(vec3 dst, vec3 src, float a, float light) {
  float k = clamp(a, 0.0, 1.0);
  vec3 lit = mix(dst, src + dst - src * dst, k);
  vec3 stain = mix(dst, dst * src, k);
  return mix(lit, stain, light);
}

void main() {
  vec2 p = gl_FragCoord.xy;
  float H = uRes.y;
  float light = smoothstep(0.35, 0.65, lum(uBg));

  // A coloured ground, faintly: the deepest field (sorted last) over --bg.
  vec3 col = mix(uBg, uFieldCol[${LAST}], GROUND * uDim);
  float fv = uFeather.x;
  float fh = uFeather.y;
  // Each field's edge waver here, in vertical px, so a seam's glow can follow the edges either
  // side of it instead of running ruler-straight between them.
  float wvs[${FIELD_COUNT}];
  for (int i = 0; i < ${FIELD_COUNT}; i++) {
    wvs[i] = 0.0;
    vec4 r = uField[i];
    // Out past the bleed, and as far as a seam glow reaches, so wvs is never 0 under a glow.
    float reach = max(fv, fh) * SOFT_REACH * (1.0 + EDGE_AMP + BLEED) + fv * ${f4(GLOW_HALF_H)};
    if (p.x < r.x - reach || p.x > r.z + reach || p.y < r.y - reach || p.y > r.w + reach) continue;
    float fi = float(i);
    vec2 q = vec2(p.x - uFieldA[i].y, p.y) / H + vec2(fi * 7.31, fi * 3.17);

    float waver = (valueNoise(q * EDGE_SCALE) - 0.5) * 2.0 * EDGE_AMP;
    float wv = waver * fv;
    float wh = waver * fh;
    wvs[i] = wv;
    float soft = uFieldSoft[i] * mix(SOFT_ALONG_MIN, SOFT_ALONG_MAX, valueNoise(q * SOFT_SCALE + 53.0));
    float sv = fv * soft;
    float sh = fh * soft;
    float core = ramp(p.y - r.y + wv, sv) * ramp(r.w - p.y + wv, sv)
               * ramp(p.x - r.x + wh, sh) * ramp(r.z - p.x + wh, sh);
    float bv = sv * BLEED;
    float bh = sh * BLEED;
    float wide = ramp(p.y - r.y + wv + 0.5 * bv, bv) * ramp(r.w - p.y + wv + 0.5 * bv, bv)
               * ramp(p.x - r.x + wh + 0.5 * bh, bh) * ramp(r.z - p.x + wh + 0.5 * bh, bh);
    float cov = core + BLEED_ALPHA * wide * (1.0 - core);
    if (cov <= 0.0) continue;

    // Large cloudy washes and dry-brush streaks; they only thin the paint.
    float wash = valueNoise(q * WASH_SCALE);
    float wash2 = valueNoise(q * WASH2_SCALE + 37.0);
    float streak = valueNoise(q * STREAK_SCALE + 5.0);
    float tex = smoothstep(0.2, 0.8, 0.45 * wash + 0.3 * wash2 + 0.25 * streak);
    float density = mix(TEX_FLOOR, 1.0, tex);
    // Scumbled edge: the streaks break up the feather, more toward the outside.
    cov *= mix(1.0 - SCUMBLE, 1.0, streak) * (1.0 - core) + core;

    float inner = smoothstep(0.55, 1.0, core) * tex;
    float hue = uFieldHue[i] + HUE_SPATIAL * (wash2 - 0.5) * 2.0;
    vec3 base = clamp(hueRotate(uFieldCol[i], hue), 0.0, 1.0);
    base *= mix(1.0, LIGHT_TONE, light);
    vec3 c = mix(base, vec3(1.0), LIFT * inner);

    float a = FIELD_ALPHA * (1.0 + ALPHA_BREATH * uFieldA[i].x) * density
            * mix(1.0, LIGHT_ALPHA, light) * uDim;
    col = mix(col, c, clamp(a * cov, 0.0, 1.0));
  }

  // Seam glows: a band across the union of the two fields, feathered at both ends so a stagger
  // never shows a hard end. Its centre follows the midpoint of the two wavering edges.
  float halfH = max(fv * ${f4(GLOW_HALF_H)}, 3.0);
  for (int j = 0; j < ${LAST}; j++) {
    vec4 s = uSeam[j];
    if (s.w <= 0.0) continue;
    // GL y-up: field j's lower edge sits at r.y - wv, field j+1's upper at r.w + wv.
    float y = s.x + 0.5 * (wvs[j + 1] - wvs[j]);
    float v = max(0.0, 1.0 - abs(p.y - y) / halfH);
    if (v <= 0.0) continue;
    float h = ramp(p.x - s.y, fh) * ramp(s.z - p.x, fh);
    float streak = valueNoise(p / H * STREAK_SCALE + 71.0);
    float tx = mix(1.0 - SEAM_STREAK, 1.0, streak);
    col = glow(col, uSeamCol[j], s.w * v * h * tx, light);
  }

  // The rare bloom: an ellipse along its seam, warm white at the core.
  if (uBloom.w > 0.0) {
    vec2 d = (p - uBloom.xy) / vec2(max(uBloom.z, 1.0), max(uBloomRy, 1.0));
    float rr = length(d);
    if (rr < 1.0) {
      vec3 c = rr < 0.45 ? mix(WARM, uBloomCol, rr / 0.45) : uBloomCol;
      float a = rr < 0.45 ? mix(1.0, 0.6, rr / 0.45) : mix(0.6, 0.0, (rr - 0.45) / 0.55);
      col = glow(col, c, uBloom.w * a, light);
    }
  }

  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`;
  // Every uniform FRAG declares: uRes, uTime and uBg by runShader, the rest by the scene's draw()
  // (web/test/chapel.test.mjs checks both, statically and on a stand-in context).
  const UNIFORMS = ["uRes", "uTime", "uBg", "uField", "uFieldCol", "uFieldA", "uFieldSoft", "uFieldHue",
                    "uFeather", "uSeam", "uSeamCol", "uBloom", "uBloomRy", "uBloomCol", "uDim"];

  // ---- the scene, on backgrounds.js runShader -------------------------------------------------
  const RES_SCALE = 0.33;  // kamp's measured budget (TUNING.md)
  const FPS = 15;          // motion periods are 20-40 s; more frames buy nothing visible
  const MAX_DT = 0.25;     // a stalled frame mustn't jump a tween

  let latest = null;  // the newest scene, for pop() (a by-hand check: blooms come every 90-300 s)

  function scene({accent: initialAccent, rng = Math.random} = {}) {
    let accent = initialAccent;
    let art = false;        // a palette from the covers has arrived
    let drawnLive = false;  // a live frame has drawn: before it, a palette lands (mount's)
    let strength = 1, lastT = 0, forceBloom = false;
    const fade = new PaletteCrossfade(fieldColors(accentPalette(accent).colors), CROSSFADE_MS);
    const proportions = new ProportionTween(randomProportions(rng), RETARGET_MS);
    const offsets = new ProportionTween(randomOffsets(rng), RETARGET_MS);
    const breaths = makeBreaths(rng);
    const blurs = makeBreaths(rng);  // each field's wandering softness, on its own slow cycle
    const seamBreaths = makeBreaths(rng, LAST);
    const hues = Array.from({length: FIELD_COUNT}, () => new DrunkWalk(rng, HUE_WANDER_DEG));
    const bloom = new SeamBloom(rng);
    const pace = new RetargetPace();

    const fieldBuf = new Float32Array(FIELD_COUNT * 4), fieldColBuf = new Float32Array(FIELD_COUNT * 3);
    const fieldABuf = new Float32Array(FIELD_COUNT * 2), fieldSoftBuf = new Float32Array(FIELD_COUNT);
    const fieldHueBuf = new Float32Array(FIELD_COUNT);
    const seamBuf = new Float32Array(LAST * 4), seamColBuf = new Float32Array(LAST * 3);

    function reproportion(animate) {
      proportions.set(randomProportions(rng), animate);
      offsets.set(randomOffsets(rng), animate);
    }

    const s = {
      bloom, pace, proportions, offsets,
      pop() { forceBloom = true; },
      onStart() { bloom.cancel(lastT); },  // nothing ticked while stopped: a stale bloom mustn't replay
      draw(gl, quad, frame) {
        const W = frame.w, H = frame.h, t = frame.elapsed, live = !frame.still, rest = frame.atRest;
        if (live) {
          drawnLive = true;
          const dt = clampDt(frame.dt, MAX_DT);
          fade.tick(dt * 1000);
          proportions.tick(dt * 1000);
          offsets.tick(dt * 1000);
          for (const h of hues) h.tick(dt);
          if (pace.due(t)) reproportion(true);
        }
        lastT = t;

        const layout = layoutFields(W, H, breathedWeights(proportions.current(), rest ? null : breaths, t),
                                    offsets.current());
        const {fields, seams, seamX, feather, featherX} = layout;
        if (live) {
          if (forceBloom && seams.length) {
            forceBloom = false;
            bloom.tick(0, Math.max(t, bloom.nextAt), seams.length);
          } else bloom.tick(clampDt(frame.dt, MAX_DT), t, seams.length);
        }

        const colors = fade.current();
        const colorAt = i => colors[i % Math.max(1, colors.length)] || WARM;
        for (let i = 0; i < FIELD_COUNT; i++) {
          const r = fields[i];
          // DOM y-down to GL y-up.
          fieldBuf.set(r ? [r.x, H - (r.y + r.h), r.x + r.w, H - r.y] : [0, 0, 0, 0], i * 4);
          const c = colorAt(i);
          fieldColBuf.set([c.r / 255, c.g / 255, c.b / 255], i * 3);
          fieldABuf.set([rest ? 0 : breathAt(breaths[i], t), r ? r.x - (W - r.w) / 2 : 0], i * 2);
          // The rest frame holds the middle of the softness range, and the selected colour itself.
          const blur = rest ? 0 : breathAt(blurs[i], t);
          fieldSoftBuf[i] = SOFT_MIN + (SOFT_MAX - SOFT_MIN) * (0.5 + 0.5 * blur);
          fieldHueBuf[i] = rest ? 0 : (hues[i].value * Math.PI) / 180;
        }
        for (let j = 0; j < LAST; j++) {
          const c = mixRgb(mixRgb(colorAt(j), colorAt(j + 1), 0.5), WARM, 0.55);
          const span = seamX[j];
          const a = seamGlow(rest ? 0 : breathAt(seamBreaths[j], t)) * strength;
          seamBuf.set(span ? [H - seams[j], span.x0, span.x1, a] : [0, 0, 0, 0], j * 4);
          seamColBuf.set([c.r / 255, c.g / 255, c.b / 255], j * 3);
        }
        gl.uniform4fv(quad.u("uField[0]"), fieldBuf);
        gl.uniform3fv(quad.u("uFieldCol[0]"), fieldColBuf);
        gl.uniform2fv(quad.u("uFieldA[0]"), fieldABuf);
        gl.uniform1fv(quad.u("uFieldSoft[0]"), fieldSoftBuf);
        gl.uniform1fv(quad.u("uFieldHue[0]"), fieldHueBuf);
        gl.uniform2f(quad.u("uFeather"), feather, featherX);
        gl.uniform4fv(quad.u("uSeam[0]"), seamBuf);
        gl.uniform3fv(quad.u("uSeamCol[0]"), seamColBuf);
        gl.uniform1f(quad.u("uDim"), strength);

        // The bloom, across its seam's whole span. A still frame never shows one.
        const b = live ? bloom.active : null;
        const span = b ? seamX[b.seam] : null;
        if (b && span) {
          const width = span.x1 - span.x0;
          const c = mixRgb(mixRgb(colorAt(b.seam), colorAt(b.seam + 1), 0.5), WARM, 0.55);
          gl.uniform4f(quad.u("uBloom"), span.x0 + b.x * width, H - seams[b.seam],
                       Math.max(1, bloom.spread() * width * 0.75), BLOOM_ALPHA * bloom.envelope() * strength);
          gl.uniform3f(quad.u("uBloomCol"), c.r / 255, c.g / 255, c.b / 255);
        } else {
          gl.uniform4f(quad.u("uBloom"), 0, 0, 0, 0);
          gl.uniform3f(quad.u("uBloomCol"), 0, 0, 0);
        }
        gl.uniform1f(quad.u("uBloomRy"), feather * GLOW_HALF_H * 1.3);
      },
      // A palette from the covers. Before the first live frame (a chart's, set right after mount)
      // it lands; after, it crossfades over 4 s while running and lands when stopped. Every one but
      // the first asks for a re-proportion, paced by RetargetPace, or landed at once when stopped.
      setPalette(colors, running) {
        const first = !art;
        art = true;
        fade.set(fieldColors(colors), running && drawnLive);
        if (first) return;
        if (!running) reproportion(false);
        else if (pace.request(lastT)) reproportion(true);
      },
      setColors({accent: nextAccent}, running) {
        if (nextAccent) accent = nextAccent;
        if (!art) fade.set(fieldColors(accentPalette(accent).colors), running && drawnLive);
      },
      setStrength(v) { strength = v; },
    };
    latest = s;
    return s;
  }

  const SPEC = {frag: FRAG, fps: FPS, resScale: RES_SCALE, scene};
  // Run Chapel on canvas: backgrounds.js's engine contract (runShader).
  const mount = (canvas, opts = {}) => runShader(canvas, opts, SPEC);
  // A bloom now, on the newest Chapel: aotyChapel.pop() in the console, for the by-hand check.
  const pop = () => { if (latest) latest.pop(); };

  const api = {FIELD_COUNT, OFFSET_MAX, RETARGET_MS, CROSSFADE_MS, GLOW_ALPHA, layoutFields, makeBreaths,
               breathAt, breathedWeights, randomProportions, randomOffsets, ProportionTween, RetargetPace,
               DrunkWalk, SeamBloom, seamGlow, fieldColors, FRAG, UNIFORMS, SPEC, mount, pop};
  root.aotyChapel = api;
  shared.register("chapel", api);  // loaded on demand: the page's load("chapel") waits for this
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
