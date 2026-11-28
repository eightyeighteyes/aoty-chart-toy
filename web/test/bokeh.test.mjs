// Bokeh's pure parts and its scene (web/src/bokeh.js, AOTY-102). Run with:
// node --test web/test/*.test.mjs
// Ported from kamp's bokehField.test.ts, bokehFocus.test.ts and bokehSprites.test.ts (the pointer
// and art-pane parts stay behind with what they tested). The blur fallback is new: Safari has no
// ctx.filter. The scene runs on a stand-in 2D context; the drawing is checked by hand.
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";
import {fake2d, fake2dCanvas, fakeOffscreen, fakePage} from "./fakegl.mjs";

const require = createRequire(import.meta.url);
const bk = require("../src/bokeh.js");
const bg = require("../src/backgrounds.js");
const {ORB_PLAN, TIERS, VISIBLE_SHARE, advanceField, makeField, visibleOrbCount, visibleShare, SpriteBakeGate,
       RackFocus, focusAlphas, FIRST_RACK, PULL_S, RACK_GAP, HIDDEN_RERACK_S, MAX_SHARP, SOFT_DIM} = bk;

function seeded(seed) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

test("the module loads in node and in a page alike, and registers itself", () => {
  assert.equal(typeof bk.mount, "function");
  assert.equal(globalThis.aotyBokeh, bk);
  assert.equal(bg.registered("bokeh"), bk);
});

// ---- the field (bokehField.test.ts) ---------------------------------------------------------

const W = 1600, H = 1000;
const ART = {x0: 0.5 - 0.225 * (H / W), x1: 0.5 + 0.225 * (H / W), y0: 0.275, y1: 0.725};  // kamp's pane
const STEP = 0.1, HOUR = 3600;

// Share of one-second samples with each visible-orb count, over seconds, on a w x h pane.
function census(seed, seconds, w, h) {
  const orbs = makeField(seeded(seed));
  const counts = new Array(ORB_PLAN.length + 1).fill(0);
  let elapsed = 0;
  for (let s = 0; s < seconds; s++) {
    for (let i = 0; i < 1 / STEP; i++) {
      advanceField(orbs, STEP);
      elapsed += STEP;
    }
    counts[visibleOrbCount(orbs, elapsed, w, h)]++;
  }
  return counts.map(c => c / seconds);
}

function run(seed, seconds, each) {
  const orbs = makeField(seeded(seed)), dt = 1 / 30;
  for (let f = 0; f < seconds * 30; f++) {
    advanceField(orbs, dt);
    each(orbs, dt);
  }
}

test("visibleShare: a centred orb is seen, one behind art or off the pane is not", () => {
  assert.equal(visibleShare(W / 2, H / 2, 200, W, H, null), 1);
  assert.equal(visibleShare(W / 2, H / 2, 200, W, H, ART), 0);
  assert.equal(visibleShare(-500, H / 2, 200, W, H, null), 0);
  const edge = visibleShare(0, H / 2, 200, W, H, null);  // 0.207 analytically, with the edge band
  assert.ok(Math.abs(edge - 0.207) < 0.03, `edge share ${edge}`);
});

test("visibleOrbCount respects VISIBLE_SHARE", () => {
  const [o] = makeField(seeded(1));
  const solo = [{...o, ax: 0, ay: 0, bx: 0.5, by: 0.5}];
  assert.equal(visibleOrbCount(solo, 0, W, H), 1);
  solo[0].bx = 1.2;
  assert.ok(visibleShare(1.2 * W, H / 2, o.sizePct * H * 0.5, W, H) < VISIBLE_SHARE);
  assert.equal(visibleOrbCount(solo, 0, W, H), 0);
});

test("each orb base stays within its tier reach", () => {
  run(1, HOUR, orbs => {
    for (const o of orbs) {
      const half = 0.5 * TIERS[o.tier].reach + 1e-9;
      assert.ok(Math.abs(o.bx - 0.5) <= half && Math.abs(o.by - 0.5) <= half, `${o.tier} out of reach`);
    }
  });
});

test("heroes keep their centre on the pane; mids and accents leave and come back", () => {
  const offAt = {hero: 0, mid: 0, accent: 0}, back = {hero: false, mid: false, accent: false}, wasOff = [];
  run(2, HOUR, orbs => {
    orbs.forEach((o, i) => {
      const off = o.bx < 0 || o.bx > 1 || o.by < 0 || o.by > 1;
      if (off) offAt[o.tier]++;
      if (wasOff[i] && !off) back[o.tier] = true;
      wasOff[i] = off;
    });
  });
  assert.equal(offAt.hero, 0);
  assert.ok(offAt.mid > 0 && back.mid, "mids never left and came back");
  assert.ok(offAt.accent > offAt.mid && back.accent, "accents should roam furthest");
});

test("the drift is calm, and small orbs move most", () => {
  const sum = {hero: 0, mid: 0, accent: 0}, n = {hero: 0, mid: 0, accent: 0};
  let prev = [];
  run(3, HOUR, (orbs, dt) => {
    orbs.forEach((o, i) => {
      if (prev[i]) {
        sum[o.tier] += Math.hypot(o.bx - prev[i].x, o.by - prev[i].y) / dt;
        n[o.tier]++;
      }
    });
    prev = orbs.map(o => ({x: o.bx, y: o.by}));
  });
  const mean = t => sum[t] / n[t];
  const band = {hero: [0.005, 0.013], mid: [0.008, 0.018], accent: [0.011, 0.024]};
  for (const t of ["hero", "mid", "accent"]) assert.ok(mean(t) > band[t][0] && mean(t) < band[t][1], `${t} ${mean(t)}`);
  assert.ok(mean("hero") < mean("mid") && mean("mid") < mean("accent"));
});

test("no orb ever turns abruptly (the walk bound is soft)", () => {
  let worst = 0;
  for (const seed of [1, 2, 3]) {
    let prev = [], frames = 0;
    run(seed, HOUR, (orbs, dt) => {
      prev = orbs.map((o, i) => {
        const p = prev[i];
        const vx = p ? (o.bx - p.x) / dt : 0, vy = p ? (o.by - p.y) / dt : 0;
        if (p && frames > 1) worst = Math.max(worst, Math.hypot(vx - p.vx, vy - p.vy));
        return {x: o.bx, y: o.by, vx, vy};
      });
      frames++;
    });
  }
  assert.ok(worst < 0.02, `worst velocity change ${worst.toFixed(4)}`);
});

test("a new field starts spread out, and the same seed rebuilds the same field", () => {
  let spread = 0, count = 0;
  for (let seed = 1; seed <= 40; seed++) {
    for (const o of makeField(seeded(seed))) {
      spread += Math.abs(o.bx - 0.5) / (0.5 * TIERS[o.tier].reach);
      count++;
    }
  }
  assert.ok(spread / count > 0.25, `mean start offset ${(spread / count).toFixed(3)}`);
  const a = makeField(seeded(9)), b = makeField(seeded(9));
  assert.deepEqual(a.map(o => [o.bx, o.by, o.sizePct, o.blurMul]), b.map(o => [o.bx, o.by, o.sizePct, o.blurMul]));
});

test("three accents in distinct colours; each orb blurs within its tier, accents least", () => {
  const accents = ORB_PLAN.filter(p => p.tier === "accent");
  assert.equal(accents.length, 3);
  assert.equal(new Set(accents.map(p => p.colorIndex)).size, 3);
  const seen = {hero: [Infinity, -Infinity], mid: [Infinity, -Infinity], accent: [Infinity, -Infinity]};
  for (let seed = 1; seed <= 50; seed++) {
    for (const o of makeField(seeded(seed))) {
      const [lo, hi] = TIERS[o.tier].blur;
      assert.ok(o.blurMul >= lo && o.blurMul <= hi);
      seen[o.tier] = [Math.min(seen[o.tier][0], o.blurMul), Math.max(seen[o.tier][1], o.blurMul)];
    }
  }
  assert.ok(seen.accent[1] < seen.mid[0] && seen.mid[1] <= seen.hero[0]);
});

// kamp's bar (KAMP-779), set before porting (AOTY-102): over an hour of drift the field is never
// blank, and shows a single orb under 2% of the time. kamp held it on a landscape pane with its
// art in the middle; here there's no art, and the page is as often a phone held upright.
for (const [w, h, name] of [[1600, 1000, "a desktop"], [390, 844, "a phone, portrait"], [1024, 1366, "a tablet, portrait"]]) {
  test(`the field is never blank on ${name}`, () => {
    for (const seed of [1, 2, 3, 4]) {
      const share = census(seed, HOUR, w, h);
      assert.equal(share[0], 0, `seed ${seed}: blank ${(share[0] * 100).toFixed(1)}% of the time`);
      assert.ok(share[1] < 0.02, `seed ${seed}: one orb ${(share[1] * 100).toFixed(1)}% of the time`);
    }
  });
}

// ---- the sprite re-bake gate --------------------------------------------------------------

const RED = [{r: 255, g: 0, b: 0}], BLUE = [{r: 0, g: 0, b: 255}];

test("a settled field never re-bakes; a fade re-bakes every frame until it settles", () => {
  const fade = new bg.PaletteCrossfade(RED, 1000), gate = new SpriteBakeGate();
  assert.equal(gate.shouldBake("k", fade.current()), true);
  for (let f = 0; f < 300; f++) {
    fade.tick(33);
    assert.equal(gate.shouldBake("k", fade.current()), false);
  }
  fade.set(BLUE, true);
  let bakes = 0;
  for (let f = 0; f < 60; f++) {
    fade.tick(33);
    if (gate.shouldBake("k", fade.current())) bakes++;
  }
  assert.ok(bakes >= 30 && bakes <= 32, `${bakes} bakes`);
  assert.equal(gate.shouldBake("k", fade.current()), false);
});

test("a palette set while stopped re-bakes once; a new key or invalidate() re-bakes", () => {
  const fade = new bg.PaletteCrossfade(RED, 1000), gate = new SpriteBakeGate();
  gate.shouldBake("k", fade.current());
  fade.set(BLUE, false);
  assert.equal(gate.shouldBake("k", fade.current()), true);
  assert.equal(gate.shouldBake("k", fade.current()), false);
  assert.equal(gate.shouldBake("k2", fade.current()), true);
  gate.invalidate();
  assert.equal(gate.shouldBake("k2", fade.current()), true);
});

// ---- the rack focus (bokehFocus.test.ts) ------------------------------------------------------

const N = 8, ACCENTS = [5, 6, 7], DT = 1 / 60, always = () => true;
const rackOf = seed => new RackFocus(N, ACCENTS, seeded(seed));
const targets = r => [...r.targets].sort();
function untilRack(r, visible, limit = 120) {
  const before = targets(r).join();
  let t = 0;
  while (t < limit) {
    r.tick(DT, visible);
    t += DT;
    if (targets(r).join() !== before) return t;
  }
  return Infinity;
}

test("bokeh's accents are the rack's eligible orbs", () => assert.deepEqual(bk.FOCUSABLE, ACCENTS));

test("nothing is in focus at first, and the first rack comes soon", () => {
  const r = rackOf(1);
  assert.deepEqual(r.focus, new Array(N).fill(0));
  assert.ok(untilRack(r, always) <= FIRST_RACK[1] + DT);
});

test("a pull lands exactly on 1 and on 0", () => {
  const r = rackOf(2);
  untilRack(r, always);
  const picked = targets(r);
  for (let t = 0; t < PULL_S + 0.1; t += DT) r.tick(DT, always);
  for (let i = 0; i < N; i++) assert.equal(r.focus[i], picked.includes(i) ? 1 : 0);
  untilRack(r, always);
  for (let t = 0; t < PULL_S + 0.1; t += DT) r.tick(DT, always);
  for (const i of picked) if (!r.targets.has(i)) assert.equal(r.focus[i], 0);
});

test("only accents come into focus; 1 or 2 at a time, and every accent gets its turn", () => {
  const r = rackOf(4), sizes = new Set(), picked = new Set();
  for (let n = 0; n < 60; n++) {
    untilRack(r, always);
    sizes.add(r.targets.size);
    for (const i of r.targets) picked.add(i);
    for (let i = 0; i < N; i++) if (!ACCENTS.includes(i)) assert.equal(r.focus[i], 0);
  }
  assert.deepEqual([...sizes].sort(), [1, 2]);
  assert.deepEqual([...picked].sort(), ACCENTS);
});

test("focus moves on: each rack changes which accents are sharp", () => {
  const r = rackOf(5);
  untilRack(r, always);
  for (let n = 0; n < 40; n++) assert.ok(untilRack(r, always) <= RACK_GAP[1] + DT);
});

test("a pull eases in and out: no snap as it starts or lands", () => {
  const r = rackOf(13);
  untilRack(r, always);
  const [i] = targets(r);
  let prev = r.focus[i];
  r.tick(DT, always);
  assert.ok(r.focus[i] - prev < 1e-3);
  while (r.focus[i] < 1) {
    prev = r.focus[i];
    r.tick(DT, always);
  }
  assert.ok(1 - prev < 1e-3);
});

test("a rack picks only visible accents; a hidden target loses focus within HIDDEN_RERACK_S", () => {
  const r = rackOf(6), notSix = i => i !== 6;
  for (let n = 0; n < 40; n++) {
    untilRack(r, notSix);
    assert.ok(!r.targets.has(6) && r.targets.size > 0);
  }
  const q = rackOf(7);
  untilRack(q, always);
  const [hid] = targets(q);
  let t = 0;
  while (q.targets.has(hid) && t < 60) {
    q.tick(DT, i => i !== hid);
    t += DT;
  }
  assert.ok(t <= HIDDEN_RERACK_S + DT);
});

test("with no accent visible every orb defocuses, and focus returns once one is back", () => {
  const r = rackOf(8);
  untilRack(r, always);
  for (let t = 0; t < HIDDEN_RERACK_S + PULL_S + 1; t += DT) r.tick(DT, () => false);
  assert.equal(r.targets.size, 0);
  assert.deepEqual(r.focus, new Array(N).fill(0));
  assert.ok(untilRack(r, always) < 10);
});

test("a re-rack mid-pull continues from where focus is", () => {
  const r = rackOf(9);
  untilRack(r, always);
  for (let t = 0; t < PULL_S / 2; t += DT) r.tick(DT, always);
  const mid = [...r.focus];
  assert.ok(mid.some(f => f > 0.2 && f < 0.8));
  r.rack(always);
  assert.deepEqual(r.focus, mid);
  r.tick(DT, always);
  for (let i = 0; i < N; i++) assert.ok(Math.abs(r.focus[i] - mid[i]) < 0.02);
});

test("focus never steps abruptly and never sharpens more than MAX_SHARP orbs", () => {
  const rng = seeded(10), hidden = new Set(), visible = i => !hidden.has(i), r = rackOf(11);
  let prev = [...r.focus], worst = 0;
  for (let t = 0; t < 3600; t += DT) {
    if (rng() < DT / 7) {
      const i = ACCENTS[Math.floor(rng() * ACCENTS.length)];
      if (hidden.has(i)) hidden.delete(i); else hidden.add(i);
    }
    r.tick(DT, visible);
    assert.ok(r.focus.filter(f => f > 0.5).length <= MAX_SHARP);
    for (let i = 0; i < N; i++) worst = Math.max(worst, Math.abs(r.focus[i] - prev[i]));
    prev = [...r.focus];
  }
  assert.ok(worst <= (1.875 / PULL_S) * DT + 1e-9);
});

const screen = (a, b) => 1 - (1 - a) * (1 - b);

test("soft under sharp: the centre never darkens while the halo dims", () => {
  for (let as = 0.05; as <= 0.95; as += 0.05) {
    for (let ak = 0.05; ak <= 1; ak += 0.05) {
      let prevCentre = -Infinity, prevHalo = Infinity;
      for (let f = 0; f <= 1 + 1e-9; f += 0.01) {
        const {soft, sharp} = focusAlphas(f, as, ak);
        const centre = screen(as * soft, ak * sharp), halo = as * soft;
        assert.ok(centre >= prevCentre - 1e-12 && halo <= prevHalo + 1e-12);
        prevCentre = centre;
        prevHalo = halo;
      }
    }
  }
  assert.deepEqual(focusAlphas(0, 0.5, 0.8), {soft: 1, sharp: 0});
  const {soft} = focusAlphas(1, 0.3, 1);
  assert.ok(Math.abs(soft - (1 - SOFT_DIM)) < 1e-12);
  assert.ok(focusAlphas(1, 0.95, 0.5).soft > 1 - SOFT_DIM);
});

// ---- sprite geometry (bokehSprites.test.ts) ---------------------------------------------------

test("blurPad covers three sigma; orbSprite works in backing px and never clips the tail", () => {
  assert.equal(bk.blurPad(0), 0);
  assert.equal(bk.blurPad(-4), 0);
  assert.ok(bk.blurPad(7) >= 21);
  const a = bk.orbSprite(200, 28, 1), b = bk.orbSprite(200, 28, 0.25);
  assert.deepEqual([a.radius, a.sigma, b.radius, b.sigma], [200, 28, 50, 7]);
  for (const [r, blur, scale] of [[200, 28, 1], [30, 28, 0.25], [1, 28, 0.125], [120, 0, 0.5]]) {
    const sp = bk.orbSprite(r, blur, scale);
    assert.ok(sp.half >= sp.radius + 3 * sp.sigma && Number.isInteger(sp.half) && sp.half >= 1);
  }
});

test("drawHalf follows the radius; alphaScale never exceeds 1; bakeKey ignores sub-pixel jitter", () => {
  const sp = bk.orbSprite(100, 28, 0.5), base = bk.drawHalf(sp, 100, 0.5);
  assert.equal(base, sp.half / 0.5);
  assert.ok(Math.abs(bk.drawHalf(sp, 116, 0.5) - base * 1.16) < 1e-9);
  assert.equal(bk.alphaScale(0.4, 0.4), 1);
  assert.equal(bk.alphaScale(0.5, 0.4), 1);
  assert.equal(bk.alphaScale(-0.1, 0.4), 0);
  assert.equal(bk.alphaScale(0.3, 0), 0);
  const k = bk.bakeKey(800, 0.5, 28);
  assert.notEqual(bk.bakeKey(900, 0.5, 28), k);
  assert.notEqual(bk.bakeKey(800, 0.25, 28), k);
  assert.equal(bk.bakeKey(800.4, 0.5, 28), k);
});

// ---- the blur without ctx.filter (new: Safari) -----------------------------------------------

const C = {r: 200, g: 100, b: 50};
// The source gradient at radius fraction t, for stops [{at, a}], linear between, 0 past the last.
function sourceAlpha(stops, t) {
  if (t > stops.at(-1).at) return 0;
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1];
    if (t >= a.at && t <= b.at) return a.a + (b.a - a.a) * ((t - a.at) / (b.at - a.at));
  }
  return stops[0].a;
}
const alphaAt = (out, rho) => {
  const t = rho / out.R, s = out.stops;
  for (let i = 0; i < s.length - 1; i++) {
    if (t >= s[i].at && t <= s[i + 1].at) return s[i].a + (s[i + 1].a - s[i].a) * ((t - s[i].at) / (s[i + 1].at - s[i].at || 1));
  }
  return s.at(-1).a;
};

test("the computed blur: a gradient of radius r + 3 sigma, stops ascending within 0..1", () => {
  const out = bk.blurredStops([{at: 0, c: C, a: 0.5}, {at: 0.4, c: C, a: 0.275}, {at: 1, c: C, a: 0}], 50, 7);
  assert.equal(out.R, 50 + 21);
  assert.equal(out.stops[0].at, 0);
  assert.equal(out.stops.at(-1).at, 1);
  for (let i = 1; i < out.stops.length; i++) assert.ok(out.stops[i].at > out.stops[i - 1].at);
  assert.ok(out.stops.at(-1).a < 0.005, `tail ${out.stops.at(-1).a}`);
  for (const s of out.stops) assert.deepEqual([Math.round(s.c.r), Math.round(s.c.g), Math.round(s.c.b)], [200, 100, 50]);
});

test("the computed blur keeps the light: total alpha over the disc is conserved", () => {
  const stops = [{at: 0, c: C, a: 0.5}, {at: 0.4, c: C, a: 0.275}, {at: 1, c: C, a: 0}];
  for (const [r, sigma] of [[50, 7], [20, 9], [120, 4]]) {
    const out = bk.blurredStops(stops, r, sigma);
    let before = 0, after = 0;
    const n = 4000;
    for (let i = 0; i < n; i++) {
      const rho = ((i + 0.5) / n) * out.R;
      before += sourceAlpha(stops, rho / r) * rho;
      after += alphaAt(out, rho) * rho;
    }
    assert.ok(Math.abs(after / before - 1) < 0.03, `r ${r} sigma ${sigma}: ${(after / before).toFixed(3)}`);
  }
});

test("the computed blur of a flat disc: full inside, half at its edge, gone past three sigma", () => {
  const disc = [{at: 0, c: C, a: 1}, {at: 1, c: C, a: 1}];
  const out = bk.blurredStops(disc, 100, 5);
  assert.ok(alphaAt(out, 0) > 0.99);
  assert.ok(alphaAt(out, 80) > 0.99);
  // A straight edge blurred is half at the edge; a curved one a touch under, inside the disc.
  assert.ok(Math.abs(alphaAt(out, 100) - 0.5) < 0.04, `edge ${alphaAt(out, 100)}`);
  assert.ok(alphaAt(out, 115) < 0.01);
});

test("the computed blur mixes colours premultiplied: a warm core over the orb's colour", () => {
  const W8 = {r: 255, g: 246, b: 230};
  const out = bk.blurredStops([{at: 0, c: W8, a: 0.92}, {at: 0.35, c: C, a: 0.64}, {at: 1, c: C, a: 0}], 40, 6);
  const centre = out.stops[0].c, rim = out.stops.find(s => s.at * out.R > 30).c;
  assert.ok(centre.r > 230 && centre.b > 150, `centre ${JSON.stringify(centre)}`);  // mostly the core
  assert.ok(Math.abs(rim.b - 50) < 15, `rim ${JSON.stringify(rim)}`);  // the orb's colour, not grey
  // Where alpha has gone to nothing the colour is the last stop's, never NaN.
  for (const s of out.stops) assert.ok(Number.isFinite(s.c.r + s.c.g + s.c.b + s.a));
});

test("no blur, no computing: the stops as given", () => {
  const stops = [{at: 0, c: C, a: 0.4}, {at: 1, c: C, a: 0}];
  assert.deepEqual(bk.blurredStops(stops, 30, 0), {R: 30, stops});
});

test("a sprite bakes with ctx.filter where there is one, and the computed blur where not", () => {
  const page = fakePage().install();
  try {
    const geom = bk.orbSprite(50, 28, 0.25);
    const withF = bk.bakeOrb(geom, C, 0.5, [0.4, 0.55]);
    assert.equal(withF.ctx.ops[0].filter, `blur(${geom.sigma}px)`);
    assert.equal(withF.ctx.gradients[0].stops.length, 3);  // kamp's three stops
    globalThis.OffscreenCanvas = fakeOffscreen({withFilter: false});  // Safari
    const without = bk.bakeOrb(geom, C, 0.5, [0.4, 0.55]);
    assert.equal(without.ctx.ops[0].filter, undefined);
    const g = without.ctx.gradients[0];
    assert.ok(g.stops.length > 20);
    assert.equal(g.r1, geom.radius + 3 * geom.sigma);
    assert.ok(g.r1 <= geom.half, "the gradient fits its sprite");
  } finally {
    page.restore();
  }
});

// ---- the scene on a stand-in context -------------------------------------------------------

const PAL = [{r: 220, g: 80, b: 60}, {r: 60, g: 90, b: 200}, {r: 230, g: 190, b: 70}, {r: 80, g: 160, b: 90},
             {r: 150, g: 70, b: 170}];

function mounted(opts, fn) {
  const page = fakePage().install();
  try {
    const ctx = fake2d(), canvas = fake2dCanvas(ctx, 390, 844);
    const api = bk.mount(canvas, {bg: "#0d0c12", accent: "#ff6b4a", ...opts});
    try { return fn({api, ctx, canvas, page}); } finally { api.destroy(); }
  } finally {
    page.restore();
  }
}
const lastFrame = ctx => {
  const i = ctx.ops.findLastIndex(o => o.op === "fillRect" && o.composite === "source-over" && o.alpha === 1);
  return ctx.ops.slice(i + 1);
};
const orbDraws = ctx => lastFrame(ctx).filter(o => o.op === "drawImage");

test("a frame is --bg, then eight orbs screened on a dark page, at kamp's 0.25 scale", () => mounted({}, ({ctx, canvas}) => {
  assert.deepEqual([canvas.width, canvas.height], [98, 211]);  // 390 x 844 x dpr 1 x 0.25
  const draws = orbDraws(ctx);
  assert.equal(draws.length, 8);
  assert.ok(draws.every(d => d.composite === "screen"));
  assert.ok(draws.every(d => d.alpha > 0 && d.alpha <= 1));
}));

test("light pages: every draw multiplies; the firefly's sprite has no warm-white core", () => {
  mounted({bg: "#ffffff"}, ({ctx, page}) => {
    bk.pop();
    page.step(16);
    for (let i = 0; i < 40; i++) page.step(34);  // past its fade-in
    const draws = orbDraws(ctx);
    assert.equal(draws.length, 9, "eight orbs and the firefly");
    assert.ok(draws.every(d => d.composite === "multiply"), draws.map(d => d.composite).join());
    const mote = draws.at(-1).img.ctx.gradients[0].stops[0][1];
    assert.ok(!mote.startsWith("rgba(255,246,230"), mote);
  });
  mounted({}, ({ctx, page}) => {
    bk.pop();
    page.step(16);
    for (let i = 0; i < 40; i++) page.step(34);
    const mote = orbDraws(ctx).at(-1);
    assert.equal(mote.composite, "lighter");
    assert.ok(mote.img.ctx.gradients[0].stops[0][1].startsWith("rgba(255,246,230"));
  });
});

// The scene a mount just made, caught through the spec it was built from.
let lastScene = null;
const realScene = bk.SPEC.scene;
bk.SPEC.scene = opts => (lastScene = realScene(opts));

test("a palette set right after mount lands: no fade, no bloom", () => mounted({}, ({api, ctx, page}) => {
  api.setPalette(PAL);
  page.step(16);
  page.step(34);
  assert.equal(lastScene.bloomAge, bk.BLOOM_DONE);
  // The orbs are in the new colours at once: the first hero's sprite is PAL[0].
  const hero = orbDraws(ctx)[0].img.ctx.gradients[0].stops[0][1];
  assert.ok(hero.startsWith("rgba(220,80,60,"), hero);
}));

test("later palettes bloom at most once per BLOOM_PACE_S; a resume drops a stale bloom", () => mounted({}, ({api, page}) => {
  page.step(16);
  page.step(34);  // a live frame has drawn
  api.setPalette(PAL);
  assert.equal(lastScene.bloomAge, 0, "the first palette after the first frame blooms");
  for (let i = 0; i < 30; i++) page.step(34);  // ~1 s
  const age = lastScene.bloomAge;
  api.setPalette(PAL.slice().reverse());
  assert.equal(lastScene.bloomAge, age, "a burst 1 s later doesn't bloom again");
  for (let i = 0; i < 200; i++) page.step(34);  // past the pace window
  api.setPalette(PAL);
  assert.equal(lastScene.bloomAge, 0);
  page.setHidden(true);
  page.setHidden(false);
  assert.equal(lastScene.bloomAge, bk.BLOOM_DONE, "a bloom never fires late");
}));

test("reduced motion: the rest frame is kamp's two-swatch gradient, and a palette lands in it", () => {
  mounted({reducedMotion: true}, ({api, ctx}) => {
    assert.equal(orbDraws(ctx).length, 0);
    api.setPalette(PAL);
    const g = ctx.gradients.at(-1);
    assert.equal(g.kind, "linear");
    assert.ok(g.stops[0][1].startsWith("rgba(220,80,60,"));
    assert.ok(g.stops[1][1].startsWith("rgba(60,90,200,"));
    api.setStrength(0.5);
    assert.ok(ctx.gradients.at(-1).stops[0][1].endsWith(",0.1100)"));  // 0.22 x 0.5
  });
});

test("a still that isn't at rest (a hidden tab's theme change) draws the frozen field", () => mounted({}, ({api, ctx, page}) => {
  page.step(16);
  page.setHidden(true);
  api.setColors({bg: "#ffffff"});
  const draws = orbDraws(ctx);
  assert.equal(draws.length, 8);
  assert.ok(draws.every(d => d.composite === "multiply"));
}));

test("setStrength dims every orb by the same factor", () => mounted({}, ({api, ctx, page}) => {
  page.setHidden(true);  // stopped: each change repaints a still, the same frozen field
  api.setStrength(1);
  const full = orbDraws(ctx).map(d => d.alpha);
  api.setStrength(0.6);
  const calm = orbDraws(ctx).map(d => d.alpha);
  full.forEach((a, i) => assert.ok(Math.abs(calm[i] - a * 0.6) < 1e-9));
}));

test("without a 2D context, mount gives up quietly", () => {
  assert.equal(bk.mount({getContext: () => null, addEventListener() {}}), null);
  assert.equal(bk.mount({getContext: () => { throw new Error("blocked"); }}), null);
});
