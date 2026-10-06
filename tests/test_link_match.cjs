const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain } = require('./helpers/link-match-fixture.cjs');
const clip = (id, start = 0, extra = {}) => ({ id, media_id: 'm', start, in_: 2, out: 8, speed: 1, ...extra });
function sourcePicture(f, native) {
  const timing=require('../frontend/timeline-time.js'), rate=f.scope.mediaRate(f.scope.S.src), frame=timing.displayFrame(native,rate);
  assert.equal(timing.displayFrame(f.mediaNode.currentTime,rate),frame);
  assert.ok(f.mediaNode.currentTime>timing.fromFrames(frame,rate)&&f.mediaNode.currentTime<timing.fromFrames(frame+1,rate));
  assert.ok(Math.abs(f.scope.sourcePlayheadTime()-f.scope.sourceLogicalTime(f.scope.S.src,native))<1e-9);
}
function pairFixture(extra = {}) {
  const f = fixture(); f.tr.clips = [clip('video', 0, extra)]; f.seq.tracks.push({ id: 'a1', kind: 'audio', index: 0, clips: [] }); f.select('video'); return f;
}

test('Group uses a unique ID and preserves clip payloads and selected order in one saved batch', async () => {
  const f = fixture(); f.clips[0].group = 'link-1'; f.select('b', 'a'); const before = plain(f.tr.clips);
  f.scope.groupSel(true); assert.equal(f.requests.length, 1); assert.equal(f.body().tool, 'group');
  for (const c of f.tr.clips.slice(0, 2)) assert.equal(c.group, 'link-2');
  assert.deepEqual(f.tr.clips.map((c, i) => ({ ...plain(c), group: before[i].group })).map(c => JSON.parse(JSON.stringify(c))), before);
  assert.deepEqual([...f.scope.S.sel], ['b', 'a']); await f.save();
  assert.equal(f.scope.groupSel(true), false); assert.equal(f.requests.length, 1);
  f.scope.groupSel(false); assert.equal(f.requests.length, 2); assert.equal(f.body(1).ops.length, 2); await f.save(1);
  assert.equal(f.scope.groupSel(false), false); assert.equal(f.requests.length, 2);
});

test('Group and Ungroup reject selected locks, stale selection, foreign sequence and Recovery without mutation', () => {
  for (const action of [true, false]) for (const mode of ['locked', 'stale', 'foreign', 'recovery', 'gesture']) {
    const f = fixture(); f.select('a', 'b'); f.clips[0].group = f.clips[1].group = 'existing';
    if (mode === 'locked') f.tr.locked = true;
    if (mode === 'stale') f.scope.S.sel.add('gone');
    if (mode === 'foreign') f.scope.S.seq = { ...f.seq };
    if (mode === 'recovery') f.scope.projectSaveState().error = 'uncertain';
    if (mode === 'gesture') f.scope.S.gesture = {};
    const before = plain(f.project), selection = [...f.scope.S.sel];
    assert.equal(f.scope.groupSel(action), false, mode); assert.deepEqual(plain(f.project), before); assert.deepEqual([...f.scope.S.sel], selection); assert.equal(f.requests.length, 0);
  }
});

test('Group refuses exhausted IDs and Ungroup ignores already ungrouped selection', () => {
  const f = fixture(); f.select('a', 'b'); f.scope.uid = () => 'a'; const before = plain(f.project);
  assert.equal(f.scope.groupSel(true), false); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  f.clips[0].group = 'g'; f.scope.groupSel(false); assert.equal(f.body().ops.length, 1); assert.equal(f.body().ops[0].clip.id, 'a');
});

test('Unlink preserves complete reverse/ramp/fade/history payload and relinks through one partner receipt', async () => {
  const f = pairFixture({ out: 12, reverse: true, group: 'group', time_remap: [{ t: 0, v: 1 }, { t: 2, v: 3 }], audio: { gain_db: -4, fade_in: 3, fade_out: 2, pan: .2, vendor: { keep: 1 } }, audio_fx: { eq: { low_db: 2 } }, keyframes: { 'audio.gain_db': [{ t: -1, v: 0 }, { t: 3, v: -8 }], 'transform.x': [{ t: 0, v: 10 }] }, markers: [{ t: -1, name: 'hidden' }], source_edit_window: { version: 1, vendor: 'kept' }, audio_transition_in: { type: 'constant_power', duration: 1 }, transition_in: { type: 'dissolve', duration: .5 } });
  const original = plain(f.tr.clips[0]); f.scope.toggleLink(); assert.equal(f.requests.length, 1);
  const parent = f.tr.clips[0], child = f.seq.tracks[1].clips[0]; assert.equal(parent.audio_detached_id, child.id); assert.equal(child.unlinked_from, parent.id);
  assert.equal(parent.group, 'group'); assert.equal(child.group, undefined); assert.equal(f.scope.clipDur(child), 4); assert.equal(child.reverse, true);
  const expected = { ...original, id: child.id, unlinked_from: parent.id, audio: { ...original.audio, linked: true } }; delete expected.group;
  assert.deepEqual(plain(child), expected); assert.deepEqual([...f.scope.S.sel], ['video']); await f.save();
  f.select(child.id); f.scope.toggleLink(); assert.equal(f.requests.length, 2); assert.equal(f.seq.tracks[1].clips.length, 0); assert.equal(parent.audio.linked, true); assert.equal(parent.audio_detached_id, null); assert.deepEqual([...f.scope.S.sel], ['video']); await f.save(1);
});

test('Unlink preserves held silence, constant speed and full audio curves instead of creating ordinary-speed sound', () => {
  for (const extra of [{ hold: true, out: 202 }, { speed: 2 }, { reverse: true }]) {
    const f = pairFixture(extra), before = plain(f.tr.clips[0]); f.scope.toggleLink(); assert.equal(f.requests.length, 1, f.messages.join(';'));
    const child = f.seq.tracks[1].clips[0]; assert.equal(f.scope.clipDur(child), f.scope.clipDur(before)); assert.equal(child.hold, before.hold); assert.equal(child.speed, before.speed); assert.equal(child.reverse, before.reverse);
    if (child.hold) assert.equal(f.scope.window.FilmocityAudioPreview.route(f.seq, f.seq.tracks[1], child), null);
  }
});

test('Link operations reject all selected and participating track locks and missing destinations atomically', () => {
  for (const mode of ['video', 'audio', 'missing', 'stale', 'recovery', 'foreign', 'gesture']) {
    const f = pairFixture();
    if (mode === 'video') f.tr.locked = true;
    if (mode === 'audio') f.seq.tracks[1].locked = true;
    if (mode === 'missing') f.seq.tracks.pop();
    if (mode === 'stale') f.scope.S.sel.add('gone');
    if (mode === 'recovery') f.scope.projectSaveState().error = 'uncertain';
    if (mode === 'foreign') f.scope.S.seq = { ...f.seq };
    if (mode === 'gesture') f.scope.S.gesture = {};
    const before = plain(f.project), selection = [...f.scope.S.sel]; assert.equal(f.scope.toggleLink(), false, mode);
    assert.deepEqual(plain(f.project), before); assert.deepEqual([...f.scope.S.sel], selection); assert.equal(f.requests.length, 0);
  }
});

test('Unlink refuses occupied audio and overlapping planned siblings without overwriting either', () => {
  for (const planned of [false, true]) {
    const f = pairFixture();
    if (planned) { f.tr.clips.push(clip('second', 2)); f.select('video', 'second'); }
    else f.seq.tracks[1].clips.push(clip('unrelated', 3));
    const before = plain(f.project); assert.equal(f.scope.toggleLink(), false); assert.equal(f.requests.length, 0); assert.deepEqual(plain(f.project), before); assert.match(f.messages.at(-1), /occupied/);
  }
});

test('Multiple nonoverlapping unlink pairs allocate unique IDs and save once regardless of selection order', () => {
  const f = pairFixture(); f.tr.clips.push(clip('second', 10)); f.select('second', 'video'); f.scope.toggleLink();
  assert.equal(f.requests.length, 1); assert.equal(f.body().ops.length, 4); assert.equal(f.seq.tracks[1].clips.length, 2);
  assert.equal(new Set(f.seq.tracks[1].clips.map(c => c.id)).size, 2); assert.deepEqual([...f.scope.S.sel], ['second', 'video']);
});

test('Selecting both partners relinks only once and selection retains the surviving parent', async () => {
  const f = pairFixture(); f.scope.toggleLink(); await f.save(); const child = f.seq.tracks[1].clips[0];
  f.select(child.id, 'video'); f.scope.toggleLink(); assert.equal(f.body(1).ops.length, 2); assert.equal(f.seq.tracks[1].clips.length, 0); assert.deepEqual([...f.scope.S.sel], ['video']);
});

test('Relink locates a moved audio partner across tracks with equivalent bus settings', async () => {
  const f = pairFixture(); f.scope.toggleLink(); await f.save(); const child = f.seq.tracks[1].clips.pop();
  f.seq.tracks.push({ id: 'a2', kind: 'audio', index: 1, gain_db: 0, clips: [child] }); f.select(child.id); f.scope.toggleLink();
  assert.equal(f.requests.length, 2); assert.equal(f.seq.tracks[2].clips.length, 0); assert.equal(f.tr.clips[0].audio.linked, true); assert.equal(f.body(1).ops[0].track, 'a2');
});

test('Relink refuses changed audio, source, automation, marker, routing and duplicate or missing partner', async () => {
  for (const mode of ['gain', 'timing', 'reverse', 'curve', 'marker', 'bus', 'lock', 'duplicate', 'missing', 'receipt']) {
    const f = pairFixture(); f.scope.toggleLink(); await f.save(); const child = f.seq.tracks[1].clips[0];
    if (mode === 'gain') child.audio.gain_db = -12;
    if (mode === 'timing') child.start = 1;
    if (mode === 'reverse') child.reverse = true;
    if (mode === 'curve') child.keyframes = { 'audio.gain_db': [{ t: 0, v: 0 }, { t: 2, v: -3 }] };
    if (mode === 'marker') child.markers = [{ t: 1, name: 'new' }];
    if (mode === 'bus') { f.seq.tracks[1].clips = []; f.seq.tracks.push({ id: 'a2', kind: 'audio', index: 1, gain_db: -6, clips: [child] }); }
    if (mode === 'lock') f.seq.tracks[1].locked = true;
    if (mode === 'duplicate') f.seq.tracks[1].clips.push({ ...plain(child), id: 'duplicate', start: 10 });
    if (mode === 'missing') f.seq.tracks[1].clips = [];
    if (mode === 'receipt') f.tr.clips[0].audio_detached_id = 'different';
    const before = plain(f.project), selection = [...f.scope.S.sel]; assert.equal(f.scope.toggleLink(), false, mode); assert.equal(f.requests.length, 1, mode); assert.deepEqual(plain(f.project), before, mode); assert.deepEqual([...f.scope.S.sel], selection);
  }
});

test('Relink ignores visual-only changes and object-key ordering while preserving audio transition meaning', async () => {
  const f = pairFixture({ audio: { gain_db: -2, pan: .2 }, keyframes: { 'audio.gain_db': [{ t: 0, v: -2 }], 'transform.x': [{ t: 0, v: 0 }] }, audio_transition_in: { type: 'constant_power', duration: .5 } });
  f.scope.toggleLink(); await f.save(); f.tr.clips[0].transform = { x: 100 }; f.tr.clips[0].keyframes['transform.x'][0].v = 100;
  const child = f.seq.tracks[1].clips[0]; child.audio = { pan: .2, linked: true, gain_db: -2 }; child.keyframes['audio.gain_db'][0] = { v: -2, t: 0 };
  f.scope.toggleLink(); assert.equal(f.requests.length, 2); assert.equal(f.seq.tracks[1].clips.length, 0); assert.equal(f.tr.clips[0].audio_transition_in.duration, .5);
});

test('Relink permits actual picture-stack/interpolation edits but preserves independent audio-stack edits', async () => {
  const originalAudio = [{ type: 'highpass', enabled: true, params: { frequency: 80 } }];
  for (const field of ['fx_stack', 'time_interpolation']) {
    const f = pairFixture({ afx_stack: plain(originalAudio) }); f.scope.toggleLink(); await f.save();
    const visual = field === 'fx_stack' ? [{ type: 'gaussian_blur', enabled: true, params: { sigma: 2 } }] : 'optical_flow';
    f.tr.clips[0][field] = visual; f.scope.toggleLink();
    assert.equal(f.requests.length, 2, field); assert.equal(f.seq.tracks[1].clips.length, 0, field);
    assert.deepEqual(plain(f.tr.clips[0][field]), visual); assert.deepEqual(plain(f.tr.clips[0].afx_stack), originalAudio);
    assert.deepEqual(plain(f.body(1).ops[1].clip.afx_stack), originalAudio);
  }
  const g = pairFixture({ afx_stack: plain(originalAudio) }); g.scope.toggleLink(); await g.save();
  g.seq.tracks[1].clips[0].afx_stack[0].params.frequency = 120; const before = plain(g.project);
  assert.equal(g.scope.toggleLink(), false); assert.equal(g.requests.length, 1); assert.deepEqual(plain(g.project), before);
});

test('Video-only placement may explicitly enable audio, but missing recorded detached partner cannot', () => {
  const f = pairFixture({ audio: { linked: false } }); f.scope.toggleLink(); assert.equal(f.requests.length, 1); assert.equal(f.tr.clips[0].audio.linked, true); assert.equal(f.seq.tracks[1].clips.length, 0);
  const g = pairFixture({ audio: { linked: false }, audio_detached_id: 'lost' }); const before = plain(g.project); assert.equal(g.scope.toggleLink(), false); assert.deepEqual(plain(g.project), before); assert.equal(g.requests.length, 0);
});

test('Unlink refuses a mute/solo audibility change and exhausted clip IDs before any mutation', () => {
  for (const mode of ['muted', 'solo', 'ids', 'missing-media']) {
    const f = pairFixture(); if (mode === 'muted') f.tr.muted = true; if (mode === 'solo') f.tr.solo = true; if (mode === 'ids') f.scope.uid = () => 'video'; if (mode === 'missing-media') delete f.project.media.m;
    const before = plain(f.project); assert.equal(f.scope.toggleLink(), false, mode); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  }
});

test('Match Frame addresses forward/ramp/reverse/held source frames with the native CFR boundary policy', () => {
  for (const [extra, timeline, expected] of [
    [{}, 11, 3], [{ reverse: true }, 11, 329 / 30],
    [{ time_remap: [{ t: 0, v: 1 }, { t: 2, v: 3 }] }, 11, 3.5],
    [{ hold: true }, 11, 2], [{ reverse: true }, 10, 359 / 30],
    [{ in_: 2.015, out: 12.015 }, 10, 2],
  ]) {
    const f = fixture(); f.tr.clips = [clip('video', 10, { out: 12, ...extra })]; f.scope.S.t = timeline; const before = plain(f.project);
    assert.equal(f.scope.matchFrame(), true, JSON.stringify(extra)); sourcePicture(f, expected);
    assert.equal(f.scope.S.srcIn, f.tr.clips[0].in_); assert.equal(f.scope.S.focus, 'source'); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  }
});

test('Reverse Match inverts retiming and reverse frame upper boundaries instead of scalar speed', () => {
  for (const [extra, native, expected] of [
    [{}, 3.001, 11], [{ reverse: true }, 10.98, 11],
    [{ time_remap: [{ t: 0, v: 1 }, { t: 2, v: 3 }] }, 3.51, 11],
    [{ reverse: true }, 11.99, 10], [{ in_: 2.015, out: 12.015 }, 2.02, 10],
  ]) {
    const f = fixture(); f.tr.clips = [clip('video', 10, { out: 12, ...extra })]; f.scope.S.src = f.project.media.m; f.mediaNode.currentTime = native; const before = plain(f.project);
    assert.equal(f.scope.reverseMatchFrame(), true); assert.ok(Math.abs(f.scope.S.t - expected) < 1e-9, `${f.scope.S.t} != ${expected}`);
    assert.deepEqual([...f.scope.S.sel], ['video']); assert.equal(f.scope.S.focus, 'timeline'); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  }
});

test('Match and Reverse Match convert interpreted subclip coordinates in both directions', () => {
  const f = fixture(); Object.assign(f.project.media.m, { sub_in: 10, interpret_fps: 15, fps: 30 }); f.tr.clips = [clip('video', 10)]; f.scope.S.t = 11;
  assert.equal(f.scope.matchFrame(), true); sourcePicture(f, 6.5); assert.equal(f.scope.S.srcIn, 2); assert.equal(f.scope.S.srcOut, 8);
  f.scope.S.t = 0; assert.equal(f.scope.reverseMatchFrame(), true); assert.ok(Math.abs(f.scope.S.t - 11) < 1e-10);
});

test('Held Reverse Match accepts only the frozen native frame and deterministically selects clip start', () => {
  const f = fixture(); f.tr.clips = [clip('held', 10, { in_: 2.015, out: 202.015, hold: true })]; f.scope.S.src = f.project.media.m; f.mediaNode.currentTime = 2.02;
  assert.equal(f.scope.reverseMatchFrame(), true); assert.equal(f.scope.S.t, 10); f.mediaNode.currentTime = 7; const selection = [...f.scope.S.sel];
  assert.equal(f.scope.reverseMatchFrame(), false); assert.equal(f.scope.S.t, 10); assert.deepEqual([...f.scope.S.sel], selection);
  f.scope.S.t = 100; assert.equal(f.scope.matchFrame(), true); sourcePicture(f, 2); assert.ok(Math.abs(f.scope.S.srcOut - 61 / 30) < 1e-10); assert.equal(f.requests.length, 0);
});

test('Frame matching is read-only during Trim Edit playback, permits locked tracks and rejects stale owners', () => {
  const f = fixture(), pauses = []; f.tr.locked = true; f.scope.S.playing = true; f.scope.S.trimMode = { type: 'ripple' }; f.scope.S.t = 1;
  f.scope.togglePlay = (on, options) => { pauses.push({ on, options }); f.scope.S.playing = on; };
  assert.equal(f.scope.matchFrame(), true); assert.equal(pauses[0].on, false); assert.equal(pauses[0].options.commitTrim, false);
  assert.equal(f.scope.reverseMatchFrame(), true); assert.equal(pauses[1].options.commitTrim, false); assert.equal(f.requests.length, 0);
  f.scope.S.src = { ...f.scope.S.src }; const t = f.scope.S.t; assert.equal(f.scope.reverseMatchFrame(), false); assert.equal(f.scope.S.t, t);
  f.scope.S.seq = { ...f.seq }; assert.equal(f.scope.matchFrame(), false); assert.equal(f.requests.length, 0);
});

test('Frame matching rejects nonfinite, unused and exclusive-end frames without selection or save changes', () => {
  for (const value of [NaN, Infinity, -1, 1, 8, 100]) {
    const f = fixture(); f.tr.clips = [clip('video', 10)]; f.scope.S.src = f.project.media.m; f.mediaNode.currentTime = value; const before = plain(f.project), selection = [...f.scope.S.sel], t = f.scope.S.t;
    assert.equal(f.scope.reverseMatchFrame(), false, String(value)); assert.equal(f.scope.S.t, t); assert.deepEqual([...f.scope.S.sel], selection); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  }
});

test('Native frame identity survives Match to Reverse Match at fractional rate and long timeline positions', () => {
  const f = fixture(); Object.assign(f.project.media.m, { fps: 30000 / 1001, frame_rate: '30000/1001' });
  for (const reverse of [false, true]) {
    f.tr.clips = [clip('video', 3600, { out: 12, reverse, time_remap: [{ t: 0, v: .5 }, { t: 1.7, v: 2.3 }, { t: 3, v: 1 }] })];
    for (const local of [0, .01, .5, 1, 2, 3, 5]) {
      f.scope.S.t = 3600 + local; assert.equal(f.scope.matchFrame(), true); const frame = f.mediaNode.currentTime;
      assert.equal(f.scope.reverseMatchFrame(), true); assert.equal(f.scope.matchFrame(), true); assert.ok(Math.abs(f.mediaNode.currentTime - frame) < 1e-9);
    }
  }
  assert.equal(f.requests.length, 0);
});
