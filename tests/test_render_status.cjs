const test = require('node:test');
const assert = require('node:assert/strict');
const { create, visible } = require('../frontend/render-status.js');
const { sameView, sameContext } = require('../frontend/rendered-preview.js');
const drain = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function fixture({ liveCapture = false } = {}) {
  let time = 0, id = 0;
  const timers = new Map(), requests = [], draws = [], accepted = [], errors = [];
  const view = { context: { workspace: 'w', project: 'p', revision: 'r' }, sequence: 's', revision: 1,
    pending: 0, viewport: { start: 10, end: 20, low: 0, high: 30 } };
  const f = { view, timers, requests, draws, accepted, errors };
  const controller = create({ capture: () => liveCapture ? view : structuredClone(view), sameView, sameContext,
    now: () => time, later: (fn, ms) => { timers.set(++id, { fn, at: time + ms }); return id; }, stopTimer: i => timers.delete(i),
    request: (view, range, signal) => new Promise((resolve, reject) => requests.push({ view, range, signal, resolve, reject })),
    draw: segments => draws.push(segments), accept: result => accepted.push(result), failed: error => errors.push(error) });
  f.controller = controller;
  f.advance = async ms => {
    time += ms;
    for (const [i, timer] of [...timers]) if (timer.at <= time) { timers.delete(i); timer.fn(); }
    await drain();
  };
  f.finish = async (i = requests.length - 1, extras = {}) => {
    const r = requests[i];
    r.resolve({ context: r.view.context, sequence: r.view.sequence, window: r.range,
      segments: [{ t0: r.range[0], t1: r.range[1], cached: true }], ...extras });
    await drain();
  };
  return f;
}

test('redraw bursts debounce, share one active inspection, and reuse its coverage', async () => {
  const f = fixture();
  for (let i = 0; i < 40; i++) f.controller.refresh();
  assert.equal(f.timers.size, 1); await f.advance(400); assert.equal(f.requests.length, 1);
  for (let i = 0; i < 40; i++) f.controller.refresh();
  await f.finish(); await f.advance(400);
  assert.equal(f.requests.length, 1); assert.equal(f.accepted.length, 1); assert.equal(f.draws.at(-1).length, 1);
  f.view.viewport = { start: 12, end: 22, low: 2, high: 32 }; // scrolling inside inspected coverage
  f.controller.refresh(); await f.advance(400);
  assert.equal(f.requests.length, 1); assert.equal(f.accepted.length, 1);
});

test('scrolling beyond coverage coalesces into the latest window after current work finishes', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400);
  for (let start of [40, 80, 200]) {
    f.view.viewport = { start, end: start + 10, low: start - 10, high: start + 20 };
    f.controller.refresh();
  }
  assert.equal(f.requests.length, 1); await f.finish(); await f.advance(400);
  assert.equal(f.requests.length, 2); assert.deepEqual(f.requests[1].range, [190, 220]);
  await f.finish(); assert.equal(f.draws.at(-1)[0].t0, 190);
});

test('completion forces fresh validation and rejects an earlier status response', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400);
  f.controller.refresh({ force: true }); await f.finish();
  assert.equal(f.accepted.length, 0); await f.advance(400); assert.equal(f.requests.length, 2);
  await f.finish(); assert.equal(f.accepted.length, 1);
});

test('a superseded failed request cannot discard the forced refresh queued behind it', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400);
  f.controller.refresh({ force: true }); f.requests[0].reject(new Error('old connection')); await drain();
  await f.advance(400); assert.equal(f.requests.length, 2); assert.equal(f.errors.length, 0);
  await f.finish(); assert.equal(f.accepted.length, 1);
});

test('captured identities do not follow in-place changes to the editor context', async () => {
  const f = fixture({ liveCapture: true }); f.controller.refresh(); await f.advance(400);
  f.view.context.revision = 'changed'; f.controller.refresh(); await f.finish();
  assert.equal(f.accepted.length, 0); assert.equal(f.requests[0].view.context.revision, 'r');
  await f.advance(400); await f.finish(); assert.equal(f.accepted.length, 1);
});

test('project, saved revision and sequence switches cannot reuse or accept old coverage', async () => {
  for (const change of [v => v.context.project = 'other', v => v.context.revision = 'new', v => v.sequence = 'new', v => v.revision++]) {
    const f = fixture(); f.controller.refresh(); await f.advance(400); change(f.view); f.controller.refresh();
    await f.finish(); assert.equal(f.accepted.length, 0); await f.advance(400); assert.equal(f.requests.length, 2);
    await f.finish(); assert.equal(f.accepted.length, 1);
  }
});

test('pending edits, failed saves, gestures and switches clear coverage and retire stale replies', async () => {
  for (const flag of ['pending', 'error', 'gesture', 'switching']) {
    const f = fixture(); f.controller.refresh(); await f.advance(400);
    f.view[flag] = true; f.controller.refresh(); await f.finish();
    assert.equal(f.accepted.length, 0); assert.equal(f.draws.at(-1).length, 0); assert.equal(f.timers.size, 0);
    f.view[flag] = false; f.controller.refresh(); await f.advance(400); assert.equal(f.requests.length, 2);
    await f.finish();
  }
});

test('failures do not spin and a later interaction can retry', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400); f.controller.refresh();
  f.requests[0].reject(new Error('offline')); await drain();
  assert.equal(f.errors.length, 1); assert.equal(f.timers.size, 0);
  f.controller.refresh(); await f.advance(400); await f.finish(); assert.equal(f.accepted.length, 1);
});

test('timeout aborts the request, releases the slot and ignores an eventual reply', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400); await f.advance(60000);
  assert.equal(f.requests[0].signal.aborted, true); assert.equal(f.errors.length, 1);
  f.controller.refresh(); await f.advance(400); assert.equal(f.requests.length, 2);
  await f.finish(0); assert.equal(f.accepted.length, 0);
  await f.finish(1); assert.equal(f.accepted.length, 1); assert.equal(f.timers.size, 0);
});

test('invalid context, window or segment coordinates cannot be painted or adopted', async () => {
  for (const extras of [{ context: { workspace: 'x' } }, { sequence: 'other' }, { window: [1, 30] },
    { segments: [{ t0: 0, t1: Infinity }] }, { segments: [{ t0: 4, t1: 3 }] },
    { segments: [{ t0: 2, t1: 3 }, { t0: 1, t1: 2 }] }]) {
    const f = fixture(); f.controller.refresh(); await f.advance(400); await f.finish(0, extras);
    assert.equal(f.accepted.length, 0); assert.equal(f.errors.length, 1); assert.equal(f.draws.at(-1).length, 0);
  }
});

test('coverage expires and reused drawing never re-enables a previously validated preview', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400); await f.finish();
  await f.advance(9999); f.controller.refresh(); assert.equal(f.accepted.length, 1);
  await f.advance(1); f.controller.refresh(); assert.equal(f.draws.at(-1).length, 0);
  await f.advance(400); assert.equal(f.requests.length, 2); await f.finish(); assert.equal(f.accepted.length, 2);
});

test('disposing cancels transport and prevents future drawing or adoption', async () => {
  const f = fixture(); f.controller.refresh(); await f.advance(400); f.controller.dispose();
  const count = f.draws.length; assert.equal(f.requests[0].signal.aborted, true);
  await f.finish(); f.controller.refresh(); assert.equal(f.accepted.length, 0);
  assert.equal(f.draws.length, count); assert.equal(f.timers.size, 0);
});

test('visible coverage remains bounded in a long sequence and retains exact segment boundaries', () => {
  const all = Array.from({ length: 20000 }, (_, i) => ({ t0: i*4, t1: (i+1)*4, cached: i % 2 === 0 }));
  assert.deepEqual(visible(all, 40000, 40020), all.slice(10000, 10005));
  assert.deepEqual(visible(all, 40001, 40019), all.slice(10000, 10005));
  assert.deepEqual(visible(all, 80000, 80010), []);
});
