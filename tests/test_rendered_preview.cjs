const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../frontend/rendered-preview.js');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const core = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const panels = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');
const JOB = '0123456789abcdef', PREVIEW = '0123456789abcdef0123456789abcdef';
const ownedOutput = (id = JOB) => `/renders/job-${id}/preview_${PREVIEW}.mp4`;
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const drain = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function fixture() {
  const view = { context: { workspace: 'w', project: 'p', revision: 'saved-1' }, sequence: 's', revision: 1, pending: 0, error: '' };
  const timers = new Map(), requests = [], changes = [], adopted = [];
  let clears = 0, timerId = 0;
  const f = { view, timers, requests, changes, adopted, jobStatus: { status: 'queued' },
    flush: async () => {}, submit: async () => ({ id: JOB }),
    validate: async () => ({ id: JOB, out: ownedOutput(), preview: {
      context: { ...view.context }, sequence: view.sequence, range: [.25, .75], signature: 'signature', ranged: true } }) };
  f.api = {
    get: async path => { requests.push(['GET', path]); return typeof f.jobStatus === 'function' ? f.jobStatus() : f.jobStatus; },
    json: async (method, path, body) => {
      requests.push([method, path, structuredClone(body)]);
      if (path === '/api/render/preview') return f.submit(body);
      if (path.endsWith('/validate')) return f.validate(body);
      if (path.endsWith('/cancel')) return f.cancel ? f.cancel() : { ok: true };
      throw new Error(path);
    },
  };
  f.controller = create({ api: f.api, capture: () => ({ ...view, context: { ...view.context } }), flush: () => f.flush(),
    later: fn => { const id = ++timerId; timers.set(id, fn); return id; }, stopTimer: id => timers.delete(id),
    requestId: () => 'test-request-123', clear: () => clears++, ready: (r, enabled) => adopted.push([r, enabled]),
    changed: state => changes.push(state) });
  f.fire = async () => { const [id, fn] = timers.entries().next().value || []; assert.ok(fn, 'Expected a scheduled poll'); timers.delete(id); fn(); await drain(); };
  f.complete = async () => { f.jobStatus = { status: 'done' }; await f.fire(); };
  f.clearCount = () => clears;
  return f;
}

test('waits for saves, captures their resulting revision, and suppresses duplicate starts', async () => {
  const f = fixture(), saving = deferred(); f.view.pending = 1; f.flush = () => saving.promise;
  const first = f.controller.start({ range: true }); assert.equal(await f.controller.start(), false);
  assert.equal(f.requests.length, 0); f.view.pending = 0; f.view.context.revision = 'saved-2'; saving.resolve(); await first;
  const body = f.requests[0][2]; assert.equal(body._context.revision, 'saved-2'); assert.equal(body.range, true);
  assert.equal(f.controller.state.phase, 'queued'); assert.equal(f.timers.size, 1);
});
test('failed saves and changed projects while saving do not submit a preview', async () => {
  for (const change of [f => f.view.error = 'save failed', f => f.view.context.project = 'other']) {
    const f = fixture(); f.flush = async () => change(f); assert.equal(await f.controller.start(), false);
    assert.equal(f.requests.length, 0); assert.equal(f.controller.state.phase, 'error');
  }
});
test('completed jobs are validated before playback, with half-open range and reverse fallback', async () => {
  const f = fixture(); await f.controller.start(); await f.complete();
  assert.equal(f.requests.at(-1)[1], `/api/render/preview/${JOB}/validate`);
  assert.ok(f.controller.playable(.25)); assert.ok(f.controller.playable(.749));
  assert.equal(f.controller.playable(.75), null); assert.equal(f.controller.playable(.5, -1), null);
  assert.match(f.controller.state.message, /reverse/); assert.ok(f.controller.playable(.5));
  assert.equal(f.controller.busy, false);
});
test('changing saved revision, sequence or local save revision releases playback', async () => {
  for (const change of [f => f.view.context.revision = 'new', f => f.view.sequence = 'other', f => f.view.revision++]) {
    const f = fixture(); await f.controller.start(); await f.complete(); const clears = f.clearCount(); change(f);
    assert.equal(f.controller.playable(.5), null); assert.equal(f.controller.current, null); assert.ok(f.clearCount() > clears);
  }
});
test('late submission after invalidation is cancelled and cannot attach to another edit', async () => {
  const f = fixture(), pending = deferred(); f.submit = () => pending.promise;
  const start = f.controller.start(); await drain(); f.controller.invalidate(); f.view.sequence = 'other';
  pending.resolve({ id: 'late' }); await start;
  assert.equal(f.adopted.length, 0); assert.ok(f.requests.some(r => r[1] === '/api/render/late/cancel'));
  assert.equal(f.controller.busy, false);
});
test('late status and validation responses cannot survive a project switch', async () => {
  for (const stage of ['status', 'validate']) {
    const f = fixture(), pending = deferred();
    if (stage === 'status') f.jobStatus = () => pending.promise;
    else { f.jobStatus = { status: 'done' }; f.validate = () => pending.promise; }
    const start = f.controller.start(); await drain(); f.view.context.project = 'other';
    pending.resolve(stage === 'status' ? { status: 'done' } : await fixture().validate()); await start;
    assert.equal(f.adopted.length, 0); assert.equal(f.controller.busy, false);
  }
});
test('unknown submission outcome retries the same request identity and immutable body', async () => {
  const f = fixture(); let calls = 0;
  f.submit = async () => { if (!calls++) throw new Error('network'); return { id: JOB }; };
  await f.controller.start({ range: true }); await f.fire();
  const submissions = f.requests.filter(r => r[1] === '/api/render/preview');
  assert.equal(submissions.length, 2); assert.deepEqual(submissions[0][2], submissions[1][2]);
  assert.equal(f.controller.state.phase, 'queued');
});
test('transient polling failures keep observing the same live job', async () => {
  const f = fixture(); let calls = 0; f.jobStatus = () => { if (!calls++) throw new Error('offline'); return { status: 'running' }; };
  await f.controller.start(); assert.equal(f.controller.state.phase, 'disconnected'); await f.fire();
  assert.equal(f.requests.filter(r => r[1] === '/api/render/preview').length, 1);
  assert.equal(f.requests.filter(r => r[0] === 'GET' && r[1] === `/api/render/${JOB}`).length, 2);
});
test('validation conflicts and missing jobs stop playback with a visible error', async () => {
  for (const code of [404, 409]) {
    const f = fixture(); f.jobStatus = { status: 'done' }; f.validate = async () => { throw Object.assign(new Error('changed'), { status: code }); };
    await f.controller.start(); assert.equal(f.controller.state.phase, 'error'); assert.equal(f.controller.busy, false);
    assert.equal(f.controller.playable(.5), null); assert.equal(f.timers.size, 0);
  }
});
test('cancel while saving never starts a render', async () => {
  const f = fixture(), saving = deferred(); f.flush = () => saving.promise;
  const start = f.controller.start(); f.controller.cancel(); saving.resolve(); await start;
  assert.equal(f.requests.length, 0); assert.equal(f.controller.busy, false);
});
test('cancel tracks cancelling status and a refused cancellation never activates the finished output', async () => {
  for (const done of [false, true]) {
    const f = fixture(); await f.controller.start(); f.controller.cancel(); await drain();
    f.jobStatus = { status: 'cancelling' }; await f.fire(); assert.equal(f.controller.busy, true);
    f.jobStatus = done ? { status: 'done' } : { status: 'error', error: 'cancelled' }; await f.fire();
    assert.equal(f.controller.busy, false); assert.equal(f.controller.playable(.5), null); assert.equal(f.adopted.length, 0);
  }
});
test('background completion offers playback without turning it on', async () => {
  const f = fixture(); await f.controller.start({ background: true }); await f.complete();
  assert.equal(f.adopted[0][1], false); assert.equal(f.controller.playable(.5), null);
  assert.equal(await f.controller.toggle(), true); assert.ok(f.controller.playable(.5));
});
test('a second toggle cancels an in-flight enable validation', async () => {
  const f = fixture(); await f.controller.start({ background: true }); await f.complete();
  const pending = deferred(), result = await f.validate(); f.validate = () => pending.promise;
  const toggle = f.controller.toggle(); assert.equal(await f.controller.toggle(), false);
  pending.resolve(result); assert.equal(await toggle, false); assert.equal(f.controller.enabled, false);
});
test('adoption rejects mismatched metadata, missing ranges and external output URLs', async () => {
  const f = fixture(), result = await f.validate();
  for (const value of [{ ...result, out: 'https://invalid.test/file.mp4' }, { ...result, preview: { ...result.preview, sequence: 'other' } },
    { ...result, preview: { ...result.preview, range: [0, Infinity] } }, { out: result.out }]) {
    assert.equal(f.controller.adopt(value, f.view, true), false);
  }
  assert.equal(f.adopted.length, 0);
});

test('only the canonical output for the returned job can be adopted', async () => {
  const f = fixture(), result = await f.validate();
  assert.equal(f.controller.adopt(result, f.view, true), true);
  assert.equal(f.controller.playable(.5).out, ownedOutput());
  const foreign = 'fedcba9876543210';
  for (const out of [
    ownedOutput(foreign), '/renders/preview_123.mp4',
    `/renders/job-${JOB}/../preview_${PREVIEW}.mp4`,
    `/renders/job-${JOB}/%2e%2e/preview_${PREVIEW}.mp4`,
    `/renders/job-${JOB}%2fpreview_${PREVIEW}.mp4`,
    `/renders/job-${JOB}/nested/preview_${PREVIEW}.mp4`,
    `/renders/job-${JOB}\\preview_${PREVIEW}.mp4`,
    ownedOutput() + '?token=test', ownedOutput() + '#fragment', ownedOutput() + '\n',
    `https://localhost${ownedOutput()}`, `//localhost${ownedOutput()}`,
    `/renders/job-${JOB}/preview_${PREVIEW}.mov`,
    `/renders/job-${JOB}/other_${PREVIEW}.mp4`,
    `/renders/job-${JOB}/preview_123.mp4`, ownedOutput().replace(JOB, JOB.toUpperCase()),
    null, 42, [ownedOutput()],
  ]) assert.equal(f.controller.adopt({ ...result, out }, f.view, true), false, JSON.stringify(out));
  for (const id of [foreign, '', null, 42, JOB.slice(1), JOB + '0', [JOB]])
    assert.equal(f.controller.adopt({ ...result, id }, f.view, true), false, JSON.stringify(id));
  assert.equal(f.adopted.length, 1);
  assert.equal(f.controller.current.id, JOB);
});

test('completed polling refuses a different valid job returned by validation', async () => {
  const f = fixture(), result = await f.validate(), foreign = 'fedcba9876543210';
  f.validate = async () => ({ ...result, id: foreign, out: ownedOutput(foreign) });
  await f.controller.start(); await f.complete();
  assert.equal(f.controller.state.phase, 'error');
  assert.equal(f.controller.playable(.5), null); assert.equal(f.adopted.length, 0);
  assert.equal(f.controller.busy, false);
});

test('toggle validation cannot switch to a different owned preview job', async () => {
  const f = fixture(), foreign = 'fedcba9876543210';
  await f.controller.start({ background: true }); await f.complete();
  const result = await f.validate();
  f.validate = async () => ({ ...result, id: foreign, out: ownedOutput(foreign) });
  assert.equal(await f.controller.toggle(), false);
  assert.equal(f.controller.playable(.5), null); assert.equal(f.controller.current, null);
  assert.equal(f.adopted.length, 1); assert.equal(f.controller.busy, false);
});

test('production full-sequence and In-Out entry points use the same controller', () => {
  const calls = [], controller = { start: options => calls.push(options || {}) };
  const scope = vm.createContext({ getRenderedPreview: () => controller, CR: { getRenderedPreview: () => controller } });
  vm.runInContext(core.match(/^function renderEntireSequence\(\).*$/m)[0] + '\n' + panels.match(/^function renderInOut\(\).*$/m)[0], scope);
  scope.renderEntireSequence(); scope.renderInOut();
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{}, { range: true }]);
});

test('production monitor owns preview media, seeks relative to In, sets rate, and handles playback rejection', async () => {
  const draws = [], paused = [], nodes = [], audioRoutes = [], audioStates = [], sweeps = [], multicamControls = []; let meterTicks = 0;
  const context = { setTransform() {}, fillRect() {}, drawImage: (...a) => draws.push(a) };
  const canvas = { width: 64, height: 64, getContext: () => context };
  let result = { out: ownedOutput(), preview: { range: [2, 4] } };
  const scope = vm.createContext({ S: { seq: { width: 64, height: 64, fps: 24 }, t: 2.5, playing: true, rate: 2 },
    window: {FilmocityTime: require('../frontend/timeline-time.js')},
    pv: { el: null }, $: () => canvas, fitCanvas() {}, updateMulticamControls: (...args) => multicamControls.push(args), routeRenderedAudio: element => audioRoutes.push(element), updateAudioPreviewStatus: state => audioStates.push(state), sweepVoices: used => sweeps.push(used.size), CR: {metersTick: () => meterTicks++}, pool: { source: { paused: false, pause: () => paused.push('source') } },
    getRenderedPreview: () => ({ playable: () => result, invalidate: () => { paused.push('invalidated'); result = null; } }),
    togglePlay: value => { scope.S.playing = value; paused.push('stopped'); },
    document: { body: { appendChild() {} }, createElement() {
      const video = { style: {}, src: '', readyState: 2, currentTime: 0, paused: true, play: () => Promise.resolve(),
        pause() { this.paused = true; paused.push('preview'); }, removeAttribute() {}, load() {}, remove() { paused.push('removed'); } };
      nodes.push(video); return video;
    } },
  });
  const a = core.indexOf('function renderProgram()'), b = core.indexOf('  const drawT =', a);
  const cleanup = core.indexOf('} finally {', b), end = core.indexOf('let exactFrames', cleanup);
  assert.ok(b > a && cleanup > b && end > cleanup);
  // Exercise the real rendered branch and its real finally cleanup; the
  // independent canvas/audio suite covers the live compositor below it.
  const seekStart = core.indexOf('function seekPreviewPicture('), seekEnd = core.indexOf('function activeClipsOf(', seekStart);
  vm.runInContext(core.slice(seekStart, seekEnd) + core.slice(a, b) + core.slice(cleanup, end), scope);
  scope.renderProgram(); assert.equal(nodes.length, 1); assert.equal(nodes[0].src, result.out);
  assert.equal(nodes[0].currentTime, .5); assert.equal(nodes[0].playbackRate, 2); assert.equal(draws.length, 1);
  assert.deepEqual(paused, ['source']); assert.equal(audioRoutes[0], nodes[0]); assert.deepEqual(audioStates, [true]); assert.deepEqual(sweeps, [0]); assert.equal(meterTicks, 1);
  assert.deepEqual(multicamControls, [[null, '']]); assert.equal(scope.S.mvGrid, null);
  scope.S.playing = false; nodes[0].paused = false; scope.renderProgram(); assert.ok(paused.includes('preview'));
  const decoded = draws.length; nodes[0].onseeked(); assert.equal(draws.length, decoded + 1);
  const staleSeeked = nodes[0].onseeked;
  nodes[0].play = () => Promise.reject(new Error('autoplay refused')); nodes[0].paused = true; scope.S.playing = true;
  scope.renderProgram(); await drain(); assert.ok(paused.includes('invalidated')); assert.ok(paused.includes('stopped'));
  const start = core.indexOf('function releasePreviewVideo()'), finish = core.indexOf('function getRenderedPreview()', start);
  vm.runInContext(core.slice(start, finish), scope); scope.releasePreviewVideo();
  assert.equal(scope.pv.el, null); assert.equal(audioRoutes.at(-1), null); assert.equal(nodes[0].onerror, null); assert.equal(nodes[0].onseeked, null); assert.ok(paused.includes('removed'));
  const retiredDraws = draws.length; staleSeeked(); assert.equal(draws.length, retiredDraws);
});

test('production edit and gesture entry points invalidate rendered playback before changing content', async () => {
  const { fixture: coreFixture, saved } = require('./helpers/gesture-fixture.cjs');
  const f = coreFixture(); let invalidations = 0;
  f.window.CR = { ...(f.window.CR || {}), invalidateRenderedPreview: () => invalidations++ };
  f.start('move'); assert.ok(invalidations > 0); f.escape();
  const before = invalidations;
  const saving = f.scope.applyOps([{ op: 'set', path: '/name', value: 'Edited' }], 'rename', 'rename');
  assert.ok(invalidations > before); f.requests[0].resolve(saved()); await saving;
});

test('production render bar rejects late context responses and never adopts an unbound filename', async () => {
  for (const stale of [false, true]) {
    const f = fixture(), response = deferred(), timers = [];
    const ruler = { appendChild() {} }, bar = { parentElement: ruler, innerHTML: '' };
    const scope = vm.createContext({ S: { prefs: { bg_render: false }, pps: 20, seq: { fps: 24 } },
      window: { FilmocityRenderedPreview: require('../frontend/rendered-preview.js'),
        FilmocityTimeline: require('../frontend/timeline-window.js'),
        FilmocityRenderStatus: { create: options => require('../frontend/render-status.js').create({ ...options,
          later: fn => { timers.push(fn); return timers.length; }, stopTimer() {} }) } },
      setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {},
      previewView: () => structuredClone(f.view), fetch: () => response.promise, readApiResponse: value => value,
      $: selector => selector === '#renderBar' ? bar : selector === '#tlBody' ? { scrollLeft: 0, clientWidth: 1200 } : { firstElementChild: ruler },
      getRenderedPreview: () => f.controller, fmtTC: value => String(value), document: {} });
    const start = core.indexOf('let idleTimer = null;'), end = core.indexOf('function clipEl(', start);
    assert.ok(end > start); vm.runInContext(core.slice(start, end), scope);
    scope.refreshRenderBar(); const pending = timers.at(-1)();
    const old = structuredClone(f.view);
    if (stale) f.view.context.project = 'other';
    response.resolve({ context: old.context, sequence: old.sequence, window: [0, 120], segments: [{ t0: 0, t1: 1, cached: true }], preview: '/renders/preview_s.mp4' });
    await pending;
    assert.equal(f.adopted.length, 0);
    if (stale) assert.equal(bar.innerHTML, ''); else assert.match(bar.innerHTML, /cached draft segment/);
  }
});

test('preview transport aborts a stalled response and retires its timer after success or failure', async () => {
  const { transport } = require('../frontend/rendered-preview.js');
  const timers = new Map(); let id = 0;
  const options = { later: fn => { timers.set(++id, fn); return id; }, stopTimer: key => timers.delete(key) };
  const api = transport((url, init) => new Promise((resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted')))), x => x, options);
  const pending = api.json('POST', '/api/render/preview', { request_id: 'stable-123' });
  timers.values().next().value(); await assert.rejects(pending, /timed out/); assert.equal(timers.size, 0);
  const success = transport(async (url, init) => ({ url, method: init.method, body: JSON.parse(init.body) }), x => x, options);
  assert.deepEqual(await success.json('POST', '/api/render/preview', { request_id: 'stable-123' }),
    { url: '/api/render/preview', method: 'POST', body: { request_id: 'stable-123' } });
  assert.equal(timers.size, 0);
});
