const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const geometry = require('../frontend/timeline-window.js');
const { createHarness, makeProject } = require('../benchmarks/timeline-harness.cjs');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const baseline = fs.readFileSync(path.join(__dirname, '../benchmarks/baselines/timeline-before.js'), 'utf8');
const app = (seconds = 180, options) => createHarness(source, makeProject(seconds), options);
const left = el => parseFloat(el.style.left);
function onscreen(h, cls, scroll, width) {
  return h.elements(cls).filter(el => left(el) <= scroll + width && left(el) + (parseFloat(el.style.width) || 0) >= scroll)
    .map(el => [el.className, el.style.left, el.style.width || '', el.title || '', el.textContent || '']).sort();
}

test('viewport rendering preserves visible positions, labels and spans across zoom/scroll', () => {
  for (const pps of [6, 15, 30, 60, 120, 600]) for (const seconds of [0, 100, 3599, 7180]) {
    const project = makeProject(7200, 1), scroll = seconds * pps, viewport = 960;
    const before = createHarness(baseline, project, { scroll, viewport, pps });
    const after = createHarness(source, project, { scroll, viewport, pps });
    before.render(); after.render();
    for (const cls of ['clip', 'ruler-tick', 'ruler-lbl', 'marker']) {
      assert.deepEqual(onscreen(after, cls, scroll, viewport), onscreen(before, cls, scroll, viewport), `${cls}: ${pps}px/s @ ${seconds}s`);
    }
    // Caption markup intentionally became literal text; geometry and titles stay equal.
    const captions = h => onscreen(h, 'capclip', scroll, viewport).map(row => row.slice(0, 4));
    assert.deepEqual(captions(after), captions(before));
  }
});

test('long-project element construction stays bounded by the viewport', () => {
  const counts = [180, 1200, 7200].map(seconds => app(seconds, { scroll: seconds / 2 * 60 }).render());
  assert.ok(Math.max(...counts) < 350, counts.join(', '));
  assert.equal(Math.max(...counts), Math.min(...counts));
});

test('selected offscreen clips remain present, without all offscreen decorations', () => {
  const h = app(7200, { scroll: 3600 * 60 }); h.S.sel.add('0-0'); h.render();
  assert.ok(h.elements('clip').some(el => el.dataset.clip === '0-0'));
  assert.ok(!h.elements('capclip').some(el => left(el) === 0));
  assert.ok(!h.elements('marker').some(el => left(el) === 0));
});

test('captions and duration markers spanning the viewport keep their original coordinates', () => {
  const h = app(7200, { scroll: 3600 * 60 });
  h.S.seq.captions = [{ id: 'span', start: 20, end: 3610, text: '<img src=x onerror=bad()>\nsecond line' }];
  h.S.seq.markers = [{ time: 10, duration: 3610, name: 'long marker' }]; h.render();
  const caption = h.elements('capclip')[0], marker = h.elements('marker')[0];
  assert.equal(caption.style.left, '1200px'); assert.equal(caption.style.width, '215400px');
  assert.equal(caption.textContent, '<img src=x onerror=bad()>');
  assert.equal(caption.children.length, 2); assert.equal(caption.children[0].className, 'h l');
  assert.equal(marker.style.left, '600px'); assert.equal(marker.style.width, '216600px');
  let edited; h.scope.CR.panels.editMarker = m => { edited = m; };
  marker.ondblclick({ stopPropagation() {} }); assert.equal(edited, h.S.seq.markers[0]);
});

test('ghost proposals are bounded, use literal labels, and belong to the active sequence', () => {
  const h = app(7200, { scroll: 3600 * 60 });
  const op = (sequence, start) => ({ op: 'set_clip', sequence, track: 'V0', clip: { id: 'new', start, in_: 0, out: 5 } });
  h.S.proj.proposals = [{ title: 'Title', items: [{ status: 'pending', reason: '<b>Review</b>', ops: [op('s', 3605), op('other', 3605), op('s', 10)] }] }];
  h.render(); const ghosts = h.elements('ghost'); assert.equal(ghosts.length, 1);
  assert.equal(ghosts[0].children[0].textContent, 'agent: <b>Review</b>');
  ghosts[0].onmousedown({ stopPropagation() {} }); assert.ok(h.calls.includes('props'));
});

test('through-edit indicators retain boundary cuts and omit distant cuts', () => {
  const h = app(7200, { scroll: 3600 * 60 });
  for (const tr of h.S.seq.tracks) for (const c of tr.clips) { c.in_ = c.start; c.out = c.start + 5; }
  h.render(); const cuts = h.elements('through');
  assert.equal(cuts.length, 13 * 4);
  assert.ok(cuts.some(el => left(el) === 3580 * 60));
  assert.ok(cuts.some(el => left(el) === 3640 * 60));
  assert.ok(cuts.every(el => left(el) >= 3580 * 60 && left(el) <= 3640 * 60));
});

test('scrolling past overscan schedules one refresh and shows the new window', () => {
  const h = app(7200); h.render();
  h.nodes.tlBody.scrollLeft = 3600 * 60; h.nodes.tlBody.scrollTop = 22;
  h.nodes.tlBody.onscroll(); h.nodes.tlBody.onscroll(); assert.equal(h.pending.size, 1);
  assert.equal(h.nodes.ruler.scrollLeft, 216000); assert.equal(h.nodes.heads.scrollTop, 22);
  h.advance(16); assert.equal(h.pending.size, 0);
  assert.equal(h.S.tlVis[0], 3600); assert.ok(h.elements('ruler-tick').some(el => left(el) === 216000));
});

test('panel width changes invalidate the viewport while height-only callbacks do not', () => {
  const element = { clientWidth: 300 }; let callback, observed, refreshes = 0;
  class Observer { constructor(cb) { callback = cb; } observe(el) { observed = el; } }
  geometry.observeWidth(element, () => refreshes++, Observer); assert.equal(observed, element);
  callback(); assert.equal(refreshes, 0);
  element.clientWidth = 900; callback(); callback(); assert.equal(refreshes, 1);
  element.clientWidth = 0; callback(); assert.equal(refreshes, 1);
  element.clientWidth = 500; callback(); assert.equal(refreshes, 2);
  assert.equal(geometry.observeWidth(element, () => refreshes++, undefined), null);
});

test('duration handles more clips than a function argument list allows and sees live changes', () => {
  const h = app(), clip = { start: 1, in_: 0, out: 8, speed: 2 };
  const seq = { tracks: [{ clips: Array(200000).fill(clip) }] };
  assert.equal(h.scope.durationOf(seq), 5);
  clip.start = 10; assert.equal(h.scope.durationOf(seq), 14);
  clip.hold = true; assert.equal(h.scope.durationOf(seq), 18);
  clip.hold = false; clip.time_remap = [{ t: 0, v: 4 }]; assert.equal(h.scope.durationOf(seq), 12);
  assert.equal(h.scope.durationOf({ tracks: [] }), 0);
});

test('repeated Play commands retain one loop and do not reset elapsed playback time', () => {
  const h = app(); h.scope.togglePlay(true); h.elapse(100);
  for (let i = 0; i < 20; i++) h.scope.togglePlay(true);
  assert.equal(h.pending.size, 1); h.advance(100); assert.equal(h.S.t, .2);
  assert.equal(h.pending.size, 1); assert.equal(h.calls.filter(x => x === 'playhead').length, 1);
});

test('pause cancels work; a stale callback cannot overwrite a resumed frame', () => {
  const h = app(); h.scope.togglePlay(true); const stale = [...h.pending.values()][0];
  h.scope.togglePlay(false); assert.equal(h.pending.size, 0); h.elapse(1000);
  h.scope.togglePlay(true); stale(1000); assert.equal(h.S.t, 0); assert.equal(h.pending.size, 1);
  h.advance(100); assert.equal(h.S.t, .1); assert.equal(h.pending.size, 1);
});

test('endpoints and reverse playback stop with no trailing animation frame', () => {
  const h = app(5); h.S.t = 4.9; h.scope.togglePlay(true); h.advance(200);
  assert.equal(h.S.playing, false); assert.equal(h.S.t, 5); assert.equal(h.pending.size, 0);
  h.S.t = .1; h.S.rate = -2; h.scope.togglePlay(true); h.advance(100);
  assert.equal(h.S.playing, false); assert.equal(h.S.t, 0); assert.equal(h.pending.size, 0);
});

test('loop playback and play In/Out reuse the existing loop and update the stopped playhead', () => {
  const h = app(); h.S.seq.in_point = 10; h.S.seq.out_point = 12; h.S.loop = true;
  h.S.t = 11.9; h.scope.togglePlay(true); h.advance(200);
  assert.equal(h.S.t, 10); assert.equal(h.S.playing, true); assert.equal(h.pending.size, 1);
  h.scope.playInOut(); assert.equal(h.pending.size, 1); h.advance(2100);
  assert.equal(h.S.t, 12); assert.equal(h.S.playing, false); assert.equal(h.pending.size, 0);
  assert.equal(h.calls.at(-1), 'playhead');
});

test('play Around during playback preserves its exact stop and a single owner', () => {
  const h = app(); h.S.t = 30; h.scope.togglePlay(true); h.scope.playAround(2, 2);
  assert.equal(h.S.t, 28); assert.equal(h.S.stopAt, 32); assert.equal(h.pending.size, 1);
  h.advance(4500); assert.equal(h.S.t, 32); assert.equal(h.S.playing, false); assert.equal(h.pending.size, 0);
});
