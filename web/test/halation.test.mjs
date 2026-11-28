// Halation's pure parts and its scene (web/src/halation.js, AOTY-99). Run with:
// node --test web/test/*.test.mjs
// Ported from kamp's halationTiming.test.ts; the jolt and the cursor's idle wait stay behind with
// what they tested. No GLSL compiler here: the shader is checked statically and on a stand-in
// context, and drawn by hand (the PR's manual test plan).
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import test from "node:test";
import {fakeCanvas, fakeGl, fakePage} from "./fakegl.mjs";

const require = createRequire(import.meta.url);
const ha = require("../src/halation.js");
const bg = require("../src/backgrounds.js");
const SRC = readFileSync(new URL("../src/halation.js", import.meta.url), "utf8");
const BG_SRC = readFileSync(new URL("../src/backgrounds.js", import.meta.url), "utf8");
const {BurnEnvelope, RareEvents, SEED_FADE_S, SEED_PERIOD_S, SEED_WRAP, grainMix, seedAt, seedBoundary} = ha;

// Deterministic RNG (mulberry32) so the random parts are reproducible.
function seeded(seed) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const variance = xs => {
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  return xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length;
};

// ---- the grain's seed schedule ------------------------------------------------------------------

test("grain re-seeds every 3-4 s, never per frame", () => {
  const [lo, hi] = SEED_PERIOD_S;
  for (let k = 0; k < 500; k++) {
    const period = seedBoundary(k + 1) - seedBoundary(k);
    assert.ok(period >= lo - 1e-9 && period <= hi + 1e-9, `epoch ${k} lasts ${period}s`);
  }
});

test("the re-seed is a 0.5 s crossfade with no jump in the grain", () => {
  const pixel = seed => (((Math.sin(seed * 12.9898) * 43758.5453) % 1) + 1) % 1;
  const dt = 1 / 60;
  let prev = null;
  for (let t = 0; t < 60; t += dt) {
    const s = seedAt(t);
    assert.ok(s.mix >= 0 && s.mix <= 1);
    const v = grainMix(pixel(s.seedA), pixel(s.seedB), s.mix);
    if (prev != null) {
      // smoothstep's steepest slope is 1.5 / fade; the normalisation amplifies by at most sqrt(2),
      // and a normalised mix spans under 2.
      const bound = (1.5 / SEED_FADE_S) * dt * Math.SQRT2 * 2 + 1e-9;
      assert.ok(Math.abs(v - prev) <= bound, `grain stepped ${Math.abs(v - prev)} at t=${t}`);
    }
    prev = v;
  }
});

test("outside a crossfade both seeds are the same, so A and B swap invisibly", () => {
  for (let k = 1; k < 50; k++) {
    const s = seedAt(seedBoundary(k) + SEED_FADE_S + 0.5);
    assert.equal(s.mix, 0);
    assert.equal(s.seedA, s.seedB);
    const end = seedAt(seedBoundary(k) + SEED_FADE_S - 1e-6);
    assert.ok(end.mix > 0.999);
    assert.equal(end.seedB, s.seedA);
  }
});

test("seeds stay small, hours in, so the GLSL hash keeps its precision", () => {
  for (let t = 0; t < 2000; t += 0.37) {
    const s = seedAt(t);
    assert.ok(s.seedA >= 0 && s.seedA < SEED_WRAP && s.seedB >= 0 && s.seedB < SEED_WRAP);
  }
  const late = seedAt(6 * 3600 + 1.25);
  assert.ok(late.seedA < SEED_WRAP && late.seedB < SEED_WRAP);
});

test("grain contrast stays constant through the crossfade", () => {
  const rng = seeded(7);
  const a = Array.from({length: 20000}, rng);
  const b = Array.from({length: 20000}, rng);
  const base = variance(a);
  for (const m of [0, 0.25, 0.5, 0.75, 1]) {
    const v = variance(a.map((x, i) => grainMix(x, b[i], m)));
    assert.ok(Math.abs(v / base - 1) < 0.05, `variance ratio ${v / base} at mix ${m}`);
  }
  // A plain linear mix sags to about half at the midpoint: the bug this prevents.
  assert.ok(variance(a.map((x, i) => x * 0.5 + b[i] * 0.5)) / base < 0.6);
});

// ---- the burn -----------------------------------------------------------------------------------

test("the burn starts with zero slope, peaks around 2 s and fades away completely", () => {
  const burn = new BurnEnvelope();
  burn.trigger(0);
  burn.tick(1 / 60);
  assert.ok(burn.value < 0.002, `first frame jumped to ${burn.value}`);
  let peakAt = 0, peak = 0;
  for (let t = 1 / 60; t < 8; t += 1 / 60) {
    burn.tick(1 / 60);
    if (burn.value > peak) [peak, peakAt] = [burn.value, t];
  }
  assert.ok(peakAt > 1.6 && peakAt < 2.5, `peak at ${peakAt}s`);
  assert.ok(peak <= BurnEnvelope.PEAK + 1e-9);
  assert.equal(burn.value, 0);
  assert.equal(burn.active, false);
});

test("ten rapid retriggers never flash or pass the peak", () => {
  const burn = new BurnEnvelope();
  const dt = 1 / 60;
  let prev = 0;
  for (let skip = 0; skip < 10; skip++) {
    burn.trigger(skip % 3);
    for (let i = 0; i < 18; i++) {
      burn.tick(dt);
      assert.ok(burn.value <= BurnEnvelope.PEAK + 1e-9);
      assert.ok(Math.abs(burn.value - prev) <= BurnEnvelope.MAX_RATE * dt + 1e-9);
      prev = burn.value;
    }
  }
});

test("a retrigger mid-burn keeps its edge; a fresh burn may pick another", () => {
  const burn = new BurnEnvelope();
  burn.trigger(1);
  for (let i = 0; i < 60; i++) burn.tick(1 / 60);
  burn.trigger(2);
  assert.equal(burn.edge, 1);
  for (let i = 0; i < 600; i++) burn.tick(1 / 60);
  burn.trigger(2);
  assert.equal(burn.edge, 2);
});

test("a huge frame gap is clamped, not jumped; cancel drops a burn in flight", () => {
  const burn = new BurnEnvelope();
  burn.trigger(0);
  burn.tick(1);
  assert.ok(burn.value <= BurnEnvelope.MAX_RATE * BurnEnvelope.MAX_DT + 1e-9);
  for (let i = 0; i < 90; i++) burn.tick(1 / 60);
  burn.cancel();
  assert.equal(burn.value, 0);
  assert.equal(burn.active, false);
  burn.tick(1 / 60);
  assert.equal(burn.value, 0);
});

// ---- the hair -----------------------------------------------------------------------------------

function runEvents(ev, seconds, burning = () => false) {
  const dt = 1 / 20, starts = [];
  let was = false, elapsed = 0;
  for (let t = 0; t < seconds; t += dt) {
    elapsed += dt;
    ev.tick(dt, elapsed, burning(elapsed));
    if (ev.active && !was) starts.push(elapsed);
    was = !!ev.active;
  }
  return starts;
}

test("hairs are rare, every one a hair, spaced at least the gap floor apart", () => {
  const starts = runEvents(new RareEvents(seeded(3)), 3600);
  // A 60 s floor, a 90 s mean tail and a 7-10 s life: about 20 an hour, never more than 54.
  assert.ok(starts.length >= 8 && starts.length <= 54, `${starts.length} hairs in an hour`);
  for (let i = 1; i < starts.length; i++) assert.ok(starts[i] - starts[i - 1] >= RareEvents.GAP_FLOOR_S);
  assert.ok(!/joltOffset|JOLT_|HAIR_CHANCE/.test(SRC), "the jolt is gone");
});

test("no hair starts during a burn", () => {
  assert.equal(runEvents(new RareEvents(seeded(9)), 1200, () => true).length, 0);
});

test("a hair fades in and out from zero, drifting all the way across", () => {
  for (const seed of [1, 2, 3, 4, 5, 6]) {
    const ev = new RareEvents(seeded(seed));
    ev.forceNext();
    ev.tick(0.05, 1000, false);
    assert.ok(ev.active);
    assert.ok(ev.active.life >= 7 && ev.active.life <= 10);
    assert.ok(ev.hairAlpha() < 0.01 && ev.hairProgress() === 0);
    let last = 0;
    while (ev.active) {
      ev.tick(0.05, 1000, false);
      if (ev.active) {
        assert.ok(ev.hairProgress() >= last);
        last = ev.hairProgress();
      }
    }
    assert.ok(last > 0.99);
    assert.equal(ev.hairAlpha(), 0);
    assert.equal(ev.hairProgress(), -1);
  }
});

test("cancel drops a hair in flight and reschedules past the floor", () => {
  const ev = new RareEvents(seeded(11));
  ev.forceNext();
  ev.tick(0.05, 500, false);
  assert.ok(ev.active);
  ev.cancel(500);
  assert.equal(ev.active, null);
  assert.ok(ev.nextAt >= 500 + RareEvents.GAP_FLOOR_S);
});

// ---- drift and wraps ----------------------------------------------------------------------------

test("the dust drift wraps at the field's period and stays small, hours in", () => {
  const span = ha.DUST_TILE * ha.CELL;
  for (const t of [0, 1, 600, 3600, 6 * 3600 + 0.5]) {
    for (const v of ha.dustAt(t)) assert.ok(v >= 0 && v < span, `${v} at ${t}`);
  }
  // Each frame's step is tiny, so the field never jumps except at an exact wrap.
  const a = ha.dustAt(100), b = ha.dustAt(100.05);
  assert.ok(Math.abs(a[1] - b[1]) < 0.001);
});

test("leaks breathe between 0.1 and 1, and the weave stays a hair's breadth", () => {
  for (let t = 0; t < 300; t += 0.7) {
    for (const l of ha.leaksAt(t)) assert.ok(l >= 0.1 - 1e-9 && l <= 1 + 1e-9);
    for (const w of ha.weaveAt(t)) assert.ok(Math.abs(w) <= 0.0011 + 1e-12);
  }
});

// ---- the shader ---------------------------------------------------------------------------------

const declared = () => [...ha.FRAG.matchAll(/^uniform \w+ (\w+)/gm)].map(m => m[1]);

test("every uniform the shader declares is set, and kamp's dropped ones are gone", () => {
  assert.deepEqual([...declared()].sort(), [...ha.UNIFORMS].sort());
  const from = SRC.indexOf("draw(gl, quad, frame) {"), to = SRC.indexOf("setPalette(colors, running) {");
  assert.ok(from >= 0 && to > from, "the scene's draw() and setPalette() must be in halation.js, in that order");
  const loop = BG_SRC.slice(BG_SRC.indexOf("function runShader("), BG_SRC.indexOf("function runCanvas("));
  assert.ok(loop.includes('quad.u("uBg")'));
  const draw = SRC.slice(from, to) + loop;
  for (const name of ha.UNIFORMS) {
    assert.ok(draw.includes(`quad.u("${name}")`) || draw.includes(`quad.u("${name}[0]")`), `${name} is never set`);
  }
  for (const gone of ["uArtShown", "uPointer", "uPointerActive", "titleMask", "uTitle", "uLevel", "quiet", "JOLT"]) {
    assert.ok(!ha.FRAG.includes(gone), `${gone} is still in the shader`);
  }
});

test("the shader indexes arrays only with a constant", () => {
  const body = ha.FRAG.slice(ha.FRAG.indexOf("void main()"));
  const indexes = [...body.matchAll(/\b(\w+)\[([^\]]+)\]/g)].map(m => m[2].trim());
  assert.ok(indexes.length && indexes.every(i => ["0", "1", "2"].includes(i)), indexes.join(", "));
});

test("glint frequencies are whole multiples of the wrap, so it's seamless", () => {
  assert.ok(ha.FRAG.includes(`GLINT_PERIOD = ${ha.GLINT_PERIOD_S.toFixed(1)}`));
  assert.match(ha.FRAG, /float k = 40\.0 \+ floor\(/);
  assert.match(ha.FRAG, /float kw = 40\.0 \+ floor\(/);
});

test("kamp's tuning stands: 0.5 scale, 20 fps", () => {
  assert.equal(ha.SPEC.resScale, 0.5);
  assert.equal(ha.SPEC.fps, 20);
});

// ---- the scene on a stand-in context ------------------------------------------------------------

const RED = {r: 200, g: 40, b: 40}, BLUE = {r: 40, g: 60, b: 200}, GREEN = {r: 40, g: 180, b: 60};

function mounted(opts = {}, fn) {
  const page = fakePage().install();
  try {
    const gl = fakeGl();
    const api = ha.mount(fakeCanvas(gl, 390, 844), {bg: "#0d0c12", accent: "#ff6b4a", ...opts});
    try { return fn({api, gl, page}); } finally { api.destroy(); }
  } finally {
    page.restore();
  }
}

const names = gl => [...gl.set.keys()].map(k => k.replace("[0]", "")).sort();
const palette = gl => [...gl.set.get("uPalette[0]")].slice(0, 3).map(v => Math.round(v * 255));
const burnAt = gl => gl.set.get("uBurn")[0];
const steps = (page, n, ms = 50) => { for (let i = 0; i < n; i++) page.step(ms); };

test("mounted, every uniform is set on the first frame, which is a still", () => mounted({}, ({gl}) => {
  assert.deepEqual(names(gl), [...ha.UNIFORMS].sort());
  assert.equal(gl.draws, 1);
  assert.deepEqual(gl.set.get("uBurn"), [0, 0]);
  assert.equal(gl.set.get("uDim"), 1);
}));

test("a palette set right after mount lands, with no fade and no burn", () => mounted({}, ({api, gl, page}) => {
  api.setPalette([RED, BLUE, GREEN]);
  page.step(16);
  assert.deepEqual(palette(gl), [RED.r, RED.g, RED.b]);
  steps(page, 80);
  assert.equal(burnAt(gl), 0);
}));

test("a first palette that arrives late (a chart's #1) crossfades in without a burn", () => mounted({}, ({api, gl, page}) => {
  steps(page, 40);  // two seconds of live frames on the accent palette
  api.setPalette([RED, BLUE, GREEN]);
  steps(page, 60);
  assert.deepEqual(palette(gl), [RED.r, RED.g, RED.b]);
  let max = 0;
  for (let i = 0; i < 200; i++) { page.step(50); max = Math.max(max, burnAt(gl)); }
  assert.equal(max, 0);
}));

test("later palettes burn at most once a window, and one inside it waits for its end", () => {
  mounted({}, ({api, gl, page}) => {
    api.setPalette([RED, BLUE, GREEN]);
    steps(page, 20);  // 1 s
    const starts = [];
    let was = false;
    // A first visit's wall: a new palette every 400 ms for 70 s.
    for (let i = 0; i < 1400; i++) {
      if (i % 8 === 0) api.setPalette(i % 16 ? [BLUE, GREEN, RED] : [GREEN, RED, BLUE]);
      page.step(50);
      const on = burnAt(gl) > 0;
      if (on && !was) starts.push(i * 0.05);
      was = on;
    }
    // The first palette opened the window at 0 s, so the first burn waits for 30 s, then 60 s.
    assert.equal(starts.length, 2, starts.join(", "));
    assert.ok(starts[0] >= ha.BURN_PACE_S - 1.5 && starts[0] <= ha.BURN_PACE_S + 0.5, String(starts[0]));
    assert.ok(starts[1] - starts[0] >= ha.BURN_PACE_S - 0.1);
  });
});

test("under reduced motion it holds kamp's designed still, and a palette lands at once", () => {
  mounted({reducedMotion: true}, ({api, gl, page}) => {
    assert.equal(page.pending(), 0);
    assert.deepEqual(gl.set.get("uGrain"), [13, 13, 0]);
    assert.deepEqual(gl.set.get("uWeave"), [0, 0]);
    assert.deepEqual(gl.set.get("uHair"), [0, 0, 0]);
    api.setPalette([GREEN, RED, BLUE]);
    assert.deepEqual(palette(gl), [GREEN.r, GREEN.g, GREEN.b]);
    assert.deepEqual(gl.set.get("uBurn"), [0, 0]);
  });
});

test("a theme change while running isn't a rest frame: the grain and weave go on", () => mounted({}, ({api, gl, page}) => {
  steps(page, 60);
  api.setColors({bg: "#ffffff", accent: "#00ff00"});
  page.step(50);
  assert.notDeepEqual(gl.set.get("uWeave"), [0, 0]);
  assert.deepEqual(gl.set.get("uBg"), [1, 1, 1]);
}));

test("setStrength reaches uDim, which the shader puts on every moving layer", () => {
  mounted({reducedMotion: true}, ({api, gl}) => {
    api.setStrength(0.6);
    assert.equal(gl.set.get("uDim"), 0.6);
  });
  const body = ha.FRAG.slice(ha.FRAG.indexOf("void main()"));
  assert.equal((body.match(/uDim/g) || []).length, 8);  // wash, three leaks, burn, grain, glints, hair
});

test("pop() brings a hair; a resume after a hidden tab drops it", () => {
  mounted({}, ({gl, page}) => {
    page.step(50);
    ha.pop();
    steps(page, 60);  // 3 s into a 7-10 s hair
    assert.ok(gl.set.get("uHair")[1] > 0, "the hair shows");
    page.setHidden(true);
    page.setHidden(false);
    steps(page, 2);
    assert.equal(gl.set.get("uHair")[1], 0, "a stale hair doesn't replay");
  });
});

test("it's registered as halation, and without WebGL mount gives up quietly", () => {
  assert.equal(bg.registered("halation"), ha);
  assert.equal(ha.mount({getContext: () => null, addEventListener() {}}), null);
  assert.equal(ha.mount({getContext: () => { throw new Error("blocked"); }}), null);
});
