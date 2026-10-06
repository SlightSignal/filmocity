/* Render coverage is inspected once per view, then redrawn locally on scroll/zoom. */
(function (root, factory) {
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else root.FilmocityRenderStatus = value;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const safe = v => v?.context && v.sequence && !v.pending && !v.error && !v.gesture && !v.switching;
  const covers = (range, view) => range[0] <= view.viewport.start && range[1] >= view.viewport.end;

  function visible(segments, low, high) {
    // Server coverage is ordered and non-overlapping. Skip the offscreen prefix.
    let a = 0, b = segments.length;
    while (a < b) { const m = (a + b) >>> 1; if (segments[m].t1 <= low) a = m + 1; else b = m; }
    const result = [];
    for (; a < segments.length && segments[a].t0 < high; a++) result.push(segments[a]);
    return result;
  }

  function validate(result, view, range, sameContext) {
    if (!result || !sameContext(view.context, result.context) || result.sequence !== view.sequence ||
        !Array.isArray(result.window) || result.window[0] !== range[0] || result.window[1] !== range[1] ||
        !Array.isArray(result.segments)) throw new Error('Render coverage no longer matches this view.');
    let end = -Infinity;
    for (const segment of result.segments) {
      if (!Number.isFinite(segment.t0) || !Number.isFinite(segment.t1) || segment.t0 < 0 ||
          segment.t0 < end || segment.t1 < segment.t0) throw new Error('Invalid render coverage.');
      end = segment.t1;
    }
    return result;
  }

  function create(options) {
    const later = options.later || setTimeout, stop = options.stopTimer || clearTimeout;
    const now = options.now || Date.now, maxAge = options.maxAge ?? 10000;
    let timer = null, flight = null, cached = null, wanted = null, epoch = 0, disposed = false;
    const same = options.sameView;
    const capture = () => {
      const view = options.capture();
      return view && { ...view, context: view.context && { ...view.context }, viewport: { ...view.viewport } };
    };
    function paint(view) {
      const usable = safe(view) && cached && same(cached.view, view) && now() - cached.at < maxAge;
      options.draw(usable ? visible(cached.result.segments, view.viewport.low, view.viewport.high) : [], view);
      return usable && covers(cached.range, view);
    }
    function schedule() {
      if (!disposed && !flight && !timer && wanted) timer = later(run, options.delay ?? 400);
    }
    function refresh({ force = false } = {}) {
      if (disposed) return;
      const view = capture();
      if (force) { epoch++; cached = null; }
      if (!safe(view)) { invalidate(); return; }
      if (paint(view)) { wanted = null; return; }
      // Keep just the latest view while one request is running. Redraws of that
      // same view share its result rather than launching another inspection.
      wanted = view;
      schedule();
    }
    async function run() {
      timer = null;
      if (disposed || flight || !wanted) return;
      const view = capture(); wanted = null;
      if (!safe(view)) return;
      const range = [view.viewport.low, view.viewport.high], ticket = epoch;
      const controller = new (options.Abort || AbortController)();
      flight = controller;
      let deadline;
      try {
        const timeout = new Promise((resolve, reject) => {
          deadline = later(() => { controller.abort(); reject(new Error('Render coverage check timed out.')); }, options.timeout ?? 60000);
        });
        const result = validate(await Promise.race([options.request(view, range, controller.signal), timeout]), view, range, options.sameContext);
        const current = capture();
        if (disposed || ticket !== epoch || !safe(current) || !same(view, current)) return;
        cached = { view, range, result, at: now() };
        options.accept?.(result, view);
        if (paint(current)) wanted = null;
        else wanted = current;
      } catch (error) {
        if (!disposed && ticket === epoch && same(view, capture())) options.failed?.(error);
        // No automatic error loop. A subsequent interaction may request again.
        if (ticket === epoch && wanted && same(wanted, view) && covers(range, wanted)) wanted = null;
      } finally {
        stop(deadline); flight = null;
        schedule();
      }
    }
    function invalidate() {
      epoch++; cached = null; wanted = null;
      if (timer !== null) stop(timer); timer = null;
      options.draw([], capture());
      // Let active server work finish; never fan out checks on each gesture.
    }
    function dispose() {
      invalidate(); disposed = true; flight?.abort();
    }
    return { refresh, invalidate, dispose };
  }
  return { create, visible };
});
