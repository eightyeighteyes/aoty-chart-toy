// Share images (AOTY-70): the pure half. Sizes, where each box goes, text fitting and the colours,
// as a list of draw operations that app.js plays onto a canvas. Tested by web/test/sharecard.test.mjs
// with a stand-in measure, so nothing here touches the DOM.
//
// Every text sits on the plain page colour (bg), never over the cover, so the contrast rules the
// theme tests already hold (ink, muted, both accents and kamp's periwinkle on bg) hold here too.
// The artist-name tile that stands in for a missing cover uses the page's tile tokens, as .noart.
// Group pages (AOTY-71) use the same card with a group caption and a member count. top1_story
// (AOTY-72) is the vertical 9:16 for Stories, Reels and TikTok; AOTY-73 and AOTY-74 add top 3 and
// top 10 beside them.
(function (root) {
  "use strict";

  // safe: the strips the target app covers with its own UI, kept free of text and the mark. Stories,
  // Reels and TikTok cover about the top 250 px and the bottom 340 px of a 1080x1920 (AOTY-72).
  // count: the albums it shows (AOTY-73's top 3 beside AOTY-70's #1).
  const SQUARE = {width: 1080, height: 1080, safe: {top: 0, bottom: 0}};
  const STORY = {width: 1080, height: 1920, safe: {top: 250, bottom: 340}};
  const FORMATS = {
    top1_square: {...SQUARE, count: 1},
    top1_story: {...STORY, count: 1, suffix: "story"},
    top3_square: {...SQUARE, count: 3, suffix: "top3"},
    top3_story: {...STORY, count: 3, suffix: "top3-story"},
    top10_square: {...SQUARE, count: 10, suffix: "top10"},
    top10_story: {...STORY, count: 10, suffix: "top10-story"},
  };
  const DEFAULT_FORMAT = "top1_square";

  const DISPLAY = '"Bricolage Grotesque", sans-serif';
  // gabbie's headings (AOTY-75). Liberation Sans is Arial's metric twin, for the card Lambda, which
  // has no Arial (AOTY-91); a browser with Arial never reaches it.
  const ARIAL = 'Arial, Helvetica, "Liberation Sans", sans-serif';
  const BODY = "Inter, system-ui, sans-serif";
  const BRAND = '"DM Sans", Inter, system-ui, sans-serif';  // the "a kamp toy" chrome (AOTY-23)
  const ELLIPSIS = "…";
  const NBSP = " ";
  // What wrap breaks at and fitText collapses: any white space but a no-break space.
  const SPACES = /[^\S ]+/;

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

  // The boxes, in canvas pixels; text `sizes` run largest first. Each album is a slot: its cover
  // (or the artist tile), then its title and the artist under the title. A text box's align is
  // "center" (on the box's middle) or "left".
  // The story keeps the square's stack, moved inside its safe band and with a bigger cover; the
  // band ends at 1580 and the link at about 1536, slack for real fonts' descenders.
  const PAD = 64, INNER = 1080 - 2 * PAD;
  const TILE_SIZES = [96, 84, 72, 60, 50, 42];  // the artist's name on a 520 tile, as .noart
  // The tile's name sizes for a cover of width w: AOTY-70's own on the #1 covers, scaled for the
  // small ones, never under 14 px.
  const tileSizes = w => (w >= 520 ? TILE_SIZES : TILE_SIZES.map(s => Math.max(14, Math.round(s * w / 520))));
  const coverBox = (x, y, w) => ({x, y, w, h: w, tile: tileSizes(w)});
  // #1 alone, centred under its cover (AOTY-70, AOTY-72).
  const solo = (coverY, coverSize, titleY) => [{
    rank: 1,
    cover: coverBox((1080 - coverSize) / 2, coverY, coverSize),
    title: {x: PAD, y: titleY, w: INNER, h: 148, lines: 2, sizes: [64, 58, 52, 46, 40], leading: 1.15,
            align: "center"},
    artist: {x: PAD, gap: 12, w: INNER, h: 48, lines: 1, sizes: [40, 36, 32, 28, 26], align: "center"},
  }];
  // A small slot's title and artist (AOTY-73), left-aligned at x in a column w wide.
  const small = (rank, cover, x, y, w, lines) => ({
    rank, cover,
    title: {x, y, w, h: lines * 36, lines, sizes: [30, 27, 24, 21, 19], leading: 1.15, align: "left"},
    artist: {x, gap: 6, w, h: 30, lines: 1, sizes: [24, 22, 20, 18], align: "left"},
  });
  // A top 10 (AOTY-74): a 5x2 grid of covers, each badged with its rank, so a cover can be matched
  // to its line in the list under the grid, which reads down two columns (1–5, then 6–10).
  const GRID = 176, GRID_GAP = 18, BADGE_H = 34;
  function top10(gridY, listY, rowH, lines, titleSizes, artistSizes) {
    return Array.from({length: 10}, (_, i) => {
      const cover = coverBox(PAD + (i % 5) * (GRID + GRID_GAP), gridY + Math.floor(i / 5) * (GRID + GRID_GAP), GRID);
      const x = i < 5 ? PAD : PAD + 492, y = listY + (i % 5) * rowH, w = 460;
      return {
        rank: i + 1, cover, badge: true,
        title: {x, y, w, h: lines * titleSizes[0] * 1.2, lines, sizes: titleSizes, leading: 1.15, align: "left"},
        artist: {x, gap: lines > 1 ? 6 : 4, w, h: artistSizes[0] * 1.2, lines: 1, sizes: artistSizes, align: "left"},
      };
    });
  }
  const LAYOUTS = {
    top10_square: {brand: 64, caption: 128, url: 976,
                   slots: top10(196, 600, 70, 1, [26, 24, 22, 20, 18], [20, 18, 16])},
    top10_story: {brand: 290, caption: 360, url: 1496,
                  slots: top10(420, 836, 120, 2, [34, 31, 28, 25, 22], [26, 24, 22, 20, 18])},
    top1_square: {brand: 64, caption: 128, url: 976, slots: solo(196, 520, 752)},
    top1_story: {brand: 290, caption: 360, url: 1496, slots: solo(440, 760, 1236)},
    // #1 big on the left, #2 and #3 stacked beside it, #1's names under everything (user, 2026-11-27).
    top3_square: {brand: 64, caption: 128, url: 976, slots: [
      {rank: 1, cover: coverBox(PAD, 196, 560),
       title: {x: PAD, y: 796, w: INNER, h: 62, lines: 1, sizes: [52, 46, 40, 34, 30], align: "left"},
       artist: {x: PAD, gap: 8, w: INNER, h: 42, lines: 1, sizes: [34, 30, 26, 22], align: "left"}},
      small(2, coverBox(656, 196, 200), 656, 404, 360, 1),
      small(3, coverBox(656, 500, 200), 656, 708, 360, 1),
    ]},
    // #1 big and centred, #2 and #3 side by side below it, each with its names beside its cover.
    top3_story: {brand: 290, caption: 360, url: 1496, slots: [
      {rank: 1, cover: coverBox(240, 430, 600),
       title: {x: PAD, y: 1054, w: INNER, h: 132, lines: 2, sizes: [56, 50, 44, 38], leading: 1.15, align: "center"},
       artist: {x: PAD, gap: 10, w: INNER, h: 44, lines: 1, sizes: [36, 32, 28, 24], align: "center"}},
      small(2, coverBox(PAD, 1270, 180), PAD + 200, 1270, 260, 2),
      small(3, coverBox(556, 1270, 180), 756, 1270, 260, 2),
    ]},
  };
  function layout(format = DEFAULT_FORMAT) {
    const {width, height, safe, count} = FORMATS[format], y = LAYOUTS[format];
    const first = y.slots[0];
    return {
      width, height, pad: PAD, safe, count, slots: y.slots,
      brand: {x: PAD, y: y.brand, w: INNER, h: 40, mark: 40},
      caption: {x: PAD, y: y.caption, w: INNER, h: 44, lines: 1, sizes: [34, 30, 26, 22]},
      // #1's boxes, as before slots.
      cover: first.cover, title: first.title, artist: first.artist,
      url: {x: PAD, y: y.url, w: INNER, h: 40, lines: 1, sizes: [30, 26, 22, 20]},
    };
  }
  // A format id from anywhere (a saved choice, the page): a known one, else the square.
  const formatOf = id => (Object.prototype.hasOwnProperty.call(FORMATS, id) ? id : DEFAULT_FORMAT);

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
  const PALETTE = {brand: "kamp", played: "muted", caption: "accent", title: "ink", artist: "muted", url: "accent2",
                   badge: "accent"};
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
    for (const word of text.split(SPACES).filter(Boolean)) {
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
    const clean = String(text ?? "").replace(new RegExp(SPACES.source, "g"), " ").trim();
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
  // count > 1 is a top N (AOTY-73, user decision 2026-11-27): "alice's top 3 albums of 2026".
  function caption(name, year, kind = "user", count = 1) {
    return kind === "group" ? `${name} · ${captionRest(year, count)}` : `${name}'s ${captionRest(year, count)}`;
  }
  const captionRest = (year, count = 1) => (count > 1 ? `top ${count} albums of ${year}` : `album of the year ${year}`);
  // The printed link, without its scheme.
  function printedUrl(url) {
    return String(url || "").replace(/^https?:\/\//, "");
  }
  // aoty-2026-alice.png: letters, digits, - and _ only (USERNAME_RE allows a "."). A story is
  // aoty-2026-alice-story.png, so both can sit side by side in a downloads folder.
  function fileName(name, year, format = DEFAULT_FORMAT) {
    const safe = String(name ?? "").normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^A-Za-z0-9_-]+/g, "-")
        .replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 64);
    const suffix = FORMATS[formatOf(format)].suffix;
    return `aoty-${year}${safe ? `-${safe}` : ""}${suffix ? `-${suffix}` : ""}.png`;
  }

  // The caption as drawn. A group name that's too long is cut, never the year after it: at the
  // smallest size, the name loses characters until "name… · album of the year 2026" fits.
  function captionFitted(texts, box, font, measure, count = 1) {
    const full = caption(texts.name, texts.year, texts.kind, count);
    if (texts.kind !== "group") return full;
    const fit = fitText({text: full, maxWidth: box.w, maxLines: box.lines, sizes: box.sizes, font, measure});
    if (!fit.cut) return full;
    const f = font(box.sizes[box.sizes.length - 1]), rest = ` · ${captionRest(texts.year, count)}`;
    const name = graphemes(String(texts.name ?? "").replace(/\s+/g, " ").trim());
    while (name.length && measure(name.join("").trimEnd() + ELLIPSIS + rest, f) > box.w) name.pop();
    return name.join("").trimEnd() + ELLIPSIS + rest;
  }

  // ---- the card -------------------------------------------------------------------------------

  // The albums a card shows, in rank order: texts.albums [{title, artist}], or texts.title and
  // texts.artist for a #1 alone.
  const albumsOf = texts => (Array.isArray(texts.albums) && texts.albums.length ? texts.albums
      : [{title: texts.title, artist: texts.artist}]);

  // The draw operations for one card. texts: {albums | title + artist, name, year, url, kind,
  // played}, where kind "group" switches the caption and played is a group's member count or
  // absent (AOTY-71, share.imageSubject; drawn on #1 cards only); tokens: a complete theme
  // (theme.complete); covers: per album, whether its cover image will be drawn (else the artist
  // tile), or cover for a #1 alone; measure(text, font). Each op is one of:
  //   {op: "fill", color, x, y, w, h}
  //   {op: "mark", x, y, size, circles}
  //   {op: "text", text, font, color, x, y, align: "left" | "center" | "right",
  //       on: "bg" | "tile" | "badge"}   (a badge is a bg fill over a grid cover's corner)
  //       (y: baseline)
  //   {op: "cover", slot, x, y, w, h, shadow, blur?, drop?}   (slot: the album's index, for its image;
  //       blur and drop only on small covers, else 48 and 18)
  //   {op: "tile", slot, x, y, w, h, from, to}   (a 135-degree gradient, as .noart)
  function compose({format = DEFAULT_FORMAT, texts, tokens, cover, covers, measure}) {
    const L = layout(formatOf(format)), F = fonts(tokens), C = palette(tokens), ops = [];
    const albums = albumsOf(texts), drawn = covers || [cover];
    ops.push({op: "fill", color: tokens.bg, x: 0, y: 0, w: L.width, h: L.height});

    // The brand row: the mark, then "a kamp toy", left-aligned as on the page.
    const b = L.brand;
    ops.push({op: "mark", x: b.x, y: b.y, size: b.mark, circles: MARK});
    const brandX = b.x + b.mark + 14, brandY = b.y + b.h / 2 + 10;
    ops.push({op: "text", text: "a kamp toy", font: F.brand(28), color: C.brand,
              x: brandX, y: brandY, align: "left", on: "bg"});
    // A group's member count, "7 of 9 members" (AOTY-71), right-aligned in what the brand leaves.
    if (texts.played && L.count === 1) {
      const left = brandX + measure("a kamp toy", F.brand(28)) + 32, right = L.width - L.pad;
      const font = s => F.body(s, 500);
      const fit = fitText({text: texts.played, maxWidth: right - left, maxLines: 1, sizes: [28, 26, 24, 22], font,
                           measure});
      fit.lines.forEach(t => ops.push({op: "text", text: t, font: font(fit.size), color: C.played, x: right,
                                       y: brandY, align: "right", on: "bg"}));
    }

    // One text box's lines, from its top y; returns the height drawn.
    const line = (box, text, font, color, y) => {
      const fit = fitText({text, maxWidth: box.w, maxLines: box.lines, sizes: box.sizes, font, measure});
      const step = fit.size * (box.leading || 1.2);
      const align = box.align || "center", x = align === "left" ? box.x : box.x + box.w / 2;
      fit.lines.forEach((t, i) => ops.push({op: "text", text: t, font: font(fit.size), color, x,
                                            y: y + fit.size * 0.92 + i * step, align, on: "bg"}));
      return fit.lines.length ? fit.size * 0.92 + (fit.lines.length - 1) * step + fit.size * 0.28 : 0;
    };

    const capFont = s => F.body(s, 600);
    line(L.caption, captionFitted(texts, L.caption, capFont, measure, L.count), capFont, C.caption, L.caption.y);

    // Every album's cover or tile first, then all the names, so no cover's shadow falls on a title
    // drawn before it (AOTY-74's grid sits right above its list).
    L.slots.forEach((s, slot) => {
      const a = albums[slot] || {};
      const c = s.cover;
      if (drawn[slot]) {
        const op = {op: "cover", slot, x: c.x, y: c.y, w: c.w, h: c.h, shadow: tokens.scheme === "light" ? 0.18 : 0.5};
        // A small cover's shadow scales down with it (the #1's 48 px blur, 18 px drop at 520).
        if (c.w < 520) Object.assign(op, {blur: Math.round(48 * c.w / 520), drop: Math.round(18 * c.w / 520)});
        ops.push(op);
      } else {
        ops.push({op: "tile", slot, x: c.x, y: c.y, w: c.w, h: c.h, from: tokens.tile, to: tokens.tileEnd});
        // The artist's name centred on the tile, as .noart: up to 5 lines, 9% side padding.
        // A badged tile keeps its name above the badge's strip, and to 4 lines.
        const area = c.h - (s.badge ? BADGE_H + 6 : 0);
        const fit = fitText({text: a.artist, maxWidth: c.w * 0.82, maxLines: s.badge ? 4 : 5, sizes: c.tile,
                             font: F.display, measure});
        const step = fit.size * 1.05, block = fit.lines.length * step;
        fit.lines.forEach((t, i) => ops.push({op: "text", text: t, font: F.display(fit.size), color: tokens.tileInk,
                                              x: c.x + c.w / 2, y: c.y + (area - block) / 2 + i * step + fit.size * 0.8,
                                              align: "center", on: "tile"}));
      }
      // A grid cover's rank, on a corner of page colour so it reads over any cover (AOTY-74).
      if (s.badge) {
        const size = 22, font = F.body(size, 700), text = String(s.rank);
        const w = Math.ceil(measure(text, font)) + 16, h = BADGE_H;
        ops.push({op: "fill", color: tokens.bg, x: c.x, y: c.y + c.h - h, w, h, slot});
        ops.push({op: "text", text, font, color: C.badge, x: c.x + 8, y: c.y + c.h - 10, align: "left", on: "badge"});
      }
    });
    L.slots.forEach((s, slot) => {
      const a = albums[slot] || {};
      // fitText cuts the end, so the rank at the start always shows. A no-break space ties it to the
      // title's first word, so a title that breaks (CJK, one long word) keeps going after it.
      const title = L.count > 1 ? `#${s.rank}${NBSP}${a.title ?? ""}` : a.title;
      const titleH = line(s.title, title, F.display, C.title, s.title.y);
      line(s.artist, a.artist, q => F.body(q, 500), C.artist, s.title.y + titleH + s.artist.gap);
    });
    line(L.url, printedUrl(texts.url), s => F.body(s, 500), C.url, L.url.y);
    return ops;
  }

  // The [font, text] pairs to load before measuring: document.fonts.load(font, text) fetches only
  // the unicode-range subsets the text needs (é, Cyrillic, CJK), which a bare load wouldn't.
  function fontLoads({texts, tokens, format = DEFAULT_FORMAT}) {
    const F = fonts(tokens), albums = albumsOf(texts), count = FORMATS[formatOf(format)].count;
    const display = albums.map((a, i) => `#${i + 1} ${a.title || ""} ${a.artist || ""}`).join(" ");
    const artists = albums.map(a => a.artist || "").join(" ");
    const body = `${artists} ${printedUrl(texts.url)} ${texts.played || ""}`;
    return [[F.display(40), display], [F.body(30, 500), body],
            [F.body(30, 600), caption(texts.name, texts.year, texts.kind, count)], [F.brand(28), "a kamp toy"]];
  }

  const api = {FORMATS, DEFAULT_FORMAT, formatOf, MARK, layout, fonts, palette, PALETTE, graphemes, wrap, fitText, caption, printedUrl,
               fileName, compose, fontLoads};
  root.aotyShareCard = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
