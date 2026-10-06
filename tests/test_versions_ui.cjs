const assert = require('node:assert/strict');
const test = require('node:test');
const { createVersionsController } = require('../frontend/project-versions.js');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.open = false; this.disabled = false; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  setAttribute() {}
  addEventListener(name, fn) { this.events[name] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; this.events.close?.(); }
  focus() { this.focused = true; }
}
function deferred() { let resolve, reject; const promise = new Promise((a,b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function fixture() {
  const nodes = Object.fromEntries(['dlgRestore', 'rsList', 'rsMessage', 'rsRestore', 'rsRefresh', 'rsCancel', 'rsTitle'].map(n => [n, new Element('div')]));
  const origin = new Element('button'), calls = [], reports = [];
  const version = { file: 'file.json', label: '<img src=x>', name: 'Earlier cut', ts: 1, sha256: 'hash', sequences: 1, clips: 2 };
  const catalog = { context: { project: 'a', workspace: 'w', revision: 'r' }, versions: [version], unavailable: [] };
  const config = { inspect: async () => catalog, commit: async () => ({ ok: true }) };
  const controller = createVersionsController({ document: { getElementById: id => nodes[id], createElement: tag => new Element(tag), activeElement: origin },
    catalog: async kind => { calls.push(['list',kind]); return config.inspect(); },
    restore: async (...args) => { calls.push(['restore',...args]); return config.commit(); }, report: (...args) => reports.push(args) });
  return { controller, nodes, origin, config, calls, reports, catalog, version,
    select() { nodes.rsList.children[1].children[0].onchange(); }, apply() { return nodes.rsRestore.onclick(); } };
}
test('versions require selection, render literal labels, and bind restore to the inspected context', async () => {
  const h = fixture(); await h.controller.open('snapshots'); await h.apply(); assert.equal(h.calls.length, 1);
  assert.equal(h.nodes.rsList.children[1].children[1].children[0].textContent, '<img src=x> · Earlier cut');
  h.select(); await h.apply(); assert.deepEqual(h.calls[1], ['restore', 'snapshots', h.version, h.catalog.context]);
  assert.equal(h.nodes.dlgRestore.open, false); assert.equal(h.origin.focused, true); assert.match(h.reports[0][0], /Undo/);
});
test('a pending restore blocks duplicate activation, refresh, close and Escape', async () => {
  const h = fixture(), pending = deferred(); h.config.commit = () => pending.promise;
  await h.controller.open('backups'); h.select(); const action = h.apply(); await h.apply(); await h.nodes.rsRefresh.onclick(); h.nodes.rsCancel.onclick();
  let prevented = false; h.nodes.dlgRestore.events.cancel({ preventDefault() { prevented = true; } });
  assert.ok(prevented); assert.ok(h.nodes.dlgRestore.open); assert.ok(h.nodes.rsRestore.disabled && h.nodes.rsCancel.disabled);
  assert.equal(h.calls.length, 2); pending.resolve({ ok: true }); await action;
});
test('failure keeps the dialog and requires a fresh selection instead of automatic replay', async () => {
  const h = fixture(); h.config.commit = async () => { throw new Error('Selected version changed'); };
  await h.controller.open('snapshots'); h.select(); await h.apply(); await h.apply();
  assert.equal(h.calls.length, 2); assert.ok(h.nodes.dlgRestore.open && h.nodes.rsRestore.disabled); assert.match(h.nodes.rsMessage.textContent, /changed/);
  await h.nodes.rsRefresh.onclick(); assert.equal(h.nodes.rsRestore.disabled, true);
});
test('closing during load prevents a late response from repopulating the dialog', async () => {
  const h = fixture(), pending = deferred(); h.config.inspect = () => pending.promise;
  const action = h.controller.open('snapshots'); h.nodes.rsCancel.onclick(); pending.resolve(h.catalog); await action;
  assert.equal(h.nodes.rsList.children.length, 0); assert.equal(h.nodes.dlgRestore.open, false);
});
test('empty and unreadable catalogs explain why restore is unavailable and contain keyboard events', async () => {
  const h = fixture(); h.catalog.versions = []; h.catalog.unavailable = [{ file: 'bad.json' }];
  await h.controller.open('backups'); assert.match(h.nodes.rsMessage.textContent, /No readable/); assert.match(h.nodes.rsMessage.textContent, /1 unreadable/);
  assert.match(h.nodes.rsTitle.textContent, /automatic backup/); let stopped = false;
  h.nodes.dlgRestore.events.keydown({ stopPropagation() { stopped = true; } }); assert.ok(stopped);
});
test('committed warnings are reported after closing and unconfirmed results remain visible', async () => {
  const h = fixture(); h.config.commit = async () => ({ ok: false }); await h.controller.open('snapshots'); h.select(); await h.apply();
  assert.ok(h.nodes.dlgRestore.open); assert.match(h.nodes.rsMessage.textContent, /not confirmed/);
  h.config.commit = async () => ({ ok: true, warning: 'Notification unavailable' }); h.select(); await h.apply();
  assert.deepEqual(h.reports[0], ['Notification unavailable', 'err']);
});
