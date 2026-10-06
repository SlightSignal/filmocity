const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const sync = require('../frontend/project-sync.js');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing production section: ${start}`);
  return source.slice(a, b);
}
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { resolve, reject, promise }; }
function response(status, body) { return { ok: status >= 200 && status < 300, status, json: async () => body }; }
const context = (project = 'folder-a', revision = 'r0') => ({ workspace: 'workspace', project, revision });
const project = (name = 'Saved name') => ({ id: 'shared-document-id', name, media: {}, sequences: [{ id: 'seq1', tracks: [] }] });
const read = (doc = project(), ctx = context()) => response(200, { project: doc, context: ctx });
const saved = (revision = 'r1', extra = {}, folder = 'folder-a') => response(200, { ok: true, context: context(folder, revision), ...extra });
async function until(predicate) { for (let i = 0; i < 50 && !predicate(); i++) await Promise.resolve(); assert.ok(predicate(), 'Expected asynchronous work did not advance'); }
function fixture() {
  const requests = [], messages = [], sockets = [], nodes = new Map(), storage = new Map();
  const localStorage = { get length() { return storage.size; }, key: i => [...storage.keys()][i], setItem: (k, v) => storage.set(k, v), getItem: k => storage.get(k) ?? null, removeItem: k => storage.delete(k) };
  const scope = {
    S: { proj: project(), context: context(), seqId: 'seq1', t: 4, hist: [], histLabels: [], hist_i: -1, sel: new Set(), agentHot: {} },
    CLIENT: 'test-client', CR: {},
    window: { localStorage, FilmocitySync: sync, FilmocityRecovery: { open: () => messages.push({ message: 'recovery opened' }) } },
    location: { protocol: 'http:', host: 'localhost:8787' },
    $: selector => { if (!nodes.has(selector)) nodes.set(selector, { textContent: '', className: '', title: '', classList: { contains: () => false } }); return nodes.get(selector); },
    status: (message, kind) => messages.push({ message, kind }), renderAll() {}, renderTimeline() {},
    fetch(url, options) { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
    WebSocket: class { constructor() { sockets.push(this); } }, setTimeout() {},
  };
  scope.S.seq = scope.S.proj.sequences[0];
  vm.createContext(scope);
  vm.runInContext([
    section('const deep =', 'const trackOf ='),
    section('// ---------- sync ----------', '// ---------- render ----------'),
    'globalThis.testApi = api; globalThis.testDrafts = drafts;',
  ].join('\n'), scope);
  scope.connectWS(); sockets[0].onopen();
  return { scope, requests, messages, sockets, storage, localStorage, badge: scope.$('#stConn'),
    save(name = 'Changed name') { return scope.applyOps([{ op: 'set', path: '/name', value: name }], 'rename', 'rename project'); },
  };
}

test('proposal decisions wait for edits, capture notes, and prevent duplicate activation and switching', async () => {
  const app = fixture(), save = app.save('Edited before decision');
  const items = [{ id: 'item', note: 'Reviewed wording', reasons: ['story'] }];
  const action = app.scope.proposalDecision('proposal/id', items, 'accept');
  items[0].note = 'Changed outside the request';
  assert.equal(app.requests.length, 1); assert.equal(app.scope.S.commandPending, true);
  assert.equal(await app.scope.proposalDecision('proposal/id', items, 'accept'), false);
  await assert.rejects(app.scope.testApi.json('POST', '/api/projects/open', { id: 'b' }), /Wait/);
  app.requests[0].resolve(saved('r1')); await save; await until(() => app.requests.length === 2);
  const request = app.requests[1]; assert.equal(request.url, '/api/proposals/proposal%2Fid/decide');
  const body = JSON.parse(request.options.body);
  assert.equal(body._context.revision, 'r1'); assert.equal(body.items[0].note, 'Reviewed wording');
  assert.deepEqual(body.items[0].reasons, ['story']);
  request.resolve(saved('r2')); await until(() => app.requests.length === 3);
  app.requests[2].resolve(read(project('Accepted result'), context('folder-a', 'r2')));
  assert.equal(await action, true); assert.equal(app.scope.S.commandPending, false);
  assert.equal(app.scope.S.proj.name, 'Accepted result'); assert.equal(app.scope.S.previewProposal, null);
});

test('failed pending edit prevents proposal mutation and retains the browser draft', async () => {
  const app = fixture(), save = app.save('Unsaved'), action = app.scope.proposalDecision('p', [{ id: 'i' }], 'accept');
  app.requests[0].resolve(response(500, { detail: 'disk full' })); await save;
  assert.equal(await action, false); assert.equal(app.requests.length, 1); assert.equal(app.storage.size, 1);
  assert.match(app.messages.at(-1).message, /Recovery/); assert.equal(app.scope.S.commandPending, false);
});

test('stale or uncertain proposal response is explicit, retains the editor, and never retries', async () => {
  for (const reply of [response(409, { detail: { code: 'project_changed', message: 'Newer work exists' } }), response(200, { ok: false }), saved('r1', {}, 'other-project')]) {
    const app = fixture(), action = app.scope.proposalDecision('p', [{ id: 'i' }], 'accept');
    await until(() => app.requests.length === 1); app.requests[0].resolve(reply);
    assert.equal(await action, false); assert.equal(app.requests.length, 1);
    assert.equal(app.scope.S.proj.name, 'Saved name'); assert.equal(app.scope.S.commandPending, false);
    assert.match(app.messages.at(-1).message, /not confirmed/);
  }
});

test('proposal secondary-history warnings are shown after a confirmed refresh', async () => {
  const app = fixture(), action = app.scope.proposalDecision('p', [{ id: 'i' }], 'reject');
  await until(() => app.requests.length === 1); app.requests[0].resolve(saved('r1', { warning: 'Training history could not be recorded' }));
  await until(() => app.requests.length === 2); app.requests[1].resolve(read(project(), context('folder-a', 'r1')));
  assert.equal(await action, true); assert.match(app.messages.at(-1).message, /Training history/);
  assert.equal(app.messages.at(-1).kind, 'err');
});

test('switching storage projects while waiting for saves prevents a proposal from following it', async () => {
  const app = fixture(), action = app.scope.proposalDecision('p', [{ id: 'i' }], 'accept');
  app.scope.S.context = context('folder-b');
  assert.equal(await action, false); assert.equal(app.requests.length, 0);
  assert.match(app.messages.at(-1).message, /active project changed/);
});

test('project switches and completed proposals clear the preview identity', async () => {
  for (const [folder, status] of [['folder-b', 'pending'], ['folder-a', 'accept']]) {
    const app = fixture(); app.scope.S.previewProposal = 'p';
    const refresh = app.scope.loadProject(true);
    app.requests[0].resolve(read({ ...project(), proposals: [{ id: 'p', items: [{ status }] }] }, context(folder, 'r1')));
    assert.equal(await refresh, true); assert.equal(app.scope.S.previewProposal, null);
  }
});

test('agent notification text remains literal and never becomes status markup', async () => {
  const app = fixture(), originalLookup = app.scope.$;
  app.scope.$ = selector => selector === '#agentDot' ? null : originalLookup(selector);
  app.scope.CR.showTab = () => {}; app.scope.S.agentEvents = 0;
  const event = app.sockets[0].onmessage({ data: JSON.stringify({ type: 'proposal', actor: 'agent', project: 'folder-a', title: '<img src=x onerror=bad()>' }) });
  await until(() => app.requests.length === 1); app.requests[0].resolve(read()); await event;
  const status = app.scope.$('#stEvents');
  assert.equal(status.textContent, 'agent: proposal — <img src=x onerror=bad()> (1)');
  assert.equal(status.innerHTML, undefined);
});

test('HTTP rejection exposes structured errors and preserves success payload contracts', async () => {
  for (const [code, body, expected] of [
    [422, { errors: ['Invalid trim', 'Unknown media'] }, /Invalid trim.*Unknown media/],
    [401, { error: 'access token required' }, /access token required/],
    [422, { detail: [{ msg: 'Field required' }] }, /Field required/],
    [409, { detail: { code: 'project_changed', message: 'Project changed' } }, /Project changed/],
  ]) {
    const app = fixture(), request = app.scope.testApi.json('PATCH', '/api/project', {});
    app.requests[0].resolve(response(code, body)); await assert.rejects(request, expected);
  }
  const app = fixture(), body = { ok: false, errors: 1, issues: ['Offline source'] };
  const request = app.scope.testApi.json('POST', '/api/render/preflight', {});
  app.requests[0].resolve(response(200, body)); assert.deepEqual(await request, body);
});

test('non-JSON errors report HTTP status without displaying HTML', async () => {
  const app = fixture(), request = app.scope.testApi.get('/api/project/state');
  app.requests[0].resolve({ ok: false, status: 500, json: async () => { throw new SyntaxError('<html>'); } });
  await assert.rejects(request, error => /500/.test(error.message) && !/html/.test(error.message));
});

test('rejected, unconfirmed, and wrong-project saves pause with local edits retained', async () => {
  for (const reply of [response(422, { errors: ['Invalid trim'] }), response(200, { ok: false }), response(200, { ok: true }), saved('r1', {}, 'different-folder')]) {
    const app = fixture(), save = app.save(); app.requests[0].resolve(reply);
    assert.equal(await save, false); assert.equal(app.badge.textContent, 'save paused');
    assert.equal(app.scope.S.proj.name, 'Changed name'); assert.equal(app.storage.size, 1);
  }
});

test('saves are sent in order with the preceding commit version and immutable op values', async () => {
  const app = fixture();
  const ops = [{ op: 'set', path: '/brief', value: { client: 'First' } }];
  const first = app.scope.applyOps(ops, 'brief'), second = app.save('Second');
  ops[0].value.client = 'Changed outside queue'; app.scope.S.proj.brief.client = 'Changed locally';
  assert.equal(app.requests.length, 1); assert.equal(app.badge.textContent, 'saving…');
  assert.equal(JSON.parse(app.requests[0].options.body).ops[0].value.client, 'First');
  assert.equal(JSON.parse(app.requests[0].options.body)._context.revision, 'r0');
  app.requests[0].resolve(saved('r1')); await first;
  assert.equal(app.requests.length, 2); assert.equal(app.badge.textContent, 'saving…');
  assert.equal(JSON.parse(app.requests[1].options.body)._context.revision, 'r1');
  app.requests[1].resolve(saved('r2')); await second;
  assert.equal(app.badge.textContent, 'saved'); assert.equal(app.storage.size, 0);
});

test('an uncertain request stops dependent requests and reconnect never replays inserts', async () => {
  const app = fixture(), first = app.save('First'), second = app.save('Second');
  app.requests[0].reject(new TypeError('Failed to fetch')); await Promise.all([first, second]);
  app.sockets[0].onclose(); app.sockets[0].onopen(); await app.save('Third');
  assert.equal(app.requests.length, 1); assert.equal(app.scope.S.proj.name, 'Third');
  assert.equal(app.badge.textContent, 'save paused');
  assert.equal(JSON.parse([...app.storage.values()][0]).project.name, 'Third');
});

test('a draft exists before the request and survives a fresh editor session', async () => {
  const app = fixture(), save = app.save();
  const recovered = sync.createDraftStore(() => app.localStorage, 'new-session').list('workspace').drafts;
  assert.equal(recovered.length, 1); assert.equal(recovered[0].project.name, 'Changed name');
  app.requests[0].reject(new Error('Connection lost')); await save;
  assert.equal(recovered[0].context.project, 'folder-a');
});

test('storage quota failures are visible while a normal server save remains usable', async () => {
  const app = fixture(); app.localStorage.setItem = () => { throw new Error('Quota exceeded'); };
  const save = app.save();
  assert.match(app.messages[0].message, /draft unavailable.*Quota exceeded.*download/i);
  app.requests[0].resolve(saved()); assert.equal(await save, true);
  assert.equal(app.requests.length, 1);
});

test('a failed or successful background refresh cannot discard an uncertain editor copy', async () => {
  for (const reply of [response(500, { detail: 'Cannot read project' }), read()]) {
    const app = fixture(), save = app.save(); app.requests[0].reject(new Error('Disconnected')); await save;
    const previous = app.scope.S.proj, reload = app.scope.loadProject(true); app.requests[1].resolve(reply);
    if (reply.ok) assert.equal(await reload, false); else await assert.rejects(reload, /Cannot read project/);
    assert.equal(app.scope.S.proj, previous); assert.equal(app.badge.textContent, 'save paused');
  }
});

test('refreshes racing new or pending edits do not overwrite the optimistic timeline', async () => {
  for (const startReloadFirst of [true, false]) {
    const app = fixture(); let reload, save;
    if (startReloadFirst) { reload = app.scope.loadProject(true); save = app.save(); }
    else { save = app.save(); reload = app.scope.loadProject(true); }
    const get = app.requests.find(r => !r.options), patch = app.requests.find(r => r.options);
    get.resolve(read()); assert.equal(await reload, false); assert.equal(app.scope.S.proj.name, 'Changed name');
    patch.resolve(saved()); await save; assert.equal(app.badge.textContent, 'saved');
  }
});

test('latest project load wins and storage identity separates copied document ids and history', async () => {
  const app = fixture(); app.scope.pushHist('Old history');
  const first = app.scope.loadProject(), second = app.scope.loadProject();
  app.requests[1].resolve(read(project('New project'), context('folder-b'))); assert.equal(await second, true);
  app.requests[0].resolve(read()); assert.equal(await first, false);
  assert.equal(app.scope.S.proj.name, 'New project'); assert.equal(app.scope.S.hist.length, 1);
  assert.equal(app.scope.S.context.project, 'folder-b'); assert.equal(app.badge.textContent, 'connected');
});

test('project switch waits for queued saves and blocks new editing until it reloads', async () => {
  const app = fixture(), save = app.save(), opening = app.scope.testApi.json('POST', '/api/projects/open', { id: 'folder-b' });
  assert.equal(await app.save('Too late'), false); assert.equal(app.requests.length, 1);
  app.requests[0].resolve(saved()); await save; await until(() => app.requests.length === 2);
  assert.equal(app.requests[1].url, '/api/projects/open'); app.requests[1].resolve(response(200, { id: 'folder-b' }));
  await until(() => app.requests.length === 3); app.requests[2].resolve(read(project('Project B'), context('folder-b'))); await opening;
  assert.equal(app.scope.S.proj.name, 'Project B'); assert.equal(app.scope.S.switching, false);
});

test('project switch preserves failed drafts and does not reuse the previous project failure', async () => {
  const app = fixture(), save = app.save(); app.requests[0].reject(new Error('Offline')); await save;
  const opening = app.scope.testApi.json('POST', '/api/projects/open', { id: 'folder-b' });
  await until(() => app.requests.length === 2); app.requests[1].resolve(response(200, { id: 'folder-b' }));
  await until(() => app.requests.length === 3); app.requests[2].resolve(read(project('Project B'), context('folder-b'))); await opening;
  assert.equal(app.badge.textContent, 'connected'); assert.equal(app.storage.size, 1);
});

test('switching is refused when an uncertain editor copy could not be persisted', async () => {
  const app = fixture(); app.localStorage.setItem = () => { throw new Error('Storage denied'); };
  const save = app.save(); app.requests[0].reject(new Error('Offline')); await save;
  await assert.rejects(app.scope.testApi.json('POST', '/api/projects/open', { id: 'folder-b' }), /Resolve your unsaved/);
  assert.equal(app.requests.length, 1); assert.equal(app.scope.S.proj.name, 'Changed name');
});

test('normalization refresh stays pending and cannot erase an edit added during that refresh', async () => {
  const app = fixture(), first = app.save('First');
  app.requests[0].resolve(saved('r1', { warnings: ['Trimmed overlapping clip'] }));
  await until(() => app.requests.length === 2); assert.equal(app.badge.textContent, 'saving…');
  const second = app.save('Second'); app.requests[1].resolve(read(project('First'), context('folder-a', 'r1'))); await first;
  assert.equal(app.scope.S.proj.name, 'Second'); await until(() => app.requests.length === 3);
  app.requests[2].resolve(saved('r2')); await until(() => app.requests.length === 4);
  app.requests[3].resolve(read(project('Second'), context('folder-a', 'r2'))); await second;
  assert.equal(app.scope.S.proj.name, 'Second'); assert.equal(app.storage.size, 0); assert.equal(app.badge.textContent, 'saved');
});

test('undo waits for ordered edits and sends their committed version', async () => {
  const app = fixture(), save = app.save(), undo = app.scope.undo();
  app.requests[0].resolve(saved()); await save; await until(() => app.requests.length === 2);
  assert.equal(app.requests[1].url, '/api/undo'); assert.equal(JSON.parse(app.requests[1].options.body)._context.revision, 'r1');
  app.requests[1].resolve(response(200, { ok: true, undone: 'rename' })); await until(() => app.requests.length === 3);
  app.requests[2].resolve(read(project('Saved name'), context('folder-a', 'r2'))); await undo;
  assert.equal(app.scope.S.proj.name, 'Saved name'); assert.equal(app.scope.S.commandPending, false);
});

test('history replacement uses the save queue and keeps its failed snapshot as a draft', async () => {
  const app = fixture(); app.scope.pushHist('Initial'); const saving = app.save();
  const history = app.scope.gotoHist(0); assert.equal(app.requests.length, 1);
  app.requests[0].resolve(saved()); await saving; assert.equal(app.requests[1].options.method, 'PUT');
  assert.equal(JSON.parse(app.requests[1].options.body)._context.revision, 'r1');
  app.requests[1].reject(new Error('Disconnected')); await history;
  assert.equal(app.scope.S.proj.name, 'Saved name'); assert.equal(app.storage.size, 1);
});

test('clip edits target the named sequence even when another sequence is selected', () => {
  const app = fixture(), first = { id: 'V1', clips: [{ id: 'same', start: 0 }] }, second = { id: 'V1', clips: [{ id: 'same', start: 2 }] };
  app.scope.S.proj.sequences = [{ id: 'seq1', tracks: [first] }, { id: 'seq2', tracks: [second] }];
  app.scope.S.seq = app.scope.S.proj.sequences[0];
  app.scope.applyLocal([{ op: 'set_clip', sequence: 'seq2', track: 'V1', clip: { id: 'same', start: 8 } }]);
  assert.equal(first.clips[0].start, 0); assert.equal(second.clips[0].start, 8);
});

test('events for another project do not mutate this timeline', async () => {
  const app = fixture();
  await app.sockets[0].onmessage({ data: JSON.stringify({ type: 'ops', project: 'folder-b', actor: 'agent', ops: [{ op: 'set', path: '/name', value: 'Wrong' }] }) });
  assert.equal(app.requests.length, 0); assert.equal(app.scope.S.proj.name, 'Saved name');
});

test('recovery explicitly replaces an uncertain copy after the server has archived it', async () => {
  const app = fixture(), save = app.save(); app.requests[0].reject(new Error('Offline')); await save;
  const reload = app.scope.loadProject(false, { recovery: true });
  app.requests[1].resolve(read(project('Recovered'), context('folder-a', 'r2'))); assert.equal(await reload, true);
  assert.equal(app.scope.S.proj.name, 'Recovered'); assert.equal(app.badge.textContent, 'connected'); assert.equal(app.storage.size, 0);
});

test('unreadable project opens recovery without discarding the current editor', async () => {
  const app = fixture(), previous = app.scope.S.proj, reload = app.scope.loadProject(true);
  app.requests[0].resolve(response(409, { detail: { code: 'project_recovery_required', message: 'Project file is not readable JSON' } }));
  await assert.rejects(reload, /not readable JSON/); assert.equal(app.scope.S.proj, previous);
  assert.equal(app.scope.S.recoveryRequired, true); assert.equal(app.messages.at(-1).message, 'recovery opened');
});

test('malformed context or project cannot replace the previous editor', async () => {
  for (const body of [{}, { project: project() }, { project: { sequences: [] }, context: context() }]) {
    const app = fixture(), previous = app.scope.S.proj, reload = app.scope.loadProject();
    app.requests[0].resolve(response(200, body)); await assert.rejects(reload, /Invalid/); assert.equal(app.scope.S.proj, previous);
  }
});

test('an acknowledged save does not hide a disconnected live connection', async () => {
  const app = fixture(), save = app.save(); app.sockets[0].onclose(); app.requests[0].resolve(saved()); await save;
  assert.equal(app.badge.textContent, 'reconnecting…'); app.sockets[0].onopen(); assert.equal(app.badge.textContent, 'saved');
});

test('empty edits and editing before a version is loaded never send an unsafe request', async () => {
  const app = fixture(); await app.scope.applyOps([], 'empty'); app.scope.S.context = null; assert.equal(await app.save(), false);
  assert.equal(app.requests.length, 0);
});

test('returning to a failed project starts a fresh queue without overwriting its older draft', async () => {
  const app = fixture(), first = app.save('Important unsaved work'); app.requests[0].reject(new Error('Offline')); await first;
  let load = app.scope.loadProject(); app.requests[1].resolve(read(project('B'), context('folder-b'))); await load;
  load = app.scope.loadProject(); app.requests[2].resolve(read()); await load;
  const save = app.save('New branch'); assert.equal(app.storage.size, 2);
  app.requests[3].resolve(saved()); await save;
  assert.equal(app.storage.size, 1); assert.equal(JSON.parse([...app.storage.values()][0]).project.name, 'Important unsaved work');
});

test('an older persisted draft is insufficient when a later draft write failed', async () => {
  const app = fixture(), first = app.save('Preserved'); app.requests[0].reject(new Error('Offline')); await first;
  app.localStorage.setItem = () => { throw new Error('Quota full'); }; await app.save('Not yet preserved');
  await assert.rejects(app.scope.testApi.json('POST', '/api/projects/open', { id: 'folder-b' }), /Resolve your unsaved/);
  assert.equal(app.requests.length, 1); assert.equal(app.scope.S.proj.name, 'Not yet preserved');
});

test('inserted sequence identity remains usable by commands that immediately format and populate it', async () => {
  const app = fixture(), sq = { id: 'new-sequence', tracks: [] };
  const first = app.scope.applyOps([{ op: 'insert', path: '/sequences/1', value: sq }], 'sequence');
  assert.equal(app.scope.S.proj.sequences.indexOf(sq), 1);
  const second = app.scope.applyOps([{ op: 'set', path: '/sequences/1/width', value: 1920 }], 'sequence format');
  assert.equal(JSON.parse(app.requests[0].options.body).ops[0].value.width, undefined);
  app.requests[0].resolve(saved()); await first; app.requests[1].resolve(saved('r2')); await second;
  assert.equal(app.scope.S.proj.sequences[1].width, 1920);
});

test('recovering another active project retains the previous project browser draft', async () => {
  const app = fixture(), save = app.save('Unsaved project A'); app.requests[0].reject(new Error('Offline')); await save;
  const recovery = app.scope.loadProject(false, { recovery: true });
  app.requests[1].resolve(read(project('Recovered B'), context('folder-b'))); await recovery;
  assert.equal(app.scope.S.proj.name, 'Recovered B'); assert.equal(app.storage.size, 1);
  assert.equal(JSON.parse([...app.storage.values()][0]).context.project, 'folder-a');
});

test('preparing review waits for queued saves and captures selected IDs without changing the editor', async () => {
  const app = fixture(), save = app.save('Waiting edit'), items = [{ id: 'one' }];
  const preview = app.scope.prepareProposalPreview('p', items); items[0].id = 'changed';
  assert.equal(app.requests.length, 1); app.requests[0].resolve(saved('r1')); await save;
  await until(() => app.requests.length === 2);
  const body = JSON.parse(app.requests[1].options.body); assert.deepEqual(body.items, ['one']); assert.equal(body._context.revision, 'r1');
  app.requests[1].resolve(response(200, { ok: true, id: 'v', plan: 'hash', proposal: 'p', items: ['one'], context: context('folder-a', 'r1'), sequences: [{ id: 'seq1' }] }));
  const view = await preview; assert.equal(view.id, 'v'); assert.equal(app.scope.S.proj.name, 'Waiting edit'); assert.equal(app.scope.S.commandPending, false);
});
test('a late or mismatched prepared preview is released rather than shown', async () => {
  const app = fixture(), action = app.scope.prepareProposalPreview('p', [{ id: 'one' }]); await until(() => app.requests.length === 1);
  app.requests[0].resolve(response(200, { ok: true, id: 'late', plan: 'hash', proposal: 'p', items: ['wrong'], context: context(), sequences: [{ id: 'seq1' }] }));
  await assert.rejects(action, /could not be verified/); assert.equal(app.scope.S.commandPending, false);
  assert.equal(app.requests[1].url, '/api/proposals/preview/late'); assert.equal(app.requests[1].options.method, 'DELETE'); app.requests[1].resolve(response(200, { ok: true }));
});
test('reviewed acceptance sends the exact binding, and changed revisions cannot silently rebase it', async () => {
  const app = fixture(), view = { id: 'v', plan: 'hash', proposal: 'p', items: ['one'], context: context() };
  const action = app.scope.proposalDecision('p', [{ id: 'one' }], 'accept', view); await until(() => app.requests.length === 1);
  assert.deepEqual(JSON.parse(app.requests[0].options.body)._preview, { id: 'v', plan: 'hash' });
  app.requests[0].resolve(saved('r1')); await until(() => app.requests.length === 2); app.requests[1].resolve(read(project('Accepted'), context('folder-a', 'r1'))); assert.equal(await action, true);
  const stale = app.scope.proposalDecision('p', [{ id: 'one' }], 'accept', view); assert.equal(await stale, false); assert.equal(app.requests.length, 2);
  assert.match(app.messages.at(-1).message, /reviewed project changed/);
});
test('preview preparation leaves failed unsaved edits available in Recovery', async () => {
  const app = fixture(), save = app.save('Keep draft'), preview = app.scope.prepareProposalPreview('p', [{ id: 'one' }]);
  app.requests[0].reject(new Error('Offline')); await save; await assert.rejects(preview, /Resolve unsaved edits/);
  assert.equal(app.requests.length, 1); assert.equal(app.storage.size, 1); assert.equal(app.scope.S.proj.name, 'Keep draft');
});

test('saved-version inspection and manual snapshots wait for the queued project revision', async () => {
  const app = fixture(), saving = app.save('Latest edit'), listing = app.scope.savedVersionCatalog('snapshots');
  assert.equal(app.requests.length, 1); app.requests[0].resolve(saved('r1')); await saving; await until(() => app.requests.length === 2);
  app.requests[1].resolve(response(200, { kind: 'snapshots', context: context('folder-a','r1'), versions: [], unavailable: [] }));
  assert.equal((await listing).context.revision, 'r1');
  const snap = app.scope.saveSnapshot('manual_save'); await until(() => app.requests.length === 3);
  assert.equal(JSON.parse(app.requests[2].options.body)._context.revision, 'r1');
  app.requests[2].resolve(saved('r1', { snapshot: 'unique_snapshot' })); assert.equal((await snap).snapshot, 'unique_snapshot');
});
test('restore keeps the chosen file/hash/context, blocks switching and refresh races, and adopts the confirmed result', async () => {
  const app = fixture(), selected = { file: 'original.json', sha256: 'original-hash' };
  app.scope.S.playing = true; app.scope.togglePlay = on => { app.scope.S.playing = on; };
  const action = app.scope.restoreSavedVersion('snapshots', selected, context()); selected.file = 'changed.json';
  await until(() => app.requests.length === 1);
  assert.equal(JSON.parse(app.requests[0].options.body).name, 'original.json');
  await assert.rejects(app.scope.testApi.json('POST','/api/projects/open',{id:'b'}), /Wait/);
  assert.equal(await app.scope.loadProject(true), false); assert.equal(app.requests.length, 1);
  assert.ok(app.storage.size > 0);
  app.requests[0].resolve(saved('r1', { file: 'original.json', sha256: 'original-hash', preserved: 'checkpoint.json' })); await until(() => app.requests.length === 2);
  app.requests[1].resolve(read(project('Restored cut'), context('folder-a','r1')));
  assert.equal((await action).ok, true); assert.equal(app.scope.S.proj.name, 'Restored cut'); assert.equal(app.storage.size, 0);
  assert.equal(app.scope.S.playing, false); assert.equal(app.scope.S.t, 0);
  assert.equal(app.scope.S.commandPending, false); assert.equal(app.scope.S.versionAction, false);
});
test('a save after selection invalidates restore instead of rebasing it', async () => {
  const app = fixture(), saving = app.save('Changed after inspection');
  const action = app.scope.restoreSavedVersion('backups', { file: 'backup.json', sha256: 'hash' }, context());
  const rejection = assert.rejects(action, /Refresh versions/);
  app.requests[0].resolve(saved('r1')); await saving; await rejection; assert.equal(app.requests.length, 1);
});
test('a definite rejected restore keeps the editor usable and never refreshes or retries', async () => {
  const app = fixture(), action = app.scope.restoreSavedVersion('backups', { file: 'backup.json', sha256: 'hash' }, context());
  const rejection = assert.rejects(action, /version changed/); await until(() => app.requests.length === 1);
  app.requests[0].resolve(response(409,{detail:'Selected version changed'})); await rejection;
  assert.equal(app.requests.length, 1); assert.equal(app.scope.projectSaveState().error, ''); assert.equal(app.scope.S.proj.name, 'Saved name');
});
test('an uncertain restore pauses saves, retains the browser copy and prevents replay', async () => {
  const app = fixture(), action = app.scope.restoreSavedVersion('snapshots', { file: 's.json', sha256: 'hash' }, context());
  const rejection = assert.rejects(action, /Open Recovery/); await until(() => app.requests.length === 1);
  app.requests[0].reject(new Error('connection lost')); await rejection;
  assert.match(app.scope.projectSaveState().error, /not confirmed/); assert.ok(app.storage.size > 0);
  await assert.rejects(app.scope.restoreSavedVersion('snapshots', { file: 's.json', sha256: 'hash' }, context()), /Recovery/);
  assert.equal(app.requests.length, 1);
});
test('a committed restore followed by another project revision preserves the editor copy for recovery', async () => {
  const app = fixture(), action = app.scope.restoreSavedVersion('snapshots', { file: 's.json', sha256: 'hash' }, context());
  const rejection = assert.rejects(action, /Restored, but/); await until(() => app.requests.length === 1);
  app.requests[0].resolve(saved('r1', { file: 's.json', sha256: 'hash', preserved: 'checkpoint.json' })); await until(() => app.requests.length === 2);
  app.requests[1].resolve(read(project('Later remote edit'), context('folder-a','r2'))); await rejection;
  assert.equal(app.scope.S.proj.name, 'Saved name'); assert.ok(app.storage.size > 0);
});
test('a failed save prevents snapshot creation and catalog replies cannot switch the selected project', async () => {
  const app = fixture(), saving = app.save('Keep this edit'), snapshot = app.scope.saveSnapshot('manual_save');
  const rejection = assert.rejects(snapshot, /Recovery/); app.requests[0].resolve(response(500,{error:'Disk full'})); await saving; await rejection;
  assert.equal(app.requests.length, 1);
  const other = fixture(), catalog = other.scope.savedVersionCatalog('snapshots'); const wrong = assert.rejects(catalog, /project changed/);
  await until(() => other.requests.length === 1); other.requests[0].resolve(response(200,{kind:'snapshots', context:context('folder-b'), versions:[], unavailable:[]})); await wrong;
});

test('refresh invalidates cached frame/preview when a source changes without a project edit',async()=>{
  const app=fixture();let invalidated=0;app.scope.window.CR={invalidateRenderedPreview:()=>invalidated++};app.scope.window.FilmocityProxyPreview=require('../frontend/proxy-preview.js');
  app.scope.S.mediaAvailability={m:{generation:'same-source',original_lease:'old-file',proxy_lease:'proxy'}};
  const reload=app.scope.loadProject(true);
  app.requests.at(-1).resolve(response(200,{project:project(),context:context(),media_availability:{m:{generation:'same-source',original_lease:'changed-file',proxy_lease:null}}}));
  assert.equal(await reload,true);assert.equal(invalidated,1);assert.equal(app.scope.S.context.revision,'r0');
});
test('proxy-only availability changes do not invalidate an original-based rendered preview',async()=>{
  const app=fixture();let invalidated=0;app.scope.window.CR={invalidateRenderedPreview:()=>invalidated++};app.scope.window.FilmocityProxyPreview=require('../frontend/proxy-preview.js');
  app.scope.S.mediaAvailability={m:{generation:'same-source',original_lease:'original',proxy_lease:'old-proxy'}};
  const reload=app.scope.loadProject(true);
  app.requests.at(-1).resolve(response(200,{project:project(),context:context(),media_availability:{m:{generation:'same-source',original_lease:'original',proxy_lease:'new-proxy'}}}));
  assert.equal(await reload,true);assert.equal(invalidated,0);
});

test('project actions send the saved owner and reject another project returned during reload',async()=>{
  const app=fixture(),opening=app.scope.testApi.json('POST','/api/projects/open',{id:'folder-b',_target_sha256:'chosen'});
  await until(()=>app.requests.length===1);const body=JSON.parse(app.requests[0].options.body);assert.deepEqual(body._context,context());assert.equal(body._target_sha256,'chosen');
  app.requests[0].resolve(response(200,{id:'folder-b'}));await until(()=>app.requests.length===2);app.requests[1].resolve(read(project('Wrong'),context('folder-c')));
  await assert.rejects(opening,/active project changed/);assert.equal(app.scope.S.proj.name,'Saved name');assert.ok(app.messages.some(m=>m.message.includes('completed')));
});
test('save-as refuses an unsaved draft even when a browser recovery copy exists',async()=>{
  const app=fixture(),save=app.save();app.requests[0].reject(Error('offline'));await save;
  for(const path of ['/api/projects/save_as','/api/projects/duplicate'])await assert.rejects(app.scope.testApi.json('POST',path,{name:'Copy'}),/unsaved editor/);
  assert.equal(app.requests.length,1);assert.equal(app.storage.size,1);
});
test('switching defers background refresh until the named destination is confirmed',async()=>{
  const app=fixture(),opening=app.scope.testApi.json('POST','/api/projects/open',{id:'folder-b'});await until(()=>app.requests.length===1);
  assert.equal(await app.scope.loadProject(true),false);assert.equal(app.requests.length,1);app.requests[0].resolve(response(200,{id:'folder-b'}));await until(()=>app.requests.length===2);
  app.requests[1].resolve(read(project('B'),context('folder-b')));await opening;assert.equal(app.scope.S.proj.name,'B');
});
test('a stale project dialog is refused before saving or submitting',async()=>{
  const app=fixture();await assert.rejects(app.scope.testApi.json('POST','/api/projects/new',{name:'Copy',_origin:context('other')}),/active project changed/);assert.equal(app.requests.length,0);
});

test('opening a sound project from recovery binds the damaged active bytes before departure',async()=>{
  const app=fixture();app.scope.S.recoveryRequired=true;
  const opening=app.scope.testApi.json('POST','/api/projects/open',{id:'folder-b'});await until(()=>app.requests.length===1);assert.equal(app.requests[0].url,'/api/projects/recovery');
  app.requests[0].resolve(response(200,{project:'folder-a',workspace:'workspace',current_sha256:'damaged-hash',current_valid:false}));await until(()=>app.requests.length===2);
  assert.equal(JSON.parse(app.requests[1].options.body)._recovery_origin.current_sha256,'damaged-hash');app.requests[1].resolve(response(200,{id:'folder-b'}));await until(()=>app.requests.length===3);
  app.requests[2].resolve(read(project('Healthy'),context('folder-b')));await opening;assert.equal(app.scope.S.recoveryRequired,false);assert.equal(app.scope.S.proj.name,'Healthy');
});

test('uncertain project actions pause edits and can be reconciled without resubmission',async()=>{
  const app=fixture(),opening=app.scope.testApi.json('POST','/api/projects/new',{name:'New'});await until(()=>app.requests.length===1);app.requests[0].reject(Error('reply lost'));await assert.rejects(opening,/not confirmed/);
  assert.equal(app.scope.S.projectSwitchUncertain,true);assert.equal(await app.save('Do not redirect'),false);await assert.rejects(app.scope.testApi.json('POST','/api/projects/new',{name:'Again'}),/Refresh the editor/);
  const refresh=app.scope.refreshProjectSelection();await until(()=>app.requests.length===2);app.requests[1].resolve(read(project('Already created'),context('created')));await refresh;
  assert.equal(app.scope.S.proj.name,'Already created');assert.equal(app.scope.S.projectSwitchUncertain,false);assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,1);
});
test('refresh cannot clear uncertainty while an owned project copy can still activate',async()=>{
  const app=fixture();app.scope.S.projectSwitchUncertain=true;const refresh=app.scope.refreshProjectSelection();await until(()=>app.requests.length===1);
  app.requests[0].resolve(response(200,{project:project(),context:context(),project_action_busy:true}));await assert.rejects(refresh,/still running/);assert.equal(app.scope.S.projectSwitchUncertain,true);
});

test('collection queues only after pending saves and never reloads the edit on completion',async()=>{
  const app=fixture();app.scope.crypto={randomUUID:()=> 'a'.repeat(32)};
  const save=app.save('Latest saved edit'),collect=app.scope.startMediaCollection();
  assert.equal(app.requests.length,1);assert.equal(app.scope.S.commandPending,true);
  app.requests[0].resolve(saved('r1'));await save;await until(()=>app.requests.length===2);
  assert.equal(app.requests[1].url,'/api/tasks/collect');assert.equal(JSON.parse(app.requests[1].options.body)._context.revision,'r1');
  app.requests[1].resolve(response(200,{ok:true,context:context('folder-a','r1'),task:{id:'a'.repeat(32)}}));await collect;
  assert.equal(app.requests.length,2);assert.equal(app.scope.S.proj.name,'Latest saved edit');assert.equal(app.scope.S.commandPending,false);
});
test('collection refuses a failed pending save and preserves the browser draft',async()=>{
  const app=fixture();app.scope.crypto={randomUUID:()=> 'a'.repeat(32)};
  const save=app.save('Unsaved edit'),collect=app.scope.startMediaCollection();
  app.requests[0].reject(Error('disk full'));await save;await assert.rejects(collect,/Recovery/);
  assert.equal(app.requests.length,1);assert.equal(app.storage.size,1);
});
test('uncertain collection apply preserves a draft and never repeats the mutation',async()=>{
  const app=fixture();app.scope.window.FilmocityWorkflowTransaction=require('../frontend/workflow-transaction.js');
  const apply=app.scope.applyMediaCollection('a'.repeat(32),{...context(),sequence:'seq1'});
  await until(()=>app.requests.length===1);assert.equal(app.requests[0].url,'/api/tasks/'+'a'.repeat(32)+'/apply');
  app.requests[0].reject(Error('reply lost'));await assert.rejects(apply,/Open Recovery/);
  assert.equal(app.requests.length,1);assert.equal(app.storage.size,1);assert.match(app.scope.projectSaveState().error,/not confirmed/);
  await assert.rejects(app.scope.applyMediaCollection('a'.repeat(32),{...context(),sequence:'seq1'}),/Recovery/);assert.equal(app.requests.length,1);
});
test('collection apply adopts only the same project and displays a committed result',async()=>{
  const app=fixture();app.scope.window.FilmocityWorkflowTransaction=require('../frontend/workflow-transaction.js');
  const apply=app.scope.applyMediaCollection('a'.repeat(32),{...context(),sequence:'seq1'});
  await until(()=>app.requests.length===1);app.requests[0].resolve(saved('r1',{message:'Paths applied',warning:'Event warning'}));
  await until(()=>app.requests.length===2);app.requests[1].resolve(read(project('Relinked'),context('folder-a','r1')));
  const result=await apply;assert.equal(result.warning,'Event warning');assert.equal(app.scope.S.proj.name,'Relinked');assert.equal(app.storage.size,0);
});

test('ducking review waits for pending saves and binds the exact saved revision without reloading',async()=>{
  const app=fixture(),saving=app.save('Edited'),reviewing=app.scope.previewAudioDucking({amount:-9},{...context(),sequence:'seq1'});
  assert.equal(app.requests.length,1);app.requests[0].resolve(saved('r1'));await saving;await until(()=>app.requests.length===2);
  const body=JSON.parse(app.requests[1].options.body);assert.equal(body.preview,true);assert.equal(body._context.revision,'r1');
  app.requests[1].resolve(saved('r1',{preview:true,plan:'hash',sequence:'seq1'}));const reviewed=await reviewing;
  assert.equal(reviewed.context.revision,'r1');assert.equal(app.requests.length,2);assert.equal(app.scope.S.proj.name,'Edited');
});
test('ducking review rejects mismatched revisions or sequences and leaves editor untouched',async()=>{
  for(const extra of [{context:context('folder-a','wrong')},{sequence:'wrong'}]){
    const app=fixture(),action=app.scope.previewAudioDucking({}, {...context(),sequence:'seq1'});
    await until(()=>app.requests.length===1);app.requests[0].resolve(saved('r0',{preview:true,plan:'hash',sequence:'seq1',...extra}));
    await assert.rejects(action,/not confirmed/);assert.equal(app.scope.S.proj.name,'Saved name');assert.equal(app.requests.length,1);
  }
});
test('ducking Apply refuses a changed edit before sending and preserves the saved change',async()=>{
  const app=fixture();app.scope.window.FilmocityWorkflowTransaction=require('../frontend/workflow-transaction.js');
  const saving=app.save('Changed after review');app.requests[0].resolve(saved('r1'));await saving;
  await assert.rejects(app.scope.applyAudioDucking({}, {context:context(),sequence:'seq1',plan:'old'}),/edit changed/);
  assert.equal(app.requests.length,1);assert.equal(app.scope.S.proj.name,'Changed after review');
});
test('ducking Apply carries reviewed plan then adopts only confirmed state',async()=>{
  const app=fixture();app.scope.window.FilmocityWorkflowTransaction=require('../frontend/workflow-transaction.js');app.scope.switchSeq=()=>{};
  const action=app.scope.applyAudioDucking({amount:-9}, {context:context(),sequence:'seq1',plan:'reviewed'});
  await until(()=>app.requests.length===1);const body=JSON.parse(app.requests[0].options.body);assert.equal(body.preview,false);assert.equal(body.preview_plan,'reviewed');
  app.requests[0].resolve(saved('r1',{sequence:'seq1'}));await until(()=>app.requests.length===2);app.requests[1].resolve(read(project('Ducked'),context('folder-a','r1')));
  await action;assert.equal(app.scope.S.proj.name,'Ducked');assert.equal(app.storage.size,0);
});
test('lost ducking Apply reply pauses workflow commits, retains draft and never auto-repeats',async()=>{
  const app=fixture();app.scope.window.FilmocityWorkflowTransaction=require('../frontend/workflow-transaction.js');
  const reviewed={context:context(),sequence:'seq1',plan:'reviewed'},action=app.scope.applyAudioDucking({},reviewed);
  await until(()=>app.requests.length===1);app.requests[0].reject(Error('lost reply'));await assert.rejects(action,/Open Recovery/);
  await assert.rejects(app.scope.applyAudioDucking({},reviewed),/Recovery/);assert.equal(app.requests.length,1);assert.equal(app.storage.size,1);
});
