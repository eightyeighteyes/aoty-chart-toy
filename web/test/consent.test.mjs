// The shared consent choice (web/src/consent.js, AOTY-28).
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const c = require("../src/consent.js");

test("cookies parse leniently", () => {
  assert.deepEqual(c.parseCookies("a=1; kamp_ask=0;b=x=y; =bad; junk"), {a: "1", kamp_ask: "0", b: "x=y"});
  assert.deepEqual(c.parseCookies(""), {});
  assert.deepEqual(c.parseCookies(undefined), {});
});

test("a choice wins; without one everyone is asked, whatever kamp-site's region cookie says", () => {
  assert.equal(c.decide({kamp_consent: "granted", kamp_ask: "1"}), "granted");
  assert.equal(c.decide({kamp_consent: "denied", kamp_ask: "0"}), "denied");
  assert.equal(c.decide({kamp_ask: "0"}), "ask");
  assert.equal(c.decide({kamp_ask: "1"}), "ask");
  assert.equal(c.decide({}), "ask");
  assert.equal(c.decide({kamp_consent: "maybe"}), "ask");
});

test("the choice cookie lasts about six months and is Secure on https", () => {
  assert.equal(c.consentCookie(true, true), "kamp_consent=granted; Path=/; Max-Age=15552000; SameSite=Lax; Secure");
  assert.equal(c.consentCookie(false, false), "kamp_consent=denied; Path=/; Max-Age=15552000; SameSite=Lax");
});

test("GA's cookies are expired on the host and the registrable domain", () => {
  const out = c.expireGaCookies({_ga: "x", _ga_ABC: "y", _gid: "z", kamp_ask: "1"}, "kamp.fm");
  assert.deepEqual(out, ["_ga=; Path=/; Max-Age=0", "_ga=; Path=/; Max-Age=0; Domain=kamp.fm",
                         "_ga=; Path=/; Max-Age=0; Domain=.kamp.fm", "_ga_ABC=; Path=/; Max-Age=0",
                         "_ga_ABC=; Path=/; Max-Age=0; Domain=kamp.fm", "_ga_ABC=; Path=/; Max-Age=0; Domain=.kamp.fm"]);
  assert.deepEqual(c.expireGaCookies({_ga: "x"}, "localhost"), ["_ga=; Path=/; Max-Age=0"]);
});

test("the copy is kamp's voice: lowercase", () => {
  for (const s of Object.values(c.TEXT)) assert.equal(s, s.toLowerCase());
});

// A just-enough document: a cookie jar (Max-Age=0 deletes) and elements that record children.
function fakeDoc({cookie = "", ga = "G-TEST", kamp = ""} = {}) {
  const jar = new Map(Object.entries(c.parseCookies(cookie)));
  const el = tag => ({
    tag, children: [], attrs: {}, hidden: false, listeners: {},
    appendChild(ch) { this.children.push(ch); return ch; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
  });
  const body = el("body");
  body.insertBefore = (ch) => { body.children.unshift(ch); return ch; };
  const metas = {"aoty-ga": ga, "kamp-analytics": kamp};
  const doc = {
    head: el("head"), body,
    createElement: el,
    querySelector(sel) {
      const name = sel.match(/name="([^"]+)"/)[1];
      return metas[name] ? {content: metas[name]} : null;
    },
    get cookie() { return [...jar].map(([k, v]) => `${k}=${v}`).join("; "); },
    set cookie(s) {
      const [pair, ...attrs] = s.split(";");
      const [k, v] = pair.split("=");
      if (attrs.some(a => a.trim() === "Max-Age=0")) jar.delete(k.trim());
      else jar.set(k.trim(), v);
    },
    jar,
  };
  return doc;
}
const LOC = {protocol: "https:", hostname: "kamp.fm"};
const bannerOf = doc => doc.body.children.find(ch => ch.attrs["aria-label"] === c.TEXT.label);
const buttons = b => b.children[1].children;

test("an asked visitor sees the banner first in the body; a yes grants without a reload", () => {
  const doc = fakeDoc({cookie: "kamp_ask=1"});
  const consent = c.create({doc, loc: LOC});
  const seen = [];
  consent.onChange(s => seen.push(s));
  const banner = bannerOf(doc);
  assert.ok(banner && !banner.hidden && doc.body.children[0] === banner);
  assert.equal(doc.head.children.length, 1);  // its style
  const [yes, no] = buttons(banner);
  assert.deepEqual([yes.textContent, no.textContent], [c.TEXT.yes, c.TEXT.no]);
  yes.listeners.click();
  assert.deepEqual(seen, ["ask", "granted"]);
  assert.equal(doc.jar.get("kamp_consent"), "granted");
  assert.equal(banner.hidden, true);
});

test("a later no deletes GA's cookies and tells the listeners", () => {
  const doc = fakeDoc({cookie: "kamp_ask=1; kamp_consent=granted; _ga=GA1.1; _ga_TEST=GS1"});
  const consent = c.create({doc, loc: LOC});
  const seen = [];
  consent.onChange(s => seen.push(s));
  assert.equal(bannerOf(doc), undefined);  // already chose
  consent.reopen();
  buttons(bannerOf(doc))[1].listeners.click();
  assert.deepEqual(seen, ["granted", "denied"]);
  assert.ok(!doc.jar.has("_ga") && !doc.jar.has("_ga_TEST"));
  assert.equal(doc.jar.get("kamp_consent"), "denied");
});

test("a visitor kamp-site marks as outside the EU/UK is still asked, and nothing runs before a yes", () => {
  const doc = fakeDoc({cookie: "kamp_ask=0"});
  const consent = c.create({doc, loc: LOC});
  assert.equal(consent.state(), "ask");
  assert.ok(bannerOf(doc) && !bannerOf(doc).hidden);
});

test("a page with no analytics never shows the banner, and stays undecided", () => {
  const doc = fakeDoc({ga: ""});
  const consent = c.create({doc, loc: LOC});
  assert.equal(consent.enabled, false);
  assert.equal(consent.state(), "ask");
  consent.reopen();
  assert.equal(bannerOf(doc), undefined);
  // kamp.fm marks itself with kamp-analytics instead of aoty-ga.
  assert.equal(c.create({doc: fakeDoc({ga: "", kamp: "1"}), loc: LOC}).enabled, true);
});

test("a listener that throws doesn't stop the rest", () => {
  const consent = c.create({doc: fakeDoc({cookie: "kamp_ask=1"}), loc: LOC});
  const seen = [];
  consent.onChange(() => { throw new Error("boom"); });
  consent.onChange(s => seen.push(s));
  consent.choose(true);
  assert.deepEqual(seen, ["ask", "granted"]);
});
