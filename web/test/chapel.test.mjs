// Chapel's pure parts and its scene (web/src/chapel.js, AOTY-103). Run with:
// node --test web/test/*.test.mjs
// Ported from kamp's chapelLayout.test.ts and drunkWalk.test.ts (the pointer, warmth and title-band
// tests stay behind with what they tested). No GLSL compiler here: the shader is checked statically
// and on a stand-in context, and drawn by hand (the PR's manual test plan).
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import test from "node:test";
import {fakeCanvas, fakeGl, fakePage} from "./fakegl.mjs";

const require = createRequire(import.meta.url);
const ch = require("../src/chapel.js");
const bg = require("../src/backgrounds.js");
const SRC = readFileSync(new URL("../src/chapel.js", import.meta.url), "utf8");
const BG_SRC = readFileSync(new URL("../src/backgrounds.js", import.meta.url), "utf8");
const {FIELD_COUNT, OFFSET_MAX, layoutFields, makeBreaths, breathedWeights, randomOffsets, randomProportions,
       ProportionTween, RetargetPace, DrunkWalk, SeamBloom, fieldColors} = ch;

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
const sum = xs => xs.reduce((a, b) => a + b, 0);
const close = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

function assertStacked(w, h, weights) {
  const {fields, seams, feather} = layoutFields(w, h, weights);
  assert.equal(fields.length, weights.length);
  assert.equal(seams.length, weights.length - 1);
  for (let i = 0; i < fields.length; i++) {
    assert.ok(fields[i].h > 0, `field ${i} collapsed`);
    if (i > 0) {
      const gap = fields[i].y - (fields[i - 1].y + fields[i - 1].h);
      // A gap of at least one feather keeps neighbouring fades from overlapping into mud.
      assert.ok(gap >= feather - 1e-9, `gap ${gap} narrower than feather ${feather}`);
      assert.ok(seams[i - 1] > fields[i - 1].y + fields[i - 1].h && seams[i - 1] < fields[i].y);
    }
  }
}

test("the module loads in node and in a page alike, and registers itself", () => {
  assert.equal(typeof ch.mount, "function");
  assert.equal(globalThis.aotyChapel, ch);
  assert.equal(bg.registered("chapel"), ch);
});

// ---- layout ---------------------------------------------------------------------------------

test("fields stack top to bottom without overlapping, landscape or a tall phone", () => {
  assertStacked(1200, 800, [1, 1, 1]);
  assertStacked(1200, 800, [0.6, 1.4, 1]);
  assertStacked(390, 844, [0.75, 1.35, 1]);  // a phone, portrait
});

test("field heights sum to the same total whatever the proportions", () => {
  const a = layoutFields(1000, 700, [1, 1, 1]), b = layoutFields(1000, 700, [0.5, 2, 1.3]);
  assert.ok(close(sum(a.fields.map(f => f.h)), sum(b.fields.map(f => f.h)), 1e-6));
});

test("breathing at its extremes keeps the total height and never overlaps", () => {
  const breaths = makeBreaths(seeded(1)), base = [0.7, 1.3, 1];
  const total = sum(layoutFields(1000, 700, base).fields.map(f => f.h));
  for (let t = 0; t < 120; t += 0.37) {
    const w = breathedWeights(base, breaths, t);
    assertStacked(1000, 700, w);
    assert.ok(close(sum(layoutFields(1000, 700, w).fields.map(f => f.h)), total, 1e-6));
  }
});

test("breathing periods fall in the 20-40 second band", () => {
  for (const b of makeBreaths(seeded(7))) assert.ok(b.period >= 20 && b.period <= 40, `period ${b.period}`);
});

test("a still frame (no breath) is the composition at rest", () => {
  const base = [0.8, 1.2, 1];
  assert.deepEqual(breathedWeights(base, null, 123), base);
});

test("without offsets the fields are centred and share one x-range", () => {
  const {fields} = layoutFields(1200, 800, [1, 1, 1]);
  for (const f of fields) {
    assert.equal(f.x, fields[0].x);
    assert.equal(f.w, fields[0].w);
    assert.ok(close(f.x + f.w / 2, 600));
  }
});

test("an offset moves its field by exactly that fraction of the width", () => {
  const centred = layoutFields(1200, 800, [1, 1, 1]).fields;
  const moved = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0]).fields;
  assert.ok(close(moved[0].x - centred[0].x, OFFSET_MAX * 1200));
  assert.ok(close(moved[1].x - centred[1].x, -OFFSET_MAX * 1200));
  assert.ok(close(moved[2].x, centred[2].x));
  assert.equal(moved[0].w, centred[0].w);
});

test("at the extreme offsets no field or its feather leaves the pane, at any aspect", () => {
  for (const [w, h] of [[1200, 800], [2400, 600], [600, 1400], [390, 844], [4000, 120], [120, 4000]]) {
    for (const off of [OFFSET_MAX, -OFFSET_MAX]) {
      const {fields, featherX} = layoutFields(w, h, [1, 1, 1], [off, off, off]);
      for (const f of fields) {
        assert.ok(f.x - featherX / 2 >= -1e-9, `${w}x${h} off ${off}: left ${f.x - featherX / 2}`);
        assert.ok(f.x + f.w + featherX / 2 <= w + 1e-9, `${w}x${h} off ${off}: right`);
      }
    }
  }
});

test("an offset past the range is held at the bound", () => {
  const a = layoutFields(1200, 800, [1, 1, 1], [1, 0, 0]).fields[0];
  const b = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, 0, 0]).fields[0];
  assert.equal(a.x, b.x);
});

test("random offsets stay in range and always stagger the stack", () => {
  const rng = seeded(9);
  for (let i = 0; i < 200; i++) {
    const o = randomOffsets(rng);
    assert.equal(o.length, FIELD_COUNT);
    for (const x of o) assert.ok(Math.abs(x) <= OFFSET_MAX, `offset ${x}`);
    assert.ok(Math.max(...o) - Math.min(...o) >= OFFSET_MAX * 0.6, `spread of ${o}`);
  }
});

test("a seam spans both fields it divides, so its glow covers a stagger", () => {
  const {fields, seamX} = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0]);
  assert.equal(seamX.length, 2);
  assert.ok(close(seamX[0].x0, Math.min(fields[0].x, fields[1].x)));
  assert.ok(close(seamX[0].x1, Math.max(fields[0].x + fields[0].w, fields[1].x + fields[1].w)));
});

test("degenerate panes give no fields rather than negative geometry", () => {
  assert.equal(layoutFields(0, 0, [1, 1, 1]).fields.length, 0);
  assert.equal(layoutFields(800, 0, [1, 1, 1]).fields.length, 0);
  assert.equal(layoutFields(800, 3, [1, 1, 1]).fields.length, 0);
  assertStacked(4000, 120, [1, 1, 1]);
  assertStacked(120, 4000, [1, 1, 1]);
});

// ---- proportions ----------------------------------------------------------------------------

test("random proportions stay within bounds so no field dominates or vanishes", () => {
  const rng = seeded(3);
  for (let i = 0; i < 200; i++) {
    const p = randomProportions(rng);
    assert.equal(p.length, FIELD_COUNT);
    for (const s of p.map(x => x / sum(p))) assert.ok(s > 0.18 && s < 0.5, `share ${s}`);
  }
});

test("a retarget while stopped lands on the new proportions immediately", () => {
  const p = new ProportionTween([1, 1, 1], 6000);
  p.set([2, 1, 1], false);
  assert.deepEqual(p.current(), [2, 1, 1]);
});

test("a retarget while running eases toward the new proportions", () => {
  const p = new ProportionTween([1, 1, 1], 6000);
  p.set([2, 1, 1], true);
  assert.deepEqual(p.current(), [1, 1, 1]);
  p.tick(3000);
  const mid = p.current()[0];
  assert.ok(mid > 1 && mid < 2, `mid ${mid}`);
  p.tick(3000);
  assert.deepEqual(p.current(), [2, 1, 1]);
});

test("a retarget mid-tween continues from what is on screen, without a jump", () => {
  const p = new ProportionTween([1, 1, 1], 6000);
  p.set([2, 1, 1], true);
  p.tick(2000);
  const onScreen = p.current();
  p.set([1, 1, 2], true);
  assert.deepEqual(p.current(), onScreen);
});

// ---- the re-proportion's pace (new here) ------------------------------------------------------

test("re-proportions: the first at once, then at most one per window, the rest folded into one", () => {
  const pace = new RetargetPace(7);
  assert.equal(pace.request(10), true);
  assert.equal(pace.request(10.4), false);  // a wall burst 400 ms later
  assert.equal(pace.request(12), false);
  assert.equal(pace.due(16.9), false);
  assert.equal(pace.due(17), true);   // one, when the window ends
  assert.equal(pace.due(30), false);  // and only one
  assert.equal(pace.request(30), true);
});

test("a steady stream of new palettes moves the fields at most once per RETARGET_MS", () => {
  const pace = new RetargetPace(ch.RETARGET_MS / 1000);
  const fired = [];
  for (let t = 0; t < 60; t += 1 / 15) {
    if (Math.round(t * 15) % 6 === 0 && pace.request(t)) fired.push(t);  // a burst every 0.4 s
    if (pace.due(t)) fired.push(t);
  }
  for (let i = 1; i < fired.length; i++) assert.ok(fired[i] - fired[i - 1] >= 7 - 1e-9, fired.join(", "));
  assert.ok(fired.length >= 8, `${fired.length} retargets in a minute`);
});

// ---- the seams' own glow --------------------------------------------------------------------

test("a seam's glow breathes within a quarter and the whole of its ambient, and rests in between", () => {
  const top = ch.GLOW_ALPHA * 0.45;
  assert.ok(close(ch.seamGlow(1), top));
  assert.ok(close(ch.seamGlow(-1), top / 4));
  assert.ok(ch.seamGlow(0) > top / 4 && ch.seamGlow(0) < top);
  assert.ok(ch.seamGlow(1) < ch.GLOW_ALPHA, "never brighter than kamp's warmed seam");
});

// ---- the hue wander (drunkWalk.test.ts) -----------------------------------------------------

const DT = 1 / 15;  // Chapel's frame cap
function trace(seed, seconds, max = 6) {
  const w = new DrunkWalk(seeded(seed), max), out = [];
  for (let t = 0; t < seconds; t += DT) {
    w.tick(DT);
    out.push(w.value);
  }
  return out;
}

test("the walk starts at the centre and never leaves its bound", () => {
  assert.equal(new DrunkWalk(seeded(1), 6).value, 0);
  for (let s = 1; s <= 20; s++) for (const v of trace(s, 600)) assert.ok(Math.abs(v) <= 6, `seed ${s}: ${v}`);
});

test("it actually wanders: over ten minutes it reaches a third of its bound on both sides", () => {
  for (let s = 1; s <= 10; s++) {
    const v = trace(s, 600);
    assert.ok(Math.max(...v) > 2 && Math.min(...v) < -2, `seed ${s}: ${Math.min(...v)}..${Math.max(...v)}`);
  }
});

test("it moves slowly and smoothly: no frame jumps more than a sliver of its range", () => {
  const v = trace(3, 600);
  for (let i = 1; i < v.length; i++) assert.ok(Math.abs(v[i] - v[i - 1]) < 6 * 0.02, `jump at ${i}`);
});

test("it is a walk, not a cycle: it changes direction irregularly", () => {
  const v = trace(5, 600);
  let turns = 0;
  for (let i = 2; i < v.length; i++) if (Math.sign(v[i] - v[i - 1]) !== Math.sign(v[i - 1] - v[i - 2])) turns++;
  assert.ok(turns > 10, `only ${turns} turns`);
});

test("it is drawn back to the centre: more than half the time within 60% of its bound", () => {
  let near = 0, total = 0;
  for (let s = 1; s <= 10; s++) {
    for (const v of trace(s, 600)) {
      total++;
      if (Math.abs(v) < 6 * 0.6) near++;
    }
  }
  assert.ok(near / total > 0.61, `near the centre ${(near / total).toFixed(2)} of the time`);
});

test("the same seed walks the same path, and a long stall is one small step", () => {
  assert.deepEqual(trace(9, 30), trace(9, 30));
  const w = new DrunkWalk(seeded(2), 6);
  w.tick(30);
  assert.ok(Math.abs(w.value) < 6 * 0.02);
});

// ---- the rare seam bloom --------------------------------------------------------------------

test("the gap before a bloom is bounded and deterministic for a given RNG", () => {
  assert.equal(new SeamBloom(seeded(11)).nextAt, new SeamBloom(seeded(11)).nextAt);
  for (let s = 0; s < 200; s++) {
    const g = new SeamBloom(seeded(s)).nextAt;
    assert.ok(g >= 90 && g <= 300, `gap ${g}`);
  }
});

test("a bloom comes when due, rises and recedes, then schedules the next", () => {
  const bloom = new SeamBloom(seeded(5));
  let t = bloom.nextAt;
  bloom.tick(0, t - 1, 2);
  assert.equal(bloom.active, null);
  bloom.tick(0, t, 2);
  const life = bloom.active.life;
  assert.ok(bloom.active.seam >= 0 && bloom.active.seam < 2);
  assert.equal(bloom.envelope(), 0);
  bloom.tick(life / 2, (t += life / 2), 2);
  assert.ok(bloom.envelope() > 0.5);
  bloom.tick(life, (t += life), 2);
  assert.equal(bloom.active, null);
  assert.ok(bloom.nextAt >= t + 90);
});

test("cancel() drops an in-flight bloom; no seams means no bloom", () => {
  const bloom = new SeamBloom(seeded(5));
  bloom.tick(0, bloom.nextAt, 2);
  bloom.cancel(bloom.nextAt);
  assert.equal(bloom.active, null);
  const none = new SeamBloom(seeded(5));
  none.tick(0, none.nextAt + 1, 0);
  assert.equal(none.active, null);
});

// ---- colours --------------------------------------------------------------------------------

const DARK = {r: 30, g: 30, b: 90}, MID = {r: 120, g: 60, b: 40}, LIGHT = {r: 230, g: 200, b: 120};

test("field colours are the dominant swatches ordered lightest at the top", () => {
  assert.deepEqual(fieldColors([DARK, LIGHT, MID, {r: 255, g: 255, b: 255}]), [LIGHT, MID, DARK]);
});

test("a short palette repeats; an empty one gives none", () => {
  const c = fieldColors([MID]);
  assert.equal(c.length, FIELD_COUNT);
  for (const x of c) assert.deepEqual(x, MID);
  assert.deepEqual(fieldColors([]), []);
});

// ---- the shader -----------------------------------------------------------------------------

const declared = () => [...ch.FRAG.matchAll(/^uniform \w+ (\w+)/gm)].map(m => m[1]);

test("every uniform the shader declares is set, and kamp's dropped ones are gone", () => {
  assert.deepEqual([...declared()].sort(), [...ch.UNIFORMS].sort());
  const from = SRC.indexOf("draw(gl, quad, frame) {"), to = SRC.indexOf("setPalette(colors, running) {");
  assert.ok(from >= 0 && to > from, "the scene's draw() and setPalette() must be in chapel.js, in that order");
  const loop = BG_SRC.slice(BG_SRC.indexOf("function runShader("), BG_SRC.indexOf("function runCanvas("));
  assert.ok(loop.includes('quad.u("uBg")'), "runShader's uniforms must sit before runCanvas");
  const draw = SRC.slice(from, to) + loop;
  for (const name of ch.UNIFORMS) {
    assert.ok(draw.includes(`quad.u("${name}")`) || draw.includes(`quad.u("${name}[0]")`), `${name} is never set`);
  }
  for (const gone of ["behindArt", "uArt", "uBand", "TITLE_DIM", "uTexture", "uPointer", "uLevel", "uPalette",
                      "WARM_GAIN"]) {
    assert.ok(!ch.FRAG.includes(gone), `${gone} is still in the shader`);
  }
});

test("the shader indexes arrays only with a loop counter or a constant", () => {
  // GLSL ES 1.0 allows array indexing by constant-index expressions: constants and loop indices.
  const body = ch.FRAG.slice(ch.FRAG.indexOf("void main()"));
  const indexes = [...body.matchAll(/\b(\w+)\[([^\]]+)\]/g)].map(m => m[2].trim());
  const ok = ["i", "j", "j + 1", String(FIELD_COUNT), String(FIELD_COUNT - 1)];
  assert.ok(indexes.length && indexes.every(i => ok.includes(i)), indexes.join(", "));
});

test("no field shows more of its colour than a light page's thickest paint allows", () => {
  // FIELD_ALPHA x the breath's peak x LIGHT_ALPHA: still well short of covering the page.
  const peak = 0.5 * 1.15 * 1.25;
  assert.ok(peak < 0.75, `peak ${peak}`);
  assert.ok(ch.FRAG.includes("LIGHT_ALPHA = 1.25") && ch.FRAG.includes("FIELD_ALPHA = 0.5000"));
});

// ---- the scene on a stand-in context --------------------------------------------------------

const RED = {r: 200, g: 40, b: 40}, BLUE = {r: 40, g: 60, b: 200}, GREEN = {r: 40, g: 180, b: 60};

function mounted(opts = {}, fn) {
  const page = fakePage().install();
  try {
    const gl = fakeGl();
    const api = ch.mount(fakeCanvas(gl, 390, 844), {bg: "#0d0c12", accent: "#ff6b4a", ...opts});
    try { return fn({api, gl, page}); } finally { api.destroy(); }
  } finally {
    page.restore();
  }
}

const names = gl => [...gl.set.keys()].map(k => k.replace("[0]", "")).sort();
const fieldCols = gl => [...gl.set.get("uFieldCol[0]")].map(v => Math.round(v * 255));

test("mounted, every uniform is set on the first frame, which is a still", () => mounted({}, ({gl}) => {
  assert.deepEqual(names(gl), [...ch.UNIFORMS].sort());
  assert.equal(gl.draws, 1);
  assert.deepEqual([...gl.set.get("uBloom")], [0, 0, 0, 0]);
}));

test("a palette set right after mount lands, with no fade (a chart's #1)", () => mounted({}, ({api, gl, page}) => {
  api.setPalette([RED, BLUE, GREEN]);
  page.step(16);
  const want = fieldColors([RED, BLUE, GREEN]).flatMap(c => [c.r, c.g, c.b]);
  assert.deepEqual(fieldCols(gl), want);
}));

test("a later palette crossfades over 4 s, and the field colours never sort mid-fade", () => mounted({}, ({api, gl, page}) => {
  api.setPalette([RED, BLUE, GREEN]);
  page.step(16);
  api.setPalette([BLUE, GREEN, RED].map(c => ({r: c.g, g: c.b, b: c.r})));
  const start = fieldCols(gl);
  for (let i = 0; i < 15; i++) page.step(70);  // about 1 s in
  const mid = fieldCols(gl);
  assert.notDeepEqual(mid, start);
  // Each field moves straight from its old colour toward its new one, never through another's.
  const target = fieldColors([BLUE, GREEN, RED].map(c => ({r: c.g, g: c.b, b: c.r}))).flatMap(c => [c.r, c.g, c.b]);
  for (let k = 0; k < mid.length; k++) {
    const lo = Math.min(start[k], target[k]) - 1, hi = Math.max(start[k], target[k]) + 1;
    assert.ok(mid[k] >= lo && mid[k] <= hi, `channel ${k}: ${mid[k]} outside ${lo}..${hi}`);
  }
  for (let i = 0; i < 70; i++) page.step(70);  // past 4 s
  const end = fieldColors([BLUE, GREEN, RED].map(c => ({r: c.g, g: c.b, b: c.r}))).flatMap(c => [c.r, c.g, c.b]);
  assert.deepEqual(fieldCols(gl), end);
}));

test("under reduced motion, everything holds its rest and a new palette lands at once", () => {
  mounted({reducedMotion: true}, ({api, gl, page}) => {
    assert.equal(page.pending(), 0);
    api.setPalette([RED, BLUE, GREEN]);
    const first = fieldCols(gl);
    api.setPalette([GREEN, GREEN, GREEN]);
    assert.notDeepEqual(fieldCols(gl), first);
    assert.deepEqual(fieldCols(gl), [GREEN, GREEN, GREEN].flatMap(c => [c.r, c.g, c.b]));
    assert.deepEqual([...gl.set.get("uFieldHue[0]")], [0, 0, 0]);
    const a = [...gl.set.get("uFieldA[0]")];
    assert.deepEqual([a[0], a[2], a[4]], [0, 0, 0]);  // no breath
  });
});

test("a theme change while running repaints nothing at rest: breath and hue go on", () => mounted({}, ({api, gl, page}) => {
  for (let i = 0; i < 60; i++) page.step(70);
  api.setColors({bg: "#ffffff", accent: "#00ff00"});  // running: no still is drawn
  page.step(70);
  const a = [...gl.set.get("uFieldA[0]")];
  assert.ok([a[0], a[2], a[4]].some(b => b !== 0));
  assert.deepEqual(gl.set.get("uBg"), [1, 1, 1]);
}));

test("setStrength calms the fields, seams and bloom together", () => mounted({reducedMotion: true}, ({api, gl}) => {
  const seamAt = () => [...gl.set.get("uSeam[0]")][3];
  const full = seamAt();
  api.setStrength(0.6);
  assert.equal(gl.set.get("uDim"), 0.6);
  assert.ok(close(seamAt(), full * 0.6, 1e-6));
}));

test("pop() brings a bloom on the next live frame; a resume after a hidden tab drops it", () => {
  mounted({}, ({gl, page}) => {
    page.step(70);
    ch.pop();
    page.step(70);
    for (let i = 0; i < 60; i++) page.step(70);  // 4 s into a 12-16 s bloom
    assert.ok(gl.set.get("uBloom")[3] > 0, "the bloom shows");
    page.setHidden(true);
    page.setHidden(false);
    page.step(70);
    page.step(70);
    assert.equal(gl.set.get("uBloom")[3], 0, "a stale bloom doesn't replay");
  });
});

test("without WebGL, mount gives up quietly", () => {
  assert.equal(ch.mount({getContext: () => null, addEventListener() {}}), null);
  assert.equal(ch.mount({getContext: () => { throw new Error("blocked"); }}), null);
});
