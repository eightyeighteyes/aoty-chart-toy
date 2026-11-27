// Share links and the share button's rules (web/src/share.js, AOTY-84). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {shareUrl, useNative, shareTitle, shareLabels, userTop, imageSubject, imageLabel} = require("../src/share.js");

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

test("a name the short host won't take falls back to the full URL", () => {
  // One-letter first segments are reserved for short routes (/g), so a one-letter name isn't short.
  assert.equal(shareUrl({...PROD, kind: "user", id: "g"}), "https://kamp.fm/toys/aoty/user/g");
  // USERNAME_RE allows a dot; aoty.kamp.fm's rule doesn't.
  assert.equal(shareUrl({...PROD, kind: "user", id: "a.b"}), "https://kamp.fm/toys/aoty/user/a.b");
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
  assert.deepEqual(shareLabels(false, null), {main: "copy link", member: null});
  assert.deepEqual(shareLabels(true, null), {main: "share", member: null});
  assert.deepEqual(shareLabels(false, "bob"), {main: "copy group link", member: "copy bob's link"});
  assert.deepEqual(shareLabels(true, "bob"), {main: "share group", member: "share bob"});
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
  assert.deepEqual(s, {kind: "user", name: "Alice", link: alice, album: hit, played: null});
  assert.equal(imageSubject({target: alice, chart: {}, top: null, member: ""}), null);
  assert.equal(imageSubject({target: null, chart: {}, top: hit, member: ""}), null);
  assert.equal(imageSubject({target: alice, chart: null, top: hit, member: ""}), null);
});

test("a group's image names the group and counts its members once they're all in", () => {
  const s = imageSubject({target: house, chart: groupChart, top: hit, member: ""});
  assert.deepEqual(s, {kind: "group", name: "Full House", link: house, album: hit, played: "3 of 4 members"});
  const loading = imageSubject({target: house, chart: {...groupChart, waiting: 2}, top: hit, member: ""});
  assert.equal(loading.played, null);
  const one = imageSubject({target: house, chart: {members: ["alice"]}, top: {album: "x", listeners: 1}, member: ""});
  assert.equal(one.played, "1 of 1 member");
  assert.equal(imageSubject({target: house, chart: groupChart, top: null, member: ""}), null);  // empty ranking
});

test("a member's view shares the member's image, never the group's link", () => {
  const s = imageSubject({target: house, chart: groupChart, top: hit, member: "bob"});
  assert.deepEqual(s, {kind: "user", name: "bob", link: {kind: "user", id: "bob", name: "bob"}, album: hit,
                       played: null});
  const url = shareUrl({origin: "https://kamp.fm", base: "/toys/aoty/", short: "aoty.kamp.fm", ...s.link});
  assert.equal(url, "https://aoty.kamp.fm/bob");
  assert.ok(!url.includes("/g/") && !url.includes("group"));
  assert.equal(imageSubject({target: house, chart: groupChart, top: null, member: "bob"}), null);
});

test("the share image's label says what a tap does", () => {
  assert.equal(imageLabel(true), "share image");
  assert.equal(imageLabel(false), "save image");
});
