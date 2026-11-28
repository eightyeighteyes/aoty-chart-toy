// Group editing's pure rules (AOTY-52): checking a group name, turning the groups API's answers
// into "done" or a message, who may edit, the share link, and when the page stops watching a
// rebuild. No DOM, so node --test runs it (web/test/groupedit.test.mjs). A classic script: it
// sets globalThis.aotyGroupEdit before app.js runs. Member names go through intro.js's cleanName.
(function (root) {
  "use strict";

  const NAME_MAX = 60;  // lambdas/common/groups.py NAME_MAX
  const MAX_MEMBERS = 25;  // and MAX_MEMBERS
  // A rebuild that hasn't shown up after this long ends quietly; members still loading are
  // watched for longer, as first syncs take minutes.
  const REBUILD_FOR_MS = 2 * 60 * 1000;
  const LOADING_FOR_MS = 10 * 60 * 1000;

  // intro.js's copy where the same thing went wrong; the rest in the same voice.
  const intro = root.aotyIntro || (typeof require === "function" ? require("./intro.js") : null);
  const M = intro.MESSAGES;
  const MESSAGES = {
    empty: M.empty,
    format: M.format,
    unknown: M.unknown,
    lastfm: M.lastfm,
    busy: M.busy,
    tooMany: M.tooMany,
    throttled: M.throttled,
    generic: M.generic,
    noName: "give the group a name first.",
    longName: `group names can be up to ${NAME_MAX} characters.`,
    needsSlug: "the group's link is made from its name, so it needs some plain letters or numbers.",
    taken: "another group already has that name. try another?",
    cannotAdd: "this person can't be added to groups.",
    full: `this group is full: ${MAX_MEMBERS} people is the most.`,
    paused: "we're not adding new people right now. try again soon.",
    forbidden: "only the group's owner can change it.",
    gone: "this group isn't here anymore.",
    signedOut: "you've been signed out. sign in again to make changes.",
    groupsOff: "group changes are switched off for now. try again later.",
    signinOff: "sign-in isn't answering right now. try again in a minute.",
    already: "they're already in the group.",
    addedLater: "added. their chart will start loading a little later.",
    // Group guardrails (AOTY-53); the two caps take the API's max (limitMessage)
    groupsPaused: "starting groups and adding people is paused for now. try again later.",
    tooManyGroups: "you can have up to {max} groups. delete one to start another.",
    tooManyAdds: "that's a lot of adds for one hour. try again in a bit.",
    // Opting out (AOTY-49)
    blockOn: "done. other people can't add you to their groups now.",
    blockOff: "done. people can add you to their groups again.",
    blockFailed: "couldn't save that just now. try again?",
    listFailed: "couldn't load your groups just now.",
  };

  // A group's display name as the API keeps it (groups.clean_name): whitespace collapsed, 1-60
  // characters, no control characters. {name} or {error: key into MESSAGES}.
  function checkName(raw) {
    const name = String(raw == null ? "" : raw).split(/\s+/).filter(Boolean).join(" ");
    if (!name) return {error: "noName"};
    if (name.length > NAME_MAX) return {error: "longName"};
    if (/\p{Cc}/u.test(name)) return {error: "noName"};
    return {name};
  }

  // Errors every write shares, by the body's error word: the status alone is ambiguous (a 404 is
  // both "no such group" and "unknown user").
  const BY_ERROR = {
    "unknown user": "unknown",
    "invalid username": "format",
    "no such group": "gone",
    "not your group": "forbidden",
    taken: "taken",
    full: "full",
    cannot_add: "cannotAdd",
    bad_name: "longName",
    needs_slug: "needsSlug",
    too_many_new: "tooMany",
    paused: "paused",
    busy: "busy",
    lastfm_unavailable: "lastfm",
    queue_unavailable: "busy",
    signed_out: "signedOut",
    groups_unavailable: "groupsOff",
    signin_unavailable: "signinOff",
    groups_paused: "groupsPaused",
    too_many_groups: "tooManyGroups",
    too_many_adds: "tooManyAdds",
  };
  // A member was added but their sync wasn't queued: they're in, their chart comes later.
  const LATER = new Set(["paused", "busy", "not_queued", "unavailable"]);

  // The answer to one write, as {ok: true, ...} or {message, signedOut?, gone?}.
  //   action: create | rename | delete | add | remove
  //   status: the HTTP status (0 when the request failed); body: the parsed JSON or null.
  function answer(action, status, body) {
    body = body && typeof body === "object" ? body : {};
    if (status >= 200 && status < 300) {
      if (action === "create" || action === "rename") return {ok: true, id: body.id};
      if (action === "add") {
        if (body.added === false) return {ok: true, note: MESSAGES.already};
        return {ok: true, member: body.member, note: LATER.has(body.sync) ? MESSAGES.addedLater : ""};
      }
      return {ok: true};
    }
    const key = BY_ERROR[body.error];
    if (key) return {message: limitMessage(MESSAGES[key], body.max), signedOut: key === "signedOut", gone: key === "gone"};
    if (status === 401) return {message: MESSAGES.signedOut, signedOut: true};
    if (status === 403) return {message: MESSAGES.forbidden};
    if (status === 429) return {message: MESSAGES.throttled};  // API Gateway's own throttle
    return {message: MESSAGES.generic};
  }

  // A message with the API's limit in it; without a usable number, the sentence still reads.
  function limitMessage(text, max) {
    if (!text.includes("{max}")) return text;
    return Number.isInteger(max) && max > 0 ? text.replace("{max}", String(max)) : text.replace("up to {max} ", "only so many ");
  }

  // Only the owner edits: ownership is the group's owner field, not membership (an owner who
  // removed themselves still owns it). me is api/me's username or null.
  function isOwner(me, group) {
    return !!me && !!group && typeof group.owner === "string" && group.owner === me;
  }

  // While watching the chart after an edit. watch = {since: ms, before: generated_at of the
  // chart on screen when the edit was made, or null}; group = GET api/groups/<id> ({ready,
  // total}) or null; chart = the latest chart.json or null. Returns {state, line}:
  //   rebuilding  the builder hasn't written a newer chart yet
  //   loading     it has, but members' first syncs are still running
  //   done        everyone's in: stop watching
  //   timeout     stop watching quietly (the regular refresh carries on)
  function rebuild(watch, group, chart, now) {
    const age = now - watch.since;
    const rebuilt = !!chart && chart.generated_at !== watch.before;
    const total = group ? group.total : 0;
    const left = group ? Math.max(0, group.total - group.ready) : 0;
    if (!rebuilt) {
      return age > REBUILD_FOR_MS ? {state: "timeout", line: ""} : {state: "rebuilding", line: "rebuilding the chart…"};
    }
    if (left > 0 || (chart.waiting || 0) > 0) {
      if (age > LOADING_FOR_MS) return {state: "timeout", line: ""};
      const n = left || chart.waiting;
      return {state: "loading", line: `${n} of ${total || n} still loading`};
    }
    return {state: "done", line: ""};
  }

  // What the remove dialog says: an owner removing themselves keeps the group.
  function removeText(name, me) {
    if (name === me) {
      return ["take yourself out?", "your plays leave the chart. you'll still own the group and can add yourself back."];
    }
    return [`remove ${name}?`, "their plays leave the chart. you can add them back any time."];
  }

  // Leaving someone else's group, and leaving all of them (AOTY-49): [title, text, yes].
  const AFTER_LEAVE = "the owner can add you back, unless you turn on “don't let others add me”.";
  function leaveText(name) {
    return [`leave ${name}?`, `your plays leave its chart. ${AFTER_LEAVE}`, "leave"];
  }
  function leaveAllText(n) {
    return [`leave all ${n} groups?`, `your plays leave their charts. ${AFTER_LEAVE}`, "leave all"];
  }

  // What the dialog says after leaving: n of total left, by name when it was one group.
  function leftLine(n, total, name) {
    if (n === total) return total === 1 && name ? `you left ${name}.` : `you left ${n} groups.`;
    return `left ${n} of ${total}. try again?`;
  }

  const api = {NAME_MAX, MAX_MEMBERS, REBUILD_FOR_MS, LOADING_FOR_MS, MESSAGES, checkName, answer, isOwner,
               rebuild, removeText, leaveText, leaveAllText, leftLine};
  root.aotyGroupEdit = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
