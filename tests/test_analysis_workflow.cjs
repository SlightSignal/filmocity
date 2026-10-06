const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, context, until, saved, read } = require('./helpers/analysis-fixture.cjs');

test('Scene and silence menu callbacks submit captured managed tasks without changing a clip', async () => {
  for (const kind of ['sceneDetect', 'removeSilences']) {
    const f = fixture(); f.scope.prompt = () => kind === 'sceneDetect' ? '.35' : '-38,.45'; const before = plain(f.project);
    const promise = f.scope[kind](); await until(() => f.requests.length === 1);
    const body = f.body(); assert.equal(body.clip_id, 'a'); assert.equal(body.sequence, 's1'); assert.equal(body.in, 5); assert.equal(body.out, 8); assert.equal(body._context.project, 'folder-a'); assert.match(body.request_id, /^[a-f0-9]{32}$/);
    assert.deepEqual(plain(f.project), before); assert.equal(f.requests[0].options.method, 'POST');
    f.reply(0, { ok: true, task: { id: 'queued-task', kind: 'analysis' }, context: context() }); await promise;
    assert.deepEqual(plain(f.project), before); assert.equal(f.opened(), 1); assert.equal(f.requests.length, 1); assert.equal(f.scope.S.commandPending, false);
  }
});

test('cancel, invalid settings, locks, held frames, missing media and Recovery state never start analysis', async () => {
  for (const kind of ['cancel', 'invalid', 'locked', 'hold', 'missing', 'error', 'gesture']) {
    const f = fixture(); const before = plain(f.project);
    if (kind === 'cancel') f.scope.prompt = () => null;
    if (kind === 'invalid') f.scope.prompt = () => 'NaN';
    if (kind === 'locked') f.tr.locked = true;
    if (kind === 'hold') f.tr.clips[0].hold = true;
    if (kind === 'missing') delete f.project.media.m;
    if (kind === 'error') f.scope.projectSaveState().error = 'unknown outcome';
    if (kind === 'gesture') f.scope.S.gesture = {};
    assert.equal(await f.scope.sceneDetect(), false); assert.equal(f.requests.length, 0, kind);
  }
});

test('submission saves pending edits first then refuses changed source clips', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'saved first' } }], 'note', 'note');
  const promise = f.scope.sceneDetect(); assert.equal(f.requests.length, 1);
  f.tr.clips[0].out = 9; f.requests[0].resolve(saved('r1')); await promise;
  assert.equal(f.requests.length, 1); assert.match(f.messages.at(-1), /source clip changed/);
});

test('unconfirmed submission tells the editor to check Tasks and never edits the project', async () => {
  const f = fixture(), before = plain(f.project), promise = f.scope.sceneDetect(); await until(() => f.requests.length === 1);
  f.requests[0].reject(Error('offline')); assert.equal(await promise, false); assert.match(f.messages.at(-1), /Check Tasks/); assert.deepEqual(plain(f.project), before);
});

test('completed submission after a project switch cannot mutate or open Tasks over the new project', async () => {
  const f = fixture(), promise = f.scope.sceneDetect(); await until(() => f.requests.length === 1);
  const other = plain(f.project); f.scope.S.proj = other; f.scope.S.seq = other.sequences[0]; f.scope.S.context = context('r0', 'other');
  const before = plain(other); f.reply(0, { ok: true, task: { id: 'task' }, context: context() }); await promise;
  assert.deepEqual(plain(other), before); assert.equal(f.opened(), 0); assert.equal(f.requests.length, 1);
});

test('review is read-only and a response for the wrong sequence or context is rejected', async () => {
  const f = fixture(), before = plain(f.project), review = await f.review(); assert.equal(review.plan.fingerprint, 'review-fingerprint'); assert.deepEqual(plain(f.project), before);
  for (const patch of [{ context: context('r8') }, { result: { media_id: 'm', sequence: 's2' } }, { task: { id: 'wrong' } }]) {
    const g = fixture(); await assert.rejects(g.review(patch), /not confirmed/); assert.equal(g.requests.length, 1);
  }
});

test('review rejects an optimistic local change or a project switch while the server response is pending', async () => {
  for (const mode of ['local', 'project']) {
    const f = fixture(), promise = f.review(); await until(() => f.requests.length === 1);
    if (mode === 'local') f.tr.clips[0].start += 1;
    else f.scope.S.context = context('r0', 'other');
    await assert.rejects(promise, /edit changed/); assert.equal(f.requests.length, 1);
  }
});

test('Apply refuses unreviewed or changed-source/local-sequence results before sending any mutation', async () => {
  for (const mode of ['source', 'clip', 'context', 'copy', 'mutated']) {
    const f = fixture(), reviewed = await f.review();
    if (mode === 'source') f.project.media.m.path = '/different.mov';
    if (mode === 'clip') f.tr.clips[0].start++;
    if (mode === 'context') { f.scope.S.context = context('r1'); f.scope.projectSaveState().context = context('r1'); }
    if (mode === 'mutated') reviewed.plan.summary.message = 'different from reviewed';
    await assert.rejects(f.scope.applyAnalysisTask('task', mode === 'copy' ? plain(reviewed) : reviewed), /changed|review/i); assert.equal(f.requests.length, 1, mode);
  }
});

test('Apply sends only the reviewed fingerprint and context through the guarded server transaction', async () => {
  const f = fixture(), reviewed = await f.review(), promise = f.scope.applyAnalysisTask('task', reviewed);
  await until(() => f.requests.length === 2); const body = f.body(1); assert.equal(body.fingerprint, 'review-fingerprint'); assert.equal(body._context.revision, 'r0'); assert.equal(body.ops, undefined);
  f.reply(1, { ok: true, context: context('r1') }); await until(() => f.requests.length === 3);
  f.requests[2].resolve(read(f.project, 'r1')); await promise; assert.equal(f.scope.S.context.revision, 'r1'); assert.equal(f.scope.S.commandPending, false);
});

test('an uncertain Apply reply enters Recovery and does not automatically replay the operation', async () => {
  const f = fixture(), reviewed = await f.review(), before = plain(f.project), promise = f.scope.applyAnalysisTask('task', reviewed);
  await until(() => f.requests.length === 2); f.requests[1].reject(Error('lost reply'));
  await assert.rejects(promise, /Recovery/); assert.match(f.scope.projectSaveState().error, /not confirmed/); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 2);
});


test('raw SDK media analysis can be reviewed without a timeline plan or Apply binding', async () => {
  for (const kind of ['scenes', 'silences', 'remix']) {
    const f = fixture(), before = plain(f.project);
    const reviewed = await f.review({ result: { version: 1, kind, clock: 'media', media_id: 'm', sequence: null, clip_id: null, cuts: [2], silences: [{ start: 2, end: 3 }] }, plan: undefined });
    assert.equal(reviewed.plan, undefined); assert.equal(reviewed.result.kind, kind); assert.deepEqual(plain(f.project), before);
    await assert.rejects(f.scope.applyAnalysisTask('task', reviewed), /Review this analysis/);
    // Adding client-side plan fields cannot turn a raw result into an accepted timeline review.
    reviewed.result.sequence = 's1'; reviewed.result.clip_id = 'a'; reviewed.plan = { fingerprint: 'invented', ops: [{}] };
    await assert.rejects(f.scope.applyAnalysisTask('task', reviewed), /Refresh this analysis review/);
    assert.equal(f.requests.length, 1); assert.deepEqual(plain(f.project), before);
  }
});

test('remix submits the selected audio window after saved context and never edits on completion', async () => {
  const f = fixture(); f.tr.kind = 'audio'; const before = plain(f.project);
  const promise = f.scope.startClipAnalysis('remix', { target: 6, bars_per_phrase: 4 });
  await until(() => f.requests.length === 1); const body = f.body();
  assert.equal(f.requests[0].url, '/api/audio/remix'); assert.equal(body.target, 6); assert.equal(body.in, 5); assert.equal(body.out, 8);
  f.reply(0, { ok: true, task: { id: 'remix-task', kind: 'analysis' }, context: context() }); await promise;
  assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 1);
  const g = fixture(); await assert.rejects(g.scope.startClipAnalysis('remix', { target: 6 }), /audio track/); assert.equal(g.requests.length, 0);
});

test('reviewed analysis Apply stops Trim Edit playback without committing a trim', async () => {
  const f = fixture(), reviewed = await f.review(), before = plain(f.project), stops = [];
  f.scope.S.playing = true; f.scope.togglePlay = (playing, options) => { stops.push({ playing, options: plain(options) }); f.scope.S.playing = playing; };
  const promise = f.scope.applyAnalysisTask('task', reviewed); await until(() => f.requests.length === 2);
  assert.deepEqual(stops, [{ playing: false, options: { commitTrim: false } }]); assert.deepEqual(plain(f.project), before);
  f.reply(1, { ok: true, context: context('r1') }); await until(() => f.requests.length === 3); f.requests[2].resolve(read(f.project, 'r1')); await promise;
});
test('a final local change before analysis dispatch refuses without mutation or false Recovery', async () => {
  const f = fixture(), reviewed = await f.review(), promise = f.scope.applyAnalysisTask('task', reviewed);
  f.tr.clips[0].note = 'Changed before dispatch';
  await assert.rejects(promise, /changed before analysis Apply/); assert.equal(f.requests.length, 1); assert.equal(f.scope.projectSaveState().error, '');
});
test('actual sequence switching immediately invalidates task reviews only after accepting the switch', () => {
  const f = fixture(); let invalidated = 0; f.scope.window.FilmocityTasks.invalidateReviews = () => invalidated++;
  f.scope.S.workflowBusy = true; f.scope.switchSeq('s2'); assert.equal(invalidated, 0); assert.equal(f.scope.S.seq.id, 's1');
  f.scope.S.workflowBusy = false; f.scope.switchSeq('absent'); assert.equal(invalidated, 0);
  f.scope.switchSeq('s2'); assert.equal(invalidated, 1); assert.equal(f.scope.S.seq.id, 's2'); assert.equal(f.requests.length, 0);
});

test('raw analysis review still rejects a foreign context, unsupported clock or unsolicited timeline plan', async () => {
  for (const change of ['context', 'clock', 'plan']) {
    const f = fixture(), response = { result: { version: 1, kind: 'scenes', clock: 'media', media_id: 'm', sequence: null, clip_id: null }, plan: undefined };
    if (change === 'context') response.context = context('r0', 'other');
    if (change === 'clock') response.result.clock = 'relative';
    if (change === 'plan') response.plan = { fingerprint: 'unexpected', ops: [] };
    await assert.rejects(f.review(response), /not confirmed/); assert.equal(f.requests.length, 1);
  }
});
