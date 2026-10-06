const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, context, until, saved, read } = require('./helpers/sync-workflow-fixture.cjs');

test('Synchronize queues captured clip IDs for explicit review without moving local clips', async () => {
  const f = fixture(), before = plain(f.project), pending = f.scope.synchronizeSel(); await until(() => f.requests.length === 1);
  assert.match(f.requests[0].url, /\/api\/audio\/sync$/); const body = f.body();
  assert.deepEqual(body.clip_ids, ['a', 'b']); assert.equal(body.media_ids, undefined); assert.equal(body.sequence, 's1'); assert.match(body.request_id, /^[a-f0-9]{32}$/);
  f.reply(0, f.queued()); await pending; assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 1); assert.equal(f.opened(), 1);
});

test('Synchronize waits for the previous save and uses the acknowledged revision', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'saved first' } }], 'note', 'note');
  const pending = f.scope.synchronizeSel(); assert.equal(f.requests.length, 1); f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2);
  assert.equal(f.body(1)._context.revision, 'r1'); f.reply(1, f.queued()); await pending;
});

test('selection, locks, source and owner changes during save wait reject before queue', async () => {
  for (const mode of ['selection', 'lock', 'source', 'parent', 'project', 'sequence']) {
    const f = fixture(); f.project.media.m.subclip_of = 'parent'; f.project.media.parent = { ...plain(f.project.media.m), id: 'parent', subclip_of: null };
    f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note'); const pending = f.scope.synchronizeSel();
    if (mode === 'selection') f.scope.S.sel.clear();
    if (mode === 'lock') f.second.locked = true;
    if (mode === 'source') f.project.media.n.duration++;
    if (mode === 'parent') f.project.media.parent.path = '/relinked.mov';
    if (mode === 'project') f.scope.S.proj = plain(f.project);
    if (mode === 'sequence') f.scope.S.seq = f.project.sequences[1];
    f.requests[0].resolve(saved('r1')); assert.equal(await pending, false, mode); assert.equal(f.requests.length, 1, mode);
  }
});

test('invalid or unsupported selected clips reject the whole selection without a request', async () => {
  for (const mode of ['locked', 'disabled', 'missing', 'same-track', 'no-audio', 'reverse', 'ramp', 'hold', 'Recovery', 'duplicate']) {
    const f = fixture(), c = f.second.clips[0];
    if (mode === 'locked') f.second.locked = true;
    if (mode === 'disabled') c.enabled = false;
    if (mode === 'missing') f.scope.S.sel.add('absent');
    if (mode === 'same-track') { f.tr.clips.push(c); f.second.clips = []; }
    if (mode === 'no-audio') f.project.media.n.has_audio = false;
    if (mode === 'reverse') c.reverse = true;
    if (mode === 'ramp') c.time_remap = [{ t: 0, v: 1 }];
    if (mode === 'hold') c.hold = true;
    if (mode === 'Recovery') f.scope.projectSaveState().error = 'unconfirmed';
    if (mode === 'duplicate') f.second.clips.push({ ...c });
    assert.equal(await f.scope.synchronizeSel(), false, mode); assert.equal(f.requests.length, 0, mode);
  }
});

test('a late queued response after A to B never edits B or opens its Tasks', async () => {
  const f = fixture(), before = plain(f.project), pending = f.scope.synchronizeSel(); await until(() => f.requests.length === 1); const reply = f.queued();
  const other = plain(f.project); f.scope.S.proj = other; f.scope.S.seq = other.sequences[0]; f.scope.S.context = context('r0', 'other');
  f.reply(0, reply); await pending; assert.deepEqual(other, before); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 1); assert.equal(f.opened(), 0);
});

test('lost or malformed queue replies never retry or produce zero-offset edits', async () => {
  for (const mode of ['lost', 'wrong-kind', 'wrong-owner']) {
    const f = fixture(), before = plain(f.project), pending = f.scope.synchronizeSel(); await until(() => f.requests.length === 1);
    if (mode === 'lost') f.requests[0].reject(Error('lost reply'));
    else { const value = f.queued(); if (mode === 'wrong-kind') value.task.kind = 'analysis'; else value.context = context('r0', 'other'); f.reply(0, value); }
    assert.equal(await pending, false); assert.match(f.messages.at(-1), /Check Tasks/); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 1);
  }
});

test('raw task polling reviews only a ready owned sync task and preserves exact offsets', async () => {
  const f = fixture(), queued = await f.queue('media'), pending = f.scope.waitSyncTask(queued); await until(() => f.requests.length === 2);
  f.reply(1, f.catalog('running')); await until(() => f.timers.length === 1); f.tick(); await until(() => f.requests.length === 3);
  f.reply(2, f.catalog()); await until(() => f.requests.length === 4); f.reply(3, f.result('media'));
  const reviewed = await pending; assert.equal(reviewed.result.offsets.n, -.2375); assert.match(f.requests[3].url, /\/sync$/); assert.equal(f.requests.length, 4);
});

test('raw polling stops after Cancel, source change, project switch or failed/foreign task', async () => {
  for (const mode of ['cancel', 'source', 'owner', 'error', 'foreign']) {
    const f = fixture(), queued = await f.queue('media'); let active = true; const pending = f.scope.waitSyncTask(queued, { isCurrent: () => active }); await until(() => f.requests.length === 2);
    const catalog = f.catalog(mode === 'error' ? 'error' : 'ready');
    if (mode === 'cancel') active = false;
    if (mode === 'source') f.project.media.n.path = '/changed.wav';
    if (mode === 'owner') f.scope.S.context = context('r0', 'other');
    if (mode === 'foreign') catalog.tasks[0].context = context('r0', 'other');
    f.reply(1, catalog); await assert.rejects(pending, /changed|Ambiguous|unavailable/); assert.equal(f.requests.length, 2, mode);
  }
});

test('review rejects malformed, incomplete, foreign or out-of-context synchronization envelopes', async () => {
  for (const mode of ['kind', 'version', 'clock', 'context', 'sequence', 'offset', 'reference', 'plan', 'raw-ops']) {
    const f = fixture(); await assert.rejects(f.review(mode === 'raw-ops' ? 'media' : 'timeline', value => {
      if (mode === 'kind') value.task.kind = 'analysis';
      if (mode === 'version') value.result.version = 2;
      if (mode === 'clock') value.result.clock = 'media';
      if (mode === 'context') value.context = context('r1');
      if (mode === 'sequence') value.result.sequence = 'other';
      if (mode === 'offset') delete value.result.offsets.b;
      if (mode === 'reference') value.result.offsets.a = 1;
      if (mode === 'plan') value.plan.fingerprint = '';
      if (mode === 'raw-ops') value.plan.ops.push({ op: 'set', path: '/name', value: 'wrong' });
    }), /confirmed|offset|different/, mode); assert.equal(f.requests.length, 1);
  }
});

test('review is read-only and late source or local changes cannot retain an Apply token', async () => {
  for (const mode of ['source', 'target', 'owner']) {
    const f = fixture(), pending = f.scope.reviewSyncTask('sync-task'); await until(() => f.requests.length === 1); const reply = f.result();
    if (mode === 'source') f.project.media.m.path = '/new.mov';
    if (mode === 'target') f.second.clips[0].start++;
    if (mode === 'owner') f.scope.S.proj = plain(f.project);
    f.reply(0, reply); await assert.rejects(pending, /changed/); assert.equal(f.requests.length, 1);
  }
});

test('Apply requires original reviewed envelope and unchanged targets, sources and locks', async () => {
  for (const mode of ['copy', 'fingerprint', 'offset', 'source', 'clip', 'locked', 'raw']) {
    const f = fixture(), reviewed = await f.review(mode === 'raw' ? 'media' : 'timeline'); let input = reviewed;
    if (mode === 'copy') input = plain(reviewed);
    if (mode === 'fingerprint') reviewed.plan.fingerprint = 'forged';
    if (mode === 'offset') reviewed.result.offsets.b = 0;
    if (mode === 'source') f.project.media.m.path = '/changed';
    if (mode === 'clip') f.second.clips[0].out++;
    if (mode === 'locked') f.second.locked = true;
    await assert.rejects(f.scope.applySyncTask('sync-task', input), /changed|review/i); assert.equal(f.requests.length, 1, mode);
  }
});

test('Apply sends fingerprint/context only, pauses Trim Edit without commit and refreshes saved move', async () => {
  const f = fixture(), reviewed = await f.review(), before = plain(f.project), stops = []; f.scope.S.playing = true;
  f.scope.togglePlay = (on, options) => { stops.push({ on, options: plain(options) }); f.scope.S.playing = on; };
  const pending = f.scope.applySyncTask('sync-task', reviewed); await until(() => f.requests.length === 2);
  assert.equal(f.body(1).fingerprint, 'sync-fingerprint'); assert.equal(f.body(1)._context.revision, 'r0'); assert.equal(f.body(1).ops, undefined); assert.deepEqual(plain(f.project), before);
  assert.deepEqual(stops, [{ on: false, options: { commitTrim: false } }]);
  f.reply(1, { ok: true, context: context('r1') }); await until(() => f.requests.length === 3); const savedProject = plain(f.project); savedProject.sequences[0].tracks[1].clips[0].start = 9.7625;
  f.requests[2].resolve(read(savedProject, 'r1')); await pending; assert.equal(f.scope.S.seq.tracks[1].clips[0].start, 9.7625);
});

test('final local change refuses Apply before dispatch; lost mutation response enters Recovery once', async () => {
  const f = fixture(), reviewed = await f.review(), pending = f.scope.applySyncTask('sync-task', reviewed); f.second.clips[0].note = 'late';
  await assert.rejects(pending, /changed/); assert.equal(f.requests.length, 1); assert.equal(f.scope.projectSaveState().error, '');
  const g = fixture(), r = await g.review(), before = plain(g.project), lost = g.scope.applySyncTask('sync-task', r); await until(() => g.requests.length === 2);
  g.requests[1].reject(Error('lost response')); await assert.rejects(lost, /Recovery/); assert.deepEqual(plain(g.project), before); assert.equal(g.requests.length, 2);
});

test('raw sequence creation refreshes source review then commits one full sequence without optimistic mutation', async () => {
  const f = fixture(), reviewed = await f.review('media'), before = plain(f.project), pending = f.scope.createSyncSequence(reviewed, { kind: 'multicam', media_ids: ['m', 'n'], name: 'Reviewed', sync: 'audio', audioMode: 'follow' });
  await until(() => f.requests.length === 2); assert.match(f.requests[1].url, /\/sync$/); f.reply(1, f.result('media')); await until(() => f.requests.length === 3);
  assert.deepEqual(plain(f.project), before); const body = f.body(2); assert.equal(body.ops.length, 1); assert.equal(body._context.revision, 'r0');
  const sequence = body.ops[0].value; assert.equal(sequence.fps, 30000 / 1001); assert.equal(sequence.multicam_audio, 'follow');
  const first = sequence.tracks.find(x => x.id === 'V1').clips[0], second = sequence.tracks.find(x => x.id === 'V2').clips[0];
  assert.equal(first.start, 7 * 1001 / 30000); assert.equal(second.start, 0); assert.equal(first.in_, 0); assert.equal(second.in_, 0); assert.equal(second.out, 20);
  assert.equal(new Set(sequence.tracks.flatMap(x => x.clips.map(c => c.id))).size, 4); await f.completeCreate(pending, 2); assert.equal(f.scope.S.proj.sequences.length, 3);
});

test('merged sequence uses fractional interpreted rate, complete source ranges and explicit reviewed offset', async () => {
  const f = fixture(); f.project.media.n.has_video = false; f.project.media.m.interpret_fps = '24000/1001'; const reviewed = await f.review('media');
  const pending = f.scope.createSyncSequence(reviewed, { kind: 'merge', media_ids: ['m', 'n'], name: 'Sound', sync: 'audio' }); await until(() => f.requests.length === 2);
  f.reply(1, f.result('media')); await until(() => f.requests.length === 3); const sq = f.body(2).ops[0].value;
  assert.equal(sq.fps, 24000 / 1001); assert.equal(sq.merged, true); assert.deepEqual(sq.tracks.map(t => t.id), ['V1', 'A1']); assert.equal(sq.tracks[0].clips[0].audio.linked, false); assert.equal(sq.tracks[1].clips[0].start, 0);
  await f.completeCreate(pending, 2);
});

test('changed raw review, source file refusal, cancellation and stale owner cannot create a partial sequence', async () => {
  for (const mode of ['fingerprint', 'source-file', 'cancel', 'owner']) {
    const f = fixture(), reviewed = await f.review('media'), before = plain(f.project); let active = true;
    const pending = f.scope.createSyncSequence(reviewed, { kind: 'multicam', media_ids: ['m', 'n'], sync: 'audio', isCurrent: () => active }); await until(() => f.requests.length === 2);
    if (mode === 'cancel') active = false;
    if (mode === 'owner') f.scope.S.context = context('r0', 'other');
    const reply = f.result('media'); if (mode === 'fingerprint') reply.plan.fingerprint = 'changed';
    if (mode === 'source-file') f.reply(1, { detail: 'Source file changed' }, 409); else f.reply(1, reply);
    await assert.rejects(pending, /changed|source|Source/); assert.equal(f.requests.length, 2, mode); assert.deepEqual(plain(f.project), before); assert.equal(f.scope.projectSaveState().error, '');
  }
});

test('in-point multicam creation has one guarded save, full media and no audio analysis', async () => {
  const f = fixture(); f.project.media.m.has_audio = false; const pending = f.scope.createMulticam('Starts', 'in', 'A1', ['m', 'n']); await until(() => f.requests.length === 1);
  assert.match(f.requests[0].url, /\/api\/project$/); const sq = f.body().ops[0].value; assert.equal(sq.fps, 30000 / 1001); assert.equal(sq.multicam_audio_track, 'A2'); assert.ok(sq.tracks.every(t => t.clips.every(c => c.start === 0 && c.in_ === 0)));
  await f.completeCreate(pending, 0);
});

test('multicam dialog waits for explicit Create after displaying reviewed offsets', async () => {
  const f = fixture(), s = f.scope; s.multicamDialog(); const pending = s.$('#mcGo').onclick(); await until(() => f.requests.length === 1); f.reply(0, f.queued());
  await until(() => f.requests.length === 2); f.reply(1, f.catalog()); await until(() => f.requests.length === 3); f.reply(2, f.result('media')); await pending;
  assert.match(s.$('#mcOut').textContent, /-0\.237500 s/); assert.match(s.$('#mcOut').textContent, /correlation/); assert.match(s.$('#mcOut').textContent, /grid residual -3\.933 ms/); assert.equal(s.$('#mcGo').textContent, 'Create reviewed multicam'); assert.equal(f.requests.length, 3);
  const create = s.$('#mcGo').onclick(); await until(() => f.requests.length === 4); f.reply(3, f.result('media')); await until(() => f.requests.length === 5); await f.completeCreate(create, 4);
  assert.equal(s.$('#dlgMulticam').classList.contains('open'), false); assert.match(f.messages.at(-1), /Created My multicam/);
});

test('merge dialog requires a separate reviewed creation click and never assumes zero on error', async () => {
  const f = fixture(), s = f.scope; f.project.media.n.has_video = false; s.mergeDialog(); const pending = s.$('#mgOk').onclick(); await until(() => f.requests.length === 1); f.reply(0, f.queued());
  await until(() => f.requests.length === 2); f.reply(1, f.catalog('error')); await pending; assert.equal(f.requests.length, 2); assert.equal(f.project.sequences.length, 2); assert.match(s.$('#mgInfo').textContent, /Ambiguous/);
  const g = fixture(); g.project.media.n.has_video = false; g.scope.mergeDialog(); const done = g.scope.$('#mgOk').onclick(); await until(() => g.requests.length === 1); g.reply(0, g.queued());
  await until(() => g.requests.length === 2); g.reply(1, g.catalog()); await until(() => g.requests.length === 3); g.reply(2, g.result('media')); await done;
  assert.equal(g.scope.$('#mgOk').textContent, 'Create reviewed merge'); assert.equal(g.requests.length, 3); assert.equal(g.project.sequences.length, 2);
});

test('dialog cancel, re-open, changed choices and project switches retire pending completion', async () => {
  for (const mode of ['cancel', 'reopen', 'choices', 'project']) {
    const f = fixture(), s = f.scope; s.multicamDialog(); const pending = s.$('#mcGo').onclick(); await until(() => f.requests.length === 1); const reply = f.queued();
    if (mode === 'cancel') s.$('#mcCancel').onclick();
    if (mode === 'reopen') s.multicamDialog();
    if (mode === 'choices') { s.$('#mcName').value = 'Changed'; s.$('#mcName').oninput(); }
    if (mode === 'project') { s.S.proj = plain(f.project); s.S.seq = s.S.proj.sequences[0]; s.S.context = context('r0', 'other'); }
    f.reply(0, reply); await pending; assert.equal(f.requests.length, 1, mode); assert.equal(s.S.proj.sequences.length, 2, mode);
  }
});


test('raw negative offsets disclose rational-frame and audio-sample residuals before creation', async () => {
  const f = fixture(), reviewed = await f.review('media', value => { value.result.offsets.n = -.123625; value.result.matches[1].offset = -.123625; });
  const alignment = plain(f.scope.previewSyncSequence(reviewed, { media_ids: ['m', 'n'] }));
  assert.equal(alignment.fps, 30000 / 1001); assert.equal(alignment.shift, .123625);
  assert.equal(alignment.placements[0].picture_start, 4 * 1001 / 30000); assert.equal(alignment.placements[0].audio_start, 5934 / 48000);
  assert.ok(Math.abs(alignment.placements[0].picture_residual - (4 * 1001 / 30000 - .123625)) < 1e-12);
  assert.equal(alignment.placements[1].picture_start, 0); assert.equal(alignment.placements[1].audio_start, 0);
  const text = f.scope.syncDialogReview(reviewed, { m: 'Camera', n: 'Other' }, alignment); assert.match(text, /grid residual 9\.842 ms/); assert.match(text, /48 kHz/);
});

test('lost read-only refresh before raw creation refuses without entering uncertain-edit Recovery', async () => {
  const f = fixture(), reviewed = await f.review('media'), before = plain(f.project);
  const pending = f.scope.createSyncSequence(reviewed, { kind: 'multicam', media_ids: ['m', 'n'], sync: 'audio' }); await until(() => f.requests.length === 2);
  f.requests[1].reject(Error('review network failed')); await assert.rejects(pending, /review network failed/); assert.equal(f.requests.length, 2); assert.equal(f.scope.projectSaveState().error, ''); assert.deepEqual(plain(f.project), before);
});

test('missing or malformed measurement quality is refused even when every offset is present', async () => {
  for (const mode of ['missing', 'confidence', 'correlation', 'source', 'offset']) {
    const f = fixture(); await assert.rejects(f.review('media', result => {
      if (mode === 'missing') result.result.matches.pop();
      if (mode === 'confidence') result.result.matches[1].confidence = null;
      if (mode === 'correlation') result.result.matches[1].correlation = 2;
      if (mode === 'source') result.result.matches[1].media_id = 'wrong';
      if (mode === 'offset') result.result.matches[1].offset = 1;
    }), /quality/); assert.equal(f.requests.length, 1);
  }
});


test('in-point multicam waits for pending edits and captures their acknowledged revision', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'save first' } }], 'note', 'note');
  const pending = f.scope.createMulticam('Starts', 'in', 'A1', ['m', 'n']); assert.equal(f.requests.length, 1); f.requests[0].resolve(saved('r1'));
  await until(() => f.requests.length === 2); assert.equal(f.body(1)._context.revision, 'r1'); assert.equal(f.body(1).ops.length, 1);
  await f.completeCreate(pending, 1);
});

test('lost sequence insertion response enters Recovery without publishing or replaying the sequence', async () => {
  const f = fixture(), reviewed = await f.review('media'), before = plain(f.project);
  const pending = f.scope.createSyncSequence(reviewed, { kind: 'multicam', media_ids: ['m', 'n'], sync: 'audio' }); await until(() => f.requests.length === 2); f.reply(1, f.result('media'));
  await until(() => f.requests.length === 3); f.requests[2].reject(Error('lost insertion acknowledgment'));
  await assert.rejects(pending, /Recovery/); assert.match(f.scope.projectSaveState().error, /not confirmed/); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 3);
});
