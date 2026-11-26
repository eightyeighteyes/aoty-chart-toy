// Page themes (web/src/theme.js, AOTY-65). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {THEMES, IDS, ORDER, LABELS, MATCH, KAMP, LIGHT_KAMP, contrast, seamOf, failures, derive, tokens, complete,
       apply, reset} = require("../src/theme.js");

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

test("every preset is legible everywhere app.css uses its colours, kamp's periwinkle and the tiles included", () => {
  // As painted: complete() fills the periwinkle, the tile, its ink and its gradient's end.
  for (const [id, t] of Object.entries(THEMES)) assert.deepEqual(failures(complete(t)), [], id);
});

test("light presets switch the page to light, with the darker periwinkle and page-ink tiles", () => {
  const light = Object.keys(THEMES).filter(id => THEMES[id].scheme === "light");
  assert.deepEqual(light.sort(), ["gabbie", "lavender-diamond", "mint-royale", "tangerine-dream", "the-blue-nile",
                                  "white-stripes", "yellow-magic-orchestra"]);
  for (const id of light) {
    const c = complete(THEMES[id]);
    assert.equal(c.scheme, "light", id);
    assert.equal(c.kamp, LIGHT_KAMP, id);
    assert.ok(contrast(c.ink, c.bg) > 12, `${id}: a light page with dark ink`);
  }
  assert.equal(complete(THEMES.kamp).scheme, "dark");
  assert.equal(complete(THEMES.kamp).kamp, KAMP);
  assert.equal(complete(THEMES.gabbie).font, "display-arial");
  assert.equal(complete(THEMES.gabbie).tile, "#ec4899");  // New Bands for Old Heads' own pink, on the tiles
});

test("a tile's gradient end eases back only when the name in its middle wouldn't read", () => {
  assert.equal(complete(THEMES.poster).tileEnd, THEMES.poster.accent);  // unchanged where it reads
  const c = complete(THEMES["green-day"]);  // a light green under light ink
  assert.notEqual(c.tileEnd, THEMES["green-day"].accent);
  assert.deepEqual(failures(c), []);
});

test("apply and reset round-trip every property, the colour scheme and the page flags", () => {
  const props = new Map(), attrs = new Map();
  const el = {style: {setProperty: (k, v) => props.set(k, v), removeProperty: k => props.delete(k)},
              setAttribute: (k, v) => attrs.set(k, v), removeAttribute: k => attrs.delete(k)};
  apply(THEMES.gabbie, el);
  assert.equal(props.get("--bg"), "#ffffff");
  assert.equal(props.get("--kamp"), LIGHT_KAMP);
  assert.equal(props.get("--bg-seam"), "#fffffe");
  assert.equal(props.get("color-scheme"), "light");
  assert.deepEqual(Object.fromEntries(attrs), {"data-scheme": "light", "data-font": "display-arial"});
  apply(THEMES["beach-house"], el);  // a dark preset after a light one: the font flag goes
  assert.deepEqual(Object.fromEntries(attrs), {"data-scheme": "dark"});
  reset(el);
  assert.equal(props.size, 0);
  assert.equal(attrs.size, 0);
});

test("the ids are the presets plus match1, each with a lowercase label", () => {
  assert.deepEqual(IDS, [...Object.keys(THEMES), "match1"]);
  for (const id of IDS) assert.match(LABELS[id], /^[a-z ]+$/, id);
  assert.equal(LABELS[MATCH], "album of the year");
});

test("the picker leads with album of the year, then dark presets, then light, each in rainbow order", () => {
  assert.deepEqual(ORDER, ["match1", "strawberry-switchblade", "poster", "foxy-brown", "golden-smog",
                           "green-day", "beach-house", "kamp", "deep-purple", "blackpink",
                           "white-stripes", "tangerine-dream", "yellow-magic-orchestra", "mint-royale",
                           "the-blue-nile", "lavender-diamond", "gabbie"]);
  assert.deepEqual([...ORDER].sort(), [...IDS].sort());  // every theme, once
  assert.equal(LABELS.poster, "oranger");
});

test("the seam colour is one step of blue away, and never wraps", () => {
  assert.equal(seamOf("#0d0c12"), "#0d0c13");  // the poster's: app.css's Seam fix 2 value
  assert.equal(seamOf("#0000ff"), "#0000fe");
  assert.equal(seamOf("#ffffff"), "#fffffe");  // a white page (gabbie)
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

// ---- whose theme (AOTY-76) ------------------------------------------------------------------
const whose = require("../src/theme.js");

test("a theme's key and save path depend on whose page it is", () => {
  assert.equal(whose.themeKey({kind: "user", id: "alice"}), "aoty-theme:alice");
  assert.equal(whose.themeKey({kind: "group", id: "alice"}), "aoty-theme:group:alice");  // never the user's key
  assert.equal(whose.savePath({kind: "user", id: "alice"}), "users/alice/theme");
  assert.equal(whose.savePath({kind: "group", id: "the-crew"}), "groups/the-crew/theme");
});

test("a user owns their page's theme; a group's owner, not a member, owns the group's", () => {
  assert.equal(whose.ownsTheme({kind: "user", id: "alice"}, "Alice"), true);
  assert.equal(whose.ownsTheme({kind: "user", id: "alice"}, "bob"), false);
  assert.equal(whose.ownsTheme({kind: "group", id: "the-crew"}, "alice", "alice"), true);
  assert.equal(whose.ownsTheme({kind: "group", id: "the-crew"}, "bob", "alice"), false);
  // A group slug equal to a username doesn't make that user the group's owner.
  assert.equal(whose.ownsTheme({kind: "group", id: "bob"}, "bob", "alice"), false);
  assert.equal(whose.ownsTheme({kind: "group", id: "g"}, "alice", undefined), false);  // record not loaded
  assert.equal(whose.ownsTheme(null, "alice"), false);
  assert.equal(whose.ownsTheme({kind: "user", id: "alice"}, null), false);
});

test("album of the year reads a user's By Play #1, a group's By Listener #1 with older fallbacks", () => {
  assert.deepEqual(whose.topRanking({rankings: {album: [[3, 9]], track: [[1, 50]]}}), [[3, 9]]);
  const g = rankings => ({kind: "group", rankings});
  assert.deepEqual(whose.topRanking(g({listener: [[2, 1]], album: [[0, 5]]})), [[2, 1]]);
  assert.deepEqual(whose.topRanking(g({track: [[1, 8]], album: [[0, 5]]})), [[1, 8]]);  // before AOTY-63
  assert.deepEqual(whose.topRanking(g({album: [[0, 5]]})), [[0, 5]]);
  assert.deepEqual(whose.topRanking(null), []);
});
