// Share images (AOTY-70): the pure half. Sizes, where each box goes, text fitting and the colours,
// as a list of draw operations that app.js plays onto a canvas. Tested by web/test/sharecard.test.mjs
// with a stand-in measure, so nothing here touches the DOM.
//
// Every text sits on the plain page colour (bg), never over the cover, so the contrast rules the
// theme tests already hold (ink, muted, both accents and kamp's periwinkle on bg) hold here too.
// The artist-name tile that stands in for a missing cover uses the page's tile tokens, as .noart.
// Group pages (AOTY-71) use the same top1_square card with a group caption and a member count.
// AOTY-72 to AOTY-74 add formats beside it.
(function (root) {
  "use strict";

  const FORMATS = {
    top1_square: {width: 1080, height: 1080},
  };

  const DISPLAY = '"Bricolage Grotesque", sans-serif';
  const ARIAL = "Arial, Helvetica, sans-serif";  // gabbie's headings (AOTY-75)
  const BODY = "Inter, system-ui, sans-serif";
  const BRAND = '"DM Sans", Inter, system-ui, sans-serif';  // the "a kamp toy" chrome (AOTY-23)
  const ELLIPSIS = "…";

  // kamp's record mark (index.html's .kamp-mark, viewBox 0 0 200 200): circles, drawn with arc()
  // so no SVG image goes through the CSP.
  const MARK = [
    {r: 86, fill: "#1c1a16", stroke: "#c4aa78", lineWidth: 3},
    {r: 70, stroke: "#2a2620", lineWidth: 2},
    {r: 54, stroke: "#2a2620", lineWidth: 2},
    {r: 40, stroke: "#2a2620", lineWidth: 2},
    {r: 26, fill: "#bf7a20"},
    {r: 5, fill: "#141414"},
  ];

  // The boxes, in canvas pixels. Text boxes are centred on the canvas; `sizes` run largest first.
  function layout(format = "top1_square") {
    const {width, height} = FORMATS[format];
    const pad = 64, inner = width - 2 * pad;
    return {
      width, height, pad,
      brand: {x: pad, y: pad, w: inner, h: 40, mark: 40},
      caption: {x: pad, y: 128, w: inner, h: 44, lines: 1, sizes: [34, 30, 26, 22]},
      cover: {x: (width - 520) / 2, y: 196, w: 520, h: 520},
      title: {x: pad, y: 752, w: inner, h: 148, lines: 2, sizes: [64, 58, 52, 46, 40], leading: 1.15},
      artist: {x: pad, gap: 12, w: inner, h: 48, lines: 1, sizes: [40, 36, 32, 28, 26]},
      url: {x: pad, y: 976, w: inner, h: 40, lines: 1, sizes: [30, 26, 22, 20]},
    };
  }

  // The fonts a theme draws with: gabbie's headings are Arial, everything else Bricolage.
  function fonts(tokens) {
    const arial = tokens && tokens.font === "display-arial";
    return {
      display: size => `${arial ? 700 : 800} ${size}px ${arial ? ARIAL : DISPLAY}`,
      body: (size, weight = 500) => `${weight} ${size}px ${BODY}`,
      brand: size => `500 ${size}px ${BRAND}`,
    };
  }

  // Each text's colour token, all on bg (a test checks every preset at 4.5:1).
  const PALETTE = {brand: "kamp", played: "muted", caption: "accent", title: "ink", artist: "muted", url: "accent2"};
  function palette(tokens) {
    const out = {};
    for (const [what, key] of Object.entries(PALETTE)) out[what] = tokens[key];
    return out;
  }

  // ---- text fitting ---------------------------------------------------------------------------

  // User-perceived characters, so an emoji or an accented letter is never split.
  function graphemes(s) {
    if (typeof Intl !== "undefined" && Intl.Segmenter) {
      return Array.from(new Intl.Segmenter(undefined, {granularity: "grapheme"}).segment(s), x => x.segment);
    }
    return Array.from(s);
  }

  // Greedy wrap by spaces. A word wider than the line (a long name, or CJK with no spaces) breaks
  // between characters, and lines.broke says so.
  function wrap(text, maxWidth, width) {
    const lines = [];
    let line = "", broke = false;
    for (const word of text.split(/\s+/).filter(Boolean)) {
      const tryLine = line ? `${line} ${word}` : word;
      if (width(tryLine) <= maxWidth) { line = tryLine; continue; }
      if (line) lines.push(line);
      line = "";
      if (width(word) <= maxWidth) { line = word; continue; }
      broke = true;
      for (const g of graphemes(word)) {
        if (line && width(line + g) > maxWidth) { lines.push(line); line = ""; }
        line += g;
      }
    }
    if (line) lines.push(line);
    lines.broke = broke;
    return lines;
  }

  // The largest size at which text fits in maxLines without breaking a word, else the largest at
  // which it fits at all (a word too long for any size, CJK), else the smallest size with the last
  // line cut short by an ellipsis. measure(text, font) is the width (canvas measureText in the page).
  function fitText({text, maxWidth, maxLines, sizes, font, measure}) {
    const clean = String(text ?? "").replace(/\s+/g, " ").trim();
    if (!clean) return {size: sizes[0], lines: [], cut: false};
    const tries = sizes.map(size => [size, wrap(clean, maxWidth, s => measure(s, font(size)))]);
    const fits = tries.filter(([, lines]) => lines.length <= maxLines);
    const best = fits.find(([, lines]) => !lines.broke) || fits[0];
    if (best) return {size: best[0], lines: [...best[1]], cut: false};
    const size = sizes[sizes.length - 1], f = font(size);
    const lines = wrap(clean, maxWidth, s => measure(s, f)).slice(0, maxLines);
    let last = graphemes(lines[maxLines - 1]);
    while (last.length && measure(last.join("").trimEnd() + ELLIPSIS, f) > maxWidth) last.pop();
    lines[maxLines - 1] = last.join("").trimEnd() + ELLIPSIS;
    return {size, lines, cut: true};
  }

  // ---- words --------------------------------------------------------------------------------

  // A person's is always 's, as share.shareTitle (user, 2026-11-27: "chris's"). A group's is its
  // name, then the rest (AOTY-71, user decision 2026-11-27), as share.shareTitle's group form.
  function caption(name, year, kind = "user") {
    return kind === "group" ? `${name} · ${captionRest(year)}` : `${name}'s album of the year ${year}`;
  }
  const captionRest = year => `album of the year ${year}`;
  // The printed link, without its scheme.
  function printedUrl(url) {
    return String(url || "").replace(/^https?:\/\//, "");
  }
  // aoty-2026-alice.png: letters, digits, - and _ only (USERNAME_RE allows a ".").
  function fileName(name, year) {
    const safe = String(name ?? "").normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^A-Za-z0-9_-]+/g, "-")
        .replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 64);
    return `aoty-${year}${safe ? `-${safe}` : ""}.png`;
  }

  // The caption as drawn. A group name that's too long is cut, never the year after it: at the
  // smallest size, the name loses characters until "name… · album of the year 2026" fits.
  function captionFitted(texts, box, font, measure) {
    const full = caption(texts.name, texts.year, texts.kind);
    if (texts.kind !== "group") return full;
    const fit = fitText({text: full, maxWidth: box.w, maxLines: box.lines, sizes: box.sizes, font, measure});
    if (!fit.cut) return full;
    const f = font(box.sizes[box.sizes.length - 1]), rest = ` · ${captionRest(texts.year)}`;
    const name = graphemes(String(texts.name ?? "").replace(/\s+/g, " ").trim());
    while (name.length && measure(name.join("").trimEnd() + ELLIPSIS + rest, f) > box.w) name.pop();
    return name.join("").trimEnd() + ELLIPSIS + rest;
  }

  // ---- the card -------------------------------------------------------------------------------

  // The draw operations for one card. texts: {title, artist, name, year, url, kind, played}, where
  // kind "group" switches the caption and played is a group's member count or absent (AOTY-71,
  // share.imageSubject); tokens: a complete
  // theme (theme.complete); cover: whether a cover image will be drawn (else the artist tile);
  // measure(text, font). Each op is one of:
  //   {op: "fill", color, x, y, w, h}
  //   {op: "mark", x, y, size, circles}
  //   {op: "text", text, font, color, x, y, align: "left" | "center", on: "bg" | "tile"}   (y: baseline)
  //   {op: "cover", x, y, w, h, shadow}
  //   {op: "tile", x, y, w, h, from, to}   (a 135-degree gradient, as .noart)
  function compose({format = "top1_square", texts, tokens, cover, measure}) {
    const L = layout(format), F = fonts(tokens), C = palette(tokens), ops = [];
    const centre = L.width / 2;
    ops.push({op: "fill", color: tokens.bg, x: 0, y: 0, w: L.width, h: L.height});

    // The brand row: the mark, then "a kamp toy", left-aligned as on the page.
    const b = L.brand;
    ops.push({op: "mark", x: b.x, y: b.y, size: b.mark, circles: MARK});
    const brandX = b.x + b.mark + 14, brandY = b.y + b.h / 2 + 10;
    ops.push({op: "text", text: "a kamp toy", font: F.brand(28), color: C.brand,
              x: brandX, y: brandY, align: "left", on: "bg"});
    // A group's member count, "7 of 9 members" (AOTY-71), right-aligned in what the brand leaves.
    if (texts.played) {
      const left = brandX + measure("a kamp toy", F.brand(28)) + 32, right = L.width - L.pad;
      const font = s => F.body(s, 500);
      const fit = fitText({text: texts.played, maxWidth: right - left, maxLines: 1, sizes: [28, 26, 24, 22], font,
                           measure});
      fit.lines.forEach(t => ops.push({op: "text", text: t, font: font(fit.size), color: C.played, x: right,
                                       y: brandY, align: "right", on: "bg"}));
    }

    const line = (box, text, font, color, y) => {
      const fit = fitText({text, maxWidth: box.w, maxLines: box.lines, sizes: box.sizes, font, measure});
      const step = fit.size * (box.leading || 1.2);
      fit.lines.forEach((t, i) => ops.push({op: "text", text: t, font: font(fit.size), color, x: centre,
                                            y: y + fit.size * 0.92 + i * step, align: "center", on: "bg"}));
      return fit.lines.length ? fit.size * 0.92 + (fit.lines.length - 1) * step + fit.size * 0.28 : 0;
    };

    const capFont = s => F.body(s, 600);
    line(L.caption, captionFitted(texts, L.caption, capFont, measure), capFont, C.caption, L.caption.y);

    const c = L.cover;
    if (cover) {
      ops.push({op: "cover", x: c.x, y: c.y, w: c.w, h: c.h, shadow: tokens.scheme === "light" ? 0.18 : 0.5});
    } else {
      ops.push({op: "tile", x: c.x, y: c.y, w: c.w, h: c.h, from: tokens.tile, to: tokens.tileEnd});
      // The artist's name centred on the tile, as .noart: up to 5 lines, 9% side padding.
      const fit = fitText({text: texts.artist, maxWidth: c.w * 0.82, maxLines: 5, sizes: [96, 84, 72, 60, 50, 42],
                           font: F.display, measure});
      const step = fit.size * 1.05, block = fit.lines.length * step;
      fit.lines.forEach((t, i) => ops.push({op: "text", text: t, font: F.display(fit.size), color: tokens.tileInk,
                                            x: c.x + c.w / 2, y: c.y + (c.h - block) / 2 + i * step + fit.size * 0.8,
                                            align: "center", on: "tile"}));
    }

    const titleH = line(L.title, texts.title, F.display, C.title, L.title.y);
    line(L.artist, texts.artist, s => F.body(s, 500), C.artist, L.title.y + titleH + L.artist.gap);
    line(L.url, printedUrl(texts.url), s => F.body(s, 500), C.url, L.url.y);
    return ops;
  }

  // The [font, text] pairs to load before measuring: document.fonts.load(font, text) fetches only
  // the unicode-range subsets the text needs (é, Cyrillic, CJK), which a bare load wouldn't.
  function fontLoads({texts, tokens}) {
    const F = fonts(tokens);
    const display = `${texts.title || ""} ${texts.artist || ""}`;
    const body = `${texts.artist || ""} ${printedUrl(texts.url)} ${texts.played || ""}`;
    return [[F.display(40), display], [F.body(30, 500), body],
            [F.body(30, 600), caption(texts.name, texts.year, texts.kind)], [F.brand(28), "a kamp toy"]];
  }

  const api = {FORMATS, MARK, layout, fonts, palette, PALETTE, graphemes, wrap, fitText, caption, printedUrl,
               fileName, compose, fontLoads};
  root.aotyShareCard = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
