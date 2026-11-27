// Analytics' pure rules and wrapper (web/src/kampstats.js). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {EVENTS, NOOP, template, templateReferrer, pageFields, clean, outcomeCode, signInDone, create} =
    require("../src/kampstats.js");
const {MESSAGES} = require("../src/intro.js");
const {IDS} = require("../src/theme.js");

const BASE = "/toys/aoty/";
const ORIGIN = "https://kamp.fm";

test("paths are templated, and only a group's slug survives", () => {
  assert.deepEqual(template(BASE, BASE), {type: "intro", path: BASE});
  assert.deepEqual(template(`${BASE}user/Alice`, BASE), {type: "user", path: `${BASE}user/:name`});
  assert.deepEqual(template(`${BASE}user/alice/`, BASE), {type: "user", path: `${BASE}user/:name`});
  assert.deepEqual(template(`${BASE}group/Night-Owls`, BASE),
                   {type: "group", path: `${BASE}group/night-owls`, group_id: "night-owls"});
  assert.deepEqual(template(`${BASE}group/not%20a%20slug`, BASE), {type: "group", path: `${BASE}group/:id`});
  // Everything else is the intro, which app.js bounces it to.
  assert.deepEqual(template(`${BASE}user/a/b`, BASE), {type: "intro", path: BASE});
  assert.deepEqual(template(`${BASE}whatever`, BASE), {type: "intro", path: BASE});
  assert.deepEqual(template("/elsewhere/user/alice", BASE), {type: "intro", path: BASE});
});

test("referrers are templated, cut to the origin, or dropped", () => {
  const ref = r => templateReferrer(r, ORIGIN, BASE);
  assert.equal(ref(""), "");
  assert.equal(ref("not a url"), "");
  assert.equal(ref(`${ORIGIN}${BASE}user/alice?x=1`), `${ORIGIN}${BASE}user/:name`);
  assert.equal(ref(`${ORIGIN}${BASE}group/crew?user=alice`), `${ORIGIN}${BASE}group/crew`);
  assert.equal(ref(`${ORIGIN}/`), `${ORIGIN}/`);
  assert.equal(ref(`${ORIGIN}/?utm=1`), `${ORIGIN}/`);
  assert.equal(ref("https://www.last.fm/user/alice"), "https://www.last.fm/");
  assert.equal(ref("https://discord.com/channels/1/2"), "https://discord.com/");
});

test("page fields carry the content group, the type and fixed titles", () => {
  const f = pageFields(`${BASE}group/crew`, ORIGIN, BASE, "");
  assert.deepEqual(f, {page_location: `${ORIGIN}${BASE}group/crew`, page_title: "group chart · a kamp toy",
                       page_referrer: "", content_group: "aoty", page_type: "group", group_id: "crew"});
  assert.equal(pageFields(`${BASE}user/alice`, ORIGIN, BASE, "").group_id, "");
  assert.equal(pageFields(`${BASE}user/alice`, ORIGIN, BASE, "").page_title, "user chart · a kamp toy");
});

test("events keep only listed params and values", () => {
  assert.equal(clean("page_view_custom", {}), null);
  assert.equal(clean("constructor", {}), null);
  assert.deepEqual(clean("intro_submit", {outcome: "ok", name: "alice"}), {outcome: "ok"});
  assert.deepEqual(clean("intro_submit", {outcome: "alice"}), {});
  assert.deepEqual(clean("control_change", {control: "filter", value: "member"}), {control: "filter", value: "member"});
  assert.deepEqual(clean("control_change", {control: "filter", value: "alice"}), {control: "filter"});
  assert.deepEqual(clean("group_loaded", {group_size: 25}), {group_size: 25});
  assert.deepEqual(clean("group_loaded", {group_size: "25"}), {});
  assert.deepEqual(clean("group_loaded", {group_size: -1}), {});
  assert.deepEqual(clean("share_copy", {url: "https://kamp.fm/toys/aoty/user/alice"}), {});
  // AOTY-84: a native share says nothing about which page either.
  assert.deepEqual(EVENTS.share_native, {});
  assert.deepEqual(clean("share_native", {url: "https://aoty.kamp.fm/alice", title: "alice's albums"}), {});
  // AOTY-70: a share image says its format and how it went, nothing about whose page or which album.
  assert.deepEqual(clean("share_image", {format: "top1_square", via: "native", album: "1989", name: "alice"}),
                   {format: "top1_square", via: "native"});
  assert.deepEqual(clean("share_image", {format: "alice", via: "alice"}), {});
  // AOTY-72, AOTY-73: the story and top 3 formats, and the image's two switches.
  for (const format of ["top1_square", "top1_story", "top3_square", "top3_story", "top10_square", "top10_story"]) {
    assert.deepEqual(clean("share_image", {format, via: "download"}), {format, via: "download"});
  }
  for (const [control, value] of [["content", "top1"], ["content", "top3"], ["content", "top10"], ["shape", "square"],
                                  ["shape", "story"]]) {
    assert.deepEqual(clean("control_change", {control, value}), {control, value});
  }
  assert.deepEqual(clean("control_change", {control: "format", value: "top1_story"}), {});  // AOTY-72's, gone
  // AOTY-43: an album link's click says nothing about which album, or whose page.
  assert.deepEqual(EVENTS.album_click, {});
  assert.deepEqual(clean("album_click", {album: "1989", artist: "Taylor Swift", user: "alice"}), {});
});

test("theme_set sends only a theme id the page knows (AOTY-65)", () => {
  assert.deepEqual(EVENTS.theme_set.theme, IDS);
  assert.deepEqual(clean("theme_set", {theme: "beach-house"}), {theme: "beach-house"});
  assert.deepEqual(clean("theme_set", {theme: "alice"}), {});
  assert.deepEqual(clean("theme_set", {theme: "kamp", user: "alice"}), {theme: "kamp"});
});

test("the intro's answers map to outcome codes", () => {
  assert.equal(outcomeCode({go: "Alice"}, MESSAGES), "ok");
  for (const key of Object.keys(MESSAGES)) {
    assert.equal(outcomeCode({message: MESSAGES[key]}, MESSAGES), key);
    assert.ok(EVENTS.intro_submit.outcome.includes(key), key);
  }
  assert.equal(outcomeCode({message: "something new"}, MESSAGES), "generic");
  assert.equal(outcomeCode(null, MESSAGES), "generic");
});

test("a sign-in counts only when this browser started one and api/me names someone", () => {
  assert.equal(signInDone("1", {username: "alice"}), true);
  assert.equal(signInDone(null, {username: "alice"}), false);
  assert.equal(signInDone("1", null), false);
  assert.equal(signInDone("1", undefined), false);
});

// A fake browser: the dataLayer, the injected script and sessionStorage.
function fakePage(pathname, {search = "", referrer = "", id = "G-TEST", hostname = "kamp.fm"} = {}) {
  const win = {};
  const loc = {pathname, search, origin: ORIGIN, hostname};
  const injected = [];
  const items = new Map();
  const storage = {getItem: k => items.get(k) ?? null, setItem: (k, v) => items.set(k, v), removeItem: k => items.delete(k)};
  const stats = create({id, base: BASE, win, loc, referrer, storage, inject: src => injected.push(src)});
  const calls = () => (win.dataLayer || []).map(a => Array.from(a));
  return {stats, win, loc, injected, items, calls};
}

test("no measurement id: nothing loads and nothing is pushed", () => {
  const p = fakePage(`${BASE}user/alice`, {id: ""});
  assert.equal(p.stats, NOOP);
  p.stats.pageView();
  p.stats.event("kamp_click");
  assert.equal(p.win.dataLayer, undefined);
  assert.deepEqual(p.injected, []);
});

test("one page view per load, with no automatic one", () => {
  const p = fakePage(`${BASE}group/crew`);
  assert.deepEqual(p.injected, ["https://www.googletagmanager.com/gtag/js?id=G-TEST"]);
  const config = p.calls().find(c => c[0] === "config");
  assert.deepEqual(config, ["config", "G-TEST", {send_page_view: false}]);
  p.stats.pageView();
  p.stats.pageView();
  const views = p.calls().filter(c => c[0] === "event" && c[1] === "page_view");
  assert.equal(views.length, 1);
  const set = p.calls().filter(c => c[0] === "set").at(-1)[1];
  assert.equal(set.group_id, "crew");
  assert.equal(set.page_type, "group");
});

test("the intro's answer is sent as its code", () => {
  const p = fakePage(BASE);
  p.stats.introSubmit({message: MESSAGES.private}, MESSAGES);
  p.stats.introSubmit({go: "Alice"}, MESSAGES);
  const sent = p.calls().filter(c => c[1] === "intro_submit").map(c => c[2].outcome);
  assert.deepEqual(sent, ["private", "ok"]);
  NOOP.introSubmit({go: "Alice"}, MESSAGES);  // the no-id wrapper takes the same calls
});

test("off kamp.fm, hits go to DebugView", () => {
  const p = fakePage(BASE, {hostname: "localhost"});
  assert.deepEqual(p.calls().find(c => c[0] === "config")[2], {send_page_view: false, debug_mode: true});
});

test("a rename's new slug reaches later hits", () => {
  const p = fakePage(`${BASE}group/crew`);
  p.stats.pageView();
  p.loc.pathname = `${BASE}group/night-owls`;
  p.stats.setPage();
  assert.equal(p.calls().filter(c => c[0] === "set").at(-1)[1].group_id, "night-owls");
});

test("the sign-in mark counts once, and a failed sign-in clears it", () => {
  const p = fakePage(BASE);
  const signIns = () => p.calls().filter(c => c[0] === "event" && c[1] === "sign_in").length;
  p.stats.checkSignIn({username: "alice"});
  assert.equal(signIns(), 0);  // nothing pending: a returning signed-in visitor
  p.stats.markSignIn();
  p.stats.checkSignIn({username: "alice"});
  p.stats.checkSignIn({username: "alice"});
  assert.equal(signIns(), 1);
  p.stats.markSignIn();
  p.stats.checkSignIn(null, true);
  assert.equal(p.items.size, 0);
  assert.equal(signIns(), 1);
});

test("blocked storage never throws", () => {
  const win = {};
  const throwing = {getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); },
                    removeItem() { throw new Error("blocked"); }};
  const stats = create({id: "G-TEST", base: BASE, win, loc: {pathname: BASE, origin: ORIGIN, hostname: "kamp.fm"},
                        referrer: "", storage: throwing, inject() {}});
  stats.markSignIn();
  stats.checkSignIn({username: "alice"});
  const none = create({id: "G-TEST", base: BASE, win: {}, loc: {pathname: BASE, origin: ORIGIN, hostname: "kamp.fm"},
                       referrer: "", storage: null, inject() {}});
  none.markSignIn();
  none.checkSignIn({username: "alice"});
});

test("a username anywhere on the page never reaches a hit", () => {
  const name = "zzsentinel";
  const p = fakePage(`${BASE}user/${name}`, {search: `?user=${name}`,
                                            referrer: `${ORIGIN}${BASE}user/${name}?user=${name}`});
  p.stats.pageView();
  p.loc.pathname = `${BASE}user/ZZSentinel`;  // the display-name rewrite
  p.stats.setPage();
  for (const event of Object.keys(EVENTS)) {
    p.stats.event(event, {outcome: name, result: name, control: name, value: name, group_size: name,
                          theme: name, format: name, via: name, name, username: name, url: `${ORIGIN}${BASE}user/${name}`,
                          album: name, artist: name, link_url: `https://www.last.fm/user/${name}`,
                          title: `${name}'s albums`, short: `https://aoty.kamp.fm/${name}`});
  }
  p.stats.event("not_listed", {name});
  p.stats.markSignIn();
  p.stats.checkSignIn({username: name});
  const sent = JSON.stringify(p.calls()).toLowerCase();
  assert.ok(!sent.includes(name), sent);
  // A group page with the member filter on: the slug is there, the member isn't.
  const g = fakePage(`${BASE}group/crew`, {search: `?user=${name}`});
  g.stats.pageView();
  g.stats.event("control_change", {control: "filter", value: "member"});
  const gsent = JSON.stringify(g.calls());
  assert.ok(gsent.includes("crew"));
  assert.ok(!gsent.toLowerCase().includes(name), gsent);
});
