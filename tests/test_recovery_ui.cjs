const assert = require('node:assert/strict');
const test = require('node:test');
const { createRecoveryController } = require('../frontend/recovery.js');

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.attributes = {}; this.disabled = false; this.open = false; this.textContent = ''; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.events[key] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; this.events.close?.(); }
  focus() { this.focused = true; }
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { resolve, reject, promise };
}
function fixture() {
  const names = ['dlgRecovery', 'recoveryVersions', 'recoveryMessage', 'recoveryRestore', 'recoveryRefresh', 'recoveryClose', 'recoveryDownload'];
  const nodes = Object.fromEntries(names.map(name => [name, new Element('div')]));
  const origin = new Element('button'), calls = [], messages = [];
  const candidate = { id: 'pending', sha256: 'selected-hash', name: 'My edit', kind: 'Interrupted save', saved_at: 1234, sequences: 1, clips: 5 };
  const catalog = { project: 'p', workspace: 'w', current_sha256: 'current-hash', current_valid: false, current_error: 'Project file is missing', candidates: [candidate], unavailable: [] };
  const state = { context: { workspace: 'w', project: 'p', revision: 'v0' }, proj: { id: 'p', name: 'Unsaved editor copy' } };
  const config = { drafts: [], unsaved: false, pending: false, get: async () => catalog, post: async () => ({ ok: true, name: 'Recovered edit' }), reload: async () => {} };
  const controller = createRecoveryController({
    document: { getElementById: id => nodes[id], createElement: tag => new Element(tag), activeElement: origin }, state,
    api: { get: async url => { calls.push(['GET', url]); return config.get(); }, json: async (method, url, body) => { calls.push([method, url, body]); return config.post(); } },
    onRestore: async () => { calls.push(['reload']); await config.reload(); },
    report: (...args) => messages.push(args), pending: () => config.pending, hasUnsaved: () => config.unsaved,
    drafts: { list: () => ({ drafts: config.drafts, unavailable: [] }), remove: key => calls.push(['remove', key]) },
    download: project => calls.push(['download', project]),
  });
  return { controller, nodes, origin, calls, messages, config, catalog, state,
    select() { nodes.recoveryVersions.children[1].children[0].onchange(); },
    restore() { return nodes.recoveryRestore.onclick(); },
  };
}

test('recovery requires selecting a version and sends both inspected fingerprints', async () => {
  const app = fixture(); await app.controller.open();
  assert.equal(app.nodes.dlgRecovery.open, true);
  assert.equal(app.nodes.recoveryRestore.disabled, true);
  await app.restore(); assert.equal(app.calls.length, 1);
  app.select(); assert.equal(app.nodes.recoveryRestore.disabled, false);
  await app.restore();
  assert.deepEqual(app.calls[1], ['POST', '/api/projects/recovery', {
    project: 'p', candidate: 'pending', sha256: 'selected-hash', current_sha256: 'current-hash', editor_project: app.state.proj,
  }]);
  assert.deepEqual(app.calls[2], ['reload']);
  assert.equal(app.nodes.dlgRecovery.open, false);
  assert.equal(app.origin.focused, true);
  assert.match(app.messages[0][0], /Restored.*Recovered edit/);
});

test('pending edits block restoration even after a version was selected', async () => {
  const app = fixture(); await app.controller.open(); app.select(); app.config.pending = true;
  await app.restore();
  assert.equal(app.calls.length, 1);
  assert.equal(app.nodes.recoveryRestore.disabled, true);
  assert.match(app.nodes.recoveryMessage.textContent, /outstanding edits/);
});

test('refresh failure never applies a previously selected version', async () => {
  const app = fixture(); await app.controller.open(); app.select();
  app.config.get = async () => { throw new Error('Disconnected'); };
  await app.nodes.recoveryRefresh.onclick(); await app.restore();
  assert.equal(app.calls.filter(call => call[0] === 'POST').length, 0);
  assert.equal(app.nodes.recoveryRestore.disabled, true);
  assert.match(app.nodes.recoveryMessage.textContent, /Disconnected/);
});

test('a conflict keeps the dialog open and never reports recovery success', async () => {
  const app = fixture(); app.config.post = async () => { throw new Error('Project changed; refresh versions'); };
  await app.controller.open(); app.select(); await app.restore();
  assert.equal(app.nodes.dlgRecovery.open, true);
  assert.match(app.nodes.recoveryMessage.textContent, /Project changed/);
  assert.equal(app.messages.length, 0);
  assert.equal(app.calls.filter(call => call[0] === 'reload').length, 0);
  assert.equal(app.nodes.recoveryRefresh.disabled, false);
});

test('repeated restore activation submits once and Escape cannot dismiss an in-flight restore', async () => {
  const app = fixture(), waiting = deferred(); app.config.post = () => waiting.promise;
  await app.controller.open(); app.select(); const first = app.restore(); await app.restore();
  assert.equal(app.calls.filter(call => call[0] === 'POST').length, 1);
  assert.equal(app.nodes.recoveryClose.disabled, true);
  let prevented = false;
  app.nodes.dlgRecovery.events.cancel({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  waiting.resolve({ ok: true, name: 'Restored' }); await first;
});

test('reload failure after restoration is reported without saying restoration failed', async () => {
  const app = fixture(); app.config.reload = async () => { throw new Error('Connection lost'); };
  await app.controller.open(); app.select(); await app.restore();
  assert.match(app.nodes.recoveryMessage.textContent, /version was restored.*Connection lost/);
  assert.equal(app.nodes.dlgRecovery.open, true);
  assert.equal(app.messages.length, 0);
});

test('a history warning remains visible after a successful recovery', async () => {
  const app = fixture(); app.config.post = async () => ({ ok: true, name: 'Restored', warning: 'Restored; history unavailable' });
  await app.controller.open(); app.select(); await app.restore();
  assert.deepEqual(app.messages[0], ['Restored; history unavailable', 'err']);
});

test('version names are inserted as text and no version is silently preselected', async () => {
  const app = fixture(); app.catalog.candidates[0].name = '<img src=x onerror=alert(1)>';
  await app.controller.open();
  const label = app.nodes.recoveryVersions.children[1];
  assert.equal(label.children[1].children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(app.nodes.recoveryRestore.disabled, true);
  assert.match(app.nodes.recoveryMessage.textContent, /Select the version/);
});

test('dialog key presses never reach timeline keyboard handlers', async () => {
  const app = fixture(); await app.controller.open(); let stopped = false;
  app.nodes.dlgRecovery.events.keydown({ stopPropagation() { stopped = true; } });
  assert.equal(stopped, true);
});

test('closing and reopening ignores a stale version-list response', async () => {
  const app = fixture(), old = deferred(); app.config.get = () => old.promise;
  const first = app.controller.open(); app.nodes.dlgRecovery.close();
  app.config.get = async () => ({ ...app.catalog, candidates: [] });
  await app.controller.open();
  old.resolve(app.catalog); await first;
  assert.equal(app.nodes.recoveryVersions.children.length, 1);
  assert.equal(app.nodes.recoveryRestore.disabled, true);
  assert.match(app.nodes.recoveryMessage.textContent, /No readable recovery versions/);
});

test('browser drafts restore only by deliberate selection and are removed after successful reload', async () => {
  const app = fixture(), draft = { key: 'browser-key', context: app.state.context, saved_at: 123, project: { name: 'Last local edit', sequences: [{ tracks: [] }] } };
  app.config.drafts = [draft]; await app.controller.open();
  app.nodes.recoveryVersions.children[2].children[0].onchange(); await app.restore();
  const body = app.calls.find(call => call[0] === 'POST')[2];
  assert.deepEqual(body.draft_project, draft.project); assert.equal(body.workspace, 'w');
  assert.equal(body.current_sha256, 'current-hash'); assert.deepEqual(body.editor_project, app.state.proj);
  assert.deepEqual(app.calls.at(-1), ['remove', 'browser-key']);
});

test('a browser draft for another project can be downloaded but cannot overwrite this project', async () => {
  const app = fixture(), draft = { key: 'foreign', context: { ...app.state.context, project: 'other' }, saved_at: 123, project: { name: 'Other project', sequences: [] } };
  app.config.drafts = [draft]; await app.controller.open();
  app.nodes.recoveryVersions.children[2].children[0].onchange();
  assert.equal(app.nodes.recoveryRestore.disabled, true); await app.restore();
  app.nodes.recoveryDownload.onclick();
  assert.equal(app.calls.filter(call => call[0] === 'POST').length, 0);
  assert.deepEqual(app.calls.at(-1), ['download', draft.project]);
});

test('a failed reload after draft restoration keeps the browser draft for inspection', async () => {
  const app = fixture(); app.config.drafts = [{ key: 'draft', context: app.state.context, saved_at: 123, project: { name: 'Draft', sequences: [] } }];
  app.config.reload = async () => { throw new Error('Offline'); };
  await app.controller.open(); app.nodes.recoveryVersions.children[2].children[0].onchange(); await app.restore();
  assert.equal(app.calls.some(call => call[0] === 'remove'), false);
  assert.match(app.nodes.recoveryMessage.textContent, /restored.*Offline/);
});

test('an unsaved open editor is recoverable even when browser storage has no copy', async () => {
  const app = fixture(); app.config.unsaved = true; app.state.proj.sequences = [{ tracks: [] }];
  await app.controller.open(); app.select(); const originalName = app.state.proj.name;
  app.state.proj.name = 'Changed after selection'; await app.restore();
  const body = app.calls.find(call => call[0] === 'POST')[2];
  assert.equal(body.draft_project.name, originalName); assert.equal(body.editor_project.name, 'Changed after selection');
});

test('editor download remains available when the recovery service is offline', async () => {
  const app = fixture(); app.config.get = async () => { throw new Error('Offline'); };
  await app.controller.open(); assert.equal(app.nodes.recoveryDownload.disabled, false); app.nodes.recoveryDownload.onclick();
  assert.deepEqual(app.calls.at(-1), ['download', app.state.proj]);
});

test('targeted recovery captures the source context and never archives an unrelated editor copy',async()=>{
  const app=fixture();app.catalog.project='other';app.catalog.origin_context={...app.state.context};await app.controller.open({project:'other'});app.select();await app.restore();
  assert.equal(app.calls[0][1],'/api/projects/recovery?project=other');const body=app.calls.find(c=>c[0]==='POST')[2];assert.equal(body.editor_project,null);assert.deepEqual(body._context,app.state.context);assert.equal(body.project,'other');
});
test('changing project during targeted recovery prevents restoring the inspected file',async()=>{
  const app=fixture();app.catalog.project='other';app.catalog.origin_context={...app.state.context};await app.controller.open({project:'other'});app.select();app.state.context.project='changed';await app.restore();assert.equal(app.calls.filter(c=>c[0]==='POST').length,0);assert.match(app.nodes.recoveryMessage.textContent,/active project changed/);
});
