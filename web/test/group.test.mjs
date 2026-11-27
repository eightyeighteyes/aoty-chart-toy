// The group page's pure rules (web/src/group.js). Run with: node --test web/test/*.test.mjs
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {rows, memberIndex, loadingLine, valid, ID_RE} = require("../src/group.js");

const ID = "abcdefghijklmnopqrstuvwxyz";
const chart = {
  version: 1, kind: "group", id: ID, top: 100, waiting: 2,
  members: ["alice", "Bob", "carol"],
  albums: [
    {album: "Shared", listeners: 3, tracks: 10, single: false, by: {0: 5, 1: 1, 2: 2}},
    {album: "Loop", listeners: 1, tracks: 2, single: false, by: {1: 40}},
    {album: "Single", listeners: 2, tracks: 1, single: true, by: {0: 9, 1: 30}},
  ],
  rankings: {listener: [[0, 0.8], [1, 20]], album: [[1, 20], [0, 0.8]], weighted: [[0, 0.267]]},
};
const names = rs => rs.map(r => r.album);

test("everyone's rankings come straight from the chart", () => {
  assert.deepEqual(names(rows(chart, "listener")), ["Shared", "Loop"]);
  assert.deepEqual(names(rows(chart, "album")), ["Loop", "Shared"]);
  assert.deepEqual(rows(chart, "weighted").map(r => r.rank), [1]);
});

test("By Listener is the default, and a saved By Track falls back to it", () => {
  assert.deepEqual(names(rows(chart, "nonsense")), ["Shared", "Loop"]);
  assert.deepEqual(names(rows(chart, "track")), ["Shared", "Loop"]);
});

test("a chart built before By Listener shows By Play until it's rebuilt", () => {
  const old = {...chart, rankings: {track: [[0, 8], [2, 39], [1, 40]], album: [[1, 20], [0, 0.8]]}};
  assert.deepEqual(names(rows(old, "listener")), ["Loop", "Shared"]);
});

test("a member re-ranks by their own album plays in every mode, singles out", () => {
  const bob = memberIndex(chart, "BOB");
  assert.equal(bob, 1);
  for (const mode of ["listener", "album", "weighted"]) {
    assert.deepEqual(names(rows(chart, mode, bob)), ["Loop", "Shared"], mode);
    assert.deepEqual(rows(chart, mode, bob).map(r => r.score), [20, 0.1], mode);
  }
});

test("ties go to the group's listeners", () => {
  const tie = {...chart, albums: [
    {album: "Few", listeners: 1, tracks: 1, single: false, by: {0: 5}},
    {album: "Many", listeners: 3, tracks: 1, single: false, by: {0: 5}},
  ]};
  assert.deepEqual(names(rows(tie, "listener", 0)), ["Many", "Few"]);
});

test("the share image's #1 is the one on screen, or null for an empty ranking", () => {
  const {top} = require("../src/group.js");
  assert.equal(top(chart, "listener").album, "Shared");
  assert.equal(top(chart, "album").album, "Loop");
  assert.equal(top(chart, "weighted").album, "Shared");
  assert.equal(top(chart, "listener", memberIndex(chart, "bob")).album, "Loop");
  const old = {...chart, rankings: {album: [[1, 20], [0, 0.8]]}};
  assert.equal(top(old, "listener").album, "Loop");  // no listener ranking yet: By Play
  assert.equal(top({...chart, rankings: {...chart.rankings, weighted: []}}, "weighted"), null);
  assert.equal(top(old, "weighted"), null);
  const silent = {...chart, members: [...chart.members, "dave"]};
  assert.equal(top(silent, "listener", 3), null);  // a member with no plays from the year
});

test("a top 3 is the first three on screen, or fewer", () => {
  const {tops} = require("../src/group.js");
  assert.deepEqual(names(tops(chart, "listener", -1, 3)), ["Shared", "Loop"]);  // only two rank
  assert.deepEqual(names(tops(chart, "album", -1, 3)), ["Loop", "Shared"]);
  assert.deepEqual(names(tops(chart, "weighted", -1, 3)), ["Shared"]);
  assert.deepEqual(names(tops(chart, "listener", memberIndex(chart, "bob"), 3)), ["Loop", "Shared"]);
  const many = {...chart, rankings: {...chart.rankings, album: [[1, 20], [0, 0.8], [2, 0.5], [0, 0.1]]}};
  assert.deepEqual(names(tops(many, "album", -1, 3)), ["Loop", "Shared", "Single"]);
});

test("unknown names are everyone", () => {
  assert.equal(memberIndex(chart, "nobody"), -1);
  assert.equal(memberIndex(chart, ""), -1);
});

test("the loading line counts members still syncing", () => {
  assert.equal(loadingLine(chart), "2 of 5 still loading");
  assert.equal(loadingLine({...chart, waiting: 0}), "");
});

test("only this group's chart is accepted", () => {
  assert.ok(valid(chart, ID));
  assert.ok(!valid({...chart, id: "zzzzzzzzzzzzzzzzzzzzzzzzzz"}, ID));
  assert.ok(!valid({...chart, kind: "user"}, ID));
  assert.ok(ID_RE.test(ID) && ID_RE.test("the-server") && !ID_RE.test("../users"));
  assert.ok(!ID_RE.test("-edge") && !ID_RE.test("edge-") && !ID_RE.test("Caps"));
});

test("checking lines: top albums first, then deep cuts on groups only, never a number", () => {
  const {checkingLine} = require("../src/group.js");
  assert.equal(checkingLine({kind: "group", pending: 900, pending_top: 3}), "still checking…");
  assert.equal(checkingLine({kind: "group", pending: 900, pending_top: 0}), "still counting deep cuts…");
  assert.equal(checkingLine({kind: "group", pending: 0, pending_top: 0}), "");
  assert.equal(checkingLine({pending: 900, pending_top: 2}), "still checking your top albums…");
  assert.equal(checkingLine({pending: 900, pending_top: 0}), "");  // a user's cold tail: quiet
  assert.equal(checkingLine({pending: 900}), "");  // a chart built before pending_top
});

test("after a visit's syncs, the page keeps looking while members sync, then a grace, then stops", () => {
  const {syncWatch, SYNC_GRACE_MS, SYNC_WATCH_MAX_MS} = require("../src/group.js");
  const t0 = 1_000_000;
  let w = syncWatch(null, undefined, t0);  // started on the POST: the record isn't re-read yet
  assert.deepEqual(w, {since: t0, lastBusy: t0});
  w = syncWatch(w, {syncing: 3}, t0 + 60000);
  assert.equal(w.lastBusy, t0 + 60000);
  w = syncWatch(w, {syncing: 0}, t0 + 120000);  // all finished: one more look for the build
  assert.ok(w);
  assert.equal(syncWatch(w, {syncing: 0}, t0 + 60000 + SYNC_GRACE_MS), null);
  assert.equal(syncWatch(null, {syncing: 0}, t0) !== null, true);  // grace from the start
});

test("a sync watch never outlives its cap, even with a member stuck as syncing", () => {
  const {syncWatch, SYNC_WATCH_MAX_MS} = require("../src/group.js");
  let w = syncWatch(null, {syncing: 1}, 0);
  for (let t = 60000; t < SYNC_WATCH_MAX_MS; t += 60000) w = syncWatch(w, {syncing: 1}, t);
  assert.ok(w);
  assert.equal(syncWatch(w, {syncing: 1}, SYNC_WATCH_MAX_MS), null);
});
