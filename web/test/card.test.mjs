// The card Lambda (lambdas/card/handler.mjs, AOTY-91): its pure rules, and a real render on
// @napi-rs/canvas with stand-in S3 and fetch. Needs `npm ci --prefix lambdas/card` first.
// Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import test from "node:test";

const CANVAS = existsSync(new URL("../../lambdas/card/node_modules/@napi-rs/canvas", import.meta.url));
const card = await import("../../lambdas/card/handler.mjs");
const canvas = CANVAS ? await import("../../lambdas/card/node_modules/@napi-rs/canvas/index.js") : null;

const ENV = {SITE_BUCKET: "site", SITE_PREFIX: "toys/aoty/", PUBLIC_ORIGIN: "https://kamp.fm", BASE_PATH: "/toys/aoty/"};
const userChart = (over = {}) => ({
  username: "alice", display_name: "AliCe", year: 2026, generated_at: 1795000000,
  albums: [{album: "Blue Lines", artist: "Massive Attack", art: "https://lastfm.freetls.fastly.net/i/u/300x300/a.png",
            fallbacks: ["https://lastfm.freetls.fastly.net/i/u/ar0/a.png"]}],
  rankings: {album: [[0, 12.5]]}, ...over,
});
const groupChart = {
  kind: "group", id: "full-house", name: "Full House", year: 2026, generated_at: 1795000001,
  members: ["alice", "bob", "carol"], waiting: 0, unavailable: 0,
  albums: [{album: "Hey What", artist: "Low", art: "", listeners: 3}], rankings: {listener: [[0, 3]]},
};

test("the card's input is the page's: Everyone's #1, the name and the share link", () => {
  const u = card.cardInput(userChart(), {origin: "https://kamp.fm", base: "/toys/aoty/", short: "aoty.kamp.fm"});
  assert.deepEqual(u.texts, {albums: [{title: "Blue Lines", artist: "Massive Attack"}], name: "AliCe", year: 2026,
                             url: "https://aoty.kamp.fm/AliCe", kind: "user", played: null});
  assert.deepEqual(u.urls, ["https://lastfm.freetls.fastly.net/i/u/300x300/a.png",
                            "https://lastfm.freetls.fastly.net/i/u/ar0/a.png"]);
  const g = card.cardInput(groupChart, {origin: "https://kamp.fm", base: "/toys/aoty-staging/", short: ""});
  assert.equal(g.texts.url, "https://kamp.fm/toys/aoty-staging/group/full-house");
  assert.equal(g.texts.played, "3 of 3 members");
  assert.deepEqual(g.urls, []);
  assert.equal(card.cardInput(userChart({rankings: {album: []}}), {origin: "", base: "/"}), null);
});

test("only text every font can draw gets a card", () => {
  const texts = albums => ({name: "alice", url: "https://kamp.fm/x", played: null, albums});
  assert.ok(card.covered(texts([{title: "Ágætis byrjun — “live”", artist: "Sigur Rós"}])));
  for (const bad of ["音楽", "Кино", "🎧", "Ελλάδα"]) {
    assert.ok(!card.covered(texts([{title: bad, artist: "x"}])), bad);
  }
  assert.ok(!card.covered({...texts([]), name: "café 🎧"}));
});

test("the theme is the page's: a preset, album of the year from the cover, else the poster", async () => {
  const th = (await import("../src/theme.js")).default;
  assert.deepEqual(card.cardTokens("beach-house", () => null), th.complete(th.tokens("beach-house")));
  assert.deepEqual(card.cardTokens(undefined, () => null), th.complete(th.THEMES.poster));
  assert.deepEqual(card.cardTokens("nope", () => null), th.complete(th.THEMES.poster));
  const red = new Uint8ClampedArray(32 * 32 * 4).map((_, i) => [220, 40, 40, 255][i % 4]);
  assert.deepEqual(card.cardTokens(th.MATCH, () => red), th.complete(th.derive(red)));
  assert.deepEqual(card.cardTokens(th.MATCH, () => null), th.complete(th.THEMES.poster));  // no cover
});

test("a card is only ever written to a chart's og.png", () => {
  for (const ok of ["toys/aoty/data/users/alice/og.png", "toys/aoty/data/users/_korps_/og.png",
                    "toys/aoty/data/groups/full-house/og.png"]) assert.ok(card.validKey(ok, "toys/aoty/"), ok);
  for (const bad of ["toys/aoty/data/users/alice/chart.json", "toys/aoty/index.html", "data/users/alice/og.png",
                     "toys/aoty/data/users/../og.png", "toys/aoty/data/users/a/b/og.png", null]) {
    assert.ok(!card.validKey(bad, "toys/aoty/"), String(bad));
  }
});

test("render refuses a bad key, an empty chart and uncovered text without drawing", async () => {
  const deps = {env: ENV, canvas: {}, fetch: () => { throw new Error("no fetch"); }, put: () => { throw new Error("no put"); }};
  assert.deepEqual(await card.render({chart: userChart(), key: "toys/aoty/index.html"}, deps), {ok: false, reason: "invalid"});
  assert.deepEqual(await card.render({chart: userChart({rankings: {album: []}}), key: "toys/aoty/data/users/alice/og.png"}, deps),
                   {ok: false, reason: "empty"});
  const cjk = userChart({display_name: "アリス"});
  assert.deepEqual(await card.render({chart: cjk, key: "toys/aoty/data/users/alice/og.png"}, deps), {ok: false, reason: "glyphs"});
});

test("a real render: a 1080x1080 PNG, the cover drawn, or its tile when the cover fails", {skip: !CANVAS && "npm ci --prefix lambdas/card"}, async () => {
  // A 300x300 cover, made on the spot, served by a stand-in fetch.
  const c = canvas.createCanvas(300, 300), x = c.getContext("2d");
  x.fillStyle = "#c03030";
  x.fillRect(0, 0, 300, 300);
  const cover = await c.encode("png");
  for (const [fetchOk, theme] of [[true, "match1"], [false, undefined]]) {
    const puts = [], fetched = [];
    const deps = {
      env: ENV, canvas,
      fetch: async url => { fetched.push(url); return fetchOk ? {ok: true, arrayBuffer: async () => cover} : {ok: false}; },
      put: async (bucket, key, body) => puts.push({bucket, key, body}),
    };
    const out = await card.render({chart: userChart({theme}), key: "toys/aoty/data/users/alice/og.png"}, deps);
    assert.deepEqual(out, {ok: true, key: "toys/aoty/data/users/alice/og.png", generated_at: 1795000000, cover: fetchOk});
    assert.equal(fetched.length, fetchOk ? 1 : 2);  // a failing cover tries its fallback, then tiles
    assert.equal(puts.length, 1);
    const png = puts[0].body;
    assert.deepEqual([...png.subarray(1, 4)].map(b => String.fromCharCode(b)).join(""), "PNG");
    const img = await canvas.loadImage(png);
    assert.deepEqual([img.width, img.height], [1080, 1080]);
  }
});
