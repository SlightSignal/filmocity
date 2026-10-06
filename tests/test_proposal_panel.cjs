const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { deferred } = require('./dom_fixture.cjs');
const source = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');
function section(a, b) { const start = source.indexOf(a), end = source.indexOf(b, start); assert.ok(start >= 0 && end > start); return source.slice(start, end); }
function panel() {
  const calls = [], previews = [], pending = deferred();
  const button = dataset => ({ dataset, disabled: false });
  const single = button({ dec: 'accept|p|i1' }), all = button({ all: 'accept|p' }), preview = button({ prev: 'p' }), onePreview = button({ onePrev: 'p|i2' });
  const items = [1, 2].map(n => ({ id: 'i' + n, status: 'pending', reason: '<img src=x>', ops: [{ op: 'set_clip', sequence: 'other', track: 'V1', clip: { id: 'same-id', out: n + 2 } }] }));
  const pane = { innerHTML: '', querySelector: selector => selector.includes('i1') ? { value: 'First note' } : { value: 'Second note' } }, badge = { style: {} };
  const scope = { S: { proj: { media: {}, proposals: [{ id: 'p', title: '<b>Suggested title</b>', ts: 0, items }], sequences: [
    { id: 'active', tracks: [{ id: 'V1', clips: [{ id: 'same-id', title: { text: 'Wrong sequence' }, out: 99 }] }] },
    { id: 'other', tracks: [{ id: 'V1', clips: [{ id: 'same-id', title: { text: 'Correct sequence' }, out: 8 }] }] }]
  }, seq: { id: 'active' } },
    CR: { proposalDecision(pid, selected, decision) { calls.push({ pid, selected, decision }); return pending.promise; }, renderProgram() {} },
    $: id => id === '#propBadge' ? badge : pane,
    $$: selector => selector === '[data-dec]' ? [single] : selector === '[data-all]' ? [all] : selector === '[data-prev]' ? [preview] : selector === '[data-one-prev]' ? [onePreview] : [single, all, preview],
    window: { FilmocityProposalReview: { open(cr, pid, selected, title) { previews.push({ pid, selected, title }); } } },
    CSS: { escape: x => x }, api: { json: async () => ({ scores: [] }) },
    reasonChips: () => '', wireReasons() {}, pickedReasons: (root, id) => id === 'i1' ? ['story'] : ['pacing'],
  };
  vm.createContext(scope);
  vm.runInContext(source.match(/const escapeExportText = [^\r\n]+/)[0], scope);
  vm.runInContext(section('function proposalClip(', '// ---------- Audio mixer'), scope);
  scope.renderProps();
  return { scope, pane, single, all, preview, calls, pending, items, previews, onePreview };
}
test('proposal panel treats agent text as display data and describes the named sequence', () => {
  const h = panel();
  assert.ok(h.pane.innerHTML.includes('&lt;b&gt;Suggested title&lt;/b&gt;'));
  assert.ok(h.pane.innerHTML.includes('&lt;img src=x&gt;')); assert.ok(!h.pane.innerHTML.includes('<img src=x>'));
  assert.ok(h.pane.innerHTML.includes('in other / V1')); assert.ok(h.pane.innerHTML.includes('(was 8)'));
  assert.ok(!h.pane.innerHTML.includes('(was 99)'));
});
test('Accept All sends one batch with each note/reason and disables controls until completion', async () => {
  const h = panel(), action = h.all.onclick();
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].selected.length, 2);
  assert.equal(h.calls[0].selected[0].note, 'First note'); assert.equal(h.calls[0].selected[1].note, 'Second note');
  assert.equal(h.calls[0].selected[1].reasons[0], 'pacing');
  assert.ok(h.single.disabled && h.all.disabled && h.preview.disabled);
  h.pending.resolve(true); await action; assert.equal(h.all.disabled, false);
});
test('single-item decision excludes other pending items and unavailable project actions', async () => {
  const h = panel(); h.scope.S.commandPending = true; await h.single.onclick(); assert.equal(h.calls.length, 0);
  h.scope.S.commandPending = false; const action = h.single.onclick();
  assert.equal(h.calls[0].selected.length, 1); assert.equal(h.calls[0].selected[0].id, 'i1');
  h.pending.resolve(false); await action; assert.equal(h.single.disabled, false);
});

test('preview all and individual review capture the exact pending selection and review notes', () => {
  const h = panel(); h.preview.onclick(); h.onePreview.onclick();
  assert.equal(h.previews.length, 2); assert.equal(h.previews[0].selected.length, 2);
  assert.equal(h.previews[1].selected.length, 1); assert.equal(h.previews[1].selected[0].id, 'i2');
  assert.equal(h.previews[1].selected[0].note, 'Second note'); assert.equal(h.previews[1].title, '<b>Suggested title</b>');
});
