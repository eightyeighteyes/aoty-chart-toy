// Share card drawing (AOTY-70, moved here for AOTY-91): plays one of sharecard.compose's draw
// operations onto a 2D canvas context. The page draws share images with it, and the card Lambda
// (lambdas/card) draws link preview cards with the same code on @napi-rs/canvas, so the two can't
// drift. Only the standard CanvasRenderingContext2D calls both support.
(function (root) {
  "use strict";

  // img: the cover for a cover op (o.slot's). A browser image has naturalWidth; a server one may
  // have only width.
  function drawOp(ctx, o, img) {
    ctx.save();
    if (o.op === "fill") {
      ctx.fillStyle = o.color;
      ctx.fillRect(o.x, o.y, o.w, o.h);
    } else if (o.op === "text") {
      ctx.font = o.font;
      ctx.fillStyle = o.color;
      ctx.textAlign = o.align;
      ctx.textBaseline = "alphabetic";
      ctx.fillText(o.text, o.x, o.y);
    } else if (o.op === "mark") {
      const k = o.size / 200, cx = o.x + o.size / 2, cy = o.y + o.size / 2;
      for (const c of o.circles) {
        ctx.beginPath();
        ctx.arc(cx, cy, c.r * k, 0, 2 * Math.PI);
        if (c.fill) { ctx.fillStyle = c.fill; ctx.fill(); }
        if (c.stroke) { ctx.strokeStyle = c.stroke; ctx.lineWidth = c.lineWidth * k; ctx.stroke(); }
      }
    } else if (o.op === "cover") {
      // Cropped to a square from the middle, as object-fit: cover; with a soft shadow under it.
      const w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
      const s = Math.min(w, h);
      ctx.shadowColor = `rgba(0, 0, 0, ${o.shadow})`;
      ctx.shadowBlur = o.blur ?? 48;  // smaller on small covers (AOTY-74)
      ctx.shadowOffsetY = o.drop ?? 18;
      ctx.drawImage(img, (w - s) / 2, (h - s) / 2, s, s, o.x, o.y, o.w, o.h);
    } else if (o.op === "tile") {
      const g = ctx.createLinearGradient(o.x, o.y, o.x + o.w, o.y + o.h);  // 135 degrees, as .noart
      g.addColorStop(0, o.from);
      g.addColorStop(1, o.to);
      ctx.fillStyle = g;
      ctx.fillRect(o.x, o.y, o.w, o.h);
    }
    ctx.restore();
  }

  // Every op of a composed card, each cover op with its slot's image. imgs: per slot, or null.
  function drawCard(ctx, ops, imgs) {
    for (const o of ops) drawOp(ctx, o, imgs[o.slot]);
  }

  const api = {drawOp, drawCard};
  root.aotyCardDraw = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
