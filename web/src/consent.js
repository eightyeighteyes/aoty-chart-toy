// The kamp.fm analytics consent choice (AOTY-28), shared by kamp.fm and this toy. A classic script
// with no dependencies: kamp-site carries a copy as site/consent.js, so keep the two the same.
// It sets globalThis.kampConsent before kampstats.js runs; the pure rules are exported for
// node --test (web/test/consent.test.mjs).
//
// User decisions (2026-11-27): one choice for all of kamp.fm; only EU/UK/EEA visitors are asked;
// gtag.js isn't loaded at all until a yes (Consent Mode's basic setup), and a yes turns analytics on
// without a reload.
//
// The contract between the two sites is two first-party cookies on kamp.fm (Path=/):
//   kamp_ask      1 | 0     set by kamp-site's viewer-response CloudFront function from
//                           CloudFront-Viewer-Country: 1 in the EU27, the EEA, the UK and CH. No
//                           cookie (a local preview, the function missing) means ask: fail closed.
//   kamp_consent  granted | denied   the visitor's choice, for CONSENT_MAX_AGE_S (about 6 months,
//                           then they're asked again)
// Nothing else is stored, and neither cookie names anyone.
//
// The banner styles itself from the page's colour tokens: the toy's names first (--panel, --ink,
// --line, --accent, --on-accent), then kamp.fm's (--surface, --text, --border), then fixed fallbacks.
//
// The banner only shows on a page that has analytics to consent to: a non-empty
// <meta name="aoty-ga"> (the toy's prod build) or <meta name="kamp-analytics" content="1"> (kamp.fm).
(function (root) {
  "use strict";

  const ASK = "kamp_ask";
  const CONSENT = "kamp_consent";
  const CONSENT_MAX_AGE_S = 180 * 24 * 3600;
  const TEXT = {
    line: "kamp.fm counts visits with google analytics, only if you're ok with it.",
    yes: "that's fine",
    no: "no thanks",
    label: "analytics choice",
  };

  // {name: value} from document.cookie. Malformed pieces are skipped; values aren't decoded
  // (these cookies hold plain words).
  function parseCookies(raw) {
    const out = {};
    for (const part of String(raw || "").split(";")) {
      const i = part.indexOf("=");
      if (i < 1) continue;
      const name = part.slice(0, i).trim();
      if (name && !(name in out)) out[name] = part.slice(i + 1).trim();
    }
    return out;
  }

  // granted | denied | ask, from the cookies. A choice wins; with none, only a visitor kamp-site
  // marked as outside the asked regions (kamp_ask=0) is granted without asking.
  function decide(cookies) {
    const choice = cookies[CONSENT];
    if (choice === "granted" || choice === "denied") return choice;
    return cookies[ASK] === "0" ? "granted" : "ask";
  }

  // Whether this visitor is one we ask (so the footer offers to change the choice).
  const asked = cookies => cookies[ASK] !== "0";

  function consentCookie(granted, secure) {
    return `${CONSENT}=${granted ? "granted" : "denied"}; Path=/; Max-Age=${CONSENT_MAX_AGE_S}; SameSite=Lax` +
      (secure ? "; Secure" : "");
  }

  // Set-strings that delete GA's cookies (_ga, _ga_<id>) after a no that follows a yes. GA puts
  // them on the registrable domain (.kamp.fm), so each is expired there, on the host, and bare.
  function expireGaCookies(cookies, hostname) {
    const names = Object.keys(cookies).filter(n => n === "_ga" || n.startsWith("_ga_"));
    const labels = String(hostname || "").split(".").filter(Boolean);
    const domains = [""];
    if (labels.length >= 2) {
      domains.push(`; Domain=${hostname}`, `; Domain=.${labels.slice(-2).join(".")}`);
    }
    return names.flatMap(n => domains.map(d => `${n}=; Path=/; Max-Age=0${d}`));
  }

  const STYLE = `
.kamp-consent { position: fixed; left: 16px; right: 16px; bottom: 16px; z-index: 50; box-sizing: border-box;
  max-width: 560px; margin: 0 auto; padding: 14px 16px; display: flex; flex-wrap: wrap; align-items: center;
  gap: 10px 14px; background: var(--panel, var(--surface, #17151f)); color: var(--ink, var(--text, #f4f1ea));
  border: 1px solid var(--line, var(--border, #2c2937)); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,.35);
  font: 14px/1.45 "DM Sans", system-ui, sans-serif; }
.kamp-consent[hidden] { display: none; }
.kamp-consent p { margin: 0; flex: 1 1 240px; }
.kamp-consent-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.kamp-consent button { font: 600 14px "DM Sans", system-ui, sans-serif; border-radius: 999px; padding: 8px 16px;
  cursor: pointer; border: 1px solid var(--line, var(--border, #2c2937)); background: transparent; color: var(--ink, var(--text, #f4f1ea)); }
.kamp-consent button.kamp-consent-yes { background: var(--accent, #ff6b4a); border-color: var(--accent, #ff6b4a);
  color: var(--on-accent, #1a1014); }
.kamp-consent button:focus-visible { outline: 2px solid var(--accent-2, #ffc24b); outline-offset: 2px; }
@media (prefers-reduced-motion: no-preference) {
  .kamp-consent { animation: kamp-consent-in .35s ease-out backwards; }
  @keyframes kamp-consent-in { from { opacity: 0; transform: translateY(12px); } }
}`;

  // The live choice. deps: {doc, loc}; tests pass stubs.
  function create(deps) {
    const {doc, loc} = deps;
    const listeners = [];
    let banner = null;
    const read = () => parseCookies(doc.cookie);
    let current = decide(read());
    const meta = name => {
      const m = doc.querySelector && doc.querySelector(`meta[name="${name}"]`);
      return m ? m.content : "";
    };
    const enabled = !!meta("aoty-ga") || meta("kamp-analytics") === "1";

    function emit() {
      for (const fn of listeners.slice()) {
        try { fn(current); } catch (_) { /* a listener's error is its own */ }
      }
    }

    function hide() { if (banner) banner.hidden = true; }

    function show() {
      if (!enabled || !doc.body) return;
      if (!banner) {
        const style = doc.createElement("style");
        style.textContent = STYLE;
        doc.head.appendChild(style);
        banner = doc.createElement("section");
        banner.className = "kamp-consent";
        banner.setAttribute("aria-label", TEXT.label);
        const p = banner.appendChild(doc.createElement("p"));
        p.textContent = TEXT.line;
        const actions = banner.appendChild(doc.createElement("div"));
        actions.className = "kamp-consent-actions";
        for (const [text, granted, cls] of [[TEXT.yes, true, "kamp-consent-yes"], [TEXT.no, false, ""]]) {
          const b = actions.appendChild(doc.createElement("button"));
          b.type = "button";
          b.textContent = text;
          if (cls) b.className = cls;
          b.addEventListener("click", () => api.choose(granted));
        }
        // First in the body, so keyboard and screen reader users meet it early; fixed at the bottom, so
        // nothing on the page moves.
        doc.body.insertBefore(banner, doc.body.firstChild);
      }
      banner.hidden = false;
    }

    const api = {
      enabled,
      state: () => current,
      asked: () => asked(read()),
      // fn(state) now, and again on every change.
      onChange(fn) {
        listeners.push(fn);
        try { fn(current); } catch (_) {}
      },
      choose(granted) {
        const was = current;
        doc.cookie = consentCookie(granted, loc.protocol === "https:");
        if (!granted && was === "granted") {
          for (const c of expireGaCookies(read(), loc.hostname)) doc.cookie = c;
        }
        current = granted ? "granted" : "denied";
        hide();
        if (current !== was) emit();
      },
      // The footer's "cookie choice": ask again, whatever was chosen.
      reopen: show,
    };
    if (current === "ask") show();
    return api;
  }

  const exported = {ASK, CONSENT, CONSENT_MAX_AGE_S, TEXT, parseCookies, decide, asked, consentCookie,
                    expireGaCookies, create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;

  if (typeof document !== "undefined") {
    root.kampConsent = create({doc: document, loc: root.location});
  }
})(typeof window !== "undefined" ? window : globalThis);
