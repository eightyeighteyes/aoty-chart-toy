// The banner's and first visit's pure rules (web/src/firstvisit.js). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {isFirstVisit, describe, makeProgress, soFar, firstVisitEnd, endMessage, makeQueue, waitText, queueLines,
       tabTitle, refusal} = require("../src/firstvisit.js");

const reading = (done, total, scrobbles = 0) => ({stage: "reading", weeks: {done, total}, scrobbles});
const dating = (checked, total, hits = 0) => ({stage: "dating", albums: {checked, total, hits}});

test("no chart, or a chart that ranks nothing, is a first visit", () => {
  assert.equal(isFirstVisit(null), true);
  assert.equal(isFirstVisit({rankings: {album: []}}), true);
  assert.equal(isFirstVisit({rankings: {album: [[0, 9]]}}), false);
  // By Play is the one ranking (AOTY-63): a chart whose only ranked albums are singles ranks nothing.
  assert.equal(isFirstVisit({rankings: {track: [[0, 9]], album: []}}), true);
  assert.equal(isFirstVisit({}), true);
});

test("the returning banner is unchanged", () => {
  assert.deepEqual(describe(reading(3, 4)), ["Checking for new scrobbles…", "3 of 4 weeks"]);
  assert.deepEqual(describe({stage: "building", new_plays: 1200}), ["+1,200 new plays · updating chart", ""]);
  assert.deepEqual(describe(dating(5, 10)), ["Checking release years…", "5 of 10 albums"]);
  assert.deepEqual(describe({stage: "idle"}), ["", ""]);
});

test("the live text changes only with the stage and each quarter", () => {
  const p = makeProgress();
  const lives = [];
  for (const s of [{stage: "queued"}, reading(0, 47), reading(5, 47), reading(11, 47), reading(12, 47),
                   reading(24, 47), reading(36, 47), reading(47, 47), {stage: "building"}, dating(0, 600)]) {
    const {live} = p(s);
    if (lives.at(-1) !== live) lives.push(live);
  }
  assert.deepEqual(lives, [
    "waiting for a turn to read your scrobbles",
    "reading your scrobbles",
    "reading your scrobbles · a quarter of the way",
    "reading your scrobbles · halfway",
    "reading your scrobbles · three quarters of the way",
    "sorting your albums",
    "checking release years",
  ]);
});

test("the numbers are readable and never fake-precise", () => {
  const p = makeProgress();
  assert.equal(p(reading(24, 47, 8412)).detail, "24 of 47 weeks · 8,412 plays · about a minute in all");
  assert.equal(p(dating(212, 640)).detail, "212 of 640 albums");
});

test("a retried sync that recounts its weeks never shows progress going backwards", () => {
  const p = makeProgress();
  p(reading(16, 47, 5000));
  const after = p(reading(0, 31, 5000));  // the retry: 31 weeks left, done back to 0
  assert.equal(after.detail, "16 of 47 weeks · 5,000 plays · about a minute in all");
  assert.equal(after.live, "reading your scrobbles · a quarter of the way");
  assert.equal(p(reading(20, 31, 9000)).detail, "20 of 31 weeks · 9,000 plays · about a minute in all");
});

test("a stage that reads as an earlier one for a poll doesn't step back", () => {
  const p = makeProgress();
  p(dating(10, 100));
  assert.equal(p(reading(47, 47)).live, "checking release years");
});

test("the running stats name the most played album so far", () => {
  assert.equal(soFar({so_far: {albums: 1204, top: {artist: "Band", album: "LP", plays: 61}}}),
               "albums found so far: 1,204 · most played: LP by Band (61 plays)");
  assert.equal(soFar({so_far: {albums: 1, top: {artist: "A", album: "B", plays: 1}}}),
               "albums found so far: 1 · most played: B by A (1 play)");
  assert.equal(soFar({}), "");
});

test("a first visit with every album checked and none from the year ends", () => {
  assert.equal(firstVisitEnd({stage: "idle", albums: {checked: 40, total: 40, hits: 0}}), "none");
  assert.match(endMessage("none", 2026, {scrobbles: 900})[1], /900 plays .* none came out in 2026/);
});

test("a first visit whose chart stopped rebuilding, or that ran out of time, is stalled", () => {
  assert.equal(firstVisitEnd({stage: "idle", albums: {checked: 10, total: 40, hits: 0}}), "stalled");
  assert.equal(firstVisitEnd(dating(10, 40), true), "stalled");
  assert.equal(endMessage("stalled", 2026)[0], "still checking release years");
});

test("a first visit still on its way keeps waiting, including a reopen at 0 hits", () => {
  assert.equal(firstVisitEnd(reading(3, 47)), null);
  assert.equal(firstVisitEnd({stage: "building"}), null);
  assert.equal(firstVisitEnd(dating(0, 640)), null);
  assert.equal(firstVisitEnd({stage: "idle", albums: {checked: null, total: null, hits: null}}), null);
});

// ---- the queue (AOTY-26) ----------------------------------------------------------------

const queued = (position, wait_s) => ({stage: "queued", queue: wait_s ? {position, wait_s} : {position}});

test("the place in line only goes down", () => {
  const q = makeQueue();
  assert.equal(q(queued(37), 0).position, 37);
  assert.equal(q(queued(40), 1000).position, 37);  // approximate positions can wobble upward
  assert.equal(q(queued(12), 2000).position, 12);
  assert.equal(q({stage: "queued"}, 3000).position, 12);  // a poll without a position keeps it
});

test("no position until the status has one", () => {
  const q = makeQueue();
  assert.deepEqual(q({stage: "queued"}, 0), {position: null, live: ""});
  assert.equal(q(reading(1, 4), 0).position, null);
});

test("position changes are announced at most once a minute", () => {
  const q = makeQueue();
  assert.equal(q(queued(37), 0).live, "you're caller #37 in line");
  assert.equal(q(queued(30), 30_000).live, "you're caller #37 in line");  // too soon: unchanged
  assert.equal(q(queued(29), 60_000).live, "you're caller #29 in line");
  assert.equal(q(queued(29), 200_000).live, "you're caller #29 in line");
});

test("the request line's words", () => {
  const lines = queueLines(1234, "https://kamp.fm/toys/aoty/user/AliCe", 0);
  assert.equal(lines.caller, "you're caller #1,234. the dj is spinning other people's years first.");
  assert.equal(lines.link, "this page is yours: kamp.fm/toys/aoty/user/AliCe. close the tab and come back. "
      + "it'll be pressed and waiting.");
  assert.equal(lines.wait, "");
  assert.equal(queueLines(3, "x", 700).wait, "about 12 minutes");
});

test("waits round up and say nothing without an estimate", () => {
  assert.equal(waitText(undefined), "");
  assert.equal(waitText(0), "");
  assert.equal(waitText(30), "about a minute");
  assert.equal(waitText(61), "about 2 minutes");
});

test("the tab title shows the place in line, then the reading progress", () => {
  const base = "alice's albums of the year 2026 · a kamp toy";
  assert.equal(tabTitle(base, 12, null), `(#12) ${base}`);
  assert.equal(tabTitle(base, null, 64), `(64%) ${base}`);
  assert.equal(tabTitle(base, null, 0), `(0%) ${base}`);
  assert.equal(tabTitle(base, null, null), base);
});

test("progress reports a forward-only percent while reading", () => {
  const p = makeProgress();
  assert.equal(p({stage: "queued"}).percent, null);
  assert.equal(p(reading(16, 47)).percent, 34);
  assert.equal(p(reading(2, 40)).percent, 34);  // a retry recounting its weeks doesn't go back
  assert.equal(p({stage: "building"}).percent, null);
});

test("capacity refusals have their own words, and anything else is null", () => {
  assert.equal(refusal(503, {error: "paused"})[0], "we're at capacity right now");
  assert.equal(refusal(503, {error: "busy"})[0], "lots of charts are pressing right now");
  assert.equal(refusal(429, {error: "too_many_new"})[0], "that's a lot of new names from here");
  assert.equal(refusal(429, {message: "Too Many Requests"})[0], "lots of people are pressing charts right now");
  assert.equal(refusal(503, {error: "unavailable"}), null);
  assert.equal(refusal(500, null), null);
  for (const [status, error] of [[503, "paused"], [503, "busy"], [429, "too_many_new"], [429, null]]) {
    const [title, text] = refusal(status, {error});
    assert.equal(title, title.toLowerCase());
    assert.equal(text, text.toLowerCase());
  }
});
