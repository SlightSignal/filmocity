const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, context, until, saved, read } = require('./helpers/render-replace-fixture.cjs');
const queued = (revision = 'r0') => ({ ok: true, task: { id: 'render-task', kind: 'render_replace', status: 'queued' }, context: context(revision) });

test('actual Render and Replace menu queues owned work without replacing clips or forcing a blocking confirmation', async () => {
  const f = fixture(), before = plain(f.project); f.scope.confirm = () => { throw Error('unexpected blocking confirmation'); };
  const pending = f.scope.renderReplace(); await until(() => f.requests.length === 1);
  assert.match(f.requests[0].url, /\/api\/render_replace$/); const body = f.body();
  assert.equal(body.sequence, 's1'); assert.equal(body.clip_id, 'a'); assert.equal(body._context.project, 'folder-a'); assert.equal(body._context.revision, 'r0'); assert.match(body.request_id, /^[a-f0-9]{32}$/);
  assert.deepEqual(plain(f.project), before); f.reply(0, queued()); await pending;
  assert.equal(f.opened(), 1); assert.equal(f.requests.length, 1); assert.deepEqual(plain(f.project), before); assert.match(f.messages.at(-1), /Review.*retained audio/); assert.equal(f.scope.S.commandPending, false);
});

test('queue waits for pending save acknowledgment and captures its resulting revision', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'save first' } }], 'note', 'note');
  const pending = f.scope.renderReplace(); assert.equal(f.requests.length, 1); f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2);
  assert.equal(f.body(1)._context.revision, 'r1'); assert.equal(f.tr.clips[0].note, 'save first'); f.reply(1, queued('r1')); await pending; assert.equal(f.requests.length, 2);
});

test('source, nested dependency, selection or project changes during the save wait reject before render submission', async () => {
  for (const mode of ['source', 'clip', 'selection', 'project', 'nested']) {
    const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'save first' } }], 'note', 'note'); const pending = f.scope.renderReplace();
    if (mode === 'source') f.project.media.m.path = '/relinked.mov';
    if (mode === 'clip') f.tr.clips[0].out = 9;
    if (mode === 'selection') f.scope.S.sel.clear();
    if (mode === 'project') f.scope.S.proj = plain(f.project);
    if (mode === 'nested') f.project.sequences.push({ id: 'new', tracks: [] });
    f.requests[0].resolve(saved('r1')); assert.equal(await pending, false, mode); assert.equal(f.requests.length, 1, mode); assert.match(f.messages.at(-1), /changed|Select/);
  }
});

test('locked, disabled, detached, dependent-compositing, stale and Recovery targets cannot queue', async () => {
  for (const mode of ['locked', 'disabled', 'detached-parent', 'detached-child', 'orphan-child', 'adjustment', 'blend', 'matte', 'missing', 'selection', 'duplicate', 'recovery', 'gesture']) {
    const f = fixture(), c = f.tr.clips[0];
    if (mode === 'locked') f.tr.locked = true;
    if (mode === 'disabled') c.enabled = false;
    if (mode === 'detached-parent') c.audio_detached_id = 'child';
    if (mode === 'detached-child') c.unlinked_from = 'parent';
    if (mode === 'orphan-child') f.seq.tracks.push({ id: 'a1', kind: 'audio', clips: [{ id: 'child', unlinked_from: c.id }] });
    if (mode === 'adjustment') c.adjustment = true;
    if (mode === 'blend') c.blend = 'multiply';
    if (mode === 'matte') c.fx_stack = [{ type: 'track_matte', enabled: true }];
    if (mode === 'missing') delete f.project.media.m;
    if (mode === 'selection') f.scope.S.sel.add('missing');
    if (mode === 'duplicate') f.tr.clips.push({ ...c });
    if (mode === 'recovery') f.scope.projectSaveState().error = 'unknown outcome';
    if (mode === 'gesture') f.scope.S.gesture = {};
    const before = plain(f.project); assert.equal(await f.scope.renderReplace(), false, mode); assert.equal(f.requests.length, 0, mode); assert.deepEqual(plain(f.project), before, mode);
  }
});

test('self-contained title, graphics, nested and held sources can be submitted for server bake validation', async () => {
  for (const mode of ['title', 'graphic', 'nested', 'hold']) {
    const f = fixture(), c = f.tr.clips[0];
    if (mode === 'title') { delete c.media_id; c.title = { text: 'Title', size: 50 }; }
    if (mode === 'graphic') { delete c.media_id; c.graphic = { layers: [{ kind: 'text', text: 'Graphic' }] }; }
    if (mode === 'nested') { delete c.media_id; c.sequence_id = 'nested'; f.project.sequences.push({ id: 'nested', tracks: [{ id: 'v', kind: 'video', clips: [{ id: 'inside', media_id: 'm', start: 0, in_: 0, out: 20, speed: 1 }] }] }); }
    if (mode === 'hold') c.hold = true;
    const before = plain(f.project), pending = f.scope.renderReplace(); await until(() => f.requests.length === 1); f.reply(0, queued()); await pending; assert.deepEqual(plain(f.project), before, mode);
  }
});

test('uncertain render submission reports Tasks inspection without replay or local mutation', async () => {
  for (const mode of ['offline', 'wrong-kind', 'wrong-project']) {
    const f = fixture(), before = plain(f.project), pending = f.scope.renderReplace(); await until(() => f.requests.length === 1);
    if (mode === 'offline') f.requests[0].reject(Error('lost reply'));
    if (mode === 'wrong-kind') f.reply(0, { ...queued(), task: { id: 'task', kind: 'analysis' } });
    if (mode === 'wrong-project') f.reply(0, { ...queued(), context: context('r0', 'other') });
    assert.equal(await pending, false); assert.match(f.messages.at(-1), /Check Tasks/); assert.equal(f.requests.length, 1); assert.equal(f.opened(), 0); assert.deepEqual(plain(f.project), before);
  }
});

test('late queue reply after owner switch neither replaces the new project nor opens its Tasks', async () => {
  const f = fixture(), pending = f.scope.renderReplace(); await until(() => f.requests.length === 1);
  const other = plain(f.project); f.scope.S.proj = other; f.scope.S.seq = other.sequences[0]; f.scope.S.context = context('r0', 'other');
  f.reply(0, queued()); await pending; assert.deepEqual(other, f.project); assert.equal(f.opened(), 0); assert.equal(f.requests.length, 1);
});

test('render replacement review is read-only and rejects foreign or malformed task/target/plan envelopes', async () => {
  const f = fixture(), before = plain(f.project), reviewed = await f.review(); assert.equal(reviewed.plan.fingerprint, 'render-fingerprint'); assert.deepEqual(plain(f.project), before);
  for (const mode of ['context', 'kind', 'status', 'sequence', 'clip', 'track', 'duration', 'plan']) {
    const g = fixture(); const pending = g.scope.reviewRenderReplaceTask('render-task'); await until(() => g.requests.length === 1);
    const response = plain(reviewed);
    if (mode === 'context') response.context = context('r8');
    if (mode === 'kind') response.task.kind = 'analysis';
    if (mode === 'status') response.task.status = 'applied';
    if (mode === 'sequence') response.result.sequence = 'other';
    if (mode === 'clip') response.result.clip_id = 'missing';
    if (mode === 'track') response.result.track_id = 'other';
    if (mode === 'duration') response.result.duration = 0;
    if (mode === 'plan') response.plan.ops = [];
    g.reply(0, response); await assert.rejects(pending, /not confirmed|missing|changed/); assert.equal(g.requests.length, 1);
  }
});

test('pending review rejects local target/source changes and project switches', async () => {
  for (const mode of ['clip', 'source', 'project']) {
    const f = fixture(), pending = f.review(); await until(() => f.requests.length === 1);
    if (mode === 'clip') f.tr.clips[0].start++;
    if (mode === 'source') f.project.media.m.path = '/changed.mov';
    if (mode === 'project') f.scope.S.context = context('r0', 'other');
    await assert.rejects(pending, /changed/); assert.equal(f.requests.length, 1);
  }
});

test('Apply rejects copied, modified and locally stale reviews without dispatching a mutation', async () => {
  for (const mode of ['copy', 'fingerprint', 'result', 'ops', 'source', 'clip', 'lock', 'context']) {
    const f = fixture(), reviewed = await f.review(); let value = reviewed;
    if (mode === 'copy') value = plain(reviewed);
    if (mode === 'fingerprint') reviewed.plan.fingerprint = 'forged';
    if (mode === 'result') reviewed.result.clip_id = 'another';
    if (mode === 'ops') reviewed.plan.ops[0].clip.media_id = 'forged';
    if (mode === 'source') f.project.media.m.path = '/changed.mov';
    if (mode === 'clip') f.tr.clips[0].out++;
    if (mode === 'lock') f.tr.locked = true;
    if (mode === 'context') { f.scope.S.context = context('r1'); f.scope.projectSaveState().context = context('r1'); }
    await assert.rejects(f.scope.applyRenderReplaceTask('render-task', value), /changed|review/i); assert.equal(f.requests.length, 1, mode);
  }
});

test('Apply sends only the reviewed fingerprint/context and observes the acknowledged saved replacement', async () => {
  const f = fixture(), reviewed = await f.review(), before = plain(f.project), pending = f.scope.applyRenderReplaceTask('render-task', reviewed);
  await until(() => f.requests.length === 2); const body = f.body(1); assert.equal(body.fingerprint, 'render-fingerprint'); assert.equal(body._context.revision, 'r0'); assert.equal(body.ops, undefined); assert.deepEqual(plain(f.project), before);
  f.reply(1, { ok: true, context: context('r1') }); await until(() => f.requests.length === 3);
  const savedProject = plain(f.project); savedProject.media.baked = { id: 'baked', duration: 3 }; savedProject.sequences[0].tracks[0].clips[0].media_id = 'baked'; f.requests[2].resolve(read(savedProject, 'r1')); await pending;
  assert.equal(f.scope.S.seq.tracks[0].clips[0].media_id, 'baked'); assert.equal(f.scope.S.context.revision, 'r1'); assert.equal(f.scope.S.commandPending, false);
});

test('Apply pauses Trim Edit without committing an unrelated trim before the guarded transaction', async () => {
  const f = fixture(), reviewed = await f.review(), stops = []; f.scope.S.playing = true;
  f.scope.togglePlay = (on, options) => { stops.push({ on, options }); f.scope.S.playing = on; };
  const pending = f.scope.applyRenderReplaceTask('render-task', reviewed); await until(() => f.requests.length === 2);
  assert.deepEqual(stops.map(x => plain(x)), [{ on: false, options: { commitTrim: false } }]); f.reply(1, { detail: 'stale' }, 409); await assert.rejects(pending, /stale/); assert.equal(f.requests.length, 2);
});

test('uncertain replacement Apply enters Recovery and never retries or publishes optimistic baked content', async () => {
  const f = fixture(), reviewed = await f.review(), before = plain(f.project), pending = f.scope.applyRenderReplaceTask('render-task', reviewed);
  await until(() => f.requests.length === 2); f.requests[1].reject(Error('lost reply')); await assert.rejects(pending, /Recovery/);
  assert.match(f.scope.projectSaveState().error, /not confirmed/); assert.deepEqual(plain(f.project), before); assert.equal(f.requests.length, 2);
});


test('a local change during final saved-context wait refuses before dispatch without claiming an uncertain server outcome', async () => {
  const f = fixture(), reviewed = await f.review(), pending = f.scope.applyRenderReplaceTask('render-task', reviewed);
  f.tr.clips[0].note = 'changed after review before dispatch';
  await assert.rejects(pending, /changed before replacement/); assert.equal(f.requests.length, 1); assert.equal(f.scope.projectSaveState().error, '');
});
