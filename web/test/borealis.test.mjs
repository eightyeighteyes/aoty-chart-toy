// Borealis's pure parts (web/src/borealis.js, AOTY-67). Run with: node --test web/test/*.test.mjs
// The engine needs WebGL, which no CI here has: the shader's uniforms are checked statically, and
// the drawing is checked by eye (the PR's manual test plan). Its palette is backgrounds.js's,
// tested in web/test/backgrounds.test.mjs (AOTY-77).
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import test from "node:test";
import {fakeCanvas, fakeGl, fakePage} from "./fakegl.mjs";

const require = createRequire(import.meta.url);
const bo = require("../src/borealis.js");
const SRC = readFileSync(new URL("../src/borealis.js", import.meta.url), "utf8");
// The uniforms runShader sets for every scene: its draw(), in backgrounds.js.
const BG_SRC = readFileSync(new URL("../src/backgrounds.js", import.meta.url), "utf8");
const LOOP_DRAW = BG_SRC.slice(BG_SRC.indexOf("function draw(still, dt)"), BG_SRC.indexOf("function frame(now)"));

test("the module loads in node and in a page alike", () => {
  assert.equal(typeof bo.mount, "function");
  assert.equal(globalThis.aotyBorealis, bo);
});

test("the flow wraps seamlessly and stays within one turn", () => {
  const [p, q] = bo.FLOW_PERIODS_S;
  for (const t of [0, 1, 96.9, 3600, 36000.5]) {
    for (const angle of bo.flowPhase(t)) assert.ok(angle >= 0 && angle < Math.PI * 2, `${t}: ${angle}`);
  }
  const [a0, b0] = bo.flowPhase(0), [a1] = bo.flowPhase(p), [, b1] = bo.flowPhase(q);
  assert.ok(Math.abs(a1 - a0) < 1e-9 && Math.abs(b1 - b0) < 1e-9);
});

test("a surge rises, holds, decays and frees its band, never jumping", () => {
  const s = new bo.SurgeEnvelope();
  s.trigger(0);
  let prev = 0;
  for (let i = 0; i < 60; i++) {
    s.tick(0.1);
    const v = s.bands[0].value;
    assert.ok(Math.abs(v - prev) <= 1.5 * 0.1 + 1e-9, `step ${i}: ${prev} -> ${v}`);  // bounded slope
    prev = v;
  }
  assert.equal(s.active, false);
  s.tick(5);  // a huge dt is clamped: nothing to advance now
  assert.equal(s.bands[0].value, 0);
});

test("a second surge early on brightens the band in place; later it rolls a second band", () => {
  const s = new bo.SurgeEnvelope();
  s.trigger(0);
  s.tick(0.5);
  const at = s.bands[0].position;
  s.trigger(1);
  assert.equal(s.bands[0].position, at);  // boosted, not moved
  assert.equal(s.bands[1].envAge, Infinity);
  for (let i = 0; i < 30; i++) s.tick(0.1);  // past halfway
  s.trigger(1);
  assert.equal(s.bands[1].envAge, 0);
  assert.equal(s.bands[1].dir, 1);
});

test("every uniform the shader declares is set by the engine, and kamp's dropped ones are gone", () => {
  const declared = [...bo.FRAG.matchAll(/^uniform \w+ (\w+)/gm)].map(m => m[1]);
  assert.deepEqual([...declared].sort(), [...bo.UNIFORMS].sort());
  // runShader sets the loop's three; the scene's draw() sets the rest. Both anchors must exist: a
  // missing one would slice to the file's end and pass vacuously.
  const from = SRC.indexOf("draw(gl, quad, frame) {"), to = SRC.indexOf("setPalette(colors, running) {");
  assert.ok(from >= 0 && to > from, "the scene's draw() and setPalette() must be in borealis.js, in that order");
  const draw = SRC.slice(from, to) + LOOP_DRAW;
  for (const name of bo.UNIFORMS) assert.ok(draw.includes(`quad.u("${name}")`), `${name} is never set`);
  for (const gone of ["uLean", "uFlicker", "titleMask", "uArtShown", "uPointer", "uLevel"]) {
    assert.ok(!bo.FRAG.includes(gone), `${gone} is still in the shader`);
  }
});

test("mounted, every uniform is set on the first frame (a stand-in context)", () => {
  const page = fakePage().install();
  try {
    const gl = fakeGl();
    const api = bo.mount(fakeCanvas(gl), {bg: "#0d0c12", accent: "#ff6b4a"});
    assert.deepEqual([...gl.set.keys()].sort(), [...bo.UNIFORMS].sort());
    assert.equal(gl.draws, 1);
    api.destroy();
  } finally {
    page.restore();
  }
});

test("the shader indexes the palette only with its loop counter", () => {
  // GLSL ES 1.0 allows uniform-array indexing by a loop index; anything else may not compile.
  const body = bo.FRAG.slice(bo.FRAG.indexOf("void main()"));  // after the uPalette[5] declaration
  const indexes = [...body.matchAll(/uPalette\[([^\]]+)\]/g)].map(m => m[1].trim());
  assert.ok(indexes.length && indexes.every(i => ["i", "i + 1", "4"].includes(i)), indexes.join(", "));
});

test("without WebGL, mount gives up quietly", () => {
  const canvas = {getContext: () => null, addEventListener() {}};
  assert.equal(bo.mount(canvas), null);
  const throwing = {getContext: () => { throw new Error("blocked"); }};
  assert.equal(bo.mount(throwing), null);
});
