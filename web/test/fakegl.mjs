// A stand-in WebGL 1 context, a canvas and a rAF clock for backgrounds.js runShader (AOTY-103). Not a
// test file itself (web/test/*.test.mjs): the background tests share it. It compiles nothing, so
// shaders are still checked statically and by hand; what it shows is what the loop and a scene do.

// Every uniform call lands in gl.set (name -> the last value), and gl.draws counts draw calls.
export function fakeGl() {
  const gl = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, ARRAY_BUFFER: 5,
    STATIC_DRAW: 6, FLOAT: 7, TRIANGLES: 8,
    set: new Map(), draws: 0, lost: false, viewports: 0, programs: [],
    createShader: () => ({}), shaderSource() {}, compileShader() {}, getShaderParameter: () => true,
    deleteShader() {}, createProgram: () => ({}), attachShader() {}, bindAttribLocation() {},
    linkProgram() {}, getProgramParameter: () => true, deleteProgram() {}, createBuffer: () => ({}),
    bindBuffer() {}, bufferData() {}, deleteBuffer() {}, useProgram() {}, enableVertexAttribArray() {},
    vertexAttribPointer() {},
    drawArrays() { gl.draws++; },
    viewport() { gl.viewports++; },
    getUniformLocation: (_, name) => name,
    getExtension: name => (name === "WEBGL_lose_context" ? {loseContext() { gl.lost = true; }} : null),
  };
  for (const f of ["uniform1f", "uniform2f", "uniform3f", "uniform4f", "uniform1fv", "uniform2fv", "uniform3fv", "uniform4fv"]) {
    gl[f] = (loc, ...v) => gl.set.set(loc, v.length === 1 ? v[0] : v);
  }
  return gl;
}

// A stand-in Canvas 2D context (AOTY-102). ops records every fill and drawImage with the state it
// was drawn in ({op, composite, alpha, ...}); gradients record their stops. Setting the owning
// canvas's width resets the state, as a real one does (resetOnResize).
export function fake2d() {
  const fresh = () => ({globalAlpha: 1, globalCompositeOperation: "source-over", fillStyle: "#000000",
                        transform: [1, 0, 0, 1, 0, 0], filter: "none"});
  const ctx = {
    ...fresh(),
    ops: [], gradients: [],
    resetState() { Object.assign(ctx, fresh()); },
    setTransform(a, b, c, d, e, f) { ctx.transform = [a, b, c, d, e, f]; },
    clearRect(x, y, w, h) { ctx.ops.push({op: "clear", x, y, w, h}); },
    fillRect(x, y, w, h) { ctx.ops.push(ctx.snap({op: "fillRect", x, y, w, h})); },
    beginPath() {}, arc() {}, fill() { ctx.ops.push(ctx.snap({op: "fill"})); },
    drawImage(img, x, y, w, h) { ctx.ops.push(ctx.snap({op: "drawImage", img, x, y, w, h})); },
    createRadialGradient(x0, y0, r0, x1, y1, r1) { return ctx.gradient({kind: "radial", x0, y0, r0, x1, y1, r1}); },
    createLinearGradient(x0, y0, x1, y1) { return ctx.gradient({kind: "linear", x0, y0, x1, y1}); },
    gradient(g) {
      g.stops = [];
      g.addColorStop = (at, color) => {
        if (!(at >= 0 && at <= 1)) throw new RangeError(`stop ${at} outside 0..1`);
        g.stops.push([at, color]);
      };
      ctx.gradients.push(g);
      return g;
    },
    snap(o) {
      return {...o, composite: ctx.globalCompositeOperation, alpha: ctx.globalAlpha, fillStyle: ctx.fillStyle,
              filter: ctx.filter, transform: ctx.transform.slice()};
    },
  };
  return ctx;
}

// A canvas carrying a 2D context: setting width or height resets the context's state.
export function fake2dCanvas(ctx, w = 400, h = 800) {
  const c = fakeCanvas(null, w, h);
  let width = 0, height = 0;
  Object.defineProperty(c, "width", {get: () => width, set: v => { width = v; ctx.resetState(); }});
  Object.defineProperty(c, "height", {get: () => height, set: v => { height = v; ctx.resetState(); }});
  c.getContext = type => (type === "2d" ? ctx : null);
  return c;
}

// OffscreenCanvas for the sprites a canvas engine bakes; each gets its own fake 2D context.
// withFilter false is Safari: no ctx.filter at all.
export function fakeOffscreen({withFilter = true} = {}) {
  return class {
    constructor(w, h) {
      this.width = w;
      this.height = h;
      this.ctx = fake2d();
      if (!withFilter) delete this.ctx.filter;
    }
    getContext(type) { return type === "2d" ? this.ctx : null; }
  };
}

export function fakeCanvas(gl, w = 400, h = 800) {
  const listeners = new Map();
  return {
    clientWidth: w, clientHeight: h, width: 0, height: 0, listeners,
    getContext: () => gl,
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: type => listeners.delete(type),
  };
}

// A controllable page: requestAnimationFrame callbacks run on step(ms), and a document whose hidden
// flag fires visibilitychange. install() puts them on globalThis; restore() takes them off again.
export function fakePage() {
  let now = 1000, queue = [], id = 0;
  const docListeners = new Map();
  const page = {
    doc: {
      hidden: false,
      addEventListener: (t, fn) => docListeners.set(t, fn),
      removeEventListener: t => docListeners.delete(t),
    },
    pending: () => queue.length,
    step(ms) {
      now += ms;
      const run = queue;
      queue = [];
      for (const [, fn] of run) fn(now);
    },
    setHidden(hidden) {
      page.doc.hidden = hidden;
      const fn = docListeners.get("visibilitychange");
      if (fn) fn();
    },
  };
  const saved = {};
  const keys = ["requestAnimationFrame", "cancelAnimationFrame", "document", "devicePixelRatio", "OffscreenCanvas"];
  page.install = () => {
    for (const k of keys) saved[k] = Object.getOwnPropertyDescriptor(globalThis, k);
    globalThis.requestAnimationFrame = fn => { queue.push([++id, fn]); return id; };
    globalThis.cancelAnimationFrame = n => { queue = queue.filter(([i]) => i !== n); };
    globalThis.document = page.doc;
    globalThis.devicePixelRatio = 1;
    globalThis.OffscreenCanvas = fakeOffscreen();
    return page;
  };
  page.restore = () => {
    for (const k of keys) {
      if (saved[k]) Object.defineProperty(globalThis, k, saved[k]);
      else delete globalThis[k];
    }
  };
  return page;
}
