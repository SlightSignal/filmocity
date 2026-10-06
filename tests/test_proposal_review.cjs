const test = require('node:test');
const assert = require('node:assert/strict');
const { fixture, deferred, settle, key } = require('./dom_fixture.cjs');
const { createReviewController } = require('../frontend/proposal-review.js');
const ids = ['Title', 'Image', 'Placeholder', 'Message', 'Sequence', 'Time', 'Back', 'Next', 'Retry', 'Accept', 'Close', 'Position', 'Warnings', 'Summary', 'Changes', 'Scope'];
const context = { workspace: 'root', project: 'a', revision: 'r1' };
const snapshot = (extra = {}) => ({ ok: true, id: 'view', proposal: 'proposal', items: ['one', 'two'], plan: 'digest', context: { ...context }, expires_at: 100,
  warnings: ['Clip was split around insert'], changes: [{ path: ['media', 'a'], after: { value: '<img onerror=bad()>' } }],
  sequences: [{ id: 'new sequence', name: '<New sequence>', fps: 24, duration: 2 }, { id: 's', name: 'Active', fps: 30, duration: 1 }], ...extra });
function setup() {
  const h = fixture(['dlgProposalReview', ...ids.map(id => 'proposalReview' + id)]), pending = deferred(), acceptance = deferred();
  const clock = { now: 0 }, timers = new Map(), requests = [], releases = [], revokes = [], urls = [], decisions = [];
  const state = { context: { ...context }, unsaved: false, sequence: 's', time: 99 }, original = JSON.stringify(state);
  const get = id => h.nodes['proposalReview' + id]; let pauses = 0;
  const controller = createReviewController({ document: h.document, prepare: () => pending.promise,
    decide: (pid, items, view) => { decisions.push({ pid, items, view }); return acceptance.promise; },
    release: id => { releases.push(id); return Promise.resolve(); },
    current: () => state, pause: () => { pauses++; }, now: () => clock.now,
    later: (fn, ms) => { const id = Symbol(); timers.set(id, { fn, ms }); return id; }, cancel: id => timers.delete(id),
    makeURL: blob => { const url = 'blob:' + blob; urls.push(url); return url; }, revokeURL: url => revokes.push(url),
    frame: (url, signal) => { const d = deferred(); requests.push({ ...d, url, signal }); return d.promise; },
  });
  const items = [{ id: 'one', note: '<literal note>', reasons: ['pacing'] }, { id: 'two', note: '', reasons: [] }];
  async function start(view = snapshot()) { const open = controller.open('proposal', items, '<Proposal>'); pending.resolve(view); await open; await settle(); }
  function runTimers(ms) { for (const [id, t] of [...timers]) if (t.ms === ms) { timers.delete(id); t.fn(); } }
  return { ...h, controller, clock, timers, requests, releases, revokes, urls, decisions, state, original, pending, acceptance, get, start, runTimers, items, pauses: () => pauses };
}
test('review uses the prepared sequences/frame bounds and displays agent data literally without changing editor state', async () => {
  const h = setup(); await h.start();
  assert.equal(h.get('Title').textContent, '<Proposal>'); assert.equal(h.get('Sequence').children[0].textContent, '<New sequence>');
  assert.ok(h.get('Changes').textContent.includes('<img onerror=bad()>'));
  assert.equal(h.get('Time').value, '29'); assert.equal(h.requests.length, 1); assert.match(h.requests[0].url, /sequence=s&t=0.966/);
  h.requests[0].resolve('first'); await settle(); assert.equal(h.get('Image').src, 'blob:first'); assert.equal(h.get('Image').hidden, false);
  h.get('Sequence').value = 'new sequence'; h.get('Sequence').onchange();
  assert.equal(h.get('Time').max, '47'); assert.equal(h.get('Time').value, '0'); assert.match(h.requests[1].url, /sequence=new%20sequence&t=0$/);
  assert.deepEqual(h.revokes, ['blob:first']); assert.equal(JSON.stringify(h.state), h.original); assert.equal(h.pauses(), 1);
});
test('scrubbing coalesces in-flight requests and never labels an older frame as the selected frame', async () => {
  const h = setup(); await h.start();
  for (let i = 2; i < 9; i++) { h.get('Time').value = String(i); h.get('Time').oninput(); }
  h.runTimers(180); assert.equal(h.requests.length, 1);
  h.requests[0].resolve('obsolete'); await settle(); assert.equal(h.urls.length, 0); assert.equal(h.requests.length, 2);
  assert.match(h.requests[1].url, /&t=0.266/); h.requests[1].resolve('latest'); await settle();
  assert.equal(h.get('Image').src, 'blob:latest'); assert.match(h.get('Image').alt, /frame 9/);
});
test('closing during preparation releases a late snapshot and restores focus without rendering', async () => {
  const h = setup(), open = h.controller.open('proposal', h.items, 'Title');
  assert.equal(h.get('Accept').disabled, true); h.get('Close').onclick(); h.pending.resolve(snapshot()); await open;
  assert.deepEqual(h.releases, ['view']); assert.equal(h.requests.length, 0); assert.equal(h.document.activeElement, h.origin);
});
test('close cancels rendering, releases the snapshot, and discards late decoded frames', async () => {
  const h = setup(); await h.start(); h.get('Close').onclick();
  assert.equal(h.requests[0].signal.aborted, true); assert.deepEqual(h.releases, ['view']);
  h.requests[0].resolve('late'); await settle(); assert.equal(h.urls.length, 0); assert.equal(h.timers.size, 0);
});
test('a reopened review ignores queued old close events and old frame completions', async () => {
  const h = setup(); await h.start(); h.get('Close').onclick(); await h.controller.open('proposal', h.items, 'Reopened');
  h.nodes.dlgProposalReview.events.close(); h.requests[0].resolve('old'); await settle();
  assert.equal(h.nodes.dlgProposalReview.open, true); assert.equal(h.requests.length, 2); assert.equal(h.urls.length, 0);
  h.requests[1].resolve('reopened'); await settle(); assert.equal(h.get('Image').src, 'blob:reopened');
});
test('changed revision, storage identity, unsaved edits, and expiry disable acceptance and hide stale pixels', async () => {
  for (const mutate of [h => h.state.context.revision = 'r2', h => h.state.context.project = 'b', h => h.state.unsaved = true, h => h.clock.now = 100001]) {
    const h = setup(); await h.start(); h.requests[0].resolve('first'); await settle(); mutate(h); h.runTimers(1000);
    assert.equal(h.get('Accept').disabled, true); assert.equal(h.get('Image').hidden, true); assert.match(h.get('Message').textContent, /expired or the project changed/);
    await h.get('Accept').onclick(); assert.equal(h.decisions.length, 0); assert.deepEqual(h.revokes, ['blob:first']);
  }
});
test('accept sends the reviewed item selection/notes and exact snapshot, blocks duplicates and waits for confirmation', async () => {
  const h = setup(); await h.start(); h.items[0].note = 'Changed outside review';
  const action = h.get('Accept').onclick(); await h.get('Accept').onclick(); h.get('Close').onclick();
  assert.equal(h.decisions.length, 1); assert.equal(h.decisions[0].items[0].note, '<literal note>'); assert.equal(h.decisions[0].view.plan, 'digest');
  assert.equal(h.get('Close').disabled, true); assert.equal(h.nodes.dlgProposalReview.open, true);
  h.state.context.revision = 'accepted'; h.runTimers(1000); assert.equal(h.releases.length, 0);
  h.acceptance.resolve(true); await action; assert.equal(h.nodes.dlgProposalReview.open, false); assert.deepEqual(h.releases, ['view']);
});
test('an uncertain decision remains explicit and cannot be retried from the same review', async () => {
  const h = setup(); await h.start(); const action = h.get('Accept').onclick(); h.acceptance.resolve(false); await action;
  assert.equal(h.nodes.dlgProposalReview.open, true); assert.equal(h.get('Accept').disabled, true); assert.equal(h.get('Close').disabled, false);
  assert.match(h.get('Message').textContent, /not confirmed/); await h.get('Accept').onclick(); assert.equal(h.decisions.length, 1);
});
test('render failure offers an explicit retry; invalidated server previews require a new review', async () => {
  const h = setup(); await h.start(); h.requests[0].reject(new Error('Decoder failed')); await settle();
  assert.match(h.get('Message').textContent, /Decoder failed/); assert.equal(h.get('Image').hidden, true); assert.equal(h.requests.length, 1);
  h.get('Retry').onclick(); assert.equal(h.requests.length, 2);
  h.requests[1].reject(Object.assign(new Error('Saved project changed'), { status: 409 })); await settle();
  assert.equal(h.get('Accept').disabled, true); assert.match(h.get('Message').textContent, /Saved project changed/);
});
test('failed preparation and keyboard cancellation leave an accessible close action', async () => {
  const h = setup(), open = h.controller.open('proposal', h.items, 'Failed'); h.pending.reject(new Error('Unsaved draft')); await open;
  assert.match(h.get('Message').textContent, /Unsaved draft/); assert.equal(h.get('Accept').disabled, true); assert.equal(h.get('Close').disabled, false);
  const press = key('Delete'); h.nodes.dlgProposalReview.events.keydown(press); assert.equal(press.stopped, true);
  const escape = key('Escape'); h.nodes.dlgProposalReview.events.cancel(escape); assert.equal(escape.defaultPrevented, true); assert.equal(h.document.activeElement, h.origin);
});
test('failed image decoding clears the claimed frame and remains explicitly retryable', async () => {
  const h = setup(); await h.start(); h.requests[0].resolve('bad-image'); await settle(); h.get('Image').onerror();
  assert.equal(h.get('Image').hidden, true); assert.match(h.get('Message').textContent, /could not be displayed/); assert.deepEqual(h.revokes, ['blob:bad-image']);
  h.get('Retry').onclick(); assert.equal(h.requests.length, 2);
});
test('acceptance returns focus to the Proposals tab when refreshed controls replace the origin', async () => {
  const h = setup(); await h.start(); h.origin.isConnected = false;
  const fallback = h.document.createElement('button'); h.document.querySelector = () => fallback;
  const action = h.get('Accept').onclick(); h.acceptance.resolve(true); await action;
  assert.equal(h.document.activeElement, fallback);
  h.get('Close').focus(); h.nodes.dlgProposalReview.events.close(); assert.equal(h.document.activeElement, h.get('Close'));
});
test('initial playhead sets the new range bounds before browser value sanitization can clamp it', async () => {
  const h = setup(); h.state.time = 5;
  let stored = '0'; h.get('Time').max = '29';
  Object.defineProperty(h.get('Time'), 'value', { get: () => stored, set: v => { stored = String(Math.max(0, Math.min(+h.get('Time').max, +v))); } });
  await h.start(snapshot({ sequences: [{ id: 's', name: 'Longer', fps: 30, duration: 10 }] }));
  assert.equal(h.get('Time').value, '150'); assert.match(h.requests[0].url, /&t=5$/);
});
test('a coalesced frame that finishes before its debounce timer does not render a second time', async () => {
  const h = setup(); await h.start(); h.get('Time').value = '2'; h.get('Time').oninput();
  h.requests[0].resolve('old'); await settle(); assert.equal(h.requests.length, 2);
  h.requests[1].resolve('new'); await settle(); h.runTimers(180); await settle();
  assert.equal(h.requests.length, 2); assert.deepEqual(h.urls, ['blob:new']);
});
