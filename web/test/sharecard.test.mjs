// Share images (web/src/sharecard.js, AOTY-70). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {FORMATS, layout, palette, PALETTE, graphemes, fitText, caption, printedUrl, fileName, compose,
       fontLoads, formatOf} = require("../src/sharecard.js");
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

test("the boxes sit inside each format's safe band, in order, without overlapping", () => {
  for (const [format, f] of Object.entries(FORMATS)) {
    const L = layout(format);
    assert.deepEqual([L.width, L.height], [f.width, f.height]);
    // A cover beside the text column (AOTY-95's landscape) sits left of it; any other cover is in
    // the column's stack, between the caption and the title.
    const beside = L.cover.x + L.cover.w <= L.col.x;
    const stack = beside ? [L.brand, L.caption, L.title, L.url] : [L.brand, L.caption, L.cover, L.title, L.url];
    for (const b of [...stack, L.cover]) {
      assert.ok(b.x >= 0 && b.y >= f.safe.top && b.x + b.w <= L.width && b.y + b.h <= L.height - f.safe.bottom,
                format);
    }
    for (let i = 1; i < stack.length; i++) assert.ok(stack[i].y >= stack[i - 1].y + stack[i - 1].h, `${format} ${i}`);
    // The column inside the pad.
    assert.ok(L.col.x >= L.pad && L.col.x + L.col.w <= L.width - L.pad, format);
    // Every slot, not only #1's: its cover and its names' boxes (title, gap, artist) inside the band
    // and the pad; names in the column, below the caption and above the link, and so is a cover in
    // the stack. A cover beside the column fills the pad's height instead.
    assert.equal(L.slots.length, f.count, format);
    for (const s of L.slots) {
      const names = {x: s.title.x, y: s.title.y, w: s.title.w, h: s.title.h + s.artist.gap + s.artist.h};
      if (s.bottom) assert.ok(s.bottom <= names.y + names.h + 0.01, `${format} #${s.rank} bottom`);
      for (const b of [s.cover, names]) {
        assert.ok(b.x >= L.pad && b.x + b.w <= L.width - L.pad && b.y + b.h <= L.height - f.safe.bottom,
                  `${format} #${s.rank}`);
        if (b === s.cover && beside) {
          assert.ok(b.y >= L.pad - 1 && b.y + b.h <= L.height - L.pad + 1, `${format} #${s.rank} cover`);
        } else {
          assert.ok(b.y >= L.caption.y + L.caption.h && b.y + b.h <= L.url.y, `${format} #${s.rank}`);
        }
        if (b === names) assert.ok(b.x >= L.col.x && b.x + b.w <= L.col.x + L.col.w, `${format} #${s.rank} names`);
      }
    }
  }
});

test("the story is 9:16 and keeps clear of Instagram's and TikTok's UI", () => {
  assert.deepEqual([FORMATS.top1_story.width, FORMATS.top1_story.height], [1080, 1920]);
  assert.deepEqual(FORMATS.top1_story.safe, {top: 250, bottom: 340});
});

test("unknown formats are the square", () => {
  assert.equal(formatOf("top1_story"), "top1_story");
  for (const x of [undefined, null, "", "nope", "__proto__", "toString"]) assert.equal(formatOf(x), "top1_square", x);
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
    for (const [format, cover] of [["top1_square", true], ["top1_square", false], ["top1_story", false],
                                   ["top1_landscape", false]]) {
      for (const o of textOps(compose({format, texts: TEXTS, tokens: t, cover, measure}))) {
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

// Every op but the background, as a box: text from an ascender of one em above its baseline to a
// descender of 0.3 em below (generous for Bricolage, Inter and Arial), the rest as drawn.
function extent(o) {
  if (o.op === "text") {
    const s = sizeOf(o.font), w = measure(o.text, o.font);
    const left = o.align === "center" ? o.x - w / 2 : o.align === "right" ? o.x - w : o.x;
    return {top: o.y - s, bottom: o.y + 0.3 * s, left, right: left + w};
  }
  const size = o.op === "mark" ? o.size : null;
  return {top: o.y, bottom: o.y + (size ?? o.h), left: o.x, right: o.x + (size ?? o.w)};
}

test("nothing but the background is drawn in a format's covered strips, whatever the texts", () => {
  const long = {title: "Everything Everywhere All at Once and Then Some More And More Again", artist: "A".repeat(90),
                name: "x".repeat(60), year: 2026, url: "https://kamp.fm/toys/aoty-staging/user/" + "x".repeat(40)};
  const group = {...long, kind: "group", played: "25 of 25 members"};
  for (const [format, f] of Object.entries(FORMATS)) {
    for (const id of ["poster", "gabbie", "white-stripes"]) {
      for (const texts of [TEXTS, long, group, {...TEXTS, artist: "Godspeed You! Black Emperor"}, TOP3, LONG3,
                           {...LONG3, kind: "group", name: "y".repeat(50)}, LONG10]) {
        for (const cover of [true, false]) {
          const covers = Array.from({length: 10}, (_, i) => (i % 2 ? !cover : cover));
          const ops = compose({format, texts, tokens: complete(THEMES[id]), covers, measure});
          assert.equal(ops[0].op, "fill");
          assert.deepEqual([ops[0].w, ops[0].h], [f.width, f.height]);
          for (const o of ops.slice(1)) {
            const e = extent(o), what = `${format} ${id} ${o.op} "${o.text || ""}"`;
            assert.ok(e.top >= f.safe.top && e.bottom <= f.height - f.safe.bottom, what);
            assert.ok(e.left >= 0 && e.right <= f.width + 0.01, what);
          }
        }
      }
    }
  }
});

test("the story's link sits well clear of the bottom strip, for real fonts' sake", () => {
  const f = FORMATS.top1_story;
  const url = textOps(compose({format: "top1_story", texts: TEXTS, tokens: complete(THEMES.poster), cover: true,
                               measure})).at(-1);
  assert.equal(url.text, "aoty.kamp.fm/alice");
  assert.ok(url.y + 0.3 * sizeOf(url.font) <= f.height - f.safe.bottom - 40);
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
  for (const format of ["top1_square", "top1_story"]) {
    const ops = textOps(compose({format, texts: long, tokens: t, cover: true, measure}));
    const [, , t1, t2, artist, url] = ops;
    assert.ok(t2.y > t1.y, format);
    assert.ok(artist.y > t2.y, format);
    assert.ok(artist.y + 10 < url.y - sizeOf(url.font), format);
    assert.ok(artist.text.endsWith("…"), format);
    for (const o of ops) assert.ok(measure(o.text, o.font) <= 952 + 0.01, o.text);
  }
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

// The #1 cards' ops as they were before AOTY-73 made them slots (fixtures/top1_ops.json), less the
// slot index cover and tile ops now carry: a top 3 must never change a #1.
test("a #1 card draws exactly what it did before slots", async () => {
  const {readFileSync} = await import("node:fs");
  const cases = JSON.parse(readFileSync(new URL("./fixtures/top1_ops.json", import.meta.url), "utf8"));
  assert.ok(cases.length >= 60);
  const strip = ops => ops.map(({slot, ...o}) => o);
  for (const {format, id, texts, cover, ops} of cases) {
    assert.deepEqual(strip(compose({format, texts, tokens: complete(THEMES[id]), cover, measure})), ops,
                     `${format} ${id} ${texts.title} ${cover}`);
  }
});

// ---- top 3 (AOTY-73) ----------------------------------------------------------------------------

const NB = " ";  // the no-break space tying a rank to its title
const TOP3 = {...TEXTS, albums: [{title: "1989", artist: "Taylor Swift"}, {title: "Blue", artist: "Joni Mitchell"},
                                 {title: "Pink Moon", artist: "Nick Drake"}]};
const LONG3 = {...TEXTS, albums: [
  {title: "Everything Everywhere All at Once and Then Some More And More", artist: "A".repeat(90)},
  {title: "The Most Ridiculously Long Second Album Title Ever Pressed", artist: "Godspeed You! Black Emperor"},
  {title: "東京事変の新しいアルバムのタイトルはとても長い", artist: "東京事変".repeat(10)},
]};

// A top 10 (AOTY-74): short names, and long ones that must be cut, CJK and "#10" included.
const TOP10 = {...TEXTS, albums: Array.from({length: 10}, (_, i) => ({title: `Album ${i + 1}`, artist: `Band ${i + 1}`}))};
const LONG10 = {...TEXTS, albums: Array.from({length: 10}, (_, i) => (i % 3 === 2
  ? {title: "東京事変の新しいアルバムのタイトルはとても長い", artist: "東京事変".repeat(8)}
  : {title: `The Most Ridiculously Long Album Title Number ${i + 1} Ever Pressed To Vinyl`, artist: "B".repeat(70)}))};
// Which covers drew, for every slot up to 10: all, none, and mixed.
const COVER_MIXES = [Array(10).fill(true), Array(10).fill(false), Array.from({length: 10}, (_, i) => i % 2 === 0)];

// A text op's ink, tighter than the safe-band test's: cap height above the baseline, a short
// descender below, so lines with 1.05–1.2 leading don't count as touching.
function ink(o) {
  const s = sizeOf(o.font), w = measure(o.text, o.font);
  const left = o.align === "center" ? o.x - w / 2 : o.align === "right" ? o.x - w : o.x;
  return {top: o.y - 0.75 * s, bottom: o.y + 0.2 * s, left, right: left + w};
}
const touch = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

test("top 3 formats are square and story, with their own file names and captions", () => {
  assert.deepEqual([FORMATS.top3_square.count, FORMATS.top3_story.count], [3, 3]);
  assert.deepEqual([FORMATS.top3_square.height, FORMATS.top3_story.height], [1080, 1920]);
  assert.deepEqual(FORMATS.top3_story.safe, FORMATS.top1_story.safe);
  assert.equal(fileName("Alice", 2026, "top3_square"), "aoty-2026-Alice-top3.png");
  assert.equal(fileName("Alice", 2026, "top3_story"), "aoty-2026-Alice-top3-story.png");
  assert.equal(caption("alice", 2026, "user", 3), "alice's top 3 albums of 2026");
  assert.equal(caption("Full House", 2026, "group", 3), "Full House · top 3 albums of 2026");
  assert.equal(caption("alice", 2026, "user", 1), "alice's album of the year 2026");
});

test("a top 3 draws three slots in rank order, each title ranked", () => {
  for (const format of ["top3_square", "top3_story"]) {
    const ops = compose({format, texts: TOP3, tokens: complete(THEMES.poster), covers: [true, true, true], measure});
    assert.deepEqual(ops.filter(o => o.op === "cover").map(o => o.slot), [0, 1, 2], format);
    const texts = textOps(ops).map(o => o.text);
    assert.deepEqual(texts, ["a kamp toy", "alice's top 3 albums of 2026", `#1${NB}1989`, "Taylor Swift",
                             `#2${NB}Blue`, "Joni Mitchell", `#3${NB}Pink Moon`, "Nick Drake", "aoty.kamp.fm/alice"],
                     format);
    // #1's cover is the big one.
    const [c1, c2, c3] = ops.filter(o => o.op === "cover");
    assert.ok(c1.w > 2 * c2.w && c2.w === c3.w, format);
  }
});

test("#2 and #3 sit beside #1 on the square, below it on the story", () => {
  const sq = compose({format: "top3_square", texts: TOP3, tokens: complete(THEMES.poster), covers: [true, true, true],
                      measure}).filter(o => o.op === "cover");
  assert.ok(sq[1].x >= sq[0].x + sq[0].w && sq[2].x === sq[1].x && sq[2].y > sq[1].y);
  const st = compose({format: "top3_story", texts: TOP3, tokens: complete(THEMES.poster), covers: [true, true, true],
                      measure}).filter(o => o.op === "cover");
  assert.ok(st[1].y >= st[0].y + st[0].h && st[2].y === st[1].y && st[2].x > st[1].x);
});

test("no two texts and no text and cover overlap, at any length, in any format", () => {
  for (const format of Object.keys(FORMATS)) {
    for (const texts of [TEXTS, TOP3, LONG3, {...LONG3, kind: "group", name: "y".repeat(50), played: "25 of 25 members"},
                         TOP10, LONG10]) {
      for (const covers of COVER_MIXES) {
        const ops = compose({format, texts, tokens: complete(THEMES.gabbie), covers, measure});
        const text = textOps(ops), boxes = ops.filter(o => o.op === "cover" || o.op === "tile");
        for (let i = 0; i < text.length; i++) {
          for (let j = i + 1; j < text.length; j++) {
            assert.ok(!touch(ink(text[i]), ink(text[j])), `${format}: "${text[i].text}" / "${text[j].text}"`);
          }
          if (text[i].on === "tile" || text[i].on === "badge") continue;  // on their own cover, by design
          for (const b of boxes) {
            const box = {top: b.y, bottom: b.y + b.h, left: b.x, right: b.x + b.w};
            assert.ok(!touch(ink(text[i]), box), `${format}: "${text[i].text}" on slot ${b.slot}`);
          }
        }
        // Every text on the page colour stays inside the text column (the pad, on one-column
        // formats), and every tile's text inside its tile.
        const L = layout(format);
        for (const o of text) {
          const e = ink(o);
          const within = o.on === "tile" ? boxes.find(b => e.left >= b.x - 0.01 && e.right <= b.x + b.w + 0.01
                                                          && o.y > b.y && o.y < b.y + b.h)
            : o.on === "badge" || (e.left >= L.col.x - 0.01 && e.right <= L.col.x + L.col.w + 0.01);
          assert.ok(within, `${format}: "${o.text}" (${o.on})`);
        }
      }
    }
  }
});

test("long top 3 titles are cut, keeping their rank and the start of the title", () => {
  for (const format of ["top3_square", "top3_story"]) {
    const ops = textOps(compose({format, texts: LONG3, tokens: complete(THEMES.poster), covers: [true, true, true],
                                 measure}));
    const titles = ops.filter(o => /^#\d /.test(o.text));  // each title's first line
    assert.deepEqual(titles.map(o => o.text.slice(0, 2)), ["#1", "#2", "#3"], format);
    for (const o of titles) assert.ok(o.text.length > 6, `${format}: "${o.text}"`);  // never "#3…"
    assert.ok(ops.filter(o => o.on === "bg" && o.text.endsWith("…")).length >= 3, format);
  }
});

test("a missing cover tiles only its own slot, with its own artist, sized to fit", () => {
  for (const format of ["top3_square", "top3_story"]) {
    const t = complete(THEMES.poster);
    const ops = compose({format, texts: TOP3, tokens: t, covers: [true, false, true], measure});
    assert.deepEqual(ops.filter(o => o.op === "cover").map(o => o.slot), [0, 2]);
    const tile = ops.find(o => o.op === "tile");
    assert.equal(tile.slot, 1);
    const onTile = textOps(ops).filter(o => o.on === "tile");
    assert.equal(onTile.map(o => o.text).join(" "), "Joni Mitchell");
    for (const o of onTile) {
      const e = ink(o);
      assert.ok(e.left >= tile.x && e.right <= tile.x + tile.w && e.top >= tile.y && e.bottom <= tile.y + tile.h,
                `${format}: "${o.text}"`);
    }
  }
});

test("the member count is for #1 cards only", () => {
  const group = {...TOP3, kind: "group", name: "Full House", played: "7 of 9 members"};
  const ops = textOps(compose({format: "top3_square", texts: group, tokens: complete(THEMES.poster),
                               covers: [true, true, true], measure}));
  assert.ok(!ops.some(o => o.text === "7 of 9 members"));
  assert.equal(ops[1].text, "Full House · top 3 albums of 2026");
});

test("a top 3 loads every title's and artist's fonts", () => {
  const loads = fontLoads({texts: {...TOP3, albums: [...TOP3.albums.slice(0, 2), {title: "Ægis", artist: "Björk"}]},
                           tokens: complete(THEMES.poster), format: "top3_square"});
  assert.ok(loads.some(([f, s]) => f.includes("Bricolage") && s.includes("Ægis") && s.includes("#3")));
  assert.ok(loads.some(([f, s]) => f.startsWith("500") && s.includes("Björk") && s.includes("Joni Mitchell")));
  assert.ok(loads.some(([, s]) => s === "alice's top 3 albums of 2026"));
});

// ---- top 10 (AOTY-74) ---------------------------------------------------------------------------

const ALL = Array(10).fill(true);

test("top 10 formats, file names and captions", () => {
  assert.deepEqual([FORMATS.top10_square.count, FORMATS.top10_story.count], [10, 10]);
  assert.equal(fileName("Alice", 2026, "top10_square"), "aoty-2026-Alice-top10.png");
  assert.equal(fileName("Alice", 2026, "top10_story"), "aoty-2026-Alice-top10-story.png");
  assert.equal(caption("alice", 2026, "user", 10), "alice's top 10 albums of 2026");
  assert.equal(caption("Full House", 2026, "group", 10), "Full House · top 10 albums of 2026");
});

test("a top 10 is a 5x2 grid across, then a list down two columns, in rank order", () => {
  for (const format of ["top10_square", "top10_story"]) {
    const ops = compose({format, texts: TOP10, tokens: complete(THEMES.poster), covers: ALL, measure});
    const covers = ops.filter(o => o.op === "cover");
    assert.deepEqual(covers.map(o => o.slot), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], format);
    // The grid: five across, then the next five under them.
    for (let i = 1; i < 5; i++) assert.ok(covers[i].x > covers[i - 1].x && covers[i].y === covers[0].y, format);
    for (let i = 5; i < 10; i++) assert.ok(covers[i].x === covers[i - 5].x && covers[i].y > covers[0].y, format);
    // The list: the titles in rank order, 1–5 down the left column and 6–10 down the right.
    const titles = textOps(ops).filter(o => /^#\d+ /.test(o.text));
    assert.deepEqual(titles.map(o => o.text), TOP10.albums.map((a, i) => `#${i + 1}${NB}${a.title}`), format);
    for (let i = 1; i < 5; i++) assert.ok(titles[i].x === titles[0].x && titles[i].y > titles[i - 1].y, format);
    for (let i = 5; i < 10; i++) assert.ok(titles[i].x > titles[0].x && titles[i].y === titles[i - 5].y, format);
    // The list starts below the grid.
    const gridBottom = Math.max(...covers.map(o => o.y + o.h));
    for (const o of titles) assert.ok(ink(o).top > gridBottom, format);
  }
});

test("each grid cover carries its rank on a page-colour badge, over the cover", () => {
  for (const id of Object.keys(THEMES)) {
    const t = complete(THEMES[id]);
    const ops = compose({format: "top10_square", texts: TOP10, tokens: t, covers: ALL, measure});
    const badges = textOps(ops).filter(o => o.on === "badge");
    assert.deepEqual(badges.map(o => o.text), ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"], id);
    for (const b of badges) {
      const fill = ops.find(o => o.op === "fill" && o.slot === Number(b.text) - 1);
      const cover = ops.find(o => o.op === "cover" && o.slot === Number(b.text) - 1);
      assert.equal(fill.color, t.bg, id);
      assert.ok(contrast(b.color, t.bg) >= TEXT_MIN, `${id}: badge ${b.text}`);
      // Inside its fill, and the fill inside its cover's corner.
      const e = ink(b);
      assert.ok(e.left >= fill.x && e.right <= fill.x + fill.w && e.top >= fill.y && e.bottom <= fill.y + fill.h,
                `${id}: badge ${b.text}`);
      assert.ok(fill.x === cover.x && fill.y + fill.h === cover.y + cover.h && fill.w < cover.w / 2, id);
      // Drawn after its cover.
      assert.ok(ops.indexOf(fill) > ops.indexOf(cover), id);
    }
  }
});

test("every cover is drawn before any title, so no shadow falls on one", () => {
  for (const format of Object.keys(FORMATS)) {
    const ops = compose({format, texts: TOP10, tokens: complete(THEMES.poster), covers: ALL, measure});
    // The names are the texts after the caption (the brand and caption come first; on the
    // landscape they sit level with the cover).
    const lastCover = Math.max(...ops.map((o, i) => (o.op === "cover" || o.op === "tile" ? i : -1)));
    const caption = ops.findIndex(o => o.op === "text" && /albums? of/.test(o.text));
    const firstName = ops.findIndex((o, i) => i > caption && o.op === "text" && o.on === "bg");
    assert.ok(caption > 0 && firstName > lastCover, format);
    assert.equal(ops[firstName].text, FORMATS[format].count > 1 ? `#1${NB}Album 1` : "Album 1", format);
  }
});

test("small covers cast a smaller shadow; the #1's stays as it was", () => {
  const ops = compose({format: "top10_square", texts: TOP10, tokens: complete(THEMES.poster), covers: ALL, measure});
  const grid = ops.find(o => o.op === "cover");
  assert.deepEqual([grid.blur, grid.drop], [16, 6]);
  const solo = compose({format: "top1_square", texts: TEXTS, tokens: complete(THEMES.poster), cover: true, measure});
  const big = solo.find(o => o.op === "cover");
  assert.ok(!("blur" in big) && !("drop" in big));
});

test("a badged tile keeps the artist's name clear of the badge", () => {
  const ops = compose({format: "top10_square", texts: LONG10, tokens: complete(THEMES.poster),
                       covers: Array(10).fill(false), measure});
  for (const badge of textOps(ops).filter(o => o.on === "badge")) {
    const slot = Number(badge.text) - 1;
    const tile = ops.find(o => o.op === "tile" && o.slot === slot);
    const names = textOps(ops).filter(o => o.on === "tile" && o.x === tile.x + tile.w / 2 && o.y > tile.y
                                       && o.y < tile.y + tile.h);
    assert.ok(names.length >= 1);
    for (const n of names) assert.ok(ink(n).bottom <= tile.y + tile.h - 34, `slot ${slot}: "${n.text}"`);
  }
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
  assert.equal(fileName("Alice", 2026, "top1_square"), "aoty-2026-Alice.png");
  assert.equal(fileName("Alice", 2026, "top1_story"), "aoty-2026-Alice-story.png");
  assert.equal(fileName("東京", 2026, "top1_story"), "aoty-2026-story.png");
  assert.equal(fileName("Alice", 2026, "nope"), "aoty-2026-Alice.png");
});

// ---- the landscape link preview card (AOTY-95) --------------------------------------------------

const WIDE = "top1_landscape";
const wide = (texts, cover = true, id = "poster") => compose({format: WIDE, texts, tokens: complete(THEMES[id]), cover,
                                                             measure});

test("the landscape card is 1200x630 (1.91:1), with the cover on the left and every text right of it", () => {
  assert.deepEqual([FORMATS[WIDE].width, FORMATS[WIDE].height], [1200, 630]);
  assert.equal(fileName("Alice", 2026, WIDE), "aoty-2026-Alice-wide.png");
  const L = layout(WIDE);
  for (const texts of [TEXTS, GROUP, {...GROUP, name: "x".repeat(60)}]) {
    for (const cover of [true, false]) {
      const ops = wide(texts, cover);
      const c = ops.find(o => o.op === "cover" || o.op === "tile");
      assert.deepEqual([c.x, c.y, c.w, c.h], [56, 55, 520, 520]);
      assert.ok(!("blur" in c), "the #1's shadow, as on the square");
      for (const o of textOps(ops).filter(o => o.on === "bg")) {
        const e = ink(o);
        assert.ok(e.left >= c.x + c.w + 32 && e.right <= L.col.x + L.col.w + 0.01, `"${o.text}"`);
        // One left edge for the column, the member count alone right-aligned against its end.
        if (o.text === texts.played) assert.equal(o.align, "right");
        else if (o.text !== "a kamp toy") assert.deepEqual([o.align, o.x], ["left", L.col.x], `"${o.text}"`);
      }
    }
  }
});

test("the landscape's link ends level with the cover, and the names sit just above it", () => {
  for (const texts of [TEXTS, {...TEXTS, title: "Everything Everywhere All at Once and Then Some More"}]) {
    const ops = wide(texts);
    const cover = ops.find(o => o.op === "cover"), text = textOps(ops);
    const url = text.at(-1), artist = text.at(-2);
    assert.equal(url.text, "aoty.kamp.fm/alice");
    assert.ok(url.y + 0.3 * sizeOf(url.font) <= cover.y + cover.h, texts.title);
    // Anchored: the artist ends at the slot's bottom, whether the title takes one line or two.
    assert.ok(Math.abs(artist.y + 0.28 * sizeOf(artist.font) - layout(WIDE).slots[0].bottom) < 0.01, texts.title);
    assert.ok(!touch(ink(artist), ink(url)), texts.title);
  }
});

test("the landscape caption keeps one line when it fits, and two when it must", () => {
  const short = textOps(wide(TEXTS)).filter(o => o.color === complete(THEMES.poster).accent);
  assert.deepEqual(short.map(o => o.text), ["alice's album of the year 2026"]);
  const group = textOps(wide({...GROUP, name: "the extremely long-winded listening club"}));
  const cap = group.filter(o => o.font.startsWith("600"));
  assert.equal(cap.length, 2);
  assert.ok(cap[1].text.endsWith("album of the year 2026"), cap.map(o => o.text).join(" / "));
});

test("on the landscape, long texts are fitted to the column and a long group name is cut, never the year", () => {
  const L = layout(WIDE);
  const long = {...TEXTS, title: "Everything Everywhere All at Once and Then Some More", artist: "A".repeat(80),
                url: "https://kamp.fm/toys/aoty-staging/user/" + "x".repeat(40)};
  for (const o of textOps(wide(long))) assert.ok(measure(o.text, o.font) <= L.col.w + 0.01, o.text);
  for (const name of ["x".repeat(80), "東".repeat(60)]) {
    const cap = textOps(wide({...GROUP, name})).filter(o => o.font.startsWith("600"));
    assert.ok(cap.at(-1).text.endsWith("… · album of the year 2026"), cap.map(o => o.text).join(" / "));
  }
});

test("the landscape's member count sits right of the brand, inside the column", () => {
  const L = layout(WIDE);
  for (const played of ["7 of 9 members", "a much longer count than any group would ever have"]) {
    const [brand, count] = textOps(wide({...GROUP, played}));
    assert.equal(count.x, L.col.x + L.col.w);
    assert.ok(count.x - measure(count.text, count.font) >= brand.x + measure(brand.text, brand.font) + 32 - 0.01, played);
  }
});
