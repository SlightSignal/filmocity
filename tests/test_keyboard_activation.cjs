const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const begin = source.indexOf('function keys(e)');
const end = source.indexOf('async function refreshMediaStatus()', begin);
assert.ok(begin >= 0 && end > begin);
function run(key, control, combo = key.toLowerCase(), options = {}) {
  const calls = [], scope = {
    document: { querySelector: () => options.modal ? {} : null },
    S: { proj: options.noProject ? null : {}, recoveryRequired: options.recovery, focus: 'program', keymap: { preview: 'enter', play: ' ', export: 'ctrl+m', commands: 'ctrl+shift+p', ...options.bindings } },
    TIMELINE_ONLY: new Set(), comboOf: () => combo,
    ACTIONS: Object.fromEntries(['preview', 'play', 'export', 'commands'].map(name => [name, ['', '', () => calls.push(name)]]))
  };
  vm.createContext(scope); vm.runInContext(source.slice(begin, end), scope);
  scope.keys({ key, shiftKey: false, ...options.event, target: {
    matches: () => control === 'input',
    closest: () => ['button', 'link', 'button-child', 'menuitem'].includes(control) ? {} : null,
    isContentEditable: control === 'editable'
  }, preventDefault: () => calls.push('preventDefault') });
  return calls;
}
test('focused control activation never invokes timeline Enter/Space shortcuts', () => {
  for (const control of ['button', 'link', 'button-child', 'menuitem']) {
    for (const key of ['Enter', ' ']) assert.deepEqual(run(key, control), [], `${control}: ${key}`);
  }
});
test('timeline shortcuts and modified commands keep their established behavior', () => {
  assert.deepEqual(run('Enter', 'canvas'), ['preventDefault', 'preview']);
  assert.deepEqual(run(' ', 'canvas'), ['preventDefault', 'play']);
  assert.deepEqual(run('m', 'button', 'ctrl+m'), ['preventDefault', 'export']);
  assert.deepEqual(run('Enter', 'input'), []);
  assert.deepEqual(run('Enter', 'editable'), []);
});

test('command search is available from text fields before project load and during recovery', () => {
  assert.deepEqual(run('P', 'input', 'ctrl+shift+p', { noProject: true }), ['preventDefault', 'commands']);
  assert.deepEqual(run('P', 'editable', 'ctrl+shift+p', { recovery: true }), ['preventDefault', 'commands']);
  assert.deepEqual(run('P', 'canvas', 'ctrl+shift+p', { event: { repeat: true } }), []);
});
test('open dialogs contain all global editing shortcuts and cannot spawn nested command search', () => {
  assert.deepEqual(run('m', 'canvas', 'ctrl+m', { modal: true }), []);
  assert.deepEqual(run('P', 'input', 'ctrl+shift+p', { modal: true }), []);
});
test('unknown stored commands and unbound shifted variants never invoke another edit', () => {
  assert.deepEqual(run('x', 'canvas', 'x', { bindings: { removed_action: 'x' } }), []);
  assert.deepEqual(run('M', 'canvas', 'ctrl+shift+m', { event: { shiftKey: true } }), []);
});
test('already handled or composing events cannot reach the editor action registry', () => {
  assert.deepEqual(run('m', 'canvas', 'ctrl+m', { event: { defaultPrevented: true } }), []);
  assert.deepEqual(run('m', 'canvas', 'ctrl+m', { event: { isComposing: true } }), []);
});
