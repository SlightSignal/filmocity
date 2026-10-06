const test = require('node:test');
const assert = require('node:assert/strict');
const { createDraftStore } = require('../frontend/project-sync.js');
function storage() {
  const values = new Map();
  return { values, get length() { return values.size; }, key: i => [...values.keys()][i], getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) };
}
const context = { workspace: 'one', project: 'project É', revision: 'v1' };
const project = { name: 'Draft', sequences: [{ tracks: [] }] };

test('separate tabs and reopened branches retain independent immutable drafts', () => {
  const data = storage(), a = createDraftStore(() => data, 'tab1'), b = createDraftStore(() => data, 'tab2');
  const first = a.save(context, project); b.save(context, { ...project, name: 'Tab 2' }); a.save(context, { ...project, name: 'Reopened' });
  assert.equal(a.list('one').drafts.length, 3);
  a.save(context, { ...project, name: 'Updated first' }, first);
  assert.equal(a.list('one').drafts.length, 3); assert.equal(JSON.parse(data.getItem(first)).project.name, 'Updated first');
});

test('unreadable drafts are reported and retained, with unrelated workspace drafts excluded', () => {
  const data = storage(), drafts = createDraftStore(() => data, 'tab');
  const damaged = drafts.save(context, project); data.setItem(damaged, '{');
  drafts.save({ ...context, workspace: 'other' }, project);
  const report = drafts.list('one'); assert.equal(report.drafts.length, 0); assert.equal(report.unavailable.length, 1); assert.equal(data.getItem(damaged), '{');
});

test('quota and access failures are surfaced without clearing the last stored copy', () => {
  const data = storage(), drafts = createDraftStore(() => data, 'tab'); const key = drafts.save(context, project), before = data.getItem(key);
  data.setItem = () => { throw new Error('Quota exceeded'); };
  assert.throws(() => drafts.save(context, { ...project, name: 'Newer' }, key), /Quota/); assert.equal(data.getItem(key), before);
  assert.throws(() => createDraftStore(() => { throw new Error('Storage denied'); }, 'tab').list('one'), /Storage denied/);
});

test('failed readback cannot be reported as a preserved draft', () => {
  const data = storage(); data.setItem = () => {};
  assert.throws(() => createDraftStore(() => data, 'tab').save(context, project), /verify the draft/);
});

test('one editor cannot overwrite a draft key from another project or tab', () => {
  const data = storage(), a = createDraftStore(() => data, 'a'), b = createDraftStore(() => data, 'b'); const key = a.save(context, project);
  assert.throws(() => b.save(context, project, key), /another editor/);
  assert.throws(() => a.save({ ...context, project: 'different' }, project, key), /another editor/);
  assert.equal(JSON.parse(data.getItem(key)).project.name, 'Draft');
});
