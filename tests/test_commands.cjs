const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const commands = require('../frontend/commands.js');
const keyboard = require('../frontend/keyboard.js');
const { fixture, key, deferred, settle } = require('./dom_fixture.cjs');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
const a = source.indexOf('const ACTIONS = ') + 'const ACTIONS = '.length, b = source.indexOf('\nS.keymap =', a);
const actions = vm.runInNewContext('(' + source.slice(a, b).trim().replace(/;$/, '') + ')', new Proxy({}, { get: () => () => {} }));
function menusFromMarkup() {
  const menus = [];
  for (const [, category, html] of index.matchAll(/<div class="menu"><button>([^<]+)<\/button><div class="dd">([\s\S]*?)<\/div><\/div>/g)) {
    for (const [, attrs, label] of html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)) {
      const match = /data-(act|new|sfx|gfx|align|ws|win)="([^"]+)"/.exec(attrs); if (!match) continue;
      const title = label.replace(/<span class="k">.*?<\/span>/g, '').replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').trim();
      menus.push({ kind: match[1], value: match[2], category, title });
    }
  }
  return menus;
}
function catalog() {
  const calls = [], keymap = keyboard.readKeymap(actions), menus = menusFromMarkup();
  const make = () => commands.buildCatalog({ actions, keymap, menus, runAction: id => calls.push('action:' + id), runMenu: menu => calls.push('menu:' + menu.kind + ':' + menu.value),
    panels: [{ value: 'browser', title: 'Media Browser', run: () => calls.push('browser') }] });
  return { calls, keymap, menus, make, list: make() };
}

test('catalog covers live keyboard actions, menu tools, panel switching and workspaces without duplicate aliases', () => {
  const app = catalog(), ids = app.list.map(c => c.id);
  assert.ok(app.list.length > 180); assert.equal(new Set(ids).size, ids.length);
  for (const id of ['action.ripple_del', 'action.import', 'menu.upload', 'menu.recovery', 'menu.shortcuts', 'panel.props', 'panel.browser', 'ws.color', 'sfx.whoosh', 'new.black']) assert.ok(ids.includes(id), id);
  assert.ok(!ids.includes('action.del2')); assert.ok(!ids.includes('menu.export')); assert.ok(!ids.includes('gfx.lower_third'));
  assert.deepEqual(app.list.find(c => c.id === 'action.del').shortcuts, ['delete', 'backspace']);
});

test('menu-only command entries have a production dispatcher or a dedicated binding', () => {
  const panels = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');
  const dispatch = panels.slice(panels.indexOf('function menuAction('), panels.indexOf('function showTab(', panels.indexOf('function menuAction(')));
  for (const menu of catalog().menus.filter(menu => menu.kind === 'act')) {
    assert.ok(new RegExp('(?:\\b' + menu.value + '\\s*[:,}]|act === "' + menu.value + '")').test(dispatch), `Missing dispatcher for ${menu.value}`);
  }
});

test('search ranks exact names and word prefixes before fuzzy aliases and finds export by format', () => {
  const app = catalog();
  assert.equal(commands.searchCommands(app.list, 'ripple delete')[0].id, 'action.ripple_del');
  assert.equal(commands.searchCommands(app.list, 'rip del')[0].id, 'action.ripple_del');
  assert.equal(commands.searchCommands(app.list, 'export mp4')[0].id, 'action.export');
  assert.equal(commands.searchCommands(app.list, 'lost draft')[0].id, 'menu.recovery');
  assert.ok(commands.searchCommands(app.list, 'captions').some(c => c.id === 'panel.caps'));
  assert.equal(commands.searchCommands(app.list, 'zzzzzzzzzzzzzzzz').length, 0);
});

test('search supports diacritics and previously chosen commands without overriding a specific query', () => {
  const list = [{ id: 'one', title: 'Étalonnage', category: 'Color' }, { id: 'two', title: 'Export', category: 'File' }];
  assert.equal(commands.searchCommands(list, 'etalonnage')[0].id, 'one');
  assert.equal(commands.searchCommands(list, '', ['two'])[0].id, 'two');
  assert.equal(commands.searchCommands(list, 'etalonnage', ['two'])[0].id, 'one');
});

test('catalog executes existing handlers and reflects current rebound or unassigned shortcuts', () => {
  const app = catalog(); app.keymap.export = 'ctrl+alt+e'; app.keymap.import = '';
  const list = app.make(), exp = list.find(c => c.id === 'action.export'), imp = list.find(c => c.id === 'action.import');
  assert.deepEqual(exp.shortcuts, ['ctrl+alt+e']); assert.deepEqual(imp.shortcuts, []);
  exp.run(); list.find(c => c.id === 'menu.recovery').run(); list.find(c => c.id === 'panel.browser').run();
  assert.deepEqual(app.calls, ['action:export', 'menu:act:recovery', 'browser']);
});

function state() { return { proj: { media: { media1: { has_audio: true } } }, seq: { tracks: [{ id: 'V1', clips: [{ id: 'c1', media_id: 'media1' }, { id: 'c2', title: {} }] }] }, sel: new Set(), binSel: new Set(), focus: 'timeline' }; }

test('selection requirements explain unavailable actions while preserving gap and bin deletion', () => {
  const s = state(), why = action => commands.disabledReason({ action }, s);
  assert.match(why('group'), /two clips/); assert.match(why('speed'), /one media clip/); assert.match(why('insert'), /Source/);
  s.sel.add('c1'); assert.equal(why('speed'), ''); assert.equal(why('audio_gain'), '');
  s.sel.add('c2'); assert.equal(why('group'), ''); s.sel.clear(); s.gap = {};
  assert.equal(why('del'), ''); s.gap = null; s.binSel.add('media1'); s.focus = 'project'; assert.equal(why('cut'), '');
});

test('recovery and opening projects remain discoverable when the editor cannot load a project', () => {
  const list = catalog().list, s = { recoveryRequired: true };
  for (const id of ['menu.recovery', 'menu.openProject', 'menu.shortcuts']) assert.equal(commands.disabledReason(list.find(c => c.id === id), s), '');
  assert.match(commands.disabledReason(list.find(c => c.id === 'action.new_seq'), s), /Open a project/);
});

function palette() {
  const dom = fixture(['dlgCommands', 'commandSearch', 'commandResults', 'commandMessage', 'commandClose']);
  const calls = [], messages = [], s = state();
  const config = { commands: [
    { id: 'one', title: 'First command', category: 'Editing', shortcuts: ['ctrl+m'], run: () => calls.push('first') },
    { id: 'two', title: 'Second command', category: 'Tools', shortcuts: [], run: () => calls.push('second') },
  ] };
  const controller = commands.createCommandController({ ...dom, getCommands: () => config.commands, getState: () => s, formatShortcut: keyboard.formatShortcut, report: (...args) => messages.push(args) });
  controller.open();
  return { ...dom, controller, config, state: s, calls, messages,
    press(event) { dom.nodes.commandSearch.events.keydown(event); },
    search(value) { dom.nodes.commandSearch.value = value; dom.nodes.commandSearch.oninput(); },
  };
}

test('command keyboard navigation keeps DOM focus on search and runs exactly one chosen action', async () => {
  const app = palette(); assert.equal(app.document.activeElement, app.nodes.commandSearch);
  assert.equal(app.nodes.commandSearch.attributes['aria-activedescendant'], 'command-result-0');
  const down = key('ArrowDown'); app.press(down); assert.equal(down.defaultPrevented, true);
  assert.equal(app.nodes.commandSearch.attributes['aria-activedescendant'], 'command-result-1');
  assert.equal(app.nodes.commandResults.children[1].attributes['aria-selected'], 'true'); assert.equal(app.nodes.commandResults.children[1].scrolled, true);
  app.press(key('Enter')); app.press(key('Enter', { repeat: true })); await settle();
  assert.deepEqual(app.calls, ['second']); assert.equal(app.nodes.dlgCommands.open, false); assert.equal(app.document.activeElement, app.origin);
});

test('text editing, composition and modifier chords are left to the search input', () => {
  const app = palette();
  for (const event of [key('Home'), key('End'), key('ArrowLeft'), key('a', { ctrlKey: true }), key('Enter', { isComposing: true }), key('Enter', { keyCode: 229 }), key('ArrowDown', { altKey: true })]) {
    app.press(event); assert.equal(event.defaultPrevented, false);
  }
  assert.deepEqual(app.calls, []);
});

test('no-result searches remove stale active selection and never run a previous result', async () => {
  const app = palette(); app.search('not here xxyyzz');
  assert.equal(app.nodes.commandSearch.attributes['aria-activedescendant'], undefined); assert.equal(app.nodes.commandSearch.attributes['aria-expanded'], 'false');
  app.press(key('Enter')); await settle(); assert.deepEqual(app.calls, []); assert.match(app.nodes.commandMessage.textContent, /No matching/);
});

test('unavailable commands explain the reason and recheck context when activated', async () => {
  const app = palette(); app.config.commands[0].action = 'group'; app.search('First');
  assert.equal(app.nodes.commandResults.children[0].attributes['aria-disabled'], 'true'); app.press(key('Enter')); await settle();
  assert.equal(app.nodes.dlgCommands.open, true); assert.match(app.nodes.commandMessage.textContent, /two clips/); assert.deepEqual(app.calls, []);
  app.config.commands[0].action = null; app.search('First'); app.state.switching = true;
  app.nodes.commandResults.children[0].onclick(); await settle(); assert.deepEqual(app.calls, []); assert.match(app.nodes.commandMessage.textContent, /Wait/);
});

test('Escape and late close events restore focus without stealing it from a command-opened dialog', async () => {
  const app = palette(); const editorField = app.document.createElement('input');
  app.config.commands[0].run = () => editorField.focus(); app.press(key('Enter')); await settle();
  assert.equal(app.document.activeElement, editorField); app.nodes.dlgCommands.events.close(); assert.equal(app.document.activeElement, editorField);
  app.controller.open(); const escape = key('Escape'); app.nodes.dlgCommands.events.cancel(escape);
  assert.equal(escape.defaultPrevented, true); assert.equal(app.document.activeElement, editorField);
});

test('palette does not nest over another modal or send typing into timeline shortcuts', () => {
  const app = palette(); app.nodes.commandClose.onclick(); app.document.otherDialog = true; app.controller.open();
  assert.equal(app.nodes.dlgCommands.open, false);
  const event = key('Delete'); app.nodes.dlgCommands.events.keydown(event); assert.equal(event.stopped, true);
});

test('async command errors remain visible and duplicate activation while running is refused', async () => {
  const app = palette(), run = deferred(); app.config.commands[0].run = () => { app.calls.push('running'); return run.promise; };
  app.press(key('Enter')); app.controller.open('First'); app.press(key('Enter'));
  assert.deepEqual(app.calls, ['running']); assert.match(app.nodes.commandMessage.textContent, /already running/);
  run.reject(new Error('Missing media')); await settle();
  assert.match(app.messages[0][0], /Could not run.*Missing media/); assert.equal(app.messages[0][1], 'err');
});

test('command labels are literal text even when supplied by an extension', () => {
  const app = palette(); app.config.commands[0].title = '<img src=x onerror=run()>'; app.search('');
  assert.equal(app.nodes.commandResults.children[0].children[0].children[0].textContent, '<img src=x onerror=run()>');
});

test('menu-launched command search returns focus to the visible menu button', () => {
  const app = palette(); app.nodes.commandClose.onclick();
  const menuButton = app.document.createElement('button'), menuItem = app.document.createElement('button');
  menuItem.closest = () => ({ querySelector: () => menuButton }); menuItem.focus();
  app.controller.open(); app.nodes.commandClose.onclick(); assert.equal(app.document.activeElement, menuButton);
});

test('opening command search without a project presents available recovery commands first', () => {
  const app = palette(); app.state.proj = null;
  app.config.commands.push({ id: 'recovery', title: 'Recover project', category: 'File', global: true, shortcuts: [], run() {} });
  app.search(''); assert.equal(app.nodes.commandResults.children[0].children[0].children[0].textContent, 'Recover project');
});
