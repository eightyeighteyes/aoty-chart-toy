// "Borealis": slow aurora curtains in the visitor's cover colours, behind the first-visit
// (chart-building) screen (AOTY-67). SPDX-License-Identifier: GPL-3.0-or-later
//
// Ported from kamp (github.com/eightyeighteyes/kamp, commit dfd8033) by its author, as plain JS
// with no build step:
//   kamp_ui/src/renderer/src/components/nowplaying/borealis/borealisSpec.ts   the shader
//   kamp_ui/src/renderer/src/components/nowplaying/webgl/glsl.ts               header, noise
//   kamp_ui/src/renderer/src/components/nowplaying/webgl/shaderEngine.ts      the engine (trimmed)
//   kamp_ui/src/shared/{borealisTiming,rareSchedule}.ts
// The palette code and the program setup it shares with the other backgrounds are in
// web/src/backgrounds.js (AOTY-77), and so is its loop (runShader, AOTY-103): this file is the scene.
// What came across (user decision 2026-11-25): the flow, the palette crossfade, and the surge band,
// which here rolls across when a new wall cover shifts the palette. Not kamp's cursor lean, rare
// edge flicker, title mask or art-shown layout: there's no art pane, so the curtains always sit
// low and tall, as kamp draws them with the art hidden.
//
// The pure parts (timing) are tested by node (web/test/borealis.test.mjs); mount() is the
// browser half. Nothing here may jump or strobe: every value moves on a bounded slope, as in kamp.
(root => {
  "use strict";

  // web/src/backgrounds.js: loaded first in a page; required in node (its tests).
  const shared = root.aotyBg || (typeof require === "function" ? require("./backgrounds.js") : null);
  if (!shared) return;
  const {PALETTE_SIZE, PaletteCrossfade, accentPalette, packPalette, runShader} = shared;

  const TAU = Math.PI * 2;
  const clampDt = (dt, max) => Math.min(Math.max(0, dt), max);
  const smoothstep = t => {
    const x = Math.max(0, Math.min(1, t));
    return x * x * (3 - 2 * x);
  };

  // ---- flow (borealisTiming.ts) ---------------------------------------------------------------
  // The curtains' noise is sampled along two circles in noise space, one per period: bounded, so
  // float32 never sees a large coordinate, and closed, so the wrap is seamless. Two coprime periods
  // repeat only every ~3.8 h.
  const FLOW_PERIODS_S = [97, 143];
  function flowPhase(t) {
    const angle = p => (TAU * (((t % p) + p) % p)) / p;
    return [angle(FLOW_PERIODS_S[0]), angle(FLOW_PERIODS_S[1])];
  }

  // ---- the surge band (borealisTiming.ts SurgeEnvelope) ---------------------------------------
  // A brighter band rolling along the curtains while a new palette crossfades in (the crossfade is
  // 1 s, which is also RISE_S, so the band peaks as the colours land). Two slots, so a second
  // trigger never moves a visible band: early on it brightens in place; past halfway a second band
  // rolls in from zero while the first finishes.
  const RISE_S = 1, HOLD_S = 0.4, DECAY_S = 3, SWEEP_S = RISE_S + HOLD_S + DECAY_S;
  const SURGE_MAX_DT = 0.1, BOOST_UNTIL = 0.5;
  const idleBand = () => ({position: 0, value: 0, dir: 0, envAge: Infinity, from: 0});
  function surgeAt(age, from) {
    if (age < RISE_S) return from + (1 - from) * smoothstep(age / RISE_S);
    if (age < RISE_S + HOLD_S) return 1;
    if (age < SWEEP_S) return 1 - smoothstep((age - RISE_S - HOLD_S) / DECAY_S);
    return 0;
  }
  class SurgeEnvelope {
    constructor() {
      this.bands = [idleBand(), idleBand()];
      this.newest = 0;
    }
    get active() { return this.bands.some(b => b.envAge !== Infinity); }
    trigger(dir) {
      const n = this.bands[this.newest];
      if (n.envAge !== Infinity && n.position < BOOST_UNTIL) return this.boost(n);
      const free = this.bands.findIndex(b => b.envAge === Infinity);
      if (free < 0) return this.boost(n);  // both busy: brighten the newest where it is
      Object.assign(this.bands[free], {position: 0, value: 0, dir, from: 0, envAge: 0});
      this.newest = free;
    }
    tick(dt) {
      const step = clampDt(dt, SURGE_MAX_DT);
      for (const b of this.bands) {
        if (b.envAge === Infinity) continue;
        b.envAge += step;
        b.position = Math.min(1, b.position + step / SWEEP_S);
        b.value = surgeAt(b.envAge, b.from);
        if (b.value === 0 && b.envAge > RISE_S) b.envAge = Infinity;
      }
    }
    cancel() { for (const b of this.bands) Object.assign(b, idleBand()); }
    boost(b) {
      b.from = b.value;
      b.envAge = 0;
    }
  }

  // ---- the shader (glsl.ts + borealisSpec.ts) ------------------------------------------------
  // GLSL 100 (WebGL 1) for the widest driver support. kamp's lean, flicker, title mask and
  // art-shown mix are gone; uDim scales the curtains for the text over them.
  const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;        // backing-store size in pixels
uniform float uTime;      // seconds of animation (frozen while stopped)
uniform vec3 uPalette[5]; // the covers' palette, 0..1, crossfaded on the CPU
uniform vec3 uBg;         // the page's --bg, 0..1: the canvas is opaque, so it paints it
uniform vec4 uFlow;       // cos/sin of the two flow angles
uniform vec3 uSurgeA;     // position 0..1, value 0..1, dir (0 L->R, 1 R->L)
uniform vec3 uSurgeB;
uniform float uDim;       // 0..1, the curtains' overall strength

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

const int CURTAINS = 3;
const float ALPHA = 0.45;      // peak curtain opacity; leaves headroom for the surge
const float SURGE = 0.55;      // surge brightness gain
const float EDGE_W = 0.018;    // lower-edge glow thickness
const float RAY_H = 0.30;      // ray fade height above the edge
const float WAVE = 0.075;      // lower-edge wave amplitude
const float FLOW_R = 1.6;      // flow circle radius, noise units

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

float surgeBand(vec3 s, float x) {
  if (s.y <= 0.0) return 0.0;
  float bx = s.z < 0.5 ? mix(-0.25, 1.25, s.x) : mix(1.25, -0.25, s.x);
  float d = (x - bx) / 0.18;
  return s.y * exp(-d * d);
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float X = uv.x * uRes.x / uRes.y;   // pane-height units
  vec2 c0 = uFlow.xy * FLOW_R;
  vec2 c1 = uFlow.zw * FLOW_R;
  float surge = surgeBand(uSurgeA, uv.x) + surgeBand(uSurgeB, uv.x);

  // A light background takes a deeper tone at lower opacity: coloured light, not grey haze.
  float light = smoothstep(0.35, 0.65, lum(uBg));

  vec3 col = uBg;
  // A faint wash of the deepest palette colour toward the top: night sky, not flat.
  col = mix(col, uPalette[4] * mix(0.45, 1.0, light), 0.06 * uv.y * (1.0 - 0.5 * light));

  for (int i = 0; i < CURTAINS; i++) {
    float fi = float(i);
    // kamp's art-hidden layout: low, tall curtains with the pane to themselves.
    float base = 0.24 + 0.15 * fi;
    float rayH = RAY_H * 1.5 * (0.8 + 0.2 * fi);

    float e1 = valueNoise(vec2(X * 1.3 + fi * 7.1, fi * 2.3) + c0) - 0.5;
    float e2 = valueNoise(vec2(X * 3.1 + fi * 3.7, fi * 5.9) + c1 * 1.3) - 0.5;
    float edge = base + WAVE * 2.0 * e1 + 0.35 * WAVE * 2.0 * e2;
    float h = uv.y - edge;

    // Lower edge: a thin bright glow; above it, rays fading upward. Never sub-pixel, and the rays
    // ramp in across the edge, or the quarter-resolution upscale shows stairs.
    float ew = max(EDGE_W, 2.0 / uRes.y);
    float below = h < 0.0 ? h / ew : 0.0;
    float glow = exp(-below * below) * (h < 0.0 ? 1.0 : exp(-h / (ew * 2.5)));
    float ramp = max(EDGE_W, 3.0 / uRes.y);
    float rays = smoothstep(-ramp, ramp, h) * exp(-max(h, 0.0) / rayH);
    float stri = 0.45 + 0.35 * valueNoise(vec2(X * 6.0 + fi * 11.0, 0.0) + c1)
                      + 0.20 * valueNoise(vec2(X * 14.0 + fi * 5.0, 3.0) + c0 * 1.7);
    // Curtains come and go along their length, never the full width at once.
    float span = 0.35 + 0.65 * smoothstep(0.25, 0.65,
                   valueNoise(vec2(X * 0.9 + fi * 13.0, 0.5) + c0 * 0.7));

    float inten = (0.75 * glow + 0.6 * rays * stri) * span;
    inten *= 1.0 + SURGE * surge;

    // Colour runs from this curtain's palette entry at the edge to the next at the ray tips; on a
    // dark page the edge lifts toward white, like a real aurora's bright fringe.
    vec3 hue = mix(uPalette[i], uPalette[i + 1], smoothstep(0.0, rayH, h));
    vec3 darkTint = mix(hue, vec3(1.0), 0.12 + 0.30 * glow + 0.15 * surge);
    vec3 lightTint = hue * 0.72;
    vec3 tint = mix(darkTint, lightTint, light);
    float a = clamp(inten * ALPHA * uDim * mix(1.0, 0.75, light), 0.0, 0.85);
    col = mix(col, tint, a);
  }

  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`;
  // Every uniform FRAG declares: uRes, uTime and uBg by runShader, the rest by the scene's draw()
  // (web/test/borealis.test.mjs checks both).
  const UNIFORMS = ["uRes", "uTime", "uPalette", "uBg", "uFlow", "uSurgeA", "uSurgeB", "uDim"];

  // ---- the scene, on backgrounds.js runShader (AOTY-103) -------------------------------------
  const RES_SCALE = 0.25;  // soft curtains survive the quarter-resolution upscale (kamp's budget)
  const FPS = 30;
  const STILL_PHASE = [1.9, 4.4];  // the reduced-motion still's curtains
  const CROSSFADE_MS = 1000;
  const DIM = 0.85;  // a little under kamp's strength: text sits over these curtains (AOTY-67)

  // The curtains' own state: their palette fade, the surge, their strength.
  function scene({accent: initialAccent}) {
    let accent = initialAccent;
    let art = null;  // the covers' palette, once there is one
    const fade = new PaletteCrossfade(accentPalette(accent).colors, CROSSFADE_MS);
    const surge = new SurgeEnvelope();
    const packed = new Float32Array(PALETTE_SIZE * 3);
    let strength = 1;  // setStrength (AOTY-96): calmer behind a built chart

    return {
      surge,
      onStart() { surge.cancel(); },  // nothing ticked while stopped: a half-finished surge mustn't replay
      draw(gl, quad, frame) {
        const still = frame.still;
        if (!still) {
          fade.tick(frame.dt * 1000);
          surge.tick(frame.dt);
        }
        const [p0, p1] = still ? STILL_PHASE : flowPhase(frame.elapsed);
        const [a, b] = surge.bands;
        gl.uniform3fv(quad.u("uPalette"), packPalette(fade.current(), packed));
        gl.uniform4f(quad.u("uFlow"), Math.cos(p0), Math.sin(p0), Math.cos(p1), Math.sin(p1));
        gl.uniform3f(quad.u("uSurgeA"), still ? 0 : a.position, still ? 0 : a.value, a.dir);
        gl.uniform3f(quad.u("uSurgeB"), still ? 0 : b.position, still ? 0 : b.value, b.dir);
        gl.uniform1f(quad.u("uDim"), DIM * strength);
      },
      // A new palette crossfades in with a surge while running, and lands directly when stopped.
      setPalette(colors, running) {
        art = colors;
        fade.set(colors, running);
        if (running) surge.trigger(Math.random() < 0.5 ? 0 : 1);
      },
      setColors({accent: nextAccent}, running) {
        if (nextAccent) accent = nextAccent;
        if (!art) fade.set(accentPalette(accent).colors, running);
      },
      setStrength(s) { strength = s; },
    };
  }

  const SPEC = {frag: FRAG, fps: FPS, resScale: RES_SCALE, scene};
  // Run Borealis on canvas: backgrounds.js's engine contract (runShader).
  const mount = (canvas, opts = {}) => runShader(canvas, opts, SPEC);

  const api = {FLOW_PERIODS_S, flowPhase, SurgeEnvelope, FRAG, UNIFORMS, SPEC, mount, STILL_PHASE};
  root.aotyBorealis = api;
  shared.register("borealis", api);  // loaded on demand: the page's load("borealis") waits for this
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
