const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, until, response } = require('./helpers/removal-fixture.cjs');
const time = require('../frontend/timeline-time.js'), audio = require('../frontend/audio-preview.js');
const clip = (id, start, duration, extra = {}) => ({ id, media_id: 'm', start, in_: 5, out: 5 + duration, speed: 1, ...extra });
const track = (id, clips, extra = {}) => ({ id, kind: id.startsWith('a') ? 'audio' : 'video', index: 0, clips, ...extra });
const near = (a, b, tolerance = 1e-8) => assert.ok(Math.abs(a - b) <= tolerance, `${a} != ${b}`);
const sourceAt = (f, c, at) => c.hold ? c.in_ : c.reverse ? c.out - f.scope.cutClock.sourceOffset(c, at) : c.in_ + f.scope.cutClock.sourceOffset(c, at);
function reject(f, call, message) { const before = plain(f.project), selected = [...f.scope.S.sel], gap = f.scope.S.gap; call(); assert.equal(f.requests.length, 0); assert.deepEqual(plain(f.project), before); assert.deepEqual([...f.scope.S.sel], selected); assert.equal(f.scope.S.gap, gap); if (message) assert.match(f.messages.at(-1), message); }

test('Lift/Extract split advanced clips and retain surviving source clocks in one guarded batch', async () => {
  for (const extract of [false, true]) for (const extra of [{}, { reverse: true }, { hold: true }, { speed: 2 }, { time_remap: [{ t: 0, v: 1 }, { t: 4, v: 2 }] }, { reverse: true, time_remap: [{ t: 0, v: 2 }, { t: 4, v: .5 }] }]) {
    const f = fixture(), original = clip('a', 0, 10, extra); f.install(original); f.select('a'); f.seq.in_point = 1; f.seq.out_point = 2;
    f.scope.liftExtract(extract); await until(() => f.requests.length === 1); assert.ok(f.body()._context); assert.equal(f.tr.clips.length, 2);
    const [left, right] = [...f.tr.clips].sort((a, b) => a.start - b.start); near(f.scope.cutClock.duration(left), 1); near(right.start, extract ? 1 : 2);
    near(f.scope.cutClock.duration(right), f.scope.cutClock.duration(original) - 2);
    for (const t of [0, .25, .75]) { near(sourceAt(f, left, t), sourceAt(f, original, t)); near(sourceAt(f, right, t), sourceAt(f, original, t + 2)); }
    assert.equal(f.scope.S.sel.size, 0); await f.save();
  }
});
test('range splitting preserves manual/Bezier, ducking and inherited fades on both surviving pieces', async () => {
  const f = fixture(), original = clip('a', 0, 6, { audio: { fade_in: 5, fade_out: 5 }, keyframes: { 'transform.x': [{ t: 0, v: 0, e: 'bezier', o: [.2, 4] }, { t: 6, v: 12, i: [.3, -2] }], 'audio.duck_db': [{ t: 0, v: 0 }, { t: 6, v: -18 }] }, markers: [{ t: .5, name: 'left' }, { t: 1.5, name: 'cut' }, { t: 4, name: 'right' }] });
  f.install(original); f.seq.in_point = 1; f.seq.out_point = 2; f.scope.liftExtract(true); await until(() => f.requests.length === 1);
  const right = f.tr.clips.find(c => c.start === 1);
  for (const t of [0, .5, 2, 3.9]) { near(f.scope.evalCurve(right.keyframes['transform.x'], t), f.scope.evalCurve(original.keyframes['transform.x'], t + 2), 1e-7); near(audio.duckDb(right, t), audio.duckDb(original, t + 2)); near(audio.fadeGain(right, 4, t), audio.fadeGain(original, 6, t + 2)); }
  assert.equal(f.tr.clips.flatMap(c => c.markers || []).filter(m => m.name === 'cut').length, 0); await f.save();
});
test('Lift leaves sequence annotations/timing untouched and applies to every unlocked track', async () => {
  const f = fixture(); f.install(clip('a', 0, 6)); f.seq.tracks.push(track('v2', [clip('b', 0, 6)], { sync_lock: false }), track('a1', [clip('c', 0, 6)], { locked: true }));
  Object.assign(f.seq, { in_point: 1, out_point: 2, markers: [{ time: 4 }], captions: [{ id: 'cap', start: .5, end: 4, text: 'Keep timing' }], transcript: [{ s: .5, e: 4, text: 'unchanged' }] });
  const annotations = plain({ markers: f.seq.markers, captions: f.seq.captions, transcript: f.seq.transcript }), locked = plain(f.seq.tracks[2]);
  f.scope.liftExtract(false); await f.save(); assert.equal(f.seq.tracks[0].clips.length, 2); assert.equal(f.seq.tracks[1].clips.length, 2); assert.deepEqual(plain(f.seq.tracks[2]), locked);
  assert.deepEqual(plain({ markers: f.seq.markers, captions: f.seq.captions, transcript: f.seq.transcript }), annotations); assert.equal(f.seq.in_point, 1); assert.equal(f.seq.out_point, 2);
});
test('Extract persists markers, caption fragments and collapsed IO with the clips in one request', async () => {
  const f = fixture(); f.install(clip('a', 0, 6)); Object.assign(f.seq, { in_point: 1, out_point: 3, markers: [{ id: 'before', time: .5 }, { id: 'inside', time: 2 }, { id: 'after', time: 5 }, { id: 'range', time: .5, duration: 5 }], captions: [{ id: 'cap', start: .5, end: 4, text: 'Same text' }], transcript: [{ s: .5, e: 4, text: 'stale input' }] });
  const transcript = plain(f.seq.transcript); f.scope.liftExtract(true); await until(() => f.requests.length === 1);
  assert.deepEqual(plain(f.seq.markers.map(m => [m.id, m.time])), [['before', .5], ['inside', 1], ['after', 3], ['range', .5]]); near(f.seq.markers[3].duration, 3);
  assert.ok(f.seq.in_point == null && f.seq.out_point == null); assert.deepEqual(plain(f.seq.transcript), transcript);
  assert.deepEqual(plain(f.seq.captions.map(c => [c.start, c.end, c.text])), [[.5, 1, 'Same text'], [1, 2, 'Same text']]);
  assert.equal(new Set(f.seq.captions.map(c => c.id)).size, 2); assert.ok(f.body().ops.some(o => o.path?.endsWith('/markers'))); assert.ok(f.body().ops.some(o => o.path?.endsWith('/captions'))); assert.equal(f.requests.length, 1); await f.save();
});
test('range endpoints use rational picture frames and audio-only ranges preserve subframes', async () => {
  for (const audioOnly of [false, true]) { const f = fixture(); f.install(clip('a', 0, 1), 29.97); if (audioOnly) f.tr.kind = 'audio'; const a = time.fromFrames(1.3, 29.97), b = time.fromFrames(4.2, 29.97); f.seq.in_point = a; f.seq.out_point = b;
    f.scope.liftExtract(false); await f.save(); const [left, right] = f.tr.clips; near(f.scope.cutClock.duration(left), audioOnly ? a : time.fromFrames(1, 29.97)); near(right.start, audioOnly ? b : time.fromFrames(4, 29.97)); }
});
test('unsupported transition anywhere aborts complete Lift/Extract planning without selection or model changes', () => {
  for (const extract of [false, true]) { const f = fixture(); f.install(clip('a', 0, 6)); f.seq.tracks.push(track('v2', [clip('b', 0, 6, { transition_in: { type: 'wipe_left', duration: 2 } })])); f.select('a'); f.seq.in_point = 1; f.seq.out_point = 3;
    reject(f, () => f.scope.liftExtract(extract), /transition/); }
});
test('selected ripple delete closes overlapping selected intervals once, preserving excluded/locked tracks', async () => {
  const f = fixture(); f.tr.clips = [clip('a', 0, 2), clip('later1', 6, 1)]; f.seq.tracks.push(track('v2', [clip('b', 1, 2), clip('later2', 5, 1)], { sync_lock: false }), track('a1', [clip('audio', 8, 1)]), track('v3', [clip('fixed1', 0, 10)], { sync_lock: false }), track('a2', [clip('fixed2', 0, 10)], { locked: true }));
  f.select('a', 'b'); f.scope.S.t = 8; const fixed = plain(f.seq.tracks.slice(3)); f.scope.deleteSel(true); await f.save();
  assert.deepEqual(plain(f.tr.clips.map(c => [c.id, c.start])), [['later1', 3]]); assert.deepEqual(plain(f.seq.tracks[1].clips.map(c => [c.id, c.start])), [['later2', 2]]); assert.equal(f.seq.tracks[2].clips[0].start, 5); assert.deepEqual(plain(f.seq.tracks.slice(3)), fixed); assert.equal(f.scope.S.t, 5);
});
test('selected ripple delete sums disjoint removed ranges once and persists markers in that same batch', async () => {
  const f = fixture(); f.tr.clips = [clip('a', 1, 1), clip('gap-content', 3, 1), clip('b', 4, 2), clip('after', 8, 1)]; f.seq.markers = [{ time: 1.5 }, { time: 5 }, { time: 8 }]; f.select('b', 'a');
  f.scope.deleteSel(true); await until(() => f.requests.length === 1); assert.deepEqual(plain(f.tr.clips.map(c => [c.id, c.start])), [['gap-content', 2], ['after', 5]]); assert.deepEqual(plain(f.seq.markers.map(m => m.time)), [1, 3, 5]); assert.ok(f.body().ops.some(o => o.path?.endsWith('/markers'))); await f.save();
});
test('ripple delete refuses unselected material on participating tracks, with Extract guidance', () => {
  for (const sameTrack of [false, true]) { const f = fixture(); f.tr.clips = [clip('a', 1, 2)]; const other = clip('unselected', 0, 5); if (sameTrack) f.tr.clips.push(other); else f.seq.tracks.push(track('a1', [other])); f.select('a');
    reject(f, () => f.scope.deleteSel(true), /unselected material.*Extract/); }
});
test('selected source track sync-lock off does not disable other unlocked synchronized peers', async () => {
  const f = fixture(); f.tr.sync_lock = false; f.tr.clips = [clip('a', 1, 1), clip('b', 4, 1)]; f.seq.tracks.push(track('a1', [clip('audio', 5, 1)])); f.select('a'); f.scope.deleteSel(true); await f.save(); assert.equal(f.tr.clips[0].start, 3); assert.equal(f.seq.tracks[1].clips[0].start, 4);
});
test('plain Delete removes only selected clips and never ripples sequence annotations or peers', async () => {
  const f = fixture(); f.tr.clips = [clip('a', 1, 2), clip('b', 4, 1)]; f.seq.tracks.push(track('a1', [clip('audio', 0, 8)])); f.seq.markers = [{ time: 5 }]; f.select('a'); f.scope.deleteSel(false); await f.save(); assert.equal(f.tr.clips[0].start, 4); assert.equal(f.seq.tracks[1].clips[0].out, 13); assert.equal(f.seq.markers[0].time, 5); assert.equal(f.body().ops.length, 1);
});
test('explicit locked selections reject whole plain/ripple delete; no partial deletion', () => {
  for (const ripple of [false, true]) { const f = fixture(); f.tr.clips = [clip('a', 0, 1)]; f.seq.tracks.push(track('a1', [clip('locked', 2, 1)], { locked: true })); f.select('a', 'locked'); reject(f, () => f.scope.deleteSel(ripple), /Unlock every selected track/); }
});
test('gap close checks current ownership/anchors and shifts sync peers plus markers in one save', async () => {
  const f = fixture(); f.tr.clips = [clip('left', 0, 1), clip('right', 3, 1)]; f.seq.tracks.push(track('a1', [clip('audio', 5, 1)])); f.seq.markers = [{ time: 4 }]; f.gap(...f.tr.clips);
  f.scope.deleteSel(false); await f.save(); assert.equal(f.tr.clips[1].start, 1); assert.equal(f.seq.tracks[1].clips[0].start, 3); assert.equal(f.seq.markers[0].time, 2); assert.equal(f.scope.S.t, 1); assert.equal(f.scope.S.gap, null); assert.equal(f.body().tool, 'close_gap');
});
test('gap close rejects locked tracks, crossing sync peers, changed gaps and old project/sequence references', () => {
  for (const mode of ['locked', 'crossing', 'boundary', 'occupied', 'project', 'sequence']) { const f = fixture(); f.tr.clips = [clip('left', 0, 1), clip('right', 3, 1)]; const gap = f.gap(...f.tr.clips);
    if (mode === 'locked') f.tr.locked = true; if (mode === 'crossing') f.seq.tracks.push(track('a1', [clip('audio', 0, 5)])); if (mode === 'boundary') f.tr.clips[0].out += .25; if (mode === 'occupied') f.tr.clips.push(clip('inserted', 2, .25)); if (mode === 'project') gap.project = {}; if (mode === 'sequence') gap.sequence = {};
    reject(f, () => f.scope.deleteSel(true), mode === 'locked' ? /Unlock/ : mode === 'crossing' ? /unselected/ : /gap.*(again|changed)|Select the gap/i); }
});
test('empty/no-op/invalid ranges and fully locked sequences create no request or history', () => {
  const f = fixture(); f.tr.clips = [clip('a', 0, 1), clip('b', 4, 1)]; f.seq.in_point = 2; f.seq.out_point = 3; reject(f, () => f.scope.liftExtract(false));
  f.select(); reject(f, () => f.scope.deleteSel(false));
  for (const points of [[null, 3], [3, 3], [-1, 3], [0, Infinity]]) { [f.seq.in_point, f.seq.out_point] = points; reject(f, () => f.scope.liftExtract(true), /In and Out/); }
  f.seq.in_point = 1; f.seq.out_point = 2; f.tr.locked = true; reject(f, () => f.scope.liftExtract(true), /Unlock/);
});
test('active gesture and Recovery errors block every removal route before local mutation', () => {
  for (const recovery of [false, true]) for (const operation of [f => f.scope.deleteSel(true), f => f.scope.deleteSel(false), f => f.scope.liftExtract(true), f => f.scope.liftExtract(false)]) { const f = fixture(); f.install(clip('a', 0, 6)); f.select('a'); f.seq.in_point = 1; f.seq.out_point = 2; if (recovery) f.scope.projectSaveState().error = { message: 'Unconfirmed save' }; else f.scope.S.gesture = {};
    reject(f, () => operation(f), recovery ? /Recovery/ : /Finish the drag/); }
});
test('rejected range save retains the complete recoverable draft and blocks replay', async () => {
  const f = fixture(); f.install(clip('a', 0, 6)); f.seq.in_point = 1; f.seq.out_point = 2; f.scope.liftExtract(true); await until(() => f.requests.length === 1); f.requests[0].resolve(response(409, { detail: 'Project changed' })); await until(() => !!f.scope.projectSaveState().error); const draft = plain(f.project); assert.ok(f.storage.size > 0); f.scope.liftExtract(true); assert.equal(f.requests.length, 1); assert.deepEqual(plain(f.project), draft);
});
test('project panel bin deletion retains its established command while timeline empty Delete is inert', () => {
  const f = fixture(); f.select(); f.scope.S.focus = 'project'; f.scope.S.binSel = new Set(['m']); f.scope.deleteSel(false); assert.equal(f.binDeletes, 1); assert.equal(f.requests.length, 0);
});
test('legacy marker ripple now returns independent persisted operations without changing the project', () => {
  const f = fixture(); f.seq.markers = [{ id: 'before', time: 1 }, { id: 'after', time: 5, vendor: { keep: 7 } }]; const before = plain(f.project);
  const ops = f.scope.rippleMarkers(3, -2); assert.deepEqual(plain(f.project), before); assert.equal(ops.length, 1); assert.deepEqual(plain(ops[0].value.map(m => m.time)), [1, 3]);
  ops[0].value[1].vendor.keep = 8; assert.equal(f.seq.markers[1].vendor.keep, 7); assert.deepEqual(plain(f.scope.rippleMarkers(10, 1)), []); assert.deepEqual(plain(f.scope.rippleMarkers(1, 0)), []);
});
test('large disjoint ripple selections test conflicts with bounded range lookup work', () => {
  const f = fixture(), ranges = Array.from({ length: 10000 }, (_, i) => [i * 2, i * 2 + 1]); let reads = 0;
  const counted = new Proxy(ranges, { get(target, key) { if (/^\d+$/.test(String(key))) reads++; return target[key]; } });
  assert.equal(f.scope.removalIntersects(clip('inside', 19998.25, .25), counted), true);
  assert.equal(f.scope.removalIntersects(clip('gap', 19997.25, .25), counted), false);
  assert.equal(f.scope.removalIntersects(clip('spanning', 0, 20000), counted), true);
  assert.ok(reads < 60, `Range lookups must stay logarithmic, observed ${reads}`);
});
test('rapid sequential ripple deletes queue independent snapshots with advancing saved contexts', async () => {
  const f = fixture(); f.tr.clips = [clip('a', 1, 1), clip('b', 4, 1), clip('after', 8, 1)]; f.select('a'); f.scope.deleteSel(true); f.select('b'); f.scope.deleteSel(true);
  await until(() => f.requests.length === 1); assert.equal(f.body()._context.revision, 'r0'); const firstClips = f.body().ops.find(o => o.path?.endsWith('/clips')).value; assert.deepEqual(firstClips.map(c => [c.id, c.start]), [['b', 3], ['after', 7]]);
  assert.deepEqual(plain(f.tr.clips.map(c => [c.id, c.start])), [['after', 6]]); f.requests[0].resolve(require('./helpers/removal-fixture.cjs').saved('r1')); await until(() => f.requests.length === 2); assert.equal(f.body(1)._context.revision, 'r1'); assert.deepEqual(f.body(1).ops.find(o => o.path?.endsWith('/clips')).value.map(c => [c.id, c.start]), [['after', 6]]); await f.save(1);
});
test('unselected overlaps above range-engine arithmetic tolerance reject even at submicrosecond scale', () => {
  const f = fixture(); f.tr.clips = [clip('selected', 1, 1), clip('tail', 0, 1 + 5e-11)]; f.select('selected'); reject(f, () => f.scope.deleteSel(true), /unselected/);
});
test('a sequence object no longer owned by the active project cannot receive a removal', () => {
  const f = fixture(); f.install(clip('a', 0, 1)); f.select('a'); f.scope.S.seq = plain(f.seq); reject(f, () => f.scope.deleteSel(false), /current sequence/);
});
