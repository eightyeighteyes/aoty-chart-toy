// What every first-visit background shares (web/src/backgrounds.js, AOTY-77): the palette, moved
// here from Borealis (AOTY-67). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";
import {fakeCanvas, fakeGl, fakePage} from "./fakegl.mjs";

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

// ---- the registry (AOTY-77) ----------------------------------------------------------------

test("the map keeps same-origin paths and nothing else, and never throws", () => {
  assert.deepEqual(bg.parseMap('{"borealis": "/toys/aoty/assets/borealis.abc.js"}'),
                   {borealis: "/toys/aoty/assets/borealis.abc.js"});
  for (const bad of ["", "not json", "[]", "null", '"x"']) assert.deepEqual(bg.parseMap(bad), {});
  assert.deepEqual(bg.parseMap('{"a": "https://evil.example/x.js", "b": "//evil.example/x.js", "c": 3, "d": "/ok.js"}'),
                   {d: "/ok.js"});
  assert.deepEqual(bg.readMap(null), {});
  assert.deepEqual(bg.readMap({getElementById: () => null}), {});
  assert.deepEqual(bg.ids({wintermute: "/w.js", borealis: "/b.js"}), ["borealis", "wintermute"]);
});

test("the pick: a preview's ?bg= when it names one, then the page's own, else none", () => {
  // AOTY-96: the random start is the server's, stored; the page never rolls its own.
  const ids = ["bokeh", "borealis", "sparks"];
  assert.equal(bg.pick({ids, override: "sparks", saved: "bokeh"}), "sparks");
  assert.equal(bg.pick({ids, override: "nonsense", saved: "bokeh"}), "bokeh");
  assert.equal(bg.pick({ids, override: "", saved: "none"}), null);
  assert.equal(bg.pick({ids, saved: "gone"}), null);  // an engine since removed shows nothing
  assert.equal(bg.pick({ids}), null);  // a page from before AOTY-96
  assert.equal(bg.pick({ids, override: "borealis", saved: "none"}), "borealis");  // a preview still works
  assert.equal(bg.pick({ids: [], override: "borealis"}), null);
  assert.equal(bg.pick({}), null);
});

test("the picker: none first, then every background alphabetically", () => {
  assert.equal(bg.NONE, "none");
  assert.deepEqual(bg.pickerOrder(["wintermute", "bokeh", "borealis"]), ["none", "bokeh", "borealis", "wintermute"]);
  assert.deepEqual(bg.pickerOrder([]), ["none"]);
  assert.deepEqual(bg.pickerOrder(["none", "sparks"]), ["none", "sparks"]);  // never twice
});

test("the owner's pick is saved to the api's user or group background route", () => {
  assert.equal(bg.savePath({kind: "user", id: "alice"}), "users/alice/background");
  assert.equal(bg.savePath({kind: "group", id: "full-house"}), "groups/full-house/background");
});

// A stand-in document: one #aoty-backgrounds block, and scripts that load (registering as they
// would) or fail when appended.
function fakeDoc(map, onScript) {
  const added = [];
  return {
    added,
    getElementById: id => (id === "aoty-backgrounds" ? {textContent: JSON.stringify(map)} : null),
    createElement: () => {
      const handlers = {};
      return {addEventListener: (type, fn) => { handlers[type] = fn; }, handlers};
    },
    head: {appendChild(s) { added.push(s); queueMicrotask(() => onScript(s)); }},
  };
}

test("load adds an engine's script once and resolves with what it registered", async () => {
  const engine = {mount: () => null};
  const doc = fakeDoc({"test-load": "/assets/t.js"}, s => {
    bg.register("test-load", engine);
    s.handlers.load();
  });
  const [a, b] = await Promise.all([bg.load("test-load", doc), bg.load("test-load", doc)]);
  assert.equal(a, engine);
  assert.equal(b, engine);
  assert.equal(doc.added.length, 1);
  assert.equal(doc.added[0].src, "/assets/t.js");
  assert.equal(await bg.load("test-load", doc), engine);  // registered: no second script
  assert.equal(doc.added.length, 1);
});

test("load gives null for an unknown id, a failed script, or one that registers nothing", async () => {
  const failing = fakeDoc({"test-fail": "/assets/f.js"}, s => s.handlers.error());
  assert.equal(await bg.load("test-unknown", failing), null);
  assert.equal(failing.added.length, 0);
  assert.equal(await bg.load("test-fail", failing), null);
  const silent = fakeDoc({"test-silent": "/assets/s.js"}, s => s.handlers.load());
  assert.equal(await bg.load("test-silent", silent), null);
  assert.equal(await bg.load("anything", null), null);
});

test("register keeps only engines with a mount", () => {
  bg.register("test-bad", {});
  bg.register("test-bad2", null);
  assert.equal(bg.registered("test-bad"), null);
  assert.equal(bg.registered("test-bad2"), null);
});

test("every engine file registers itself with the contract's mount", async () => {
  // build_shell.BACKGROUNDS is the list; tests/test_build_shell.py checks each file names its id.
  const {createRequire: cr} = await import("node:module");
  cr(import.meta.url)("../src/borealis.js");
  const engine = bg.registered("borealis");
  assert.ok(engine, "borealis.js didn't register");
  assert.equal(typeof engine.mount, "function");
  assert.equal(engine.mount({getContext: () => null, addEventListener() {}}), null);  // no WebGL: null
  // With a context, the contract's whole surface, setStrength (AOTY-96) included.
  withPage(() => {
    const api = engine.mount(fakeCanvas(fakeGl()), {bg: "#0d0c12", accent: "#ff6b4a"});
    for (const f of ["setPalette", "setColors", "setStrength", "destroy"]) assert.equal(typeof api[f], "function", f);
    api.destroy();
  });
});

// ---- runShader (AOTY-103): the loop every WebGL background shares ------------------------------

// A scene that records what the loop hands it.
function recordingSpec(fps = 30) {
  const log = {frames: [], starts: 0, palettes: [], strengths: [], colors: []};
  return {
    log,
    spec: {
      frag: "void main() {}", fps, resScale: 0.5,
      scene: () => ({
        onStart() { log.starts++; },
        draw(_gl, _quad, frame) { log.frames.push({...frame}); },
        setPalette(colors, running) { log.palettes.push({colors, running}); },
        setColors(c, running) { log.colors.push({...c, running}); },
        setStrength(s) { log.strengths.push(s); },
      }),
    },
  };
}

function withPage(fn) {
  const page = fakePage().install();
  try { return fn(page); } finally { page.restore(); }
}

test("runShader: a still first, then frames at the spec's rate, with a clamped clock", () => withPage(page => {
  const gl = fakeGl(), {log, spec} = recordingSpec(15);
  const api = bg.runShader(fakeCanvas(gl), {bg: "#ffffff"}, spec);
  assert.ok(api);
  assert.equal(log.frames.length, 1);
  assert.equal(log.frames[0].still, true);
  assert.equal(log.frames[0].atRest, false);  // still, but not reduced motion's rest frame
  assert.equal(log.starts, 1);
  assert.deepEqual(gl.set.get("uBg"), [1, 1, 1]);
  assert.deepEqual(gl.set.get("uRes"), [200, 400]);  // CSS size x dpr 1 x resScale 0.5
  page.step(16);  // the first live frame: dt 0
  page.step(30);  // under 1000/15 - 1 ms since: skipped
  page.step(40);
  const live = log.frames.slice(1);
  assert.equal(live.length, 2);
  assert.ok(live.every(f => !f.still));
  assert.ok(Math.abs(live[1].dt - 0.07) < 1e-9);
  page.step(10_000);  // a stalled tab: raw dt for the scene, but the clock moves 0.25 s at most
  const last = log.frames.at(-1);
  assert.ok(last.dt > 9);
  assert.ok(Math.abs(last.elapsed - (0.07 + 0.25)) < 1e-9);
  api.destroy();
}));

test("runShader: a hidden tab stops the loop; showing it again starts afresh", () => withPage(page => {
  const gl = fakeGl(), {log, spec} = recordingSpec();
  const api = bg.runShader(fakeCanvas(gl), {}, spec);
  page.step(40);
  page.setHidden(true);
  assert.equal(page.pending(), 0);
  const n = log.frames.length;
  page.step(1000);
  assert.equal(log.frames.length, n);
  page.setHidden(false);
  assert.equal(log.starts, 2);  // the scene drops anything stale
  page.step(40);
  assert.equal(log.frames.at(-1).dt, 0);  // the first frame after a start never counts the gap
  api.destroy();
}));

test("runShader: a tab that starts hidden doesn't run", () => withPage(page => {
  page.doc.hidden = true;
  const {log, spec} = recordingSpec();
  const api = bg.runShader(fakeCanvas(fakeGl()), {}, spec);
  assert.equal(log.starts, 0);
  assert.equal(page.pending(), 0);
  api.destroy();
}));

test("runShader: reduced motion draws rest frames only, and lands changes on a still", () => withPage(page => {
  const gl = fakeGl(), {log, spec} = recordingSpec();
  const api = bg.runShader(fakeCanvas(gl), {reducedMotion: true}, spec);
  assert.equal(page.pending(), 0);
  assert.equal(log.frames[0].atRest, true);
  api.setPalette([{r: 1, g: 2, b: 3}]);
  assert.equal(log.palettes[0].running, false);
  api.setStrength(2);
  assert.deepEqual(log.strengths, [1]);  // clamped
  api.setColors({bg: "#000000", accent: "#ff0000"});
  assert.equal(log.frames.length, 4);  // each one repainted the still
  assert.ok(log.frames.every(f => f.still && f.atRest));
  api.setPalette([]);  // nothing to show: ignored
  assert.equal(log.palettes.length, 1);
  api.destroy();
}));

test("runShader: a same-size resize is skipped; a real one resizes the backing store", () => withPage(() => {
  const gl = fakeGl(), {spec} = recordingSpec(), canvas = fakeCanvas(gl);
  const handlers = new Map();
  globalThis.addEventListener = (t, fn) => handlers.set(t, fn);
  globalThis.removeEventListener = t => handlers.delete(t);
  try {
    const api = bg.runShader(canvas, {}, spec);
    const v = gl.viewports;
    handlers.get("resize")();
    assert.equal(gl.viewports, v);
    canvas.clientHeight = 600;
    handlers.get("resize")();
    assert.equal(gl.viewports, v + 1);
    assert.equal(canvas.height, 300);
    api.destroy();
    assert.equal(handlers.has("resize"), false);
  } finally {
    delete globalThis.addEventListener;
    delete globalThis.removeEventListener;
  }
}));

test("runShader: destroy frees the context and stops everything; a lost context destroys it", () => withPage(page => {
  const gl = fakeGl(), {log, spec} = recordingSpec(), canvas = fakeCanvas(gl);
  const api = bg.runShader(canvas, {}, spec);
  api.destroy();
  assert.equal(gl.lost, true);
  assert.equal(page.pending(), 0);
  api.setPalette([{r: 1, g: 2, b: 3}]);
  assert.equal(log.palettes.length, 0);
  const gl2 = fakeGl(), canvas2 = fakeCanvas(gl2);
  let lost = 0;
  bg.runShader(canvas2, {onLost: () => lost++}, recordingSpec().spec);
  canvas2.listeners.get("webglcontextlost")({preventDefault() {}});
  assert.equal(lost, 1);
  assert.equal(gl2.lost, true);
}));

test("runShader: no WebGL, or a shader that won't compile, gives null", () => withPage(() => {
  const {spec} = recordingSpec();
  assert.equal(bg.runShader({getContext: () => null}, {}, spec), null);
  assert.equal(bg.runShader({getContext: () => { throw new Error("blocked"); }}, {}, spec), null);
  const gl = fakeGl();
  gl.getShaderParameter = () => false;
  assert.equal(bg.runShader(fakeCanvas(gl), {}, spec), null);
  assert.equal(gl.lost, true);  // its context is freed at once
}));

test("the palette uniform is always five full colours", () => {
  for (const colors of [[], [{r: 255, g: 0, b: 0}], bg.accentPalette("#ff6b4a").colors]) {
    const packed = bg.packPalette(colors);
    assert.equal(packed.length, 15);
    assert.ok([...packed].every(v => Number.isFinite(v) && v >= 0 && v <= 1));
  }
});
