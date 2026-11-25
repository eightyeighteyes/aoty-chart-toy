// Borealis's pure parts (web/src/borealis.js, AOTY-67). Run with: node --test web/test/*.test.mjs
// The engine needs WebGL, which no CI here has: the shader's uniforms are checked statically, and
// the drawing is checked by eye (the PR's manual test plan).
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const bo = require("../src/borealis.js");
const SRC = readFileSync(new URL("../src/borealis.js", import.meta.url), "utf8");

function pixels(...parts) {
  const out = [];
  for (const [n, [r, g, b]] of parts) for (let i = 0; i < n; i++) out.push(r, g, b, 255);
  return Uint8ClampedArray.from(out);
}
const hue = ({r, g, b}) => {
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  if (!d) return 0;
  const h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return h * 60;
};

test("the module loads in node and in a page alike", () => {
  assert.equal(typeof bo.mount, "function");
  assert.equal(globalThis.aotyBorealis, bo);
});

test("a mostly black cover surfaces its one bright colour", () => {
  const {colors, source} = bo.extractPalette(pixels([950, [5, 5, 5]], [50, [20, 230, 60]]), "#ff6b4a");
  assert.equal(source, "art");
  assert.equal(colors.length, bo.PALETTE_SIZE);
  assert.ok(Math.abs(hue(colors[0]) - 129) < 15, `got hue ${hue(colors[0])}`);  // the green
});

test("a grey or empty cover falls back to the page's accent", () => {
  for (const px of [pixels([1000, [128, 128, 128]]), new Uint8ClampedArray(0), Uint8ClampedArray.from([255, 0, 0, 0])]) {
    const out = bo.extractPalette(px, "#7c86e1");
    assert.equal(out.source, "accent");
    assert.equal(out.colors.length, bo.PALETTE_SIZE);
  }
  assert.ok(Math.abs(hue(bo.accentPalette("#7c86e1").colors[0]) - hue({r: 124, g: 134, b: 225})) < 4);
  assert.equal(bo.accentPalette("not a colour").colors.length, bo.PALETTE_SIZE);  // parsed defensively
});

test("a stopped crossfade lands directly; a running one fades", () => {
  const red = [{r: 255, g: 0, b: 0}], blue = [{r: 0, g: 0, b: 255}];
  const still = new bo.PaletteCrossfade(red);
  still.set(blue, false);  // reduced motion, a hidden tab: nothing would advance a fade
  assert.ok(still.settled);
  assert.deepEqual(still.current(), blue);

  const fade = new bo.PaletteCrossfade(red, 1000);
  fade.set(blue, true);
  assert.ok(!fade.settled);
  fade.tick(500);
  const mid = fade.current()[0];
  assert.ok(mid.r > 0 && mid.r < 255 && mid.b > 0 && mid.b < 255, JSON.stringify(mid));
  fade.tick(600);
  assert.ok(fade.settled);
  assert.deepEqual(fade.current(), blue);
});

test("only a real change of palette is worth a crossfade and a surge", () => {
  const a = bo.accentPalette("#ff6b4a").colors;
  const near = a.map(c => ({r: c.r + 3, g: c.g - 2, b: c.b}));
  const far = bo.accentPalette("#2f9be0").colors;
  assert.equal(bo.paletteChanged(a, near), false);
  assert.equal(bo.paletteChanged(a, far), true);
  assert.equal(bo.paletteChanged(null, a), true);
});

test("the palette uniform is always five full colours", () => {
  for (const colors of [[], [{r: 255, g: 0, b: 0}], bo.accentPalette("#ff6b4a").colors]) {
    const packed = bo.packPalette(colors);
    assert.equal(packed.length, 15);
    assert.ok([...packed].every(v => Number.isFinite(v) && v >= 0 && v <= 1));
  }
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
  const draw = SRC.slice(SRC.indexOf("function draw("), SRC.indexOf("function frame("));
  for (const name of bo.UNIFORMS) assert.ok(draw.includes(`quad.u("${name}")`), `${name} is never set`);
  for (const gone of ["uLean", "uFlicker", "titleMask", "uArtShown", "uPointer", "uLevel"]) {
    assert.ok(!bo.FRAG.includes(gone), `${gone} is still in the shader`);
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
