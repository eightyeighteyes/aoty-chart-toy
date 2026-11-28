// Share card drawing (web/src/carddraw.js, AOTY-91), against a recording stand-in context.
// Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {drawOp, drawCard} = require("../src/carddraw.js");
const {compose, graphemes} = require("../src/sharecard.js");
const {THEMES, complete} = require("../src/theme.js");

// Records every call and property set, in order.
function fakeCtx() {
  const calls = [];
  const gradient = {addColorStop: (...a) => calls.push(["addColorStop", ...a])};
  const ctx = new Proxy({}, {
    get(_, name) {
      if (name === "calls") return calls;
      if (name === "createLinearGradient") return (...a) => { calls.push([name, ...a]); return gradient; };
      return (...a) => calls.push([name, ...a]);
    },
    set(_, name, value) { calls.push(["set", name, value]); return true; },
  });
  return ctx;
}

const measure = (text, font) => graphemes(text).length * Number(/(\d+)px/.exec(font)[1]) * 0.6;
const tokens = complete(THEMES.poster);
const texts = {albums: [{title: "Blue Lines", artist: "Massive Attack"}], name: "alice", year: 2026,
               url: "https://kamp.fm/toys/aoty/user/alice", kind: "user", played: null};

test("every op is drawn between a save and a restore", () => {
  const ctx = fakeCtx();
  const ops = compose({format: "top1_square", texts, tokens, covers: [false], measure});
  drawCard(ctx, ops, [null]);
  const names = ctx.calls.map(c => c[0]);
  assert.equal(names.filter(n => n === "save").length, ops.length);
  assert.equal(names.filter(n => n === "restore").length, ops.length);
});

test("text is drawn with its font, colour and alignment on the alphabetic baseline", () => {
  const ctx = fakeCtx();
  drawOp(ctx, {op: "text", text: "hi", x: 10, y: 20, font: "800 64px X", color: "#fff", align: "center"});
  assert.deepEqual(ctx.calls.slice(1, -1), [
    ["set", "font", "800 64px X"], ["set", "fillStyle", "#fff"], ["set", "textAlign", "center"],
    ["set", "textBaseline", "alphabetic"], ["fillText", "hi", 10, 20],
  ]);
});

test("a cover is cropped to its middle square, from naturalWidth in a page or width on the server", () => {
  for (const img of [{naturalWidth: 400, naturalHeight: 300}, {width: 400, height: 300}]) {
    const ctx = fakeCtx();
    drawOp(ctx, {op: "cover", x: 0, y: 0, w: 520, h: 520, shadow: 0.4}, img);
    assert.deepEqual(ctx.calls.find(c => c[0] === "drawImage"), ["drawImage", img, 50, 0, 300, 300, 0, 0, 520, 520]);
  }
});

test("a tile is a 135 degree gradient between its two colours", () => {
  const ctx = fakeCtx();
  drawOp(ctx, {op: "tile", x: 1, y: 2, w: 10, h: 10, from: "#111", to: "#222"});
  assert.deepEqual(ctx.calls.find(c => c[0] === "createLinearGradient"), ["createLinearGradient", 1, 2, 11, 12]);
  assert.deepEqual(ctx.calls.filter(c => c[0] === "addColorStop"), [["addColorStop", 0, "#111"], ["addColorStop", 1, "#222"]]);
});
