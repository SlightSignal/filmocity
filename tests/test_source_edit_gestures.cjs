// Production pointer gestures, source clock, edit planner, evaluator and save queue.
// DOM/network adapters are controlled; native WebView playback is separate acceptance.
const assert = require('node:assert/strict'), test = require('node:test');
const { fixture, plain, saved } = require('./helpers/gesture-fixture.cjs');
const audio = require('../frontend/audio-preview.js');
const near = (a, b, note = '') => assert.ok(Math.abs(a - b) < 1e-7, `${note}: ${a} != ${b}`);
const ramp = [{ t: 0, v: 0.5 }, { t: 2, v: 2 }, { t: 4, v: 1, e: 'hold' }];
function setup(patch = {}) {
  const app = fixture(), c = app.clips[1]; Object.assign(c, { in_: 3, out: 9 }, plain(patch));
  app.clips[2].start = c.start + app.scope.clipDur(c); app.scope.S.snap = false;
  return { app, c };
}
function source(app, c, time) { return c.hold ? c.in_ : c.reverse ? c.out - app.scope.sourceOffset(c, time) : c.in_ + app.scope.sourceOffset(c, time); }
function move(app, delta) { app.move({ clientX: delta * app.scope.S.pps }); }
function assertSources(app, before, after, oldOffset = 0) {
  const duration = app.scope.clipDur(after);
  for (let i = 0; i < 31; i++) { const t = duration * i / 31; near(source(app, after, t), source(app, before, t + oldOffset), 'source frame'); }
}

test('head and tail gestures retain the source clock for reverse, hold, empty and nonlinear ramps', () => {
  for (const patch of [{ reverse: true }, { hold: true }, { time_remap: [] }, { time_remap: ramp }, { time_remap: ramp, reverse: true }]) for (const side of ['l', 'r']) {
    const { app, c } = setup(patch), before = plain(c), duration = app.scope.clipDur(c);
    app.start(side, 1); move(app, side === 'l' ? 1 : -1); app.up();
    near(app.scope.clipDur(c), duration - 1); assertSources(app, before, c, side === 'l' ? 1 : 0);
    assert.equal(app.requests.length, 1); assert.equal(app.body().ops[0].clip.media_id, 'm');
  }
});

test('head trimming preserves linear, hold, ease and Bezier automation and original metadata', () => {
  for (const easing of ['linear', 'hold', 'ease', 'ease_in', 'ease_out', 'bezier']) {
    const points = [{ t: 0, v: -12, e: easing, o: [0.3, 4], vendor: 'retain' }, { t: 5, v: 3, i: [0.1, -2] }];
    const { app, c } = setup({ keyframes: { 'audio.gain_db': points, 'transform.x': points }, note: 'original' }), before = plain(c);
    app.start('l', 1); move(app, 1); app.up();
    for (let t = 0; t < 4.9; t += 0.1) near(app.scope.evaluateKeyframes(c.keyframes['audio.gain_db'], t), app.scope.evaluateKeyframes(before.keyframes['audio.gain_db'], t + 1), easing);
    const payload = app.body().ops[0].clip;
    assert.deepEqual(payload.keyframes, plain(c.keyframes)); assert.equal(payload.note, 'original'); assert.equal(c.keyframes['audio.gain_db'][0].vendor, 'retain');
  }
});

test('inherited fade and ducking clocks remain continuous during a head trim', () => {
  const { app, c } = setup({ audio: { fade_in: 3, fade_out: 3, fade_window: { duration: 9, offset: 1, settings: [3, 3, true, '', 0, '', 0] } }, keyframes: { 'audio.duck_db': [{ t: 0, v: 0 }, { t: 2, v: -18 }, { t: 5, v: -3 }] } });
  const before = plain(c), duration = app.scope.clipDur(c); app.start('l', 1); move(app, 1); app.up();
  for (let t = 0; t < duration - 1; t += 0.1) { near(audio.fadeGain(c, duration - 1, t), audio.fadeGain(before, duration, t + 1)); near(audio.duckDb(c, t), audio.duckDb(before, t + 1)); }
  assert.ok(c.keyframes['audio.duck_db'].every(point => point.t >= 0)); assert.equal(app.body().ops[0].clip.audio.fade_window.offset, 2);
});

test('trimmed ramp knots and source markers recover when a subsequent gesture extends the head again', async () => {
  const { app, c } = setup({ time_remap: ramp, markers: [{ t: 0.5, name: 'restore' }, { t: 2, name: 'keep' }], keyframes: { 'audio.duck_db': [{ t: 0, v: 0 }, { t: 2, v: -18 }, { t: 5, v: 0 }] } });
  const before = plain(c); app.start('l', 1); move(app, 1); app.up();
  assert.ok(c.markers.find(marker => marker.name === 'restore').t < 0);
  app.requests[0].resolve(saved()); await app.scope.flushSaves();
  app.start('l', 1); move(app, -1); app.up();
  near(c.start, before.start); near(app.scope.clipDur(c), app.scope.clipDur(before)); assertSources(app, before, c);
  assert.deepEqual(plain(c.markers), before.markers);
  for (let t = 0; t < app.scope.clipDur(c); t += 0.1) near(audio.duckDb(c, t), audio.duckDb(before, t));
});

test('slip moves ramped forward and reverse source while keeping local automation and fade timing', () => {
  for (const reverse of [false, true]) {
    const { app, c } = setup({ reverse, time_remap: ramp, markers: [{ t: 2, name: 'source mark' }], audio: { fade_in: 1, fade_out: 1 }, keyframes: { 'audio.gain_db': [{ t: 0, v: -3 }, { t: 5, v: 2 }] } });
    const before = plain(c), duration = app.scope.clipDur(c), travel = app.scope.sourceOffset(before, 0.5);
    app.scope.S.tool = 'slip'; app.start(null, 1); move(app, 0.5); app.up();
    near(app.scope.clipDur(c), duration); near(c.start, before.start); assert.deepEqual(plain(c.time_remap), before.time_remap); assert.deepEqual(plain(c.keyframes), before.keyframes);
    for (let t = 0; t < duration; t += 0.1) { near(source(app, c, t) - source(app, before, t), reverse ? -travel : travel); near(audio.fadeGain(c, duration, t), audio.fadeGain(before, duration, t)); }
    near(source(app, c, c.markers[0].t), source(app, before, 2), 'source marker');
    assert.deepEqual(app.body().ops[0].clip.markers, plain(c.markers));
  }
});

test('held source slips change the frozen frame without changing its held duration', () => {
  const { app, c } = setup({ hold: true }); const before = plain(c); app.scope.S.tool = 'slip';
  app.start(null, 1); move(app, 0.5); app.up();
  near(c.in_, before.in_ + 0.5); near(app.scope.clipDur(c), app.scope.clipDur(before)); near(c.start, before.start);
});

test('reverse trims constrain the correct source handle and still keep a picture frame boundary', () => {
  const { app, c } = setup({ reverse: true, in_: 0.05, out: 3.05 }); app.project.media.m.duration = 3.1; app.clips[0].out -= 1;
  app.start('l', 1); move(app, -10); app.up();
  near(c.start, 3 - 1 / 30); near(c.out, 3.05 + 1 / 30); assert.ok(c.out <= 3.1);
});

test('roll plans both remapped neighbors as one operation group and preserves their surviving source clocks', () => {
  const { app, c } = setup({ reverse: true, time_remap: ramp }); app.clips[0].reverse = true;
  const leftBefore = plain(app.clips[0]), rightBefore = plain(c), end = c.start + app.scope.clipDur(c);
  app.scope.S.tool = 'roll'; app.start('l', 1); move(app, 0.5); app.up();
  near(app.scope.clipEnd(app.clips[0]), c.start); near(app.scope.clipEnd(c), end); assertSources(app, rightBefore, c, 0.5);
  for (let t = 0; t < 3; t += 0.1) near(source(app, app.clips[0], t), source(app, leftBefore, t));
  assert.equal(app.body().ops.length, 2); assert.equal(app.body().ops.every(op => !!op.clip.media_id), true);
});

test('slide preserves the selected reversed clip while rebasing its neighbors automation', () => {
  const { app, c } = setup({ reverse: true }); app.clips[2].keyframes = { 'transform.x': [{ t: 0, v: 0 }, { t: 3, v: 100 }] };
  const before = plain(c), next = plain(app.clips[2]); app.scope.S.tool = 'slide'; app.start(null, 1); move(app, 0.5); app.up();
  near(c.start, before.start + 0.5); assertSources(app, before, c); near(app.scope.clipEnd(app.clips[0]), c.start); near(app.scope.clipEnd(c), app.clips[2].start);
  for (let t = 0; t < 2.4; t += 0.1) near(app.scope.evaluateKeyframes(app.clips[2].keyframes['transform.x'], t), app.scope.evaluateKeyframes(next.keyframes['transform.x'], t + 0.5));
  assert.equal(app.body().ops.length, 3);
});

test('a later invalid neighbor plan clears prior preview and cannot leave partial edits or a save', () => {
  const { app, c } = setup(); app.clips[0].transition_out = { type: 'dissolve', duration: 2 };
  const before = plain(app.project); app.scope.S.tool = 'roll'; app.start('l', 1);
  move(app, -2); assert.notEqual(c.start, before.sequences[0].tracks[0].clips[1].start);
  move(app, 0.5); assert.deepEqual(plain(app.project), before); app.up();
  assert.equal(app.requests.length, 0); assert.match(app.messages.at(-1), /transition/i);
});

test('Escape and lost focus restore field absence as well as complete ramp and automation values', () => {
  for (const cancel of ['escape', 'blur']) {
    const { app, c } = setup({ time_remap: ramp, keyframes: { 'transform.scale': [{ t: 0, v: 1 }, { t: 5, v: 2 }] }, markers: [{ t: 0.5, name: 'hidden by trim' }], audio: { fade_in: 2 } });
    const before = plain(app.project); app.start('l', 1); move(app, 1);
    assert.equal(Object.hasOwn(c, 'source_edit_window'), true);
    if (cancel === 'escape') app.escape(); else app.window.emit('blur');
    assert.deepEqual(plain(app.project), before); assert.equal(Object.hasOwn(c, 'source_edit_window'), false); assert.equal(app.requests.length, 0);
  }
});

test('returning an advanced trim to zero leaves no new metadata and no queued save', () => {
  const { app } = setup({ time_remap: ramp, audio: { fade_in: 2 } }), before = plain(app.project);
  app.start('l', 1); move(app, 1); move(app, 0); app.up();
  assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0);
});

test('roll and slide refuse separated neighbors instead of resizing material across a gap', () => {
  for (const tool of ['roll', 'slide']) {
    const { app } = setup(); app.clips[0].out -= 0.5; const before = plain(app.project);
    app.scope.S.tool = tool; app.start(tool === 'roll' ? 'l' : null, 1); move(app, 0.5); app.up();
    assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0); assert.match(app.messages.at(-1), /sharing|adjoining/);
  }
});


test('regular trims stop at their neighbor and never resize or overwrite it', () => {
  for (const side of ['l', 'r']) for (const gap of [0, 0.5]) {
    const { app, c } = setup(); if (side === 'l') app.clips[0].out -= gap; else app.clips[2].start += gap;
    const neighbors = [plain(app.clips[0]), plain(app.clips[2])], start = c.start, end = app.scope.clipEnd(c);
    app.start(side, 1); move(app, side === 'l' ? -1 : 1); app.up();
    near(side === 'l' ? c.start : app.scope.clipEnd(c), side === 'l' ? start - gap : end + gap);
    assert.deepEqual([plain(app.clips[0]), plain(app.clips[2])], neighbors); assert.equal(app.requests.length, gap === 0 ? 0 : 1);
  }
});

test('a ramped ripple head rebases automation and shifts only following clips on its own track', () => {
  const { app, c } = setup({ time_remap: ramp, keyframes: { 'transform.x': [{ t: 0, v: 0 }, { t: 5, v: 50 }] } });
  app.seq.tracks.push({ id: 'v2', kind: 'video', clips: [{ id: 'untouched', start: 9, in_: 0, out: 3 }] });
  const before = plain(c), nextStart = app.clips[2].start, other = plain(app.seq.tracks[1]);
  app.scope.S.tool = 'ripple'; app.start('l', 1); move(app, 1); app.up();
  near(c.start, before.start); near(app.clips[2].start, nextStart - 1); assertSources(app, before, c, 1); assert.deepEqual(plain(app.seq.tracks[1]), other);
  near(app.scope.evaluateKeyframes(c.keyframes['transform.x'], 0.5), app.scope.evaluateKeyframes(before.keyframes['transform.x'], 1.5));
  assert.equal(app.body().ops.length, 2);
});

test('a slide clamps by a nonlinear neighbor source integral rather than its scalar speed', () => {
  const { app, c } = setup(), previous = app.clips[0];
  Object.assign(previous, { media_id: 'ramped', in_: 0.1, out: 5.1, time_remap: [{ t: 0, v: 1 }, { t: 2, v: 2 }] });
  app.project.media.ramped = { duration: 5.18 }; const before = plain(previous);
  app.scope.S.tool = 'slide'; app.start(null, 1); move(app, 10); app.up();
  near(c.start, 3 + 1 / 30); near(previous.out, 5.1 + 2 / 30); assert.ok(previous.out <= 5.18);
  for (let t = 0; t < 3; t += 0.1) near(source(app, previous, t), source(app, before, t));
});

test('a roll clamps against the upper source handle of its reversed next clip', () => {
  const { app, c } = setup(), next = app.clips[2]; next.reverse = true; next.media_id = 'reverse'; app.project.media.reverse = { duration: 8.05 };
  const before = plain(c), cut = app.scope.clipEnd(c); app.scope.S.tool = 'roll'; app.start('r', 1); move(app, -10); app.up();
  near(app.scope.clipEnd(c), cut - 1 / 30); near(next.start, cut - 1 / 30); near(next.out, 8 + 1 / 30); assert.ok(next.out <= 8.05); assertSources(app, before, c);
});

test('changing a cross-track move into an Alt slip restores track ownership before saving', () => {
  const { app, c } = setup(); app.seq.tracks.push({ id: 'v2', kind: 'video', clips: [] });
  app.document.elementsFromPoint = () => [{ classList: { contains: () => true }, dataset: { track: 'v2' } }];
  const before = plain(c); app.start(null, 1); move(app, 1); app.move({ clientX: 60, altKey: true }); app.up({ altKey: true });
  near(c.start, before.start); near(c.in_, before.in_ + 1); assert.equal(app.seq.tracks[1].clips.length, 0);
  assert.equal(app.body().ops.length, 1); assert.equal(app.body().ops[0].op, 'set_clip'); assert.equal(app.body().ops[0].track, 'v1');
});
