const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');
const begin = source.indexOf('function showRight(on)');
const end = source.indexOf('const WORKSPACES =', begin);
assert.ok(begin >= 0 && end > begin);

function fixture(dock = 'right', columns = '1fr 1fr') {
  const classes = () => {
    const names = new Set();
    return { contains: name => names.has(name), toggle: (name, on) => on ? names.add(name) : names.delete(name) };
  };
  const row = { style: { gridTemplateColumns: columns }, classList: classes() };
  const right = { style: { display: 'none' }, contains: pane => pane.dock === 'right' };
  const pane = { dock, classList: classes(), parentElement: { parentElement: {} } };
  const sibling = { classList: classes() };
  const tab = { dataset: { pane: 'queue' }, classList: classes() };
  const nodes = { '#rowTop': row, '#rowBottom': { classList: classes() }, '#rightTop': right, '#rightBottom': { style: {} }, '#pane-queue': pane };
  const calls = [];
  const scope = { $: selector => nodes[selector], $$: selector => selector.startsWith(':scope') ? [pane, sibling] : [tab], S: { seq: {} }, CR: { renderProgram: () => calls.push('preview'), panels: { render: () => calls.push('panels') } } };
  vm.createContext(scope); vm.runInContext(source.slice(begin, end), scope);
  return { ...scope, row, right, pane, sibling, tab, calls };
}

test('Render Queue reveals its actual dock and fits in the Editing row', () => {
  const app = fixture(); app.showTab('queue');
  assert.equal(app.right.style.display, '');
  assert.equal(app.row.style.gridTemplateColumns, '1fr 1fr 340px');
  assert.ok(app.pane.classList.contains('on') && app.tab.classList.contains('on'));
  assert.ok(!app.sibling.classList.contains('on'));
  assert.deepEqual(app.calls, ['preview', 'panels']);
});
test('right-panel hide/reopen retains main widths and avoids duplicate columns', () => {
  const app = fixture('right', '420px 1fr 380px');
  app.showRight(true); assert.equal(app.row.style.gridTemplateColumns, '420px 1fr 380px');
  app.showRight(false); assert.equal(app.row.style.gridTemplateColumns, '420px 1fr');
  assert.equal(app.right.style.display, 'none');
  app.showRight(true); app.showRight(true);
  assert.equal(app.row.style.gridTemplateColumns, '420px 1fr 340px');
});
test('a queue docked elsewhere does not open the right panel; absent panes do nothing', () => {
  const app = fixture('project'); app.showTab('queue');
  assert.equal(app.right.style.display, 'none');
  assert.equal(app.row.style.gridTemplateColumns, '1fr 1fr');
  assert.deepEqual(app.calls, ['panels']);
  app.showTab('absent'); assert.deepEqual(app.calls, ['panels']);
});
