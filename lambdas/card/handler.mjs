// Card Lambda (AOTY-91): a chart's link preview card, the share image a visitor would save from the
// page, drawn on the server and published beside the chart's og.html.
//
// The chart builder (lambdas/chart/handler.py) invokes it synchronously after each build and each
// restyle, with the chart it just published:
//
//   {"chart": <chart.json>, "key": "<SITE_PREFIX>data/users/<u>/og.png" | ".../groups/<slug>/og.png"}
//
// and writes og.html with the card when the answer is {"ok": true, "key", "generated_at"}, else with
// AOTY-85's bare cover. The chart builder runs one build at a time, so there's no race to guard here.
//
// It runs the page's own modules (web/src/share.js, group.js, theme.js, sharecard.js, carddraw.js)
// unchanged, so the card can't drift from the page's share image. User decisions (2026-11-27): Node +
// @napi-rs/canvas; the #1 square (top1_square); Latin fonts only, so a card whose text needs other
// glyphs isn't drawn (reason "glyphs") and the bare cover stays.

import {existsSync, readFileSync} from "node:fs";
import {createRequire} from "node:module";
import {fileURLToPath} from "node:url";

const require = createRequire(import.meta.url);
const HERE = fileURLToPath(new URL(".", import.meta.url));
// Packaged (dist/card.zip): web/ beside this file. In the repo: web/src/.
const WEB = existsSync(`${HERE}web/sharecard.js`) ? `${HERE}web/` : `${HERE}../../web/src/`;
const share = require(`${WEB}share.js`);
const grp = require(`${WEB}group.js`);
const th = require(`${WEB}theme.js`);
const sc = require(`${WEB}sharecard.js`);
const cd = require(`${WEB}carddraw.js`);

const FORMAT = "top1_square";
const COVER_WAIT_MS = 5000;
const FONTS_DIR = `${HERE}fonts/`;
// Each file registers under its own family name, which is the one sharecard.js asks for; the weight
// comes from the file (OS/2). Liberation Sans Bold is metric-compatible with Arial, and sharecard's
// gabbie stack names it after Arial (AOTY-75): Lambda has no system fonts at all.
const FONTS = [
  ["BricolageGrotesque-800.ttf", "Bricolage Grotesque"],
  ["Inter-500.ttf", "Inter"], ["Inter-600.ttf", "Inter"], ["Inter-700.ttf", "Inter"],
  ["DMSans-500.ttf", "DM Sans"],
  ["LiberationSans-Bold.ttf", "Liberation Sans"],
];
const COVERAGE = JSON.parse(readFileSync(`${FONTS_DIR}coverage.json`, "utf8")).ranges;

// ---- pure ---------------------------------------------------------------------------------------

// Whether every character of every text has a glyph in every font a text can be drawn with
// (tools/make_card_fonts.py writes the ranges). Anything else would draw as an empty box.
export function covered(texts, ranges = COVERAGE) {
  const strings = [texts.name, texts.url, texts.played, ...texts.albums.flatMap(a => [a.title, a.artist])];
  for (const s of strings) {
    for (const ch of String(s ?? "")) {
      const cp = ch.codePointAt(0);
      if (!ranges.some(([lo, hi]) => cp >= lo && cp <= hi)) return false;
    }
  }
  return true;
}

// The card's input from a chart, as the page computes it for Everyone's view: the subject, its
// texts with the share link, and the cover's URLs (art, then its fallbacks). Null when nothing ranks.
export function cardInput(chart, {origin, base, short}) {
  const top = chart.kind === "group" ? grp.top(chart, "listener") : share.userTop(chart);
  const subject = share.previewSubject(chart, top);
  if (!subject) return null;
  const url = share.shareUrl({origin, base, short, ...subject.link});
  const texts = share.cardTexts(subject, chart.year, url, 1);
  const a = subject.album;
  return {texts, urls: a.art ? [a.art, ...(a.fallbacks || [])] : []};
}

// The theme the page would paint: a preset's tokens, "album of the year" derived from the #1
// cover's pixels (pixels() returns them, or null), else the default (the poster look).
export function cardTokens(themeId, pixels) {
  if (themeId === th.MATCH) {
    const px = pixels();
    const derived = px ? th.derive(px) : null;
    return th.complete(derived || th.THEMES[th.DEFAULT]);
  }
  return th.complete(th.tokens(themeId) || th.THEMES[th.DEFAULT]);
}

// The key this card may be written to: a chart's og.png under the site prefix, nothing else.
export function validKey(key, prefix) {
  const rest = typeof key === "string" && key.startsWith(prefix) ? key.slice(prefix.length) : "";
  return /^data\/(users\/[a-z0-9_][a-z0-9_.-]{0,63}|groups\/[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?)\/og\.png$/.test(rest)
    && !rest.includes("..");
}

// ---- I/O ----------------------------------------------------------------------------------------

let fontsReady = false;

async function defaultDeps() {
  const canvas = await import("@napi-rs/canvas");
  const {S3Client, PutObjectCommand} = await import("@aws-sdk/client-s3");  // in the Lambda runtime
  const s3 = new S3Client({});
  return {
    canvas,
    env: process.env,
    fetch: globalThis.fetch,
    put: (Bucket, Key, Body) => s3.send(new PutObjectCommand({Bucket, Key, Body, ContentType: "image/png",
                                                              CacheControl: "max-age=300"})),
  };
}

// The first of urls that loads as an image within the time left, or null.
async function loadCover(deps, urls) {
  const deadline = Date.now() + COVER_WAIT_MS;
  for (const url of urls) {
    const left = deadline - Date.now();
    if (left <= 0 || !/^https:\/\//.test(url)) continue;
    try {
      const res = await deps.fetch(url, {signal: AbortSignal.timeout(left), redirect: "follow"});
      if (!res.ok) continue;
      return await deps.canvas.loadImage(Buffer.from(await res.arrayBuffer()));
    } catch (_) {
      // the next size, or the tile
    }
  }
  return null;
}

function registerFonts(canvas) {
  if (fontsReady) return;
  for (const [file, family] of FONTS) canvas.GlobalFonts.registerFromPath(`${FONTS_DIR}${file}`, family);
  fontsReady = true;
}

export async function render(event, deps) {
  const {chart, key} = event || {};
  const env = deps.env;
  if (!chart || !validKey(key, env.SITE_PREFIX || "")) return {ok: false, reason: "invalid"};
  const input = cardInput(chart, {origin: env.PUBLIC_ORIGIN || "https://kamp.fm", base: env.BASE_PATH || "/",
                                  short: env.SHORT_HOST || ""});
  if (!input) return {ok: false, reason: "empty"};
  if (!covered(input.texts)) return {ok: false, reason: "glyphs"};

  registerFonts(deps.canvas);
  const img = await loadCover(deps, input.urls);
  const tokens = cardTokens(chart.theme, () => {
    if (!img) return null;
    const c = deps.canvas.createCanvas(32, 32), x = c.getContext("2d");
    x.drawImage(img, 0, 0, 32, 32);
    return x.getImageData(0, 0, 32, 32).data;
  });
  const {width, height} = sc.FORMATS[FORMAT];
  const canvas = deps.canvas.createCanvas(width, height);
  const ctx = canvas.getContext("2d");
  const measure = (t, f) => { ctx.font = f; return ctx.measureText(t).width; };
  const ops = sc.compose({format: FORMAT, texts: input.texts, tokens, covers: [!!img], measure});
  cd.drawCard(ctx, ops, [img]);
  const png = await canvas.encode("png");
  await deps.put(env.SITE_BUCKET, key, png);
  return {ok: true, key, generated_at: chart.generated_at, cover: !!img};
}

let _deps = null;

export async function handler(event) {
  _deps = _deps || await defaultDeps();
  try {
    const out = await render(event, _deps);
    console.log(JSON.stringify({card: out.ok ? "drawn" : out.reason, cover: out.cover ?? null}));
    return out;
  } catch (e) {
    console.error("card failed", e);
    return {ok: false, reason: "error"};
  }
}
