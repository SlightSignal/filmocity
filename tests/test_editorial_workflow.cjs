const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, until, context, saved, read } = require('./helpers/editorial-workflow-fixture.cjs');

test('actual Split words button sends one owned canonical command and adopts only saved fields', async () => {
  const f = fixture(), b = f.splitUI(), before = plain(f.project), pending = b.onclick();
  await until(() => f.requests.length === 1);
  assert.equal(b.disabled, true); assert.equal(b.attributes['aria-busy'], 'true'); assert.equal(await b.onclick(), false);
  assert.equal(f.requests[0].url, '/api/graphics/split_words');
  assert.deepEqual(f.body(), { sequence: 's1', clip_id: 'a', layer: 0, anim: { type: 'pop', duration: .35, ease: 'back_out' }, stagger: .12, _context: context(), actor: 'human', client: 'test-client' });
  assert.deepEqual(plain(f.project), before);
  const after = plain(before); after.sequences[0].tracks[0].clips[0].graphic.layers = [{ kind: 'text', text: 'Two' }, { kind: 'text', text: 'words' }];
  await f.ack(pending, after, { sequence: 's1', layers: 2, summary: { message: 'Split into 2 words.' }, warnings: ['Existing In animation replaced.'] });
  assert.equal(f.scope.S.seq.tracks[0].clips[0].graphic.layers.length, 2); assert.match(f.messages.at(-1), /Split into 2 words.*Existing In animation replaced/);
  assert.equal(f.scope.S.commandPending, false); assert.equal(f.requests.length, 2);
});

test('Extract Audio menu preserves source metadata and uses acknowledged media creation without optimistic changes', async () => {
  const f = fixture(), before = plain(f.project), pending = f.scope.extractAudio(); await until(() => f.requests.length === 1);
  assert.equal(f.requests[0].url, '/api/media/extract_audio'); assert.deepEqual(f.body(), { media_id: 'm', _context: context(), actor: 'human', client: 'test-client' });
  assert.deepEqual(plain(f.project), before);
  const after = plain(before); after.media.extracted = { ...after.media.m, id: 'extracted', has_video: false, extracted_from: 'm' };
  await f.ack(pending, after, { media_id: 'extracted', media: after.media.extracted, summary: { message: 'Created Source audio.' }, warnings: ['Preparation queued.'] });
  assert.deepEqual(plain(f.scope.S.proj.media.m), before.media.m); assert.equal(f.scope.S.proj.media.extracted.has_video, false); assert.match(f.messages.at(-1), /Created Source audio.*Preparation queued/);
});

test('both commands flush earlier saved changes and use their acknowledged revision', async () => {
  for (const kind of ['split', 'extract']) {
    const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending saved note' } }], 'note', 'note');
    const pending = kind === 'split' ? f.splitUI().onclick() : f.scope.extractAudio(); f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2);
    assert.equal(f.body(1)._context.revision, 'r1'); f.reply(1, { detail: 'controlled refusal' }, 422); await pending; assert.equal(!!f.scope.projectSaveState().error, false);
  }
});

test('pending split rejects changed selection, locks, detached controls, source clip or owner before dispatch', async () => {
  const changes = [f => f.scope.S.sel.clear(), f => f.tr.locked = true, (f, b) => b.isConnected = false, f => f.clips[0].graphic.layers[0].text = 'New words',
    f => { f.scope.S.proj = plain(f.project); f.scope.S.seq = f.scope.S.proj.sequences[0]; }, f => f.scope.S.context = context('r0', 'other'), f => f.scope.S.seq = f.project.sequences[1]];
  for (const change of changes) {
    const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note');
    const button = f.splitUI(), pending = button.onclick(); change(f, button); f.requests[0].resolve(saved('r1')); assert.equal(await pending, false); assert.equal(f.requests.length, 1);
  }
});

test('pending extraction rejects changed bin selection, selected media, parent source or same-ID replacement', async () => {
  for (const change of [f => f.scope.S.binSel.clear(), f => f.project.media.m.path = '/changed.mov', f => f.project.media.m = plain(f.project.media.m), f => f.project.media.parent.path = '/relinked.mov']) {
    const f = fixture(); f.project.media.parent = { ...plain(f.project.media.m), id: 'parent' }; f.project.media.m.subclip_of = 'parent';
    f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note'); const pending = f.scope.extractAudio(); change(f);
    f.requests[0].resolve(saved('r1')); assert.equal(await pending, false); assert.equal(f.requests.length, 1);
  }
});

test('Escape cancels either command while awaiting saves, and repeated invocation never queues a duplicate', async () => {
  for (const kind of ['split', 'extract']) {
    const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note');
    const run = () => kind === 'split' ? f.scope.splitGraphicWords(f.clips[0], f.tr, 0) : f.scope.extractAudio(); const pending = run(); assert.equal(await run(), false);
    f.escape(); f.requests[0].resolve(saved('r1')); assert.equal(await pending, false); assert.equal(f.requests.length, 1); assert.match(f.messages.at(-1), /canceled before submission/);
  }
});

test('locked, unavailable, wrong-stream, save-error and Recovery inputs refuse without a request', async () => {
  const changes = [f => f.tr.locked = true, f => f.scope.S.sel.clear(), f => f.scope.S.recoveryRequired = true, f => f.scope.S.gesture = {}, f => f.scope.projectSaveState().error = 'unknown', f => f.clips[0].graphic.layers[0].kind = 'shape'];
  for (const change of changes) { const f = fixture(); change(f); assert.equal(await f.scope.splitGraphicWords(f.clips[0], f.tr, 0), false); assert.equal(f.requests.length, 0); }
  for (const change of [f => f.project.media.m.has_audio = false, f => f.scope.S.binSel.add('missing'), f => f.project.media.m.subclip_of = 'missing']) {
    const f = fixture(); change(f); assert.equal(await f.scope.extractAudio(), false); assert.equal(f.requests.length, 0);
  }
});

test('lost or malformed mutation acknowledgement enters Recovery and never auto-replays', async () => {
  for (const kind of ['split', 'extract']) for (const lost of [true, false]) {
    const f = fixture(), before = plain(f.project), run = () => kind === 'split' ? f.scope.splitGraphicWords(f.clips[0], f.tr, 0) : f.scope.extractAudio();
    const pending = run(); await until(() => f.requests.length === 1); if (lost) f.requests[0].reject(Error('lost reply')); else f.reply(0, { ok: true, context: context('r1', 'wrong') });
    assert.equal(await pending, false); assert.match(f.scope.projectSaveState().error, /not confirmed/); assert.equal(await run(), false); assert.equal(f.requests.length, 1); assert.deepEqual(plain(f.project), before);
  }
});

test('late command acknowledgement after foreign project switch never reloads or publishes success there', async () => {
  for (const kind of ['split', 'extract']) {
    const f = fixture(), pending = kind === 'split' ? f.scope.splitGraphicWords(f.clips[0], f.tr, 0) : f.scope.extractAudio(); await until(() => f.requests.length === 1);
    const foreign = plain(f.project); f.scope.S.proj = foreign; f.scope.S.seq = foreign.sequences[0]; f.scope.S.context = context('r0', 'foreign'); const count = f.messages.length;
    f.reply(0, { ok: true, context: context('r1'), summary: { message: 'Saved original.' } }); assert.equal(await pending, false); assert.equal(f.requests.length, 1); assert.equal(f.messages.length, count); assert.deepEqual(plain(f.scope.S.proj), foreign);
  }
});

test('authoritative no-op reloads without optimistic history and pauses Trim playback without committing it', async () => {
  const f = fixture(), before = plain(f.project), calls = []; f.scope.S.playing = true;
  f.scope.togglePlay = (...args) => { calls.push(plain(args)); f.scope.S.playing = false; };
  const pending = f.scope.splitGraphicWords(f.clips[0], f.tr, 0); await until(() => f.requests.length === 1); assert.deepEqual(calls, [[false, { commitTrim: false }]]);
  f.reply(0, { ok: true, changed: false, context: context(), summary: { message: 'A single word needs no split.' } }); await until(() => f.requests.length === 2); f.requests[1].resolve(read(before)); await pending;
  assert.match(f.messages.at(-1), /single word/); assert.deepEqual(plain(f.scope.S.proj), before);
});

test('Cut Summary sends exact saved owner and validates the acknowledged sequence response', async () => {
  const f = fixture(), pending = f.summary(); await until(() => f.requests.length === 1);
  const url = new URL(f.requests[0].url, 'http://local'); assert.equal(url.pathname, '/api/sequence/describe');
  assert.deepEqual(Object.fromEntries(url.searchParams), { sequence: 's1', ...context() });
  f.reply(0, { sequence: 's1', context: context(), text: 'Canonical ramp: 2.750000s', duration: 2.75 }); await pending;
  assert.equal(f.scope.$('#cutSummary').textContent, 'Canonical ramp: 2.750000s'); assert.equal(f.scope.$('#cutSummary').attributes['aria-busy'], undefined);
});

test('Cut Summary awaits pending saves and read failures never enter Recovery', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note'); const pending = f.summary();
  f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2); assert.match(f.requests[1].url, /revision=r1/);
  f.requests[1].reject(Error('summary unavailable')); await pending; assert.match(f.scope.$('#cutSummary').textContent, /summary unavailable/); assert.equal(!!f.scope.projectSaveState().error, false);
});

test('Cut Summary retires older, detached and foreign callbacks without replacing current text', async () => {
  const f = fixture(), first = f.summary(); await until(() => f.requests.length === 1); const second = f.summary(); await until(() => f.requests.length === 2);
  f.reply(1, { sequence: 's1', context: context(), text: 'Latest' }); await second; f.reply(0, { sequence: 's1', context: context(), text: 'Old' }); await first; assert.equal(f.scope.$('#cutSummary').textContent, 'Latest');
  for (const change of [f => f.scope.$('#cutSummary').isConnected = false, f => f.scope.S.seq = f.project.sequences[1], f => { f.scope.S.proj = plain(f.project); f.scope.S.seq = f.scope.S.proj.sequences[0]; }, f => f.scope.S.context = context('r0', 'foreign')]) {
    const f = fixture(), pending = f.summary(); await until(() => f.requests.length === 1); change(f); f.scope.$('#cutSummary').textContent = 'New owner'; f.reply(0, { sequence: 's1', context: context(), text: 'Stale' }); await pending; assert.equal(f.scope.$('#cutSummary').textContent, 'New owner');
  }
});

test('Cut Summary refuses foreign revision and detects edits made while the read is pending', async () => {
  for (const change of [f => f.clips[0].note = 'new local edit', f => f.scope.S.context = context('r1')]) {
    const f = fixture(), pending = f.summary(); await until(() => f.requests.length === 1); change(f); f.reply(0, { sequence: 's1', context: context(), text: 'Old read' }); await pending; assert.match(f.scope.$('#cutSummary').textContent, /changed/); assert.equal(!!f.scope.projectSaveState().error, false);
  }
  const f = fixture(), pending = f.summary(); await until(() => f.requests.length === 1); f.reply(0, { sequence: 's1', context: context('foreign'), text: 'Wrong revision' }); await pending; assert.match(f.scope.$('#cutSummary').textContent, /did not confirm/);
});

test('existing Source color inspector still saves the physical parent through the actual guarded edit queue', async () => {
  const color = require('../frontend/media-color.js'), dom = require('./dom_fixture.cjs');
  const f = fixture(), d = dom.fixture(['host']); d.nodes.host.ownerDocument = d.document; d.document.body = {};
  f.project.media.sub = { ...plain(f.project.media.m), id: 'sub', subclip_of: 'm', sub_in: 7, duration: 3, input_transform: 'vlog' };
  f.project.media.m.custom = { keep: true }; const beforeSub = plain(f.project.media.sub);
  const view = color.mount(f.scope.CR, d.nodes.host, 'sub'); view.transform.value = 'slog3';
  assert.match(view.note.textContent, /parent source and its subclips/);
  const pending = view.form.events.submit({ preventDefault() {} }); await until(() => f.requests.length === 1);
  assert.equal(f.requests[0].url, '/api/project'); assert.equal(f.requests[0].options.method, 'PATCH');
  const body = f.body(); assert.equal(body.ops.length, 1); assert.equal(body.ops[0].path, '/media/m'); assert.equal(body.ops[0].value.input_transform, 'slog3'); assert.deepEqual(body.ops[0].value.custom, { keep: true });
  assert.deepEqual(plain(f.project.media.sub), beforeSub); assert.deepEqual(body._context, context());
  f.requests[0].resolve(saved('r1')); await pending; assert.equal(f.scope.projectSaveState().error, '');
  const fresh = color.mount(f.scope.CR, d.nodes.host, 'sub'); fresh.transform.value = 'clog3'; fresh.form.isConnected = false;
  await fresh.form.events.submit({ preventDefault() {} }); assert.equal(f.requests.length, 1);
});

test('extracted interpreted subclip preview uses its own proxy owner and complete native source clock', () => {
  const f = fixture(), { alias, video, availability } = f.aliasPreview(), proxy = require('../frontend/proxy-preview.js');
  const url = new URL(video.src, 'http://local'); assert.equal(url.pathname, '/api/media/file/audio');
  assert.equal(url.searchParams.get('generation'), 'audio-generation'); assert.equal(url.searchParams.get('lease'), 'audio-proxy'); assert.equal(url.searchParams.get('proxy'), '1');
  assert.equal(f.scope.S.src, alias); assert.equal(video.currentTime, 3); f.scope.seekSourceTime(2); assert.equal(video.currentTime, 4); assert.equal(f.scope.sourceLogicalTime(alias, 4), 2);
  assert.equal(f.scope.window.FilmocitySourceClock.interpretationFactor(alias), 2);
  assert.equal(proxy.label(alias, availability), 'AAC stereo preview (lossy)'); assert.equal(proxy.availabilityMessage(alias, availability), 'AAC stereo preview (lossy)');
  assert.equal(f.project.media.m.id, 'm'); assert.equal(f.project.media.m.proxy, undefined);
});

test('audio source presentation hides movie pixels without overriding Prefer original and restores video on selection or project clear', async () => {
  const f = fixture(), { alias, video } = f.aliasPreview(), s = f.scope;
  assert.equal(video.style.visibility, 'hidden'); assert.equal(video.attributes['aria-hidden'], 'true');
  assert.match(s.$('#srcAudioLabel').textContent, /Audio-only source.*Selected range/); assert.equal(s.$('#srcAudioWave').style.display, 'none');
  s.S.useProxy = false; const original = new URL(s.sourceMediaUrl(alias), 'http://local'); assert.equal(original.pathname, '/api/media/file/audio'); assert.equal(original.searchParams.get('proxy'), '0'); assert.equal(original.searchParams.get('lease'), 'audio-original');
  alias.wave = '/waves/audio-token.png'; s.updateSourcePresentation(); assert.equal(s.$('#srcAudioWave').style.display, 'block'); assert.equal(s.$('#srcAudioWaveImage').style.width, '500%'); assert.equal(s.$('#srcAudioWaveImage').style.left, '-150%');
  const staleError = s.$('#srcAudioWaveImage').onerror; s.loadSource('m'); assert.equal(video.style.visibility, ''); assert.equal(video.attributes['aria-hidden'], undefined); assert.equal(s.$('#srcAudioPreview').style.display, 'none'); staleError();
  s.loadSource('audio'); const pending = s.loadProject(false); await until(() => f.requests.length === 1);
  f.reply(0, { project: plain(f.project), context: context('r0', 'new-project') }); await pending;
  assert.equal(s.S.src, null); assert.equal(video.style.visibility, ''); assert.equal(s.$('#srcAudioPreview').style.display, 'none'); assert.equal(s.$('#srcAudioLabel').textContent, '');
});

test('Source hides stale alias waveform and timeline maps only verified forward clocks without lying about retimed audio', () => {
  const vm = require('node:vm'), base = require('./helpers/editorial-workflow-fixture.cjs'), f = fixture(), { alias, availability } = f.aliasPreview(), s = f.scope;
  alias.wave = '/waves/audio-token.png'; s.updateSourcePresentation(); assert.equal(s.$('#srcAudioWave').style.display, 'block');
  availability.proxy_state = 'stale_source'; s.updateSourcePresentation(); assert.equal(s.$('#srcAudioWave').style.display, 'none'); assert.match(s.$('#srcAudioLabel').textContent, /Audio-only source/);
  s.S.tlopt = { waves: true }; s.S.agentHot = {}; s.fmtTC = value => String(value); s.document.createElement = () => Object.assign(base.node(), { innerHTML: '' });
  vm.runInContext(base.section('function clipEl(', 'function refreshSel('), s);
  const c = { id: 'alias-clip', media_id: 'audio', start: 0, in_: 1, out: 3, speed: 2 }, track = { id: 'a1', kind: 'audio', index: 1, clips: [c] }; s.S.seq.tracks.push(track);
  assert.doesNotMatch(s.clipEl(c, track, false).innerHTML, /class="wave"/);
  availability.proxy_state = 'ready'; const shown = s.clipEl(c, track, false);
  assert.match(shown.innerHTML, /background-size:600px 100%;background-position:-210px 0/);
  for (const patch of [{ reverse: true }, { hold: true }, { time_remap: [{ t: 0, v: 1 }, { t: 1, v: 2 }] }]) {
    const hidden = s.clipEl({ ...c, ...patch }, track, false); assert.doesNotMatch(hidden.innerHTML, /class="wave"/); assert.match(hidden.title, /rendered audio review/);
  }
  availability.source_id = 'm'; assert.doesNotMatch(s.clipEl(c, track, false).innerHTML, /class="wave"/);
});

test('Project bin offers owned Prepare for unprepared/stale audio aliases, preserves ordinary stale refusal and targets their parent', async () => {
  const vm = require('node:vm'), base = require('./helpers/editorial-workflow-fixture.cjs'), f = fixture(), { alias, availability } = f.aliasPreview(), s = f.scope;
  s.FilmocityMediaColor = require('../frontend/media-color.js'); s.window.FilmocityMediaInfo = require('../frontend/media-info.js'); s.window.FilmocityWelcome = { renderWelcome() {} };
  s.fmtTC = value => String(value); s.wireBin = () => {};
  vm.runInContext(base.section('const seqDurOf =', 'const frame =') + base.section('function renderBin()', 'function wireBin('), s);
  alias.status = 'unprepared'; delete alias.proxy; availability.proxy_available = false; availability.proxy_state = 'none'; s.renderBin();
  let row = s.$('#bin').innerHTML.split('data-id="audio"')[1]; assert.match(row, /data-prepare="audio"/); assert.match(row, /Prepare media/);
  availability.proxy_state = 'stale_source'; s.renderBin(); row = s.$('#bin').innerHTML.split('data-id="audio"')[1];
  assert.match(row, /data-relink="m"/); assert.doesNotMatch(row, /data-relink="audio"/); assert.match(row, /shared original source/);
  assert.match(row, /data-prepare="audio"/); assert.match(row, /accepted shared source/); assert.match(row, /Unaccepted file changes or changed interpretation will refuse/);
  alias.status = 'ready'; alias.proxy = '/old-audio.m4a'; s.S.mediaAvailability.m.proxy_state = 'stale_source'; s.renderBin();
  assert.doesNotMatch(s.$('#bin').innerHTML, /data-prepare="m"/); assert.match(s.$('#bin').innerHTML, /data-prepare="audio"[^>]*>Rebuild proxy/);
  const button = s.$('#aliasPrepare'); Object.assign(button, { dataset: { prepare: 'audio' }, disabled: false });
  const bin = s.$('#bin'); s.$$ = (selector, owner) => selector === '[data-prepare]' && owner === bin ? [button] : [];
  vm.runInContext(base.section('async function prepareMedia(', 'function importXml(') + base.section('function wireBin(', 'function trackRows('), s);
  s.wireBin(bin); const before = plain(f.project), pending = button.onclick({ preventDefault() {}, stopPropagation() {} });
  await until(() => f.requests.length === 1); assert.equal(button.disabled, true);
  assert.equal(f.requests[0].url, '/api/tasks/media/audio/prepare'); assert.deepEqual(f.body(), { _context: context() });
  f.reply(0, { detail: 'Source file bytes changed; relink the original source before preparing.' }, 409); await pending;
  assert.match(f.messages.at(-1), /Preparation could not start:.*Source file bytes changed/);
  assert.equal(button.disabled, false); assert.equal(!!s.projectSaveState().error, false); assert.deepEqual(plain(f.project), before);
  assert.equal(f.requests.length, 1);
});
