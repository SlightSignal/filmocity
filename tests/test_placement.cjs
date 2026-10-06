const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, context, saved, until } = require('./helpers/placement-fixture.cjs');
const time = require('../frontend/timeline-time.js');
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);
const clip = (id, start, duration, extra = {}) => ({ id, media_id: 'm', start, in_: 2, out: 2 + duration, speed: 1, ...extra });
const source = (f, c, t) => c.hold ? c.in_ : c.reverse ? c.out - f.scope.sourceOffset(c, t) : c.in_ + f.scope.sourceOffset(c, t);
function sourceSetup(patch = {}) {
  const f = fixture(); f.tr.clips = [clip('original', 0, 8, patch)]; f.seq.markers = [{ id: 'm0', time: 1 }, { id: 'm1', time: 4 }];
  return f;
}

test('source insert splits and shifts clips and annotations in one guarded request', () => {
  const f = sourceSetup(); f.seq.captions = [{ id: 'cap', start: 0.5, end: 2, text: 'caption' }];
  f.place({ mode: 'insert', at: 1, out: 1 }); assert.equal(f.requests.length, 1);
  const body = f.body(); assert.equal(body._context.project, 'folder-a'); assert.equal(body.tool, 'insert');
  assert.ok(body.ops.some(op => op.path.endsWith('/clips'))); assert.ok(body.ops.some(op => op.path.endsWith('/markers'))); assert.ok(body.ops.some(op => op.path.endsWith('/captions')));
  assert.deepEqual(f.seq.markers.map(m => m.time), [2, 5]);
  const parts = f.tr.clips.filter(c => c.id !== 'placed-1').sort((a, b) => a.start - b.start);
  assert.deepEqual(plain(parts.map(c => [c.start, c.in_, c.out])), [[0, 2, 3], [2, 3, 10]]);
});

test('insert shifts explicit destination even without sync lock, honors other sync and track locks', () => {
  const f = sourceSetup(); f.tr.sync_lock = false;
  f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [clip('audio', 2, 1)] }, { id: 'off', kind: 'video', sync_lock: false, clips: [clip('offclip', 2, 1)] }, { id: 'locked', kind: 'video', locked: true, clips: [clip('lockedclip', 2, 1)] });
  f.place({ mode: 'insert', at: 1 });
  assert.equal(f.seq.tracks[1].clips[0].start, 3); assert.equal(f.seq.tracks[2].clips[0].start, 2); assert.equal(f.seq.tracks[3].clips[0].start, 2);
  assert.equal(f.tr.clips.find(c => c.id === 'placed-1').start, 1);
});

test('overwrite preserves surviving reverse, hold and ramp source clocks plus automation', () => {
  for (const patch of [{ reverse: true }, { hold: true }, { time_remap: [{ t: 0, v: 1 }, { t: 2, v: 2 }] }, { reverse: true, time_remap: [{ t: 0, v: 1 }, { t: 2, v: 2 }] }]) {
    const f = sourceSetup({ ...patch, keyframes: { 'transform.x': [{ t: 0, v: 0, e: 'ease' }, { t: 8, v: 100 }] } }), before = plain(f.tr.clips[0]);
    f.place({ at: 1, out: 1 }); assert.equal(f.requests.length, 1);
    for (const part of f.tr.clips.filter(c => c.id !== 'placed-1')) for (let t = 0; t < f.scope.clipDur(part); t += 0.1) {
      near(source(f, part, t), source(f, before, part.start + t));
      near(f.scope.evaluateKeyframes(part.keyframes['transform.x'], t), f.scope.evaluateKeyframes(before.keyframes['transform.x'], part.start + t));
    }
  }
});

test('a partial picture transition rejection leaves clips markers selection and playhead untouched', () => {
  const f = sourceSetup({ transition_in: { type: 'dissolve', duration: 2 } }); f.scope.S.sel = new Set(['original']);
  const before = plain(f.project), position = f.scope.S.t;
  assert.equal(f.place({ mode: 'insert', at: 1 }), false); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  assert.deepEqual([...f.scope.S.sel], ['original']); assert.equal(f.scope.S.t, position); assert.match(f.messages.at(-1), /transition/);
});

test('sequential clipboard overwrites keep every previously planned surviving fragment', () => {
  const f = fixture(); f.tr.clips = [clip('one', 0, 1), clip('two', 2, 1)]; f.copy(['two', 'one']);
  f.tr.clips = [clip('cover', 0, 8)]; f.scope.S.t = 1; f.scope.pasteClips(); assert.equal(f.requests.length, 1);
  const ordered = [...f.tr.clips].sort((a, b) => a.start - b.start);
  assert.deepEqual(ordered.map(c => [c.start, f.scope.clipEnd(c)]), [[0, 1], [1, 2], [2, 3], [3, 4], [4, 8]]);
  for (const c of ordered.filter(c => !f.scope.S.sel.has(c.id))) near(c.in_, c.start + 2);
});

test('Paste Insert opens one whole group span and writes clips and annotations once', () => {
  const f = fixture(); f.tr.clips = [clip('one', 0, 1), clip('two', 2, 1)]; f.copy(['one', 'two']);
  f.tr.clips = [clip('cover', 0, 8)]; f.scope.S.t = 1; f.scope.pasteInsert();
  assert.equal(f.requests.length, 1); assert.equal(f.body().tool, 'paste_insert');
  const shifted = f.tr.clips.find(c => c.start === 4); assert.ok(shifted); near(shifted.in_, 3);
  assert.deepEqual(f.seq.markers.map(m => m.time), [4, 7]); assert.equal(f.scope.S.sel.size, 2);
});

test('clipboard snapshots do not alias pasted data and each paste remaps groups and internal links', async () => {
  const f = fixture(); f.tr.clips = [clip('one', 0, 1, { group: 'original-group', audio: { gain_db: -6 } }), clip('two', 2, 1, { group: 'original-group', unlinked_from: 'one' })];
  f.copy(['one', 'two']); const copied = f.scope.readClipboard(); f.scope.S.t = 10; f.scope.pasteClips();
  const first = f.tr.clips.filter(c => f.scope.S.sel.has(c.id)), group = first[0].group;
  assert.notEqual(group, 'original-group'); assert.equal(first[1].group, group); assert.equal(first[1].unlinked_from, first[0].id);
  first[0].audio.gain_db = 12; assert.deepEqual(f.scope.readClipboard(), copied);
  await f.save(); f.scope.S.t = 20; f.scope.pasteClips(); const second = f.tr.clips.filter(c => f.scope.S.sel.has(c.id));
  assert.equal(second[0].audio.gain_db, -6); assert.notEqual(second[0].group, group);
});

test('missing or locked clipboard destinations reject the entire paste without moving annotations', () => {
  for (const blocked of ['missing', 'locked', 'kind']) {
    const f = fixture(); f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [clip('audio', 1, 1)] }); f.copy(['a', 'audio']);
    if (blocked === 'missing') f.seq.tracks.pop(); else if (blocked === 'locked') f.seq.tracks[1].locked = true; else f.seq.tracks[1].kind = 'video';
    const before = plain(f.project), selection = [...f.scope.S.sel]; assert.equal(f.scope.pasteInsert(), false);
    assert.deepEqual(plain(f.project), before); assert.deepEqual([...f.scope.S.sel], selection); assert.equal(f.requests.length, 0);
  }
});

test('unknown source, wrong track kind and invalid bounds never produce a placement draft', () => {
  for (const options of [{ media: 'unknown' }, { track: 'missing' }, { in_: -1 }, { out: 101 }, { in_: 2, out: 1 }, { at: NaN }, { mode: 'unknown' }]) {
    const f = fixture(), before = plain(f.project); assert.equal(f.place(options), false); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  }
  const f = fixture(); f.project.media.m.has_video = false; assert.equal(f.place(), false); assert.equal(f.requests.length, 0);
});

test('nested source validation rejects self nesting, indirect cycles and missing dependencies', () => {
  for (const kind of ['self', 'cycle', 'indirect', 'missing']) {
    const f = fixture(); f.project.sequences.push({ id: 'nested', tracks: [{ id: 'nv', kind: 'video', clips: [clip('nestedclip', 0, 3)] }] });
    const nested = f.project.sequences.at(-1); if (kind === 'cycle') nested.tracks[0].clips[0] = { id: 'cycle', sequence_id: 'nested', start: 0, in_: 0, out: 3, speed: 1 };
    if (kind === 'indirect') {
      nested.tracks[0].clips[0] = { id: 'to-dependency', sequence_id: 'dependency', start: 0, in_: 0, out: 3, speed: 1 };
      f.project.sequences.push({ id: 'dependency', tracks: [{ id: 'dependency-track', kind: 'video', clips: [{ id: 'to-nested', sequence_id: 'nested', start: 0, in_: 0, out: 3, speed: 1 }] }] });
    }
    if (kind === 'missing') nested.tracks[0].clips[0].media_id = 'missing';
    assert.equal(f.place({ media: kind === 'self' ? 'seq:s1' : 'seq:nested' }), false); assert.equal(f.requests.length, 0);
  }
});

test('source monitor insertion rejects a retained media object from a different library', () => {
  const f = fixture(); f.scope.S.src = { ...f.project.media.m }; const before = plain(f.project);
  assert.equal(f.scope.insertFromSource('insert'), false); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 0);
  f.scope.S.src = f.project.media.m; f.scope.S.srcIn = 0; f.scope.S.srcOut = 1; f.scope.insertFromSource('insert'); assert.equal(f.requests.length, 1);
});

test('video-only placement stores its audio flag in the same request', () => {
  const f = fixture(); f.place({ options: { audioLinked: false } });
  assert.equal(f.requests.length, 1); assert.equal(f.tr.clips.find(c => c.id === 'placed-1').audio.linked, false);
});

test('fractional picture insert preserves source range and discloses frame padding', () => {
  const f = fixture(); f.seq.fps = 30000 / 1001; f.tr.clips = []; f.place({ mode: 'insert', at: 1.0123, in_: 0.2, out: 1.215 });
  const c = f.tr.clips[0]; near(c.start, time.fromFrames(30, f.seq.fps)); assert.equal(c.in_, 0.2); assert.equal(c.out, 1.215);
  const gap = time.fromFrames(Math.ceil(1.015 * 30000 / 1001), f.seq.fps); near(f.scope.S.t, c.start + gap); assert.match(f.messages.at(-1), /trailing space/);
});

test('audio-only insertion retains subframes when no picture track participates', () => {
  const f = fixture(); f.tr.kind = 'audio'; f.tr.clips = []; f.place({ mode: 'insert', at: 1.0123, out: 0.10123 });
  assert.equal(f.tr.clips[0].start, 1.0123); assert.equal(f.tr.clips[0].out, 0.10123); near(f.scope.S.t, 1.11353); assert.equal(f.messages.length, 0);
});

test('cross-project clipboard remaps only uniquely matching physical sources and rejects reused wrong IDs', () => {
  for (const mode of ['match', 'wrong', 'ambiguous']) {
    const f = fixture(); f.copy(['a']); const current = plain(f.project.media.m); f.scope.S.context = context('r0', 'project-b');
    f.project.media = mode === 'match' ? { renamed: { ...current, id: 'renamed' } } : mode === 'ambiguous' ? { one: current, two: current } : { m: { ...current, path: '/different/source.mov' } };
    f.tr.clips = []; const before = plain(f.project); f.scope.S.t = 10; const result = f.scope.pasteClips();
    if (mode === 'match') { assert.equal(f.requests.length, 1); assert.equal(f.tr.clips[0].media_id, 'renamed'); }
    else { assert.equal(result, false); assert.equal(f.requests.length, 0); assert.deepEqual(plain(f.project), before); }
  }
});

test('same-project changed media and cross-project nested clips require a fresh valid copy', () => {
  const f = fixture(); f.copy(['a']); f.project.media.m.path = '/changed.mov'; assert.equal(f.scope.pasteClips(), false); assert.equal(f.requests.length, 0);
  const g = fixture(); g.project.sequences.push({ id: 'nested', tracks: [] }); g.tr.clips[0] = { id: 'a', sequence_id: 'nested', start: 0, in_: 0, out: 1, speed: 1 }; g.copy(['a']);
  g.scope.S.context = context('r0', 'other'); assert.equal(g.scope.pasteClips(), false); assert.equal(g.requests.length, 0);
});

test('cut copies a deep snapshot and removes selected clips in one save; locked cuts preserve the existing clipboard', () => {
  const f = fixture(); f.copy(['a']); const initial = f.scope.readClipboard(); f.tr.locked = true; f.scope.S.sel = new Set(['b']);
  assert.equal(f.scope.cutSel(), false); assert.deepEqual(f.scope.readClipboard(), initial); assert.equal(f.requests.length, 0);
  f.tr.locked = false; f.scope.cutSel(); assert.equal(f.requests.length, 1); assert.equal(f.body().tool, 'cut'); assert.equal(f.tr.clips.some(c => c.id === 'b'), false);
  assert.equal(f.scope.readClipboard().items[0].clip.id, 'b'); assert.deepEqual(f.seq.markers.map(m => m.time), [1, 4]);
});

test('queued placements capture independent operations and advance from confirmed context without rebasing source', async () => {
  const f = fixture(); f.place({ at: 1 }); f.place({ at: 10 });
  assert.equal(f.requests.length, 1); const first = f.body(); assert.ok(first.ops[0].value.some(c => c.start === 1 && c.id === 'placed-1')); assert.equal(first.ops[0].value.some(c => c.start === 10), false);
  f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2); assert.equal(f.body(1)._context.revision, 'r1');
  assert.ok(f.body(1).ops[0].value.some(c => c.start === 10));
});

test('active gestures and uncertain saves refuse placement before changing clipboard selection or markers', () => {
  for (const kind of ['gesture', 'error']) {
    const f = fixture(); f.copy(['a']); const before = plain(f.project), selection = [...f.scope.S.sel];
    if (kind === 'gesture') f.scope.S.gesture = {}; else f.scope.projectSaveState().error = 'unknown outcome';
    assert.equal(f.place({ mode: 'insert' }), false); assert.equal(f.scope.pasteInsert(), false); assert.equal(f.scope.cutSel(), false);
    assert.deepEqual(plain(f.project), before); assert.deepEqual([...f.scope.S.sel], selection); assert.equal(f.requests.length, 0);
  }
});


test('mixed clipboard paste at zero keeps the picture grid and the audio-leading offset', () => {
  const f = fixture(); f.seq.fps = 30000 / 1001;
  f.tr.clips = [clip('picture', 0.005, 1)]; f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [clip('sound', 0, 1)] });
  f.copy(['picture', 'sound']); f.tr.clips = []; f.seq.tracks[1].clips = []; f.scope.S.t = 0;
  f.scope.pasteClips(); assert.equal(f.requests.length, 1);
  const picture = f.tr.clips[0], sound = f.seq.tracks[1].clips[0];
  near(picture.start, time.fromFrames(1, f.seq.fps)); near(picture.start - sound.start, 0.005); assert.ok(sound.start >= 0);
});

test('nested sources allow a shared dependency DAG without treating it as a cycle', () => {
  const f = fixture();
  const nested = (id, sources) => ({ id, tracks: [{ id: id + '-track', kind: 'video', clips: sources.map((sequence_id, n) => ({ id: id + n, sequence_id, start: n, in_: 0, out: 1, speed: 1 })) }] });
  f.project.sequences.push({ id: 'leaf', tracks: [{ id: 'leaf-track', kind: 'video', clips: [clip('leaf-source', 0, 2)] }] }, nested('left', ['leaf']), nested('right', ['leaf']), nested('dag', ['left', 'right']));
  f.place({ media: 'seq:dag', at: 10, out: 1 }); assert.equal(f.requests.length, 1);
  assert.equal(f.tr.clips.find(c => c.id === 'placed-1').sequence_id, 'dag');
});

test('copied detached audio pairs remap the parent receipt and child association together',()=>{
 const f=fixture();f.tr.clips=[clip('parent',0,2,{audio:{linked:false},audio_detached_id:'child'})];f.seq.tracks.push({id:'a1',kind:'audio',clips:[clip('child',0,2,{unlinked_from:'parent'})]});f.copy(['parent','child']);f.scope.S.t=10;f.scope.pasteClips();
 const parent=f.tr.clips.find(c=>f.scope.S.sel.has(c.id)),child=f.seq.tracks[1].clips.find(c=>f.scope.S.sel.has(c.id));assert.equal(parent.audio_detached_id,child.id);assert.equal(child.unlinked_from,parent.id);assert.notEqual(parent.id,'parent');assert.notEqual(child.id,'child');assert.equal(f.requests.length,1);
});


test('copying only one half of a detached pair creates an independent clip without borrowing its original partner',async()=>{
 for(const selected of ['parent','child']){
  const f=fixture();f.tr.clips=[clip('parent',0,2,{audio:{linked:false,gain_db:-3},audio_detached_id:'child'})];
  f.seq.tracks.push({id:'a1',kind:'audio',index:0,clips:[clip('child',0,2,{audio:{linked:true,gain_db:-3},unlinked_from:'parent'})]});
  const originals=plain(f.seq.tracks.map(t=>t.clips[0]));f.copy([selected]);f.scope.S.t=10;assert.notEqual(f.scope.pasteClips(),false);
  const pasted=f.seq.tracks.flatMap(t=>t.clips).find(c=>f.scope.S.sel.has(c.id));assert.ok(pasted);assert.equal(pasted.audio_detached_id,undefined);assert.equal(pasted.unlinked_from,undefined);
  assert.deepEqual(plain(f.seq.tracks.map(t=>t.clips[0])),originals);assert.equal(pasted.audio.linked,selected==='child');await f.save();
  if(selected==='parent'){
   const link=require('./helpers/link-match-fixture.cjs').fixture();Object.assign(link.seq,plain(f.seq));link.project.media=plain(f.project.media);link.select(pasted.id);link.scope.toggleLink();
   assert.equal(link.requests.length,1);assert.equal(link.seq.tracks[0].clips.find(c=>c.id===pasted.id).audio.linked,true);
   assert.deepEqual(plain(link.seq.tracks.map(t=>t.clips[0])),originals);assert.equal(link.seq.tracks[1].clips.length,1);
  }
 }
});
