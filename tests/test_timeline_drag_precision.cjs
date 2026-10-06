const assert = require('node:assert/strict');
const test = require('node:test');
const { fixture, event, node, plain } = require('./helpers/gesture-fixture.cjs');
const close = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-10, `${message || 'timing'}: ${actual} != ${expected}`);

function rational(rate = 30000, speed = 1) {
  const app = fixture(); app.seq.fps = rate / 1001; app.scope.S.pps = 600; app.scope.S.snap = false;
  for (let i = 0; i < app.clips.length; i++) Object.assign(app.clips[i], { start: i * 120 * 1001 / rate, in_: 5, out: 5 + 90 * 1001 / rate * speed, speed });
  return app;
}

test('picture moves stay on 23.976, 29.97 and 59.94 frame boundaries with magnetic snapping off', () => {
  for (const rate of [24000, 30000, 60000]) {
    const app = rational(rate); app.start(); app.move({ clientX: 101 }); app.up();
    close(app.clips[0].start, Math.round(101 / 600 * rate / 1001) * 1001 / rate);
    close(app.body().ops.find(op => op.op === 'set_clip').clip.start, app.clips[0].start);
  }
});

test('fractional frame trims preserve their source mapping at quarter, normal and double speed', () => {
  for (const rate of [24000, 30000, 60000]) for (const speed of [0.25, 1, 2]) for (const side of ['l', 'r']) {
    const app = rational(rate, speed), original = plain(app.clips[0]); app.start(side); app.move({ clientX: 101 }); app.up();
    const boundary = side === 'l' ? app.clips[0].start : app.clips[0].start + (app.clips[0].out - app.clips[0].in_) / speed;
    close(boundary * rate / 1001, Math.round(boundary * rate / 1001), 'boundary in frames');
    if (side === 'l') close(app.clips[0].in_, original.in_ + (boundary - original.start) * speed, 'head source');
    else close(app.clips[0].out, original.in_ + (boundary - original.start) * speed, 'tail source');
  }
});

test('picture magnetic snapping aligns subframe playheads, markers and audio endpoints to the frame grid', () => {
  for (const point of ['playhead', 'marker', 'audio']) {
    const app = rational(); app.scope.S.snap = true; app.seq.markers = []; app.scope.S.t = 100;
    const target = 20.12 * 1001 / 30000;
    if (point === 'playhead') app.scope.S.t = target;
    else if (point === 'marker') app.seq.markers = [{ time: target }];
    else app.seq.tracks.push({ id: 'audio', kind: 'audio', clips: [{ id: 'd', start: target, in_: 0, out: 1 }] });
    app.start(); app.move({ clientX: target * 600 + 3 }); app.up();
    close(app.clips[0].start, 20 * 1001 / 30000, point);
  }
});

test('group moves clamp one shared delta against zero without collapsing spacing', () => {
  const app = fixture(); app.scope.S.sel = new Set(['b', 'c']);
  app.scope.startDrag(event(), app.clips[2], app.tr, null, node()); app.move({ clientX: -600 }); app.up();
  assert.deepEqual(app.body().ops.filter(op => op.op === 'set_clip').map(op => [op.clip.id, op.clip.start]), [['b', 0], ['c', 3]]);
});

test('moving an audio anchor in a mixed group preserves its subframe offset from picture', () => {
  const app = rational(), video = app.clips[0]; video.start = 30 * 1001 / 30000;
  const sound = { id: 'sound', media_id: 'm', start: video.start + 0.01, in_: 1, out: 2 };
  const audio = { id: 'a1', kind: 'audio', clips: [sound] }; app.seq.tracks.push(audio);
  app.scope.S.sel = new Set(['sound', video.id]);
  app.scope.startDrag(event(), sound, audio, null, node()); app.move({ clientX: 101 }); app.up();
  close(video.start * 30000 / 1001, 35, 'picture frame'); close(sound.start - video.start, 0.01, 'sound offset');
});

test('audio-only moves, trims and slips retain deliberate subframe adjustments', () => {
  for (const [tool, handle] of [['select', null], ['select', 'l'], ['select', 'r'], ['slip', null]]) {
    const app = rational(), before = plain(app.clips[0]); app.tr.kind = 'audio'; app.scope.S.tool = tool;
    app.start(handle); app.move({ clientX: 101 }); app.up();
    if (tool === 'slip') close(app.clips[0].in_ - before.in_, 101 / 600);
    else if (handle === 'r') close(app.clips[0].out - before.out, 101 / 600);
    else close(app.clips[0].start - before.start, 101 / 600);
  }
});

test('tail trims clamp to the last whole timeline frame within a fractional source handle', () => {
  const app = rational(), original = plain(app.clips[0]); app.project.media.m.duration = original.out + 0.05;
  app.start('r'); app.move({ clientX: 600 }); app.up();
  close(app.clips[0].out, original.out + 1001 / 30000);
  assert.ok(app.clips[0].out <= app.project.media.m.duration);
});

test('minimum clip duration is one timeline frame even at non-unit source speed', () => {
  for (const speed of [0.25, 2, 8]) for (const side of ['l', 'r']) {
    const app = rational(30000, speed); app.start(side); app.move({ clientX: side === 'l' ? 100000 : -100000 }); app.up();
    close((app.clips[0].out - app.clips[0].in_) / speed, 1001 / 30000);
    assert.ok(app.clips[0].in_ >= 0); assert.ok(app.clips[0].out <= app.project.media.m.duration);
  }
});

test('slips stop on whole timeline frames inside both source handles', () => {
  for (const direction of [-1, 1]) {
    const app = rational(30000, 2); Object.assign(app.clips[0], { in_: 0.1, out: 2 }); app.project.media.m.duration = 2.1;
    app.scope.S.tool = 'slip'; app.start(); app.move({ clientX: 600 * direction }); app.up();
    close(app.clips[0].in_, 0.1 + direction * 2 * 1001 / 30000);
    close(app.clips[0].out - app.clips[0].in_, 1.9);
    assert.ok(app.clips[0].in_ >= 0 && app.clips[0].out <= 2.1);
  }
});

test('rolls constrain both sides at the neighboring source handle', () => {
  for (const side of ['l', 'r']) {
    const app = fixture(); app.scope.S.tool = 'roll';
    if (side === 'l') { app.project.media.m.duration = 8.05; app.start('l', 1); app.move({ clientX: 600 }); }
    else { app.clips[1].in_ = 0.05; app.clips[1].out = 3.05; app.start('r', 0); app.move({ clientX: -600 }); }
    app.up();
    close(app.clips[1].start, 3 + (side === 'l' ? 1 : -1) / 30);
    if (side === 'l') assert.ok(app.clips[0].out <= app.project.media.m.duration);
    else assert.ok(app.clips[1].in_ >= 0);
    close(app.clips[0].start + app.clips[0].out - app.clips[0].in_, app.clips[1].start, 'shared cut');
  }
});

test('slide preserves selected duration and constrains both neighboring source handles', () => {
  for (const direction of [-1, 1]) {
    const app = fixture(); app.scope.S.tool = 'slide'; app.project.media.m.duration = 8.05;
    Object.assign(app.clips[2], { in_: 0.05, out: 3.05 }); app.start(null, 1); app.move({ clientX: 600 * direction }); app.up();
    close(app.clips[1].start, 3 + direction / 30); close(app.clips[1].out - app.clips[1].in_, 3);
    assert.ok(app.clips[0].out <= 8.05 && app.clips[2].in_ >= 0);
    close(app.clips[0].out - app.clips[0].in_, app.clips[1].start);
    close(app.clips[1].start + 3, app.clips[2].start);
  }
});

test('unknown source duration allows contraction but prevents extension past known out', () => {
  const app = fixture(); delete app.project.media.m.duration;
  app.start('r'); app.move({ clientX: 600 }); app.up(); assert.equal(app.requests.length, 0);
  app.start('r'); app.move({ clientX: -60 }); app.up(); assert.equal(app.clips[0].out, 7);
});

test('quantized clicks and drags that return to their original location create no save or undo entry', () => {
  for (const returning of [false, true]) {
    const app = rational(), before = plain(app.project); app.start(); app.move({ clientX: returning ? 101 : 3 });
    if (returning) app.move({ clientX: 0 }); app.up();
    assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0); assert.equal(app.storage.size, 0);
  }
});

test('source drags reject malformed speed ramps without changing neighboring clips', () => {
  for (const [tool, side] of [['select', 'l'], ['select', 'r'], ['slip', null]]) {
    const app = fixture(); app.clips[1].time_remap = [{ t: 0, v: 1 }, { t: 0, v: 2 }]; app.scope.S.tool = tool; const before = plain(app.project);
    app.start(side, 1); app.move({ clientX: 60 }); app.up();
    assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0); assert.match(app.messages.at(-1), /ramp|speed/i);
  }
});

test('roll refuses to alter a neighboring picture transition that cannot retain its clock', () => {
  const app = fixture(); app.clips[0].transition_out = { type: 'dissolve', duration: 1 }; app.scope.S.tool = 'roll'; const before = plain(app.project);
  app.start('l', 1); app.move({ clientX: 30 }); app.up();
  assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0); assert.match(app.messages.at(-1), /transition/i);
});

test('a group containing a locked track stays intact and does not submit a partial move', () => {
  const app = fixture(); const locked = { id: 'locked', kind: 'video', locked: true, clips: [{ id: 'd', start: 4, in_: 1, out: 2 }] };
  app.seq.tracks.push(locked); app.scope.S.sel = new Set(['a', 'd']); const before = plain(app.project);
  app.scope.startDrag(event(), app.clips[0], app.tr, null, node()); app.move({ clientX: 60 }); app.up();
  assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0); assert.match(app.messages.at(-1), /Unlock every selected track/);
});

test('vertical-only cross-track moves preserve exact existing timing', () => {
  const app = rational(); app.clips[0].start = 0.01;
  app.seq.tracks.push({ id: 'v2', kind: 'video', clips: [] });
  app.document.elementsFromPoint = () => [{ classList: { contains: () => true }, dataset: { track: 'v2' } }];
  app.start(); app.move({ clientY: 80 }); app.up();
  assert.equal(app.body().ops[1].clip.start, 0.01); assert.equal(app.body().ops[1].track, 'v2');
});

test('ripple head can extend source handles at timeline zero while the clip start stays fixed', () => {
  const app = rational(), before = plain(app.clips); app.scope.S.tool = 'ripple';
  app.start('l'); app.move({ clientX: -600 }); app.up();
  const extension = 30 * 1001 / 30000;
  assert.equal(app.clips[0].start, 0); close(app.clips[0].in_, before[0].in_ - extension);
  close(app.clips[1].start, before[1].start + extension); close(app.clips[2].start, before[2].start + extension);
  assert.equal(app.body().ops.length, 3);
});

test('audio tracks can trim down to one 48 kHz output sample instead of one picture frame', () => {
  for (const side of ['l', 'r']) {
    const app = fixture(); app.tr.kind = 'audio'; app.start(side); app.move({ clientX: side === 'l' ? 6000 : -6000 }); app.up();
    close(app.clips[0].out - app.clips[0].in_, 1 / 48000);
  }
});

test('explicit fade edits invalidate inherited windows and restore ordinary source dragging', () => {
  const app = fixture(); app.clips[0].audio = { fade_in: 0, fade_window: { duration: 6, offset: 1, settings: [1, 0, true, '', 0, '', 0] } };
  app.start('l'); app.move({ clientX: 60 }); app.up();
  assert.equal(app.clips[0].start, 1); assert.equal(app.requests.length, 1); assert.equal(app.messages.some(message => /not supported yet/.test(message)), false);
});

test('still-image duration remains extendable when the probed source has zero duration', () => {
  const app = fixture(); app.project.media.m = { is_image: true, duration: 0 };
  Object.assign(app.clips[0], { in_: 0, out: 3 }); app.clips[1].start = 5; app.start('r'); app.move({ clientX: 60 }); app.up();
  assert.equal(app.clips[0].out, 4); assert.equal(app.requests.length, 1);
});

test('Ctrl held during an Alt slip does not turn the completed source edit into an insert move', () => {
  const app = fixture(), originalNeighbors = plain(app.clips.slice(1)), originalMarkers = plain(app.seq.markers);
  app.start(); app.move({ clientX: 60, altKey: true, ctrlKey: true }); app.up({ altKey: true, ctrlKey: true });
  assert.equal(app.clips[0].start, 0); assert.equal(app.clips[0].in_, 6); assert.equal(app.clips[0].out, 9);
  assert.deepEqual(plain(app.clips.slice(1)), originalNeighbors); assert.deepEqual(plain(app.seq.markers), originalMarkers);
  assert.equal(app.body().ops.length, 1); assert.notEqual(app.body().tool, 'insert_drag');
});

test('ordinary same-track overwrite moves reinsert the dragged clip last and preserve all clip properties', () => {
  const app = fixture(); Object.assign(app.clips[0], { note: 'keep me', keyframes: { 'transform.scale': [{ t: 0, v: 1.2 }] } });
  const expected = { ...plain(app.clips[0]), start: 3 };
  app.start(); app.move({ clientX: 180 }); app.up();
  assert.deepEqual(app.body().ops, [
    { op: 'remove_clip', sequence: 's1', track: 'v1', clip_id: 'a' },
    { op: 'set_clip', sequence: 's1', track: 'v1', clip: expected },
  ]);
  assert.deepEqual(app.tr.clips.map(clip => clip.id), ['b', 'c', 'a']);
  assert.deepEqual(plain(app.tr.clips.at(-1)), expected);
});

test('group overwrite priority preserves original track and clip ordering rather than selection click order', () => {
  const app = fixture(); app.scope.S.sel = new Set(['c', 'a']);
  app.scope.startDrag(event(), app.clips[0], app.tr, null, node()); app.move({ clientX: 60 }); app.up();
  assert.deepEqual(app.body().ops.map(op => [op.op, op.clip_id || op.clip.id]), [['remove_clip', 'a'], ['remove_clip', 'c'], ['set_clip', 'a'], ['set_clip', 'c']]);
  assert.deepEqual(app.tr.clips.map(clip => clip.id), ['b', 'a', 'c']);
});

test('source edits retain clip ordering and do not acquire overwrite priority', () => {
  for (const [tool, handle] of [['select', 'l'], ['select', 'r'], ['slip', null], ['slide', null], ['roll', 'l']]) {
    const app = fixture(); app.scope.S.tool = tool; app.start(handle, 1); app.move({ clientX: handle === 'r' ? -60 : 60 }); app.up();
    assert.deepEqual(app.tr.clips.map(clip => clip.id), ['a', 'b', 'c']);
    assert.equal(app.body().ops.every(op => op.op === 'set_clip'), true);
  }
});
