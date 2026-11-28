// What every first-visit background shares (web/src/backgrounds.js, AOTY-77): the palette, moved
// here from Borealis (AOTY-67). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const bg = require("../src/backgrounds.js");

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
  assert.equal(typeof bg.extractPalette, "function");
  assert.equal(globalThis.aotyBg, bg);
});

test("a mostly black cover surfaces its one bright colour", () => {
  const {colors, source} = bg.extractPalette(pixels([950, [5, 5, 5]], [50, [20, 230, 60]]), "#ff6b4a");
  assert.equal(source, "art");
  assert.equal(colors.length, bg.PALETTE_SIZE);
  assert.ok(Math.abs(hue(colors[0]) - 129) < 15, `got hue ${hue(colors[0])}`);  // the green
});

test("a grey or empty cover falls back to the page's accent", () => {
  for (const px of [pixels([1000, [128, 128, 128]]), new Uint8ClampedArray(0), Uint8ClampedArray.from([255, 0, 0, 0])]) {
    const out = bg.extractPalette(px, "#7c86e1");
    assert.equal(out.source, "accent");
    assert.equal(out.colors.length, bg.PALETTE_SIZE);
  }
  assert.ok(Math.abs(hue(bg.accentPalette("#7c86e1").colors[0]) - hue({r: 124, g: 134, b: 225})) < 4);
  assert.equal(bg.accentPalette("not a colour").colors.length, bg.PALETTE_SIZE);  // parsed defensively
});

test("a stopped crossfade lands directly; a running one fades", () => {
  const red = [{r: 255, g: 0, b: 0}], blue = [{r: 0, g: 0, b: 255}];
  const still = new bg.PaletteCrossfade(red);
  still.set(blue, false);  // reduced motion, a hidden tab: nothing would advance a fade
  assert.ok(still.settled);
  assert.deepEqual(still.current(), blue);

  const fade = new bg.PaletteCrossfade(red, 1000);
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
  const a = bg.accentPalette("#ff6b4a").colors;
  const near = a.map(c => ({r: c.r + 3, g: c.g - 2, b: c.b}));
  const far = bg.accentPalette("#2f9be0").colors;
  assert.equal(bg.paletteChanged(a, near), false);
  assert.equal(bg.paletteChanged(a, far), true);
  assert.equal(bg.paletteChanged(null, a), true);
});

test("the palette uniform is always five full colours", () => {
  for (const colors of [[], [{r: 255, g: 0, b: 0}], bg.accentPalette("#ff6b4a").colors]) {
    const packed = bg.packPalette(colors);
    assert.equal(packed.length, 15);
    assert.ok([...packed].every(v => Number.isFinite(v) && v >= 0 && v <= 1));
  }
});
