const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, dragEvent, plain, context } = require('./helpers/placement-fixture.cjs');
function setup() {
  const f = fixture(); f.tr.clips = []; f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [] }); f.project.bins = [{ id: 'folder', name: 'Folder' }];
  f.scope.S.src = f.project.media.m; f.scope.S.srcIn = 2; f.scope.S.srcOut = 3; return { f, ui: f.dragUI() };
}

test('actual Source monitor video/audio drags preserve marked range and stream choice on Program drop', () => {
  for (const only of ['video', 'audio']) {
    const { f, ui } = setup(), ev = dragEvent(); assert.equal(ui[only].ondragstart(ev), true);
    f.scope.S.srcIn = 20; f.scope.S.srcOut = 30; f.scope.S.t = 10; ui.program.ondrop(ev);
    assert.equal(f.requests.length, 1); const track = f.seq.tracks[only === 'video' ? 0 : 1], c = track.clips[0];
    assert.equal(c.in_, 2); assert.equal(c.out, 3); assert.equal(c.start, 10); assert.equal(c.audio.linked, only !== 'video');
    assert.equal(f.scope.S.mediaDrag, null);
  }
});

test('actual Source drag rejects a retained source with a reused current-project ID before writing a payload', () => {
  const { f, ui } = setup(), ev = dragEvent(); f.scope.S.src = { ...f.project.media.m, path: '/old-project/video.mov' }; const before = plain(f.project);
  assert.equal(ui.video.ondragstart(ev), false); assert.equal(ev.prevented, true); assert.equal(ev.dataTransfer.types.length, 0);
  ui.program.ondrop(ev); assert.equal(f.requests.length, 0); assert.deepEqual(plain(f.project), before);
});

test('actual drop rejects project/workspace changes, media replacement or relink and ID-only payloads', () => {
  for (const mode of ['project', 'workspace', 'replace', 'relink', 'raw']) {
    const { f, ui } = setup(), ev = dragEvent(); ui.video.ondragstart(ev);
    if (mode === 'project') f.scope.S.context = context('r0', 'different-project');
    if (mode === 'workspace') f.scope.S.context.workspace = 'different-workspace';
    if (mode === 'replace') f.project.media.m = { ...f.project.media.m };
    if (mode === 'relink') f.project.media.m.path = '/another/source.mov';
    if (mode === 'raw') { ev.dataTransfer.clearData(); ev.dataTransfer.setData('text/media', 'm'); ev.dataTransfer.setData('text/only', 'video'); }
    const before = plain(f.project); ui.program.ondrop(ev); assert.equal(f.requests.length, 0, mode); assert.deepEqual(plain(f.project), before, mode);
  }
});

test('retained bin media and sequence drag callbacks cannot publish sources after project reload or replacement', () => {
  for (const kind of ['media', 'sequence']) for (const mode of ['project', 'source']) {
    const { f, ui } = setup(), element = kind === 'media' ? ui.media[0] : ui.sequences[1], ev = dragEvent();
    if (mode === 'project') { f.scope.S.proj = plain(f.project); f.scope.S.seq = f.scope.S.proj.sequences[0]; }
    else if (kind === 'media') f.project.media.m = { ...f.project.media.m }; else f.project.sequences[1] = plain(f.project.sequences[1]);
    assert.equal(element.ondragstart(ev), false); assert.equal(ev.dataTransfer.types.length, 0); assert.equal(f.requests.length, 0);
  }
});

test('bin-to-Program placement uses full bin source rather than unrelated Source monitor marks', () => {
  const { f, ui } = setup(), ev = dragEvent(); ui.media[0].ondragstart(ev); f.scope.S.t = 10; ui.program.ondrop(ev);
  assert.equal(f.requests.length, 1); const c = f.tr.clips[0]; assert.equal(c.in_, 0); assert.equal(c.out, 100); assert.equal(c.audio.linked, true);
});

test('actual row audio-only routing refuses video tracks atomically and accepts audio tracks', () => {
  for (const kind of ['video', 'audio']) {
    const { f, ui } = setup(), ev = dragEvent({ clientX: 10 }); ui.audio.ondragstart(ev); ui.row(f.seq.tracks[kind === 'video' ? 0 : 1]).ondrop(ev);
    assert.equal(f.requests.length, kind === 'audio' ? 1 : 0); if (kind === 'audio') { assert.equal(f.seq.tracks[1].clips[0].in_, 2); assert.equal(f.seq.tracks[1].clips[0].out, 3); }
  }
});

test('actual Shift drop inserts the captured range and shifts annotations in the same save', () => {
  const { f, ui } = setup(); f.tr.clips = [{ id: 'old', media_id: 'm', start: 0, in_: 0, out: 5, speed: 1 }];
  const ev = dragEvent({ shiftKey: true }); ui.video.ondragstart(ev); f.scope.S.t = 1; ui.program.ondrop(ev);
  assert.equal(f.requests.length, 1); assert.equal(f.body().tool, 'insert'); assert.ok(f.body().ops.some(o => o.path.endsWith('/markers'))); assert.equal(f.tr.clips.find(c => f.scope.S.sel.has(c.id)).out, 3);
});

test('actual folder drop uses source ownership and refuses retained destination folders', () => {
  for (const mode of ['valid', 'source', 'destination']) {
    const { f, ui } = setup(), ev = dragEvent(); ui.media[0].ondragstart(ev);
    if (mode === 'source') f.project.media.m = { ...f.project.media.m, path: '/new.mov' };
    if (mode === 'destination') { f.scope.S.proj = plain(f.project); f.scope.S.seq = f.scope.S.proj.sequences[0]; const current = f.dragUI(); current.media[0].ondragstart(ev); }
    ui.bins[1].ondrop(ev); assert.equal(f.requests.length, mode === 'valid' ? 1 : 0); if (mode === 'valid') assert.equal(f.project.media.m.bin, 'folder');
  }
});

test('drag end invalidates a captured payload and missing stream/range sources cannot begin a drag', () => {
  const { f, ui } = setup(), ev = dragEvent(); ui.video.ondragstart(ev); ui.video.ondragend(); ui.program.ondrop(ev); assert.equal(f.requests.length, 0);
  f.project.media.m.has_audio = false; assert.equal(ui.audio.ondragstart(dragEvent()), false);
  f.scope.S.srcOut = 200; assert.equal(ui.video.ondragstart(dragEvent()), false); assert.equal(f.requests.length, 0);
});


test('bin-folder organization accepts incomplete media metadata while timeline placement still rejects it', () => {
  for (const patch of [{ duration: 0 }, { duration: null, has_video: false, has_audio: false }]) {
    const { f, ui } = setup(); Object.assign(f.project.media.m, patch);
    const file = dragEvent(); assert.equal(ui.media[0].ondragstart(file), true); ui.bins[1].ondrop(file);
    assert.equal(f.requests.length, 1); assert.equal(f.project.media.m.bin, 'folder');
    const timeline = dragEvent(); assert.equal(ui.media[0].ondragstart(timeline), true); ui.program.ondrop(timeline);
    assert.equal(f.requests.length, 1); assert.equal(f.tr.clips.length, 0);
  }
});
