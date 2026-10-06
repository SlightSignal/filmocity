/* Timeline geometry. Coordinates stay in sequence seconds, including offscreen spans. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FilmocityTimeline = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  function viewport(scroll, width, pps) {
    const start = scroll / pps, end = (scroll + width) / pps;
    const margin = Math.max(5, end - start);
    return { start, end, low: Math.max(0, start - margin), high: end + margin };
  }
  function intersects(view, start, end = start) {
    return end >= view.low && start <= view.high;
  }
  function* ticks(view, duration, step) {
    // Integer tick indices retain the origin-aligned grid at every scroll position.
    const first = Math.max(0, Math.floor(view.low / step));
    const last = Math.floor(Math.min(duration, view.high) / step);
    for (let i = first; i <= last; i++) yield i * step;
  }
  function observeWidth(element, onChange, Observer) {
    if (!Observer) return null;
    let width = element.clientWidth;
    const observer = new Observer(() => {
      const next = element.clientWidth;
      if (next > 0 && next !== width) { width = next; onChange(); }
    });
    observer.observe(element);
    return observer;
  }
  return { viewport, intersects, ticks, observeWidth };
});
