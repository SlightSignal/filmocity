const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, until, context, saved, read } = require('./helpers/source-replacement-fixture.cjs');

test('replacement preserves constant, reverse, ramp and held edit clocks and all live automation', () => {
  for (const patch of [{ speed: 2 }, { speed: 2, reverse: true }, { time_remap: [{ t: 0, v: .5 }, { t: 2, v: 2 }, { t: 4, v: 1 }], reverse: true }, { hold: true, speed: 3 }]) {
    const f = fixture(), c = f.tr.clips[0]; Object.assign(c, patch, { name: 'custom', label: 'violet', group: 'g', note: 'editor note', audio: { gain_db: -3, linked: false, fade_in: 1, fade_out: .7 }, keyframes: { 'audio.gain_db': [{ t: 0, v: -3 }, { t: 2, v: -6 }], 'audio.duck_db': [{ t: 0, v: -2 }, { t: 2, v: 0 }] }, fade_window: { version: 1, duration: 8, offset: 2 } });
    const before = plain(c), duration = f.scope.clipDur(c), result = f.plan(c, f.project.media.n, { sourceIn: 7 }).clip;
    assert.equal(f.scope.clipDur(result), duration); assert.deepEqual(plain(c), before);
    for (const key of ['name', 'label', 'group', 'note', 'audio', 'keyframes', 'fade_window', 'time_remap', 'reverse', 'hold']) assert.deepEqual(result[key], before[key]);
    assert.equal(result.in_, 7); assert.equal(result.out, 10);
    for (const t of [0, duration / 3, duration]) assert.equal(f.scope.sourceOffset(result, t), f.scope.sourceOffset(c, t));
  }
});

test('replace retains active editorial markers but clears foreign source restoration and render provenance', () => {
  const f = fixture(), c = f.tr.clips[0]; Object.assign(c, { markers: [{ t: -1 }, { t: 0, name: 'head' }, { t: 1, note: 'important' }, { t: 3 }, { t: 8 }], source_edit_window: { version: 1, ramp: { points: [] } }, rendered_from: { source: 'old' }, render_replace_task: 'task' });
  const noop = f.plan(c, f.project.media.m, { sourceIn: 5 }); assert.equal(noop.changed, false); assert.deepEqual(noop.clip, plain(c));
  const result = f.plan(); assert.equal(result.discardedMarkers, 2); assert.deepEqual(result.clip.markers, plain(c.markers.slice(1, 4)));
  for (const k of ['source_edit_window', 'rendered_from', 'render_replace_task']) assert.equal(k in result.clip, false);
});

test('source handles use source span and held frames use only the chosen picture', () => {
  const f = fixture(), c = f.tr.clips[0]; c.speed = 2; f.project.media.n.duration = 4;
  assert.throws(() => f.plan(c, f.project.media.n, { sourceIn: 2 }), /too short/);
  assert.throws(() => f.plan(c, f.project.media.n, { sourceOut: 2 }), /too short/);
  c.hold = true; const held = f.plan(c, f.project.media.n, { sourceIn: 3.9 }); assert.equal(held.clip.out, 6.9);
  assert.throws(() => f.plan(c, f.project.media.n, { sourceIn: 4 }), /held frame/);
  f.project.media.n.is_image = true; assert.equal(f.plan(c, f.project.media.n, { sourceIn: 100 }).clip.out, 103);
});

test('actual bin callback uses one canonical command, no optimistic edits, and reloads the acknowledged result', async () => {
  const f = fixture(), before = plain(f.project), promise = f.scope.replaceFromBin(); await until(() => f.requests.length === 1);
  assert.equal(f.requests[0].url, '/api/clip/replace-source'); assert.equal(f.requests[0].options.method, 'POST');
  assert.deepEqual(f.body(), { _context: context(), actor: 'human', sequence: 's1', clip_id: 'a', media_id: 'n', in: 0 }); assert.deepEqual(plain(f.project), before);
  const after = plain(before); after.sequences[0].tracks[0].clips[0] = f.plan().clip;
  f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2); f.requests[1].resolve(read(after, 'r1')); await promise;
  assert.equal(f.scope.S.proj.sequences[0].tracks[0].clips[0].media_id, 'n'); assert.equal(f.scope.S.commandPending, false); assert.match(f.messages.at(-1), /Review markers/);
});

test('Source replacement captures logical interpreted/subclip time and respects explicit IO', async () => {
  for (const marked of [false, true]) {
    const f = fixture(), m = f.project.media.n; Object.assign(m, { frame_rate: '60000/1001', fps: 59.94, interpret_fps: '30000/1001', subclip_of: 'm', sub_in: 6 });
    f.scope.$('#srcVideo').currentTime = 3.5; if (marked) { f.scope.S.srcIn = 4; f.scope.S.srcOut = 10; }
    const promise = f.scope.replaceWithSource(); await until(() => f.requests.length === 1); assert.equal(f.body().in, marked ? 4 : 1); assert.equal(f.body().out, marked ? 10 : undefined);
    f.reply(0, { detail: 'test rejection' }, 422); await promise;
  }
});

test('queued save advances revision; ongoing Source playback does not redirect captured range', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'save first' } }], 'note', 'note'); f.scope.$('#srcVideo').currentTime = 2;
  const promise = f.scope.replaceWithSource(); f.scope.$('#srcVideo').currentTime = 8; f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2);
  assert.equal(f.body(1)._context.revision, 'r1'); assert.equal(f.body(1).in, 2); f.reply(1, { detail: 'rejected' }, 422); await promise;
});

test('queued replacement refuses changes to source, target, selection, marks or current ownership', async () => {
  const changes = [f => f.tr.clips[0].note = 'changed', f => f.project.media.n.path = '/different.mov', f => f.scope.S.sel.clear(), f => f.scope.S.srcIn = 1, f => f.scope.S.src = plain(f.project.media.n), f => { f.scope.S.proj = plain(f.project); f.scope.S.seq = f.scope.S.proj.sequences[0]; }, f => f.scope.S.context = context('r0', 'other'), f => f.scope.S.seq = f.project.sequences[1]];
  for (const change of changes) {
    const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note'); const promise = f.scope.replaceWithSource(); change(f); f.requests[0].resolve(saved('r1')); await promise;
    assert.equal(f.requests.length, 1); assert.match(f.messages.at(-1), /changed/);
  }
});

test('locked, malformed, detached, stale, wrong stream, active gesture and Recovery targets reject before requests', async () => {
  const changes = [f => f.tr.locked = true, f => f.tr.clips[0].time_remap = [{ t: 0, v: -1 }], f => f.tr.clips[0].audio_detached_id = 'x', f => f.project.sequences[1].tracks[0].clips[0].unlinked_from = 'a', f => f.scope.S.src = plain(f.project.media.n), f => f.project.media.n.has_video = false, f => f.scope.S.gesture = {}, f => f.scope.projectSaveState().error = 'uncertain', f => f.scope.S.sel.add('b'), f => f.scope.S.srcOut = 2];
  for (const change of changes) { const f = fixture(); change(f); const before = plain(f.project); assert.equal(await f.scope.replaceWithSource(), false); assert.equal(f.requests.length, 0); assert.deepEqual(plain(f.project), before); }
});

test('same source range is a no-op; same-source stabilization remains valid but foreign-source analysis refuses', async () => {
  const f = fixture(); f.scope.S.src = f.project.media.m; f.scope.S.srcIn = 5; f.tr.clips[0].fx_stack = [{ type: 'stabilize', enabled: true }];
  assert.equal(await f.scope.replaceWithSource(), false); assert.equal(f.requests.length, 0);
  assert.equal(f.plan(f.tr.clips[0], f.project.media.m, { sourceIn: 1 }).changed, true); assert.throws(() => f.plan(), /stabilization/);
});

test('lost replacement response enters Recovery without mutation or automatic replay', async () => {
  const f = fixture(), before = plain(f.project), promise = f.scope.replaceFromBin(); await until(() => f.requests.length === 1); f.requests[0].reject(Error('lost reply')); assert.equal(await promise, false);
  assert.match(f.scope.projectSaveState().error, /not confirmed/); assert.match(f.messages.at(-1), /Recovery/); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 1);
  assert.equal(await f.scope.replaceFromBin(), false); assert.equal(f.requests.length, 1);
});

test('replacement pauses Program trim playback without committing an unrelated edit', async () => {
  const f = fixture(), calls = []; f.scope.S.playing = true; f.scope.togglePlay = (...args) => { calls.push(plain(args)); f.scope.S.playing = args[0]; };
  const promise = f.scope.replaceFromBin(); await until(() => f.requests.length === 1); assert.deepEqual(calls, [[false, { commitTrim: false }]]); f.reply(0, { detail: 'rejected' }, 422); await promise;
});
