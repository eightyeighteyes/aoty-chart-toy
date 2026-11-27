// Group editing's pure rules (web/src/groupedit.js). Run with: node --test web/test/
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const ge = require("../src/groupedit.js");
const {checkName, answer, isOwner, rebuild, removeText, MESSAGES: MSG} = ge;
const intro = require("../src/intro.js");

test("group names collapse whitespace and keep their casing", () => {
  assert.deepEqual(checkName("  New   Bands\tfor Old Heads "), {name: "New Bands for Old Heads"});
  assert.deepEqual(checkName("日本"), {name: "日本"});  // the API asks for a slug, not the page
});

test("group names must be 1 to 60 characters with no control characters", () => {
  assert.deepEqual(checkName("   "), {error: "noName"});
  assert.deepEqual(checkName(null), {error: "noName"});
  assert.deepEqual(checkName("x".repeat(60)), {name: "x".repeat(60)});
  assert.deepEqual(checkName("x".repeat(61)), {error: "longName"});
  assert.deepEqual(checkName("a\u0007b"), {error: "noName"});
});

test("member names reuse the intro's parser", () => {
  assert.equal(ge.MESSAGES.format, intro.MESSAGES.format);
  assert.deepEqual(intro.cleanName("https://www.last.fm/user/Bob"), {name: "bob"});
});

test("successful writes", () => {
  assert.deepEqual(answer("create", 201, {id: "crew", sync: "queued"}), {ok: true, id: "crew"});
  assert.deepEqual(answer("rename", 200, {id: "crew-two"}), {ok: true, id: "crew-two"});
  assert.deepEqual(answer("delete", 204, null), {ok: true});
  assert.deepEqual(answer("remove", 204, null), {ok: true});
  assert.deepEqual(answer("add", 201, {member: "bob", added: true, sync: "queued"}), {ok: true, member: "bob", note: ""});
  assert.deepEqual(answer("add", 201, {member: "bob", added: true, sync: "cooldown"}), {ok: true, member: "bob", note: ""});
});

test("an add that wasn't queued or was already there says so", () => {
  for (const sync of ["paused", "busy", "not_queued", "unavailable"]) {
    assert.equal(answer("add", 201, {member: "bob", added: true, sync}).note, MSG.addedLater);
  }
  assert.deepEqual(answer("add", 200, {member: "bob", added: false}), {ok: true, note: MSG.already});
});

test("refusals are told apart by their error word, not only the status", () => {
  const cases = [
    [404, {error: "unknown user"}, MSG.unknown],
    [404, {error: "no such group"}, MSG.gone],
    [400, {error: "invalid username"}, MSG.format],
    [409, {error: "cannot_add"}, MSG.cannotAdd],
    [409, {error: "full", max: 25}, MSG.full],
    [409, {error: "taken"}, MSG.taken],
    [400, {error: "bad_name", max: 60}, MSG.longName],
    [400, {error: "needs_slug"}, MSG.needsSlug],
    [403, {error: "not your group"}, MSG.forbidden],
    [429, {error: "too_many_new"}, MSG.tooMany],
    [503, {error: "paused"}, MSG.paused],
    [503, {error: "busy"}, MSG.busy],
    [503, {error: "lastfm_unavailable"}, MSG.lastfm],
    [503, {error: "queue_unavailable"}, MSG.busy],
    [503, {error: "groups_unavailable"}, MSG.groupsOff],
    [503, {error: "signin_unavailable"}, MSG.signinOff],
  ];
  for (const [status, body, message] of cases) assert.equal(answer("add", status, body).message, message, body.error);
});

test("signed out and gone are flagged for the page", () => {
  assert.deepEqual(answer("rename", 401, {error: "signed_out"}), {message: MSG.signedOut, signedOut: true, gone: false});
  assert.equal(answer("delete", 404, {error: "no such group"}).gone, true);
});

test("bodiless answers fall back on the status", () => {
  assert.equal(answer("add", 429, {message: "Too Many Requests"}).message, MSG.throttled);
  assert.equal(answer("add", 401, null).signedOut, true);
  assert.equal(answer("add", 0, null).message, MSG.generic);
  assert.equal(answer("add", 415, {error: "send content-type: application/json"}).message, MSG.generic);
  assert.equal(answer("add", 502, null).message, MSG.generic);
});

test("only the owner edits, member or not", () => {
  assert.equal(isOwner("alice", {owner: "alice", members: []}), true);  // removed themselves
  assert.equal(isOwner("bob", {owner: "alice", members: ["bob"]}), false);
  assert.equal(isOwner(null, {owner: "alice"}), false);
  assert.equal(isOwner("alice", null), false);
  assert.equal(isOwner("alice", {}), false);
});

test("a rebuild is watched until the chart is newer and everyone's in", () => {
  const watch = {since: 0, before: "2026-11-24T10:00:00Z"};
  const old = {generated_at: "2026-11-24T10:00:00Z", waiting: 0};
  const fresh = {generated_at: "2026-11-24T10:01:00Z", waiting: 0};
  assert.deepEqual(rebuild(watch, {ready: 3, total: 3}, old, 5000), {state: "rebuilding", line: "rebuilding the chart…"});
  assert.deepEqual(rebuild(watch, {ready: 3, total: 3}, null, 5000).state, "rebuilding");
  assert.deepEqual(rebuild(watch, {ready: 2, total: 4}, fresh, 5000), {state: "loading", line: "2 of 4 still loading"});
  assert.deepEqual(rebuild(watch, {ready: 4, total: 4}, {...fresh, waiting: 1}, 5000).state, "loading");
  assert.deepEqual(rebuild(watch, {ready: 4, total: 4}, fresh, 5000), {state: "done", line: ""});
});

test("a first chart counts as rebuilt, and watching ends quietly", () => {
  const watch = {since: 0, before: null};
  assert.equal(rebuild(watch, {ready: 1, total: 1}, {generated_at: "x"}, 1).state, "done");
  assert.equal(rebuild(watch, {ready: 1, total: 1}, null, ge.REBUILD_FOR_MS + 1).state, "timeout");
  assert.equal(rebuild(watch, {ready: 0, total: 1}, {generated_at: "x"}, ge.REBUILD_FOR_MS + 1).state, "loading");
  assert.equal(rebuild(watch, {ready: 0, total: 1}, {generated_at: "x"}, ge.LOADING_FOR_MS + 1).state, "timeout");
});

test("an owner removing themselves is told they keep the group", () => {
  assert.match(removeText("alice", "alice")[1], /still own/);
  assert.equal(removeText("bob", "alice")[0], "remove bob?");
});
