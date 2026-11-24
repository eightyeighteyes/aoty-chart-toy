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
  rankings: {track: [[0, 8], [2, 39], [1, 40]], album: [[1, 20], [0, 0.8]], weighted: [[0, 0.267]]},
};
const names = rs => rs.map(r => r.album);

test("everyone's rankings come straight from the chart", () => {
  assert.deepEqual(names(rows(chart, "track")), ["Shared", "Single", "Loop"]);
  assert.deepEqual(rows(chart, "weighted").map(r => r.rank), [1]);
  assert.deepEqual(names(rows(chart, "nonsense")), ["Shared", "Single", "Loop"]);
});

test("a member re-ranks by their own plays, singles out of album modes", () => {
  const bob = memberIndex(chart, "BOB");
  assert.equal(bob, 1);
  assert.deepEqual(names(rows(chart, "track", bob)), ["Loop", "Single", "Shared"]);
  assert.deepEqual(names(rows(chart, "album", bob)), ["Loop", "Shared"]);
  assert.deepEqual(rows(chart, "weighted", bob).map(r => r.score), [20, 0.1]);
});

test("ties go to the group's listeners", () => {
  const tie = {...chart, albums: [
    {album: "Few", listeners: 1, tracks: 1, single: false, by: {0: 5}},
    {album: "Many", listeners: 3, tracks: 1, single: false, by: {0: 5}},
  ]};
  assert.deepEqual(names(rows(tie, "track", 0)), ["Many", "Few"]);
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
