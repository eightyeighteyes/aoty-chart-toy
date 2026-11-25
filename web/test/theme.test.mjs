// Page themes (web/src/theme.js, AOTY-65). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {THEMES, IDS, LABELS, MATCH, contrast, seamOf, failures, derive, tokens} = require("../src/theme.js");

// A cover's pixels: n of each [r, g, b], opaque.
function cover(...parts) {
  const out = [];
  for (const [n, [r, g, b]] of parts) for (let i = 0; i < n; i++) out.push(r, g, b, 255);
  return Uint8ClampedArray.from(out);
}

test("contrast is the WCAG ratio", () => {
  assert.equal(Math.round(contrast("#000000", "#ffffff")), 21);
  assert.equal(contrast("#777777", "#777777"), 1);
});

test("every preset is legible everywhere app.css uses its colours, kamp's periwinkle included", () => {
  for (const [id, t] of Object.entries(THEMES)) assert.deepEqual(failures(t), [], id);
});

test("the ids are the presets plus match1, each with a lowercase label", () => {
  assert.deepEqual(IDS, [...Object.keys(THEMES), "match1"]);
  for (const id of IDS) assert.match(LABELS[id], /^[a-z ]+$/, id);
  assert.equal(LABELS[MATCH], "album of the year");
});

test("the seam colour is one step of blue away, and never wraps", () => {
  assert.equal(seamOf("#0d0c12"), "#0d0c13");  // the poster's: app.css's Seam fix 2 value
  assert.equal(seamOf("#0000ff"), "#0000fe");
  assert.equal(seamOf("#000000"), "#000001");
});

test("tokens: the default and unknown ids are null, presets come with a tile and a seam", () => {
  assert.equal(tokens("poster"), null);
  assert.equal(tokens("match1"), null);
  assert.equal(tokens("nonsense"), null);
  assert.equal(tokens("__proto__"), null);
  const t = tokens("beach-house");
  assert.equal(t.bg, THEMES["beach-house"].bg);
  assert.match(t.tile, /^#[0-9a-f]{6}$/);
  assert.equal(t.seam, seamOf(t.bg));
});

test("a vivid cover gives a legible dark theme in its hue", () => {
  const t = derive(cover([900, [200, 40, 60]], [100, [30, 30, 30]]));  // mostly red
  assert.ok(t);
  assert.deepEqual(failures(t), []);
  const [r, g, b] = [1, 3, 5].map(i => parseInt(t.accent.slice(i, i + 2), 16));
  assert.ok(r > g && r > b, `accent ${t.accent} should be reddish`);
});

test("a two-colour cover uses its second colour for accent-2", () => {
  const t = derive(cover([600, [30, 90, 220]], [400, [240, 190, 40]]));  // blue and gold
  assert.ok(t);
  assert.deepEqual(failures(t), []);
  const [r, , b] = [1, 3, 5].map(i => parseInt(t.accent2.slice(i, i + 2), 16));
  assert.ok(r > b, `accent-2 ${t.accent2} should be the gold`);
});

test("covers with too little colour keep the default", () => {
  assert.equal(derive(cover([1000, [255, 255, 255]])), null);  // white
  assert.equal(derive(cover([1000, [0, 0, 0]])), null);  // black
  assert.equal(derive(cover([1000, [128, 128, 128]])), null);  // grey
  assert.equal(derive(cover([990, [20, 20, 20]], [10, [255, 0, 0]])), null);  // a speck of colour
  assert.equal(derive(new Uint8ClampedArray(0)), null);  // nothing loaded
  assert.equal(derive(Uint8ClampedArray.from([255, 0, 0, 0])), null);  // transparent
});

test("derived themes stay legible across the hue wheel", () => {
  for (let h = 0; h < 360; h += 15) {
    const c = h / 60, x = 1 - Math.abs((c % 2) - 1);
    const [r, g, b] = [[1, x, 0], [x, 1, 0], [0, 1, x], [0, x, 1], [x, 0, 1], [1, 0, x]][Math.floor(c) % 6];
    const t = derive(cover([1000, [r * 230, g * 230, b * 230]]));
    assert.ok(t, `hue ${h}`);
    assert.deepEqual(failures(t), [], `hue ${h}`);
  }
});
