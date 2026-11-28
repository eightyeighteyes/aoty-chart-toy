// Share links and the share button's rules (web/src/share.js, AOTY-84). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {shareUrl, useNative, shareTitle, shareLabels, userTop, userTops, CONTENTS, SHAPES, cardContent, formatId,
       imageSubject, imageLabel, cardTexts, previewAlt, previewSubject} = require("../src/share.js");

const PROD = {origin: "https://kamp.fm", base: "/toys/aoty/", short: "aoty.kamp.fm"};
const STAGING = {origin: "https://kamp.fm", base: "/toys/aoty-staging/", short: ""};

test("prod links use the short host, in the name's casing", () => {
  assert.equal(shareUrl({...PROD, kind: "user", id: "Alice"}), "https://aoty.kamp.fm/Alice");
  assert.equal(shareUrl({...PROD, kind: "user", id: "Some_User-2"}), "https://aoty.kamp.fm/Some_User-2");
  assert.equal(shareUrl({...PROD, kind: "group", id: "full-house"}), "https://aoty.kamp.fm/g/full-house");
  assert.equal(shareUrl({...PROD, kind: "group", id: "a"}), "https://aoty.kamp.fm/g/a");
});

test("without a short host, links are the full page with no query", () => {
  assert.equal(shareUrl({...STAGING, kind: "user", id: "Alice"}), "https://kamp.fm/toys/aoty-staging/user/Alice");
  assert.equal(shareUrl({...STAGING, kind: "group", id: "the-crew"}),
               "https://kamp.fm/toys/aoty-staging/group/the-crew");
  assert.equal(shareUrl({...PROD, short: undefined, kind: "group", id: "the-crew"}),
               "https://kamp.fm/toys/aoty/group/the-crew");
});

test("a name that starts with an underscore gets the short link too (AOTY-89)", () => {
  assert.equal(shareUrl({...PROD, kind: "user", id: "_korps_"}), "https://aoty.kamp.fm/_korps_");
});

test("a name the short host won't take falls back to the full URL", () => {
  // One-letter first segments are reserved for short routes (/g), so a one-letter name isn't short.
  assert.equal(shareUrl({...PROD, kind: "user", id: "g"}), "https://kamp.fm/toys/aoty/user/g");
  // USERNAME_RE allows a dot; aoty.kamp.fm's rule doesn't.
  assert.equal(shareUrl({...PROD, kind: "user", id: "a.b"}), "https://kamp.fm/toys/aoty/user/a.b");
  // "_" alone is one character, so reserved like "g".
  assert.equal(shareUrl({...PROD, kind: "user", id: "_"}), "https://kamp.fm/toys/aoty/user/_");
  assert.equal(shareUrl({...PROD, kind: "user", id: "-x"}), "https://kamp.fm/toys/aoty/user/-x");
  assert.equal(shareUrl({...PROD, kind: "user", id: "x".repeat(65)}),
               `https://kamp.fm/toys/aoty/user/${"x".repeat(65)}`);
  assert.equal(shareUrl({...PROD, kind: "user", id: "a b"}), "https://kamp.fm/toys/aoty/user/a%20b");
  assert.equal(shareUrl({...PROD, kind: "group", id: "Not_A_Slug"}), "https://kamp.fm/toys/aoty/group/Not_A_Slug");
});

test("native share only on a coarse pointer, and only where it can share the link", () => {
  const data = {url: "https://aoty.kamp.fm/alice", title: "t"};
  const share = () => Promise.resolve();
  assert.equal(useNative({share}, true, data), true);
  assert.equal(useNative({share, canShare: () => true}, true, data), true);
  assert.equal(useNative({share, canShare: () => false}, true, data), false);
  assert.equal(useNative({share}, false, data), false);  // desktop copies (user decision)
  assert.equal(useNative({}, true, data), false);
  assert.equal(useNative(null, true, data), false);
  assert.equal(useNative({share, canShare: () => { throw new Error("no"); }}, true, data), false);
});

test("the share title names the page, never the tab's queue number", () => {
  assert.equal(shareTitle({kind: "user", name: "Alice", year: "2026", product: "albums of the year"}),
               "Alice's albums of the year 2026");
  assert.equal(shareTitle({kind: "group", name: "Full House", year: "2026", product: "albums of the year"}),
               "Full House · albums of the year 2026");
});

test("labels: one button, or the group and the member in a member's view", () => {
  // AOTY-98: the same words whether a tap shares or copies.
  assert.equal(shareLabels.length, 1);
  assert.deepEqual(shareLabels(null), {main: "share link", member: null});
  assert.deepEqual(shareLabels("bob"), {main: "share group link", member: "share bob's link"});
});

test("files go through the share sheet only where canShare confirms them (AOTY-70)", () => {
  const files = {files: [{name: "aoty-2026-alice.png", type: "image/png"}]};
  const share = () => Promise.resolve();
  assert.equal(useNative({share, canShare: d => !!d.files}, true, files), true);
  // Firefox Android: a coarse pointer and share(), but no file sharing.
  assert.equal(useNative({share, canShare: d => !d.files}, true, files), false);
  assert.equal(useNative({share}, true, files), false);  // no canShare: can't tell, so download
  assert.equal(useNative({share, canShare: () => true}, false, files), false);  // desktop downloads
});

test("a user chart's #1 is its By Play leader, once it ranks something", () => {
  const ranked = {albums: [{album: "B"}, {album: "A"}], rankings: {album: [[1, 9.5], [0, 2]]}};
  assert.equal(userTop(ranked).album, "A");
  assert.equal(userTop({albums: [], rankings: {album: []}}), null);  // a first visit's chart
  assert.equal(userTop({}), null);
  assert.equal(userTop(null), null);
});

const alice = {kind: "user", id: "Alice", name: "Alice"};
const house = {kind: "group", id: "full-house", name: "Full House"};
const groupChart = {members: ["alice", "bob", "carol"], unavailable: 1, waiting: 0};
const hit = {album: "1989", listeners: 3};

test("a user page's image is theirs", () => {
  const s = imageSubject({target: alice, chart: {}, top: hit, member: ""});
  assert.deepEqual(s, {kind: "user", name: "Alice", link: alice, album: hit, albums: [hit], played: null});
  assert.equal(imageSubject({target: alice, chart: {}, top: null, member: ""}), null);
  assert.equal(imageSubject({target: null, chart: {}, top: hit, member: ""}), null);
  assert.equal(imageSubject({target: alice, chart: null, top: hit, member: ""}), null);
});

test("a group's image names the group and counts its members once they're all in", () => {
  const s = imageSubject({target: house, chart: groupChart, top: hit, member: ""});
  assert.deepEqual(s, {kind: "group", name: "Full House", link: house, album: hit, albums: [hit],
                       played: "3 of 4 members"});
  const loading = imageSubject({target: house, chart: {...groupChart, waiting: 2}, top: hit, member: ""});
  assert.equal(loading.played, null);
  const one = imageSubject({target: house, chart: {members: ["alice"]}, top: {album: "x", listeners: 1}, member: ""});
  assert.equal(one.played, "1 of 1 member");
  assert.equal(imageSubject({target: house, chart: groupChart, top: null, member: ""}), null);  // empty ranking
});

test("a member's view shares the member's image, never the group's link", () => {
  const s = imageSubject({target: house, chart: groupChart, top: hit, member: "bob"});
  assert.deepEqual(s, {kind: "user", name: "bob", link: {kind: "user", id: "bob", name: "bob"}, album: hit,
                       albums: [hit], played: null});
  const url = shareUrl({origin: "https://kamp.fm", base: "/toys/aoty/", short: "aoty.kamp.fm", ...s.link});
  assert.equal(url, "https://aoty.kamp.fm/bob");
  assert.ok(!url.includes("/g/") && !url.includes("group"));
  assert.equal(imageSubject({target: house, chart: groupChart, top: null, member: "bob"}), null);
});

test("a card's texts are its albums' titles and artists, the name, year, link and count (AOTY-91)", () => {
  const albums = [{album: "Blue Lines", artist: "Massive Attack"}, {album: "Dummy", artist: "Portishead"}];
  const s = {kind: "group", name: "Full House", albums, played: "3 of 4 members"};
  assert.deepEqual(cardTexts(s, 2026, "https://aoty.kamp.fm/g/full-house"), {
    albums: [{title: "Blue Lines", artist: "Massive Attack"}], name: "Full House", year: 2026,
    url: "https://aoty.kamp.fm/g/full-house", kind: "group", played: "3 of 4 members",
  });
  assert.equal(cardTexts(s, 2026, "u", 3).albums.length, 2);  // fewer when fewer rank
});

test("a preview card's subject is the page as a visitor first sees it (AOTY-91)", () => {
  const user = {username: "alice", display_name: "AliCe", albums: [hit], rankings: {album: [[0, 9]]}};
  assert.deepEqual(previewSubject(user, userTop(user)), {kind: "user", name: "AliCe",
    link: {kind: "user", id: "AliCe", name: "AliCe"}, album: hit, albums: [hit], played: null});
  assert.equal(previewSubject({...user, display_name: undefined}, hit).name, "alice");
  const group = {kind: "group", id: "full-house", name: "Full House", ...groupChart};
  const s = previewSubject(group, hit);
  assert.deepEqual([s.kind, s.name, s.link.id, s.played], ["group", "Full House", "full-house", "3 of 4 members"]);
  assert.equal(previewSubject({...group, name: ""}, hit).name, "full-house");
  assert.equal(previewSubject(user, null), null);  // nothing ranks yet
  assert.equal(previewSubject(null, hit), null);
});

test("a user chart's top 3 is its first three, or fewer", () => {
  const chart = {albums: [{album: "C"}, {album: "A"}, {album: "B"}, {album: "D"}],
                 rankings: {album: [[1, 9], [2, 8], [0, 7], [3, 6]]}};
  assert.deepEqual(userTops(chart, 3).map(a => a.album), ["A", "B", "C"]);
  assert.deepEqual(userTops({...chart, rankings: {album: [[1, 9]]}}, 3).map(a => a.album), ["A"]);
  assert.deepEqual(userTops(null, 3), []);
});

test("a top 3 subject keeps the order on screen; a member's links to their page", () => {
  const three = [{album: "1", listeners: 3}, {album: "2", listeners: 2}, {album: "3", listeners: 1}];
  const s = imageSubject({target: house, chart: groupChart, tops: three, member: ""});
  assert.deepEqual(s.albums.map(a => a.album), ["1", "2", "3"]);
  assert.equal(s.album, three[0]);
  assert.equal(s.played, "3 of 4 members");  // #1's, drawn on #1 cards only
  const m = imageSubject({target: house, chart: groupChart, tops: three, member: "bob"});
  assert.deepEqual(m.link, {kind: "user", id: "bob", name: "bob"});
  assert.deepEqual(m.albums, three);
  assert.equal(imageSubject({target: alice, chart: {}, tops: [], member: ""}), null);
});

test("top 3 needs three on screen, else the image is the #1; shapes and formats", () => {
  assert.equal(cardContent("top3", 3), "top3");
  assert.equal(cardContent("top3", 10), "top3");
  assert.equal(cardContent("top3", 2), "top1");
  assert.equal(cardContent("top1", 3), "top1");
  for (const x of [undefined, null, "", "nope", "__proto__", "toString"]) assert.equal(cardContent(x, 3), "top1", x);
  assert.equal(formatId("top3", "story"), "top3_story");
  assert.equal(formatId("top1", "square"), "top1_square");
  assert.equal(formatId("nope", "nope"), "top1_square");
  assert.deepEqual(SHAPES, ["square", "story"]);
  assert.deepEqual(Object.keys(CONTENTS), ["top1", "top3", "top10"]);
});

test("a saved top 10 falls back to the biggest that fits, never past what was saved", () => {
  assert.equal(cardContent("top10", 10), "top10");
  assert.equal(cardContent("top10", 25), "top10");
  assert.equal(cardContent("top10", 9), "top3");
  assert.equal(cardContent("top10", 3), "top3");
  assert.equal(cardContent("top10", 2), "top1");
  assert.equal(cardContent("top10", 0), "top1");
  assert.equal(cardContent("top3", 10), "top3");  // never more than was saved
  assert.equal(cardContent("top1", 10), "top1");
  assert.equal(formatId("top10", "story"), "top10_story");
});

test("the share image's label says what a tap does", () => {
  assert.equal(imageLabel(true), "share image");
  assert.equal(imageLabel(false), "save image");
});

test("the preview's alt text names whose it is and every album drawn (AOTY-98)", () => {
  const a = (title, artist) => ({title, artist});
  assert.equal(previewAlt({kind: "user", name: "alice", albums: [a("1989", "Taylor Swift")]}),
               "alice's #1: 1989 by Taylor Swift");
  assert.equal(previewAlt({kind: "user", name: "alice", albums: [a("A", "X"), a("B", "Y"), a("C", "Z")]}),
               "alice's top 3: A by X, B by Y, C by Z");
  assert.equal(previewAlt({kind: "group", name: "Full House", albums: [a("A", "X")]}), "Full House · #1: A by X");
});
