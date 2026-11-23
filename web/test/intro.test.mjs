// The intro page's pure rules (web/src/intro.js). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {cleanName, outcome, MESSAGES, lastfmUserUrl} = require("../src/intro.js");

test("names are trimmed and lowercased", () => {
  assert.deepEqual(cleanName("  AliCe "), {name: "alice"});
  assert.deepEqual(cleanName("dj_k.9-x"), {name: "dj_k.9-x"});
});

test("a leading @ is dropped", () => {
  assert.deepEqual(cleanName("@Alice"), {name: "alice"});
});

test("pasted profile links work", () => {
  for (const url of ["https://www.last.fm/user/Alice", "http://last.fm/user/alice/library",
                     "www.last.fm/user/ALICE?date=1", "last.fm/user/alice#top"]) {
    assert.deepEqual(cleanName(url), {name: "alice"}, url);
  }
  assert.deepEqual(cleanName("https://www.last.fm/user/a%2Eb"), {name: "a.b"});
});

test("empty and malformed names are caught before any request", () => {
  assert.deepEqual(cleanName(""), {error: "empty"});
  assert.deepEqual(cleanName("   "), {error: "empty"});
  assert.deepEqual(cleanName("@"), {error: "empty"});
  for (const bad of ["a b", "__smoke__", "<script>", "x".repeat(65), "-lead"]) {
    assert.deepEqual(cleanName(bad), {error: "format"}, bad);
  }
});

test("a queued or running sync goes to the chart in Last.fm's casing", () => {
  const body = {queued: true, status: {stage: "queued", display_name: "AliCe"}};
  assert.deepEqual(outcome(202, body, "alice"), {go: "AliCe"});
  const cooldown = {queued: false, reason: "cooldown", status: {stage: "idle", display_name: "AliCe"}};
  assert.deepEqual(outcome(200, cooldown, "alice"), {go: "AliCe"});
});

test("a display name that isn't this user's is ignored", () => {
  assert.deepEqual(outcome(202, {status: {display_name: "mallory"}}, "alice"), {go: "alice"});
  assert.deepEqual(outcome(202, {status: {}}, "alice"), {go: "alice"});
  assert.deepEqual(outcome(202, null, "alice"), {go: "alice"});
});

test("a known private profile says so", () => {
  const body = {queued: false, reason: "cooldown", status: {stage: "error", reason: "private"}};
  assert.deepEqual(outcome(200, body, "alice"), {message: MESSAGES.private});
});

test("API errors map to friendly messages", () => {
  assert.deepEqual(outcome(400, {error: "invalid username"}, "x"), {message: MESSAGES.format});
  assert.deepEqual(outcome(404, {error: "unknown user"}, "x"), {message: MESSAGES.unknown});
  assert.deepEqual(outcome(503, {error: "lastfm_unavailable"}, "x"), {message: MESSAGES.lastfm});
  assert.deepEqual(outcome(503, {error: "busy"}, "x"), {message: MESSAGES.busy});
  assert.deepEqual(outcome(503, {error: "paused"}, "x"), {message: MESSAGES.paused});
  assert.deepEqual(outcome(429, {error: "too_many_new"}, "x"), {message: MESSAGES.tooMany});
  assert.deepEqual(outcome(429, {message: "Too Many Requests"}, "x"), {message: MESSAGES.throttled});
  assert.deepEqual(outcome(429, null, "x"), {message: MESSAGES.throttled});
});

test("a paused sync for a known user still goes to their chart", () => {
  const st = {stage: "idle", display_name: "AliCe"};
  assert.deepEqual(outcome(200, {queued: false, reason: "paused", status: st}, "alice"), {go: "AliCe"});
});

test("anything else is the generic message", () => {
  for (const [status, body] of [[503, {error: "queue_unavailable"}], [503, {error: "unavailable"}],
                                [502, null], [504, null], [415, {}], [0, null]]) {
    assert.deepEqual(outcome(status, body, "x"), {message: MESSAGES.generic}, String(status));
  }
});

test("copy stays lowercase", () => {
  for (const [key, text] of Object.entries(MESSAGES)) {
    assert.equal(text, text.toLowerCase(), key);
  }
});

test("the last.fm profile link keeps the name's casing and escapes it", () => {
  assert.equal(lastfmUserUrl("AliCe"), "https://www.last.fm/user/AliCe");
  assert.equal(lastfmUserUrl("a.b_c-d"), "https://www.last.fm/user/a.b_c-d");
  assert.equal(lastfmUserUrl("x/../y?z"), "https://www.last.fm/user/x%2F..%2Fy%3Fz");
});
