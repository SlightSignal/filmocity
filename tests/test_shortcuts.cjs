const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const keyboard = require('../frontend/keyboard.js');
const { fixture, key, deferred, settle } = require('./dom_fixture.cjs');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const a = source.indexOf('const ACTIONS = ') + 'const ACTIONS = '.length, b = source.indexOf('\nS.keymap =', a);
const actions = vm.runInNewContext('(' + source.slice(a, b).trim().replace(/;$/, '') + ')', new Proxy({}, { get: () => () => {} }));
const defaults = keyboard.readKeymap(actions);

test('real default shortcuts distinguish Shift letters, Ctrl/Alt chords, Space and shifted punctuation', () => {
  const cases = [
    [key('T', { shiftKey: true }), 'trim_edit'], [key('t'), 'tool_type'],
    [key('R', { shiftKey: true }), 'rev_match'], [key('P', { ctrlKey: true, shiftKey: true }), 'commands'],
    [key('ArrowLeft', { altKey: true }), 'nudge_l'], [key('ArrowLeft', { ctrlKey: true, altKey: true }), 'rtrim_back'],
    [key(' ', { ctrlKey: true, shiftKey: true }), 'play_inout'],
    [key('+', { shiftKey: true, code: 'Equal' }), 'expand'], [key('_', { shiftKey: true, code: 'Minus' }), 'minimize'],
    [key('?', { shiftKey: true, code: 'Slash' }), 'mark_clip'], [key(')', { shiftKey: true, code: 'Digit0' }), 'multiview'],
    [key('m', { metaKey: true }), 'export'],
  ];
  for (const [event, expected] of cases) assert.equal(keyboard.resolveAction(event, defaults, actions), expected, JSON.stringify(event));
});

test('composition, AltGraph and modifier-only keys never trigger an editing shortcut', () => {
  for (const event of [key('t', { isComposing: true }), key('t', { keyCode: 229 }), key('Control'), key('Shift'), key('Dead'), key('t', { getModifierState: name => name === 'AltGraph' })]) {
    assert.equal(keyboard.resolveAction(event, defaults, actions), undefined);
  }
});

test('shortcut matching preserves non-US letters and does not turn an unbound shifted key into an edit', () => {
  assert.equal(keyboard.comboOf(key('É', { shiftKey: true, code: 'Digit2' })), 'shift+é');
  assert.equal(keyboard.comboOf(key('+', { code: 'NumpadAdd' })), '+');
  assert.equal(keyboard.resolveAction(key('C', { shiftKey: true }), defaults, actions), undefined);
});

test('all published nonempty defaults have a single command owner', () => {
  const owners = new Map();
  for (const [id, combo] of Object.entries(defaults)) {
    if (!combo) continue;
    assert.equal(owners.has(combo), false, `${combo}: ${owners.get(combo)} and ${id}`); owners.set(combo, id);
  }
  assert.ok(owners.size > 90);
});

test('stored bindings retain explicit unbinding and ignore retired or invalid entries', () => {
  const map = keyboard.readKeymap({ play: ['Play', ' '], export: ['Export', 'ctrl+m'] }, { play: '', export: 42, removed: 'r' });
  assert.deepEqual(map, { play: '', export: 'ctrl+m' });
  assert.equal(keyboard.formatShortcut('ctrl+alt+shift+arrowleft'), 'Ctrl+Alt+Shift+←');
  assert.equal(keyboard.formatShortcut('ctrl+shift+ '), 'Ctrl+Shift+Space');
  assert.equal(keyboard.formatShortcut('+'), '+');
});

function editor() {
  const dom = fixture(['dlgKeys', 'keySearch', 'keyList', 'keyMessage', 'keysClose', 'keysReset']);
  const actions = { trim: ['Trim edit', 'shift+t'], play: ['Play / pause', ' '], gain: ['Audio gain', 'g'] };
  const config = { map: keyboard.readKeymap(actions), save: async () => {} }, saves = [], changes = [];
  const controller = keyboard.createShortcutController({ ...dom, actions, getKeymap: () => config.map,
    save: async map => { saves.push(map); await config.save(map); config.map = map; }, changed: () => changes.push(true) });
  controller.open();
  return { ...dom, controller, config, saves, changes,
    bind(id) { dom.nodes.keyList.children.find(row => row.children[1].dataset.key === id).children[1].onclick(); },
    press(event) { dom.nodes.dlgKeys.events.keydown(event); },
  };
}

test('shortcut editing captures full chords, waits for persistence, and then refreshes hints', async () => {
  const app = editor(), save = deferred(); app.config.save = () => save.promise; app.bind('trim');
  app.press(key('V', { ctrlKey: true, altKey: true, shiftKey: true }));
  assert.equal(app.saves[0].trim, 'ctrl+alt+shift+v'); assert.equal(app.config.map.trim, 'shift+t');
  assert.equal(app.nodes.keysClose.disabled, true); assert.equal(app.changes.length, 0);
  save.resolve(); await settle();
  assert.equal(app.config.map.trim, 'ctrl+alt+shift+v'); assert.equal(app.changes.length, 1); assert.equal(app.nodes.keysClose.disabled, false);
});

test('a save failure retains the working bindings and exposes the error', async () => {
  const app = editor(); app.config.save = async () => { throw new Error('Settings are read only'); }; app.bind('trim'); app.press(key('x'));
  await settle(); assert.equal(app.config.map.trim, 'shift+t'); assert.equal(app.changes.length, 0);
  assert.match(app.nodes.keyMessage.textContent, /not confirmed.*read only/); assert.equal(app.nodes.keysClose.disabled, false);
});

test('conflicting bindings are rejected, with a clear way to unbind the previous owner', async () => {
  const app = editor(); app.bind('trim'); app.press(key('g'));
  assert.equal(app.saves.length, 0); assert.match(app.nodes.keyMessage.textContent, /assigned to.*Audio gain/);
  app.nodes.keyList.children[2].children[2].onclick(); await settle();
  assert.equal(app.config.map.gain, ''); app.bind('trim'); app.press(key('g')); await settle();
  assert.equal(app.config.map.trim, 'g');
});

test('Escape cancels capture and close cannot leave a listener that steals later typing', () => {
  const app = editor(); app.bind('trim'); app.press(key('Escape'));
  assert.match(app.nodes.keyMessage.textContent, /canceled/); assert.equal(app.nodes.dlgKeys.open, true);
  app.nodes.keysClose.onclick(); app.press(key('x'));
  assert.equal(app.saves.length, 0); assert.equal(app.document.activeElement, app.origin);
});

test('searching shortcuts and resetting defaults use the same live registry', async () => {
  const app = editor(); app.nodes.keySearch.value = 'shift+t'; app.nodes.keySearch.oninput();
  assert.equal(app.nodes.keyList.children.length, 1);
  app.bind('trim'); app.press(key('x')); await settle();
  assert.equal(app.config.map.trim, 'x');
  assert.equal(app.document.activeElement, app.nodes.keySearch, 'A rebound shortcut that no longer matches search must leave focus in the search field');
  await app.nodes.keysReset.onclick(); assert.equal(app.config.map.trim, 'shift+t');
});

test('Tab, IME composition, and modifier presses cannot create accidental shortcut bindings', () => {
  const app = editor(); app.bind('trim');
  for (const event of [key('Tab'), key('Shift'), key('x', { isComposing: true })]) app.press(event);
  assert.equal(app.saves.length, 0); assert.match(app.nodes.keyMessage.textContent, /Tab is reserved/);
});

test('the production shortcut adapter accepts the actual settings response shape and checks acknowledgment', async () => {
  const extras = fs.readFileSync(path.join(__dirname, '../frontend/extras.js'), 'utf8');
  let config; const state = { keymap: { trim: 'shift+t' } }, scope = { document: {}, CR: { ACTIONS: {} }, S: state,
    window: { FilmocityKeyboard: { createShortcutController(value) { config = value; return { open() {} }; } }, FilmocityCommands: { updateShortcutHints() {} } },
    api: { json: async (method, url, body) => ({ keymap: body.keymap, workspace: 'editing' }) } };
  vm.createContext(scope); vm.runInContext(extras.slice(extras.indexOf('let shortcutController;'), extras.indexOf('$("#xmlInput").onchange')), scope);
  scope.keysDialog(); await config.save({ trim: 'ctrl+alt+t' }); assert.equal(state.keymap.trim, 'ctrl+alt+t');
  scope.api.json = async () => ({}); await assert.rejects(config.save({ trim: 'x' }), /did not confirm/); assert.equal(state.keymap.trim, 'ctrl+alt+t');
});

test('reset restores a keyboard focus target and a menu-launched editor returns to the visible menu', async () => {
  const app = editor(); await app.nodes.keysReset.onclick(); assert.equal(app.document.activeElement, app.nodes.keysReset);
  app.nodes.keysClose.onclick(); const menuButton = app.document.createElement('button'), menuItem = app.document.createElement('button');
  menuItem.closest = () => ({ querySelector: () => menuButton }); menuItem.focus();
  app.controller.open(); app.nodes.keysClose.onclick(); assert.equal(app.document.activeElement, menuButton);
});

test('a new default shortcut cannot override an existing custom binding', () => {
  const map = keyboard.readKeymap(actions, { export: 'ctrl+shift+p' });
  assert.equal(map.export, 'ctrl+shift+p'); assert.equal(map.commands, '');
  assert.equal(keyboard.resolveAction(key('P', { ctrlKey: true, shiftKey: true }), map, actions), 'export');
});
