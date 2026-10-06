const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, until, response } = require('./helpers/keyboard-trim-fixture.cjs');
const time = require('../frontend/timeline-time.js'), audio = require('../frontend/audio-preview.js');
const clip = extra => ({ id: 'a', media_id: 'm', start: 0, in_: 5, out: 9, speed: 1, ...extra });
const near = (a, b, epsilon = 1e-9) => assert.ok(Math.abs(a - b) < epsilon, `${a} != ${b}`);
const sourceAt = (f, c, t) => c.hold ? c.in_ : c.reverse ? c.out - f.scope.cutClock.sourceOffset(c, t) : c.in_ + f.scope.cutClock.sourceOffset(c, t);
function rejection(f, callback, match) { const before = plain(f.project); callback(); assert.equal(f.requests.length, 0); assert.deepEqual(plain(f.project), before); if (match) assert.match(f.messages.at(-1), match); }

test('keyboard head trims preserve advanced source clocks, duration and complete payloads in one saved batch', async () => {
  for (const extra of [{}, { reverse: true }, { speed: 2 }, { hold: true }, { time_remap: [{ t: 0, v: .5 }, { t: 3, v: 2 }] }, { reverse: true, time_remap: [{ t: 0, v: 1 }, { t: 4, v: 2 }] }]) {
    const f = fixture(), original = clip({ ...extra, vendor: { keep: 42 } }); f.edit(original); const duration = f.scope.cutClock.duration(original);
    f.scope.trimEditPoint(15, false); await until(() => f.requests.length === 1);
    const changed = f.tr.clips[0]; near(changed.start, .5); near(f.scope.cutClock.duration(changed), duration - .5);
    for (const t of [0, .1, Math.min(.8, duration - .6)]) near(sourceAt(f, changed, t), sourceAt(f, original, t + .5));
    assert.equal(f.body().ops.length, 1); assert.deepEqual(f.body().ops[0].clip.vendor, { keep: 42 }); assert.ok(f.body()._context); await f.save();
  }
});
test('head trim shifts automation and markers while inherited audio fades retain their original clock', async () => {
  const f = fixture(), original = clip({ audio: { fade_in: 3, fade_out: 2, gain_db: -6 }, keyframes: { 'transform.x': [{ t: 0, v: 0, e: 'bezier', o: [.2, 3] }, { t: 4, v: 20, i: [.3, -2] }], 'audio.duck_db': [{ t: 0, v: 0 }, { t: 4, v: -12 }] }, markers: [{ t: .25, name: 'removed' }, { t: 2, name: 'kept' }] });
  f.edit(original); f.scope.trimEditPoint(30, false); await until(() => f.requests.length === 1); const changed = f.tr.clips[0];
  for (const t of [0, .5, 1, 2.9]) { near(f.scope.evalCurve(changed.keyframes['transform.x'], t), f.scope.evalCurve(original.keyframes['transform.x'], t + 1), 1e-7); near(audio.duckDb(changed, t), audio.duckDb(original, t + 1)); near(audio.fadeGain(changed, 3, t), audio.fadeGain(original, 4, t + 1)); }
  assert.deepEqual(plain(changed.markers), [{ t: -.75, name: 'removed' }, { t: 1, name: 'kept' }]); await f.save();
});
test('fractional-rate keyboard edges remain exact across repeated frame trims', async () => {
  for (const fps of [23.976, 29.97, 59.94, '30000/1001']) {
    const f = fixture(), start = time.fromFrames(100000, fps); f.edit(clip({ start, in_: 0, out: 10 }), 'l', fps);
    for (let i = 0; i < 5; i++) { f.scope.trimEditPoint(1, false); await f.save(i); assert.equal(f.tr.clips[0].start, time.fromFrames(100001 + i, fps)); }
  }
});
test('regular trim stops at source handles, adjacent clips, sequence zero and minimum duration', () => {
  for (const setup of [
    f => { f.edit(clip({ in_: 0, out: 4 }), 'l'); return [-1, /source|handle|range|sequence/i]; },
    f => { f.edit(clip({ start: 1 }), 'l'); return [-31, /sequence start/]; },
    f => { f.edit(clip(), 'r'); f.project.media.m.duration = 9; return [1, /source|handle|range/i]; },
    f => { f.edit(clip(), 'r'); f.tr.clips.push(clip({ id: 'b', start: 4 })); return [1, /neighbor/]; },
    f => { f.edit(clip(), 'l'); return [120, /one video frame|sample/]; }
  ]) { const f = fixture(), [amount, message] = setup(f); rejection(f, () => f.scope.trimEditPoint(amount, false), message); }
});
test('regular head extension uses earlier source and preserves visible automation positions', async () => {
  const f = fixture(), original = clip({ start: 2, keyframes: { 'transform.x': [{ t: 0, v: 0 }, { t: 4, v: 10 }] } });
  f.edit(original); f.scope.trimEditPoint(-30, false); await until(() => f.requests.length === 1); const changed = f.tr.clips[0];
  assert.equal(changed.start, 1); assert.equal(changed.in_, 4); near(f.scope.cutClock.duration(changed), 5);
  near(f.scope.evalCurve(changed.keyframes['transform.x'], 2), f.scope.evalCurve(original.keyframes['transform.x'], 1)); await f.save();
});
test('ripple head/tail trims move only downstream clips on their track by the actual duration change', async () => {
  for (const side of ['l', 'r']) {
    const f = fixture(), original = clip({ reverse: true, time_remap: [{ t: 0, v: 1 }, { t: 4, v: 2 }], out: 11 }); f.edit(original, side);
    f.tr.clips.push(clip({ id: 'b', start: 4 }), clip({ id: 'c', start: 9 }));
    f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [clip({ id: 'd', start: 4 })] });
    f.scope.trimEditPoint(side === 'l' ? 30 : -30, true); await until(() => f.requests.length === 1);
    assert.equal(f.tr.clips[0].start, 0); near(f.scope.cutClock.duration(f.tr.clips[0]), 3); near(f.tr.clips[1].start, 3); near(f.tr.clips[2].start, 8); assert.equal(f.seq.tracks[1].clips[0].start, 4);
    assert.equal(f.body().ops.length, 3); await f.save();
  }
});
test('roll plans both advanced clips and preserves the outer span in one batch', async () => {
  for (const side of ['l', 'r']) {
    const f = fixture(), left = clip(), right = clip({ id: 'b', start: 4, in_: 10, out: 14, reverse: true, time_remap: [{ t: 0, v: .5 }, { t: 4, v: 1.5 }] });
    f.edit(side === 'r' ? left : right, side); f.tr.clips = [plain(left), plain(right)]; f.scope.S.trimType = 'roll'; f.scope.trimByType(30); await until(() => f.requests.length === 1);
    assert.equal(f.body().ops.length, 2); near(f.scope.cutClock.duration(f.tr.clips[0]), 5); assert.equal(f.tr.clips[1].start, 5); near(f.scope.cutClock.duration(f.tr.clips[1]), 3);
    for (const t of [0, .5, 2.9]) near(sourceAt(f, f.tr.clips[1], t), sourceAt(f, right, t + 1)); await f.save();
  }
});
test('roll refuses gaps, missing neighbors and either missing source handle atomically', () => {
  for (const mode of ['gap', 'missing', 'left-limit', 'right-limit']) {
    const f = fixture(); f.edit(clip(), 'r'); f.scope.S.trimType = 'roll';
    if (mode !== 'missing') f.tr.clips.push(clip({ id: 'b', start: mode === 'gap' ? 5 : 4, in_: 0, out: 4 }));
    if (mode === 'left-limit') f.project.media.m.duration = 9;
    rejection(f, () => f.scope.trimByType(mode === 'right-limit' ? -30 : 30), mode === 'gap' || mode === 'missing' ? /adjoining/ : /source|handle|range/i);
  }
});
test('locked edit tracks and in-progress gestures cannot receive keyboard trim variants', () => {
  for (const command of [f => f.scope.trimEditPoint(1, false), f => f.scope.trimByType(1), f => f.scope.trimToPlayhead(), f => f.scope.extendEdit(), f => f.scope.rippleTrimToPlayhead('head')]) {
    const f = fixture(); f.edit(clip()); f.tr.locked = true; f.scope.S.t = 1; rejection(f, () => command(f), /Unlock/);
    const other = fixture(); other.edit(clip()); other.scope.S.gesture = {}; rejection(other, () => command(other), /Finish the drag/);
  }
});
test('trim-to-playhead and Extend use exact target frames and honor the selected edge', async () => {
  const f = fixture(), fps = 29.97; f.edit(clip({ start: time.fromFrames(5, fps) }), 'r', fps); f.scope.S.trimType = 'regular';
  f.scope.S.t = time.fromFrames(100.3, fps); f.scope.trimToPlayhead(); await f.save(); near(f.scope.S.t, time.fromFrames(100, fps)); near(f.tr.clips[0].start + f.scope.cutClock.duration(f.tr.clips[0]), time.fromFrames(100, fps));
  f.scope.S.t = time.fromFrames(70, fps); f.scope.extendEdit(); await f.save(1); near(f.tr.clips[0].start + f.scope.cutClock.duration(f.tr.clips[0]), time.fromFrames(70, fps)); assert.equal(f.tr.clips[0].start, time.fromFrames(5, fps));
});
test('audio trim-to-playhead preserves sample/subframe timing while keyboard increments remain sequence frames', async () => {
  const f = fixture(); f.edit(clip(), 'l'); f.tr.kind = 'audio'; f.scope.S.trimType = 'regular'; f.scope.S.t = 1 / 48000;
  f.scope.trimToPlayhead(); await f.save(); near(f.tr.clips[0].start, 1 / 48000);
  f.scope.trimEditPoint(1, false); await f.save(1); near(f.tr.clips[0].start, 1 / 48000 + 1 / 30);
});
test('ripple-to-playhead handles reverse ramps and moves the playhead to the surviving edit', async () => {
  for (const which of ['head', 'tail']) {
    const f = fixture(); f.edit(clip({ reverse: true, time_remap: [{ t: 0, v: 1 }, { t: 4, v: 2 }], out: 11 })); f.tr.clips.push(clip({ id: 'b', start: 4 }));
    f.scope.S.t = 1.001; f.scope.rippleTrimToPlayhead(which); await f.save();
    near(f.scope.cutClock.duration(f.tr.clips[0]), which === 'head' ? 3 : 1); near(f.tr.clips[1].start, which === 'head' ? 3 : 1); assert.equal(f.scope.S.t, which === 'head' ? 0 : 1);
  }
});
test('failed keyboard trim saves retain one recoverable draft and refuse another edit', async () => {
  const f = fixture(); f.edit(clip()); f.scope.trimEditPoint(30, false); await until(() => f.requests.length === 1);
  f.requests[0].resolve(response(409, { detail: 'Project changed' })); await until(() => !!f.scope.projectSaveState().error);
  const draft = plain(f.project); assert.ok(f.storage.size > 0); f.scope.trimEditPoint(30, false); assert.equal(f.requests.length, 1); assert.deepEqual(plain(f.project), draft); assert.match(f.messages.at(-1), /Recovery/);
});
test('group nudges preserve spacing at zero, exact picture frames and original overwrite priority', async () => {
  const f = fixture(); f.edit(clip({ start: 1 / 30 })); f.tr.clips.push(clip({ id: 'b', start: 1 }), clip({ id: 'stationary', start: 10 })); f.scope.S.sel = new Set(['b', 'a']);
  f.scope.nudge(-5); await until(() => f.requests.length === 1); assert.deepEqual(f.body().ops.map(o => o.op), ['remove_clip', 'remove_clip', 'set_clip', 'set_clip']); assert.deepEqual(f.body().ops.slice(2).map(o => o.clip.id), ['a', 'b']);
  assert.deepEqual(f.tr.clips.map(c => c.id), ['stationary', 'a', 'b']); assert.equal(f.tr.clips[1].start, 0); near(f.tr.clips[2].start, 1 - 1 / 30); await f.save();
  f.scope.nudge(-1); assert.equal(f.requests.length, 1);
});
test('mixed video/audio nudges preserve intentional offsets and stop before either crosses zero', async () => {
  const f = fixture(); f.edit(clip({ start: .04 })); f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [clip({ id: 'audio', start: .001 })] }); f.scope.S.sel.add('audio');
  const offset = .04 - .001; f.scope.nudge(-5); assert.equal(f.requests.length, 0); assert.equal(f.tr.clips[0].start, .04);
  f.scope.nudge(1); await f.save(); near(f.tr.clips[0].start - f.seq.tracks[1].clips[0].start, offset); near(f.tr.clips[0].start, 2 / 30);
});
test('nudges reject locked groups, nonintegral counts, empty selections and zero movement without partial edits', () => {
  const f = fixture(); f.edit(clip({ start: 1 })); f.seq.tracks.push({ id: 'a1', kind: 'audio', locked: true, clips: [clip({ id: 'audio', start: 2 })] }); f.scope.S.sel.add('audio');
  rejection(f, () => f.scope.nudge(1), /Unlock every selected track/);
  for (const n of [0, .5, NaN, Infinity]) rejection(f, () => f.scope.nudge(n));
  f.scope.S.sel.clear(); rejection(f, () => f.scope.nudge(1));
});
test('stills and hold clips may extend without inventing a new frozen source frame', async () => {
  for (const still of [true, false]) { const f = fixture(); const original = clip(still ? {} : { hold: true, reverse: true }); f.edit(original, 'r'); f.project.media.m = { duration: 9, is_image: still }; f.scope.trimEditPoint(30, false); await f.save(); assert.equal(f.tr.clips[0].in_, 5); near(f.scope.cutClock.duration(f.tr.clips[0]), 5); }
});
test('nested sequence trim handles stop at the actual nested content duration', () => {
  const f = fixture(); f.edit(clip({ media_id: null, sequence_id: 'nested', in_: 0, out: 4 }), 'r'); f.project.sequences.push({ id: 'nested', tracks: [{ id: 'n', clips: [clip({ in_: 0, out: 4 })] }] });
  rejection(f, () => f.scope.trimEditPoint(1, false), /source|handle|range/i);
});
test('rapid repeated trim commands queue captured payloads with sequential saved-project contexts', async () => {
  const f = fixture(); f.edit(clip()); f.scope.trimEditPoint(1, false); f.scope.trimEditPoint(1, false);
  await until(() => f.requests.length === 1); assert.equal(f.body()._context.revision, 'r0'); near(f.body().ops[0].clip.start, 1 / 30); near(f.tr.clips[0].start, 2 / 30);
  f.requests[0].resolve(require('./helpers/keyboard-trim-fixture.cjs').saved('r1')); await until(() => f.requests.length === 2);
  assert.equal(f.body(1)._context.revision, 'r1'); near(f.body(1).ops[0].clip.start, 2 / 30); await f.save(1);
});
test('roll failure in the second clip discards the already planned first payload', () => {
  const f = fixture(); f.edit(clip(), 'r'); f.tr.clips.push(clip({ id: 'b', start: 4, transition_in: { type: 'dissolve', duration: 2 } })); f.scope.S.trimType = 'roll';
  rejection(f, () => f.scope.trimByType(30), /picture transition/);
});
test('no selected edit, zero frame commands and playhead on the same boundary create no history entries', () => {
  const f = fixture(); f.edit(clip()); f.scope.S.t = 0;
  for (const call of [() => f.scope.trimEditPoint(0, false), () => f.scope.trimEditPoint(.5, false), () => f.scope.trimToPlayhead()]) rejection(f, call);
  f.scope.S.editPoint = null; rejection(f, () => f.scope.trimByType(1), /Select an edit point/);
});
