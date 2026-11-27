// Share images (web/src/sharecard.js, AOTY-70). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {FORMATS, layout, palette, PALETTE, graphemes, fitText, caption, printedUrl, fileName, compose,
       fontLoads} = require("../src/sharecard.js");
const {THEMES, complete, contrast, mix, TEXT_MIN} = require("../src/theme.js");

// A stand-in for canvas measureText: every character is 0.6em wide, read off the font string.
const sizeOf = font => Number(/(\d+(?:\.\d+)?)px/.exec(font)[1]);
const measure = (text, font) => graphemes(text).length * sizeOf(font) * 0.6;
const font = s => `800 ${s}px X`;
const SIZES = [64, 52, 40];

test("short text fits on one line at the largest size", () => {
  assert.deepEqual(fitText({text: "Blue", maxWidth: 900, maxLines: 2, sizes: SIZES, font, measure}),
                   {size: 64, lines: ["Blue"], cut: false});
});

test("empty or blank text draws nothing", () => {
  for (const text of ["", "   ", null, undefined]) {
    assert.deepEqual(fitText({text, maxWidth: 900, maxLines: 2, sizes: SIZES, font, measure}).lines, [], String(text));
  }
});

test("longer text steps down a size before it wraps past the line limit", () => {
  // 30 characters: 1152 px at 64 (two lines fit), so it stays at 64 on two lines.
  const two = fitText({text: "you seem pretty sad for a girl", maxWidth: 900, maxLines: 2, sizes: SIZES, font, measure});
  assert.equal(two.size, 64);
  assert.equal(two.lines.length, 2);
  // On one line it has to shrink: 30 × 0.6 × 40 = 720 fits at 40 only.
  const one = fitText({text: "you seem pretty sad for a girl", maxWidth: 900, maxLines: 1, sizes: SIZES, font, measure});
  assert.deepEqual(one, {size: 40, lines: ["you seem pretty sad for a girl"], cut: false});
});

test("very long text is cut with an ellipsis at the smallest size, never wider than the line", () => {
  const text = "the most ridiculously long album title anybody ever put on a record sleeve, deluxe";
  const fit = fitText({text, maxWidth: 600, maxLines: 2, sizes: SIZES, font, measure});
  assert.equal(fit.size, 40);
  assert.equal(fit.cut, true);
  assert.equal(fit.lines.length, 2);
  assert.ok(fit.lines[1].endsWith("…"));
  for (const l of fit.lines) assert.ok(measure(l, font(40)) <= 600, l);
});

test("a single word wider than the line breaks between characters", () => {
  const word = "x".repeat(60);
  const fit = fitText({text: word, maxWidth: 600, maxLines: 3, sizes: SIZES, font, measure});
  assert.equal(fit.lines.join(""), word);
  for (const l of fit.lines) assert.ok(measure(l, font(fit.size)) <= 600);
});

test("a smaller size wins over breaking a word in the middle", () => {
  // "Godspeed" is 8 × 0.6 × 96 = 461 px at 96: too wide for 430, so the fit steps down to 84.
  const fit = fitText({text: "Godspeed You! Black Emperor", maxWidth: 430, maxLines: 5, sizes: [96, 84, 72],
                       font, measure});
  assert.equal(fit.size, 84);
  assert.deepEqual(fit.lines.join(" ").split(" "), ["Godspeed", "You!", "Black", "Emperor"]);
});

test("CJK with no spaces wraps, and emoji and accents are never split", () => {
  const cjk = "東京事変の新しいアルバムのタイトルはとても長い";
  const fit = fitText({text: cjk, maxWidth: 500, maxLines: 3, sizes: SIZES, font, measure});
  assert.equal(fit.lines.join(""), cjk);
  assert.ok(fit.lines.length > 1);
  const emoji = "👩‍👩‍👧‍👦".repeat(30) + "é".normalize("NFD").repeat(10);
  const cut = fitText({text: emoji, maxWidth: 400, maxLines: 2, sizes: SIZES, font, measure});
  for (const l of cut.lines) {
    for (const g of graphemes(l.replace("…", ""))) assert.ok(g === "👩‍👩‍👧‍👦" || g === "é".normalize("NFD"), g);
  }
});

test("the boxes sit inside the canvas, in order, without overlapping", () => {
  const L = layout("top1_square");
  assert.deepEqual([L.width, L.height], [FORMATS.top1_square.width, FORMATS.top1_square.height]);
  const stack = [L.brand, L.caption, L.cover, L.title, L.url];
  for (const b of stack) {
    assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.w <= L.width && b.y + b.h <= L.height);
  }
  for (let i = 1; i < stack.length; i++) assert.ok(stack[i].y >= stack[i - 1].y + stack[i - 1].h, `box ${i}`);
});

const TEXTS = {title: "1989", artist: "Taylor Swift", name: "alice", year: 2026, url: "https://aoty.kamp.fm/alice"};
const textOps = ops => ops.filter(o => o.op === "text");

test("every preset's text colours read at 4.5:1 on the card's background", () => {
  for (const [id, preset] of Object.entries(THEMES)) {
    const t = complete(preset);
    for (const [what, fg] of Object.entries(palette(t))) {
      assert.ok(contrast(fg, t.bg) >= TEXT_MIN, `${id}: ${what} (${PALETTE[what]}) on bg`);
    }
    // And as drawn: every text op over bg or, on the tile, over its gradient's start and middle.
    for (const cover of [true, false]) {
      for (const o of textOps(compose({texts: TEXTS, tokens: t, cover, measure}))) {
        const under = o.on === "tile" ? [t.tile, mix(t.tile, t.tileEnd, 0.5)] : [t.bg];
        for (const bg of under) assert.ok(contrast(o.color, bg) >= TEXT_MIN, `${id}: "${o.text}" on ${o.on}`);
      }
    }
  }
});

test("the card: background, mark, brand, caption, cover, title, artist and link, in that order", () => {
  const t = complete(THEMES.poster);
  const ops = compose({texts: TEXTS, tokens: t, cover: true, measure});
  assert.deepEqual(ops.map(o => o.op), ["fill", "mark", "text", "text", "cover", "text", "text", "text"]);
  assert.deepEqual(textOps(ops).map(o => o.text),
                   ["a kamp toy", "alice's album of the year 2026", "1989", "Taylor Swift", "aoty.kamp.fm/alice"]);
  assert.equal(ops[0].color, t.bg);
  // Text never runs past the canvas, and every colour is a real one.
  for (const o of textOps(ops)) {
    assert.ok(o.y > 0 && o.y < 1080, o.text);
    assert.match(o.color, /^#[0-9a-f]{6}$/i);
  }
});

test("with no usable cover, the artist-name tile in the theme's tile colours", () => {
  const t = complete(THEMES["white-stripes"]);
  const ops = compose({texts: {...TEXTS, artist: "Godspeed You! Black Emperor"}, tokens: t, cover: false, measure});
  assert.ok(!ops.some(o => o.op === "cover"));
  const tile = ops.find(o => o.op === "tile");
  assert.deepEqual([tile.from, tile.to], [t.tile, t.tileEnd]);
  const onTile = textOps(ops).filter(o => o.on === "tile");
  for (const o of onTile) assert.ok(o.color === t.tileInk && o.y > tile.y && o.y < tile.y + tile.h);
  assert.ok(onTile.length >= 1);
  assert.equal(onTile.map(o => o.text).join(" "), "Godspeed You! Black Emperor");
});

test("the title wraps to two lines and the artist follows it without overlapping the link", () => {
  const t = complete(THEMES.poster);
  const long = {...TEXTS, title: "Everything Everywhere All at Once and Then Some More", artist: "A".repeat(80)};
  const ops = textOps(compose({texts: long, tokens: t, cover: true, measure}));
  const [, , t1, t2, artist, url] = ops;
  assert.ok(t2.y > t1.y);
  assert.ok(artist.y > t2.y);
  assert.ok(artist.y + 10 < url.y - sizeOf(url.font));
  assert.ok(artist.text.endsWith("…"));
  for (const o of ops) assert.ok(measure(o.text, o.font) <= 952 + 0.01, o.text);
});

test("a long link (off prod) is fitted too", () => {
  const t = complete(THEMES.poster);
  const url = "https://kamp.fm/toys/aoty-staging/user/" + "x".repeat(40);
  const op = textOps(compose({texts: {...TEXTS, url}, tokens: t, cover: true, measure})).at(-1);
  assert.ok(measure(op.text, op.font) <= 952);
});

test("gabbie draws its headings in Arial, everything else in Bricolage", () => {
  const g = textOps(compose({texts: TEXTS, tokens: complete(THEMES.gabbie), cover: true, measure}));
  assert.match(g.find(o => o.text === "1989").font, /^700 .*Arial/);
  const p = textOps(compose({texts: TEXTS, tokens: complete(THEMES.poster), cover: true, measure}));
  assert.match(p.find(o => o.text === "1989").font, /^800 .*Bricolage/);
});

test("fonts are loaded with the real text, so the right subsets come", () => {
  const loads = fontLoads({texts: {...TEXTS, title: "Ægis", artist: "Björk"}, tokens: complete(THEMES.poster)});
  assert.ok(loads.some(([f, s]) => f.includes("Bricolage") && s.includes("Ægis") && s.includes("Björk")));
  assert.ok(loads.some(([f]) => f.includes("Inter")));
});

test("the caption is always 's", () => {
  assert.equal(caption("alice", 2026), "alice's album of the year 2026");
  assert.equal(caption("chris", 2026), "chris's album of the year 2026");
});

const GROUP = {...TEXTS, name: "Full House", kind: "group", played: "7 of 9 members",
               url: "https://aoty.kamp.fm/g/full-house"};

test("a group's caption is its name, then the year (AOTY-71)", () => {
  assert.equal(caption("Full House", 2026, "group"), "Full House · album of the year 2026");
  const ops = textOps(compose({texts: GROUP, tokens: complete(THEMES.poster), cover: true, measure}));
  assert.deepEqual(ops.map(o => o.text),
                   ["a kamp toy", "7 of 9 members", "Full House · album of the year 2026", "1989", "Taylor Swift",
                    "aoty.kamp.fm/g/full-house"]);
});

test("a long group name is cut, never the year", () => {
  for (const name of ["x".repeat(50), "the extremely long-winded listening club of new york", "東".repeat(50)]) {
    const ops = textOps(compose({texts: {...GROUP, name}, tokens: complete(THEMES.poster), cover: true, measure}));
    const cap = ops[2];
    assert.ok(cap.text.endsWith("… · album of the year 2026"), cap.text);
    assert.ok(measure(cap.text, cap.font) <= 952 + 0.01, cap.text);
  }
});

test("the member count sits right of the brand, inside the pad, at any length", () => {
  const t = complete(THEMES.poster);
  for (const played of ["7 of 9 members", "25 of 25 members", "a much longer count than any group would ever have"]) {
    const ops = textOps(compose({texts: {...GROUP, played}, tokens: t, cover: true, measure}));
    const [brand, count] = ops;
    assert.equal(count.align, "right");
    assert.equal(count.x, 1080 - 64);
    assert.equal(count.y, brand.y);
    assert.equal(count.color, t.muted);
    const brandEnd = brand.x + measure(brand.text, brand.font);
    assert.ok(count.x - measure(count.text, count.font) >= brandEnd + 32 - 0.01, played);
  }
});

test("no member count, no op: users, member views and groups still loading", () => {
  const ops = textOps(compose({texts: {...GROUP, played: null}, tokens: complete(THEMES.poster), cover: true,
                               measure}));
  assert.deepEqual(ops.map(o => o.text).slice(0, 2), ["a kamp toy", "Full House · album of the year 2026"]);
});

test("a group's count and caption load their fonts", () => {
  const loads = fontLoads({texts: {...GROUP, played: "7 of 9 членов"}, tokens: complete(THEMES.poster)});
  assert.ok(loads.some(([f, s]) => f.startsWith("500") && f.includes("Inter") && s.includes("членов")));
  assert.ok(loads.some(([f, s]) => f.startsWith("600") && s === "Full House · album of the year 2026"));
});

test("the printed link drops its scheme", () => {
  assert.equal(printedUrl("https://aoty.kamp.fm/Alice"), "aoty.kamp.fm/Alice");
  assert.equal(printedUrl("http://localhost:8001/toys/aoty/user/a"), "localhost:8001/toys/aoty/user/a");
});

test("file names are safe everywhere", () => {
  assert.equal(fileName("Alice", 2026), "aoty-2026-Alice.png");
  assert.equal(fileName("a.b", 2026), "aoty-2026-a-b.png");
  assert.equal(fileName("Björk", 2026), "aoty-2026-Bjork.png");
  assert.equal(fileName("../../etc", 2026), "aoty-2026-etc.png");
  assert.equal(fileName("東京", 2026), "aoty-2026.png");
  assert.equal(fileName("", 2026), "aoty-2026.png");
  assert.ok(fileName("x".repeat(200), 2026).length <= 80);
});
