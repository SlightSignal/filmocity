const assert = require('node:assert/strict');
const test = require('node:test');
const { makeModel, makeSession, createController } = require('../frontend/curve-values.js');
const { fixture: editor, plain, saved, until } = require('./helpers/gesture-fixture.cjs');
const { fixture: dom, key, deferred } = require('./dom_fixture.cjs');

function frames(value, extra = {}) { return makeModel({ kind: 'keyframes', key: 'transform.scale', value, duration: 3, playhead: 1, ...extra }); }
function app(kind = 'keyframes', prop = 'transform.scale') {
  const app = editor();
  Object.assign(app.scope.CR, { S: app.scope.S, clipDur: app.scope.clipDur, canEdit: app.scope.canEdit, applyOps: app.scope.applyOps });
  app.clips[0].keyframes = { 'transform.scale': [{ t: 0, v: 1 }, { t: 2, v: 2 }], 'transform.x': [{ t: 0, v: 15 }] };
  app.clips[0].color = { contrast: 0.2, wheels: { shadows: { r: 0, g: 0, b: 0, custom: 1 }, highlights: { r: 0.3 } } };
  app.openSession = () => makeSession(app.scope.CR, { kind, key: prop, c: app.clips[0], tr: app.tr });
  return app;
}
const ids = ['dlgCurveValues', 'curveValuesForm', 'curvePoint', 'curveFields', 'curveMessage', 'curveApply', 'curveCancel', 'curveAdd', 'curveRemove', 'curveReset', 'curveTitle', 'curvePointTools', 'curveHandleHelp'];
function dialog(session) {
  const env = dom(ids), reports = [];
  const controller = createController({ document: env.document, report: (...args) => reports.push(args) });
  controller.open(session, env.origin);
  const nodes = env.nodes;
  return { ...env, controller, reports, session, nodes,
    input(name) { return nodes.curveFields.children.flatMap(x => x.children).find(x => x.name === name); },
    set(name, value, inputEvent = true) { const node = this.input(name); node.value = String(value); if (inputEvent) node.oninput(); },
    submit() { return nodes.curveValuesForm.onsubmit(key('Enter')); },
    cancel() { nodes.curveCancel.onclick(); nodes.dlgCurveValues.events.close(); },
  };
}

test('keyframe drafts retain extension data, easing and handles and never mutate their input', () => {
  const source = [{ t: 2, v: 3 }, { t: 0, v: 1, e: 'bezier', o: [0.4, 0.7], custom: { keep: true } }], before = plain(source);
  const model = frames(source); model.set(0, 'v', '2'); model.set(0, 'o.1', '-0.2');
  assert.deepEqual(source, before); assert.deepEqual(model.sortedValue()[0], { t: 0, v: 2, e: 'bezier', o: [0.4, -0.2], custom: { keep: true } });
});

test('untouched defaults do not introduce easing or tangent fields', () => {
  const model = frames([{ t: 0, v: 1 }]); for (const field of model.fields(0)) model.set(0, field.name, String(field.value));
  assert.equal(model.changed(), false); assert.deepEqual(model.value(), [{ t: 0, v: 1 }]);
});

test('invalid numeric, timing and Bézier values are rejected before project publication', () => {
  const model = frames([{ t: 0, v: 1 }, { t: 2, v: 2 }]);
  for (const value of ['', ' ', 'NaN', 'Infinity']) assert.throws(() => model.set(0, 'v', value), /finite/);
  for (const [field, value] of [['t', -1], ['t', 4], ['i.0', -0.1], ['o.0', 1.1]]) assert.throws(() => model.set(0, field, value), /range/);
  model.set(0, 't', 2); assert.throws(() => model.validate(), /distinct/);
});

test('adding and removing keyframes supports empty/single lists and deterministic free positions', () => {
  const model = frames([]); assert.equal(model.add(), 0); assert.deepEqual(model.value(), [{ t: 1, v: 1 }]);
  model.add(); const times = model.sortedValue().map(p => p.t); assert.equal(new Set(times).size, 2);
  model.remove(0); model.remove(0); assert.deepEqual(model.sortedValue(), []); assert.equal(model.canRemove, false);
});

test('time-remapping values offer only supported easing and keep a positive speed', () => {
  const model = frames([{ t: 0, v: 1 }], { key: 'speed' });
  assert.deepEqual(model.fields(0).find(x => x.name === 'e').options.map(x => x[0]), ['linear', 'hold']);
  assert.equal(model.fields(0).length, 3); assert.throws(() => model.set(0, 'v', 0), /range/); assert.throws(() => model.set(0, 'e', 'bezier'), /listed/);
});

test('trimmed and retimed clips retain editable keys beyond the visible end and existing slow motion', () => {
  const model = frames([{ t: 0, v: 0.02 }, { t: 7, v: 1 }], { key: 'speed', duration: 2 });
  assert.equal(model.fields(1)[0].max, 7); assert.equal(model.validate(), true); model.set(1, 'v', 0.01);
  assert.deepEqual(model.sortedValue(), [{ t: 0, v: 0.02 }, { t: 7, v: 0.01 }]);
});

test('RGB points enforce export spacing, bounds and the minimum point count', () => {
  const model = makeModel({ kind: 'rgb', value: [[0, 0], [1, 1]] }); model.add();
  assert.deepEqual(model.sortedValue(), [[0, 0], [0.5, 0.5], [1, 1]]);
  assert.throws(() => model.set(0, '1', 2), /range/); model.set(2, '0', 0.0004); assert.throws(() => model.validate(), /0.001/);
  model.remove(2); assert.equal(model.canRemove, false); assert.throws(() => model.remove(0), /two/);
});

test('wheel channels reset independently while preserving unknown wheel attributes', () => {
  const value = { r: 0.3, custom: 'keep' }, model = makeModel({ kind: 'wheel', value });
  model.set(0, 'b', -0.4); assert.deepEqual(value, { r: 0.3, custom: 'keep' }); assert.throws(() => model.set(0, 'g', -2), /range/);
  model.reset(); assert.deepEqual(model.sortedValue(), { r: 0, g: 0, b: 0, custom: 'keep' });
});

test('a keyframe session submits one ordered core edit and preserves unrelated animation and transform', async () => {
  const editor = app(); editor.clips[0].transform = { scale: 0.75 }; const session = editor.openSession(); session.model.set(1, 'v', 3);
  const saving = session.apply(); assert.equal(editor.requests.length, 1); const patch = editor.body().ops[0];
  assert.equal(patch.sequence, 's1'); assert.equal(patch.clip.transform, undefined);
  assert.deepEqual(patch.clip.keyframes['transform.x'], [{ t: 0, v: 15 }]); assert.equal(editor.body()._context.project, 'folder-a');
  editor.requests[0].resolve(saved()); assert.deepEqual(await saving, { saved: true, changed: true }); assert.equal(editor.storage.size, 0);
});

test('deleting the last keyframe retains other animated properties and time remap clears to null', async () => {
  const editor = app(), session = editor.openSession(); session.model.remove(1); session.model.remove(0); const saving = session.apply();
  assert.deepEqual(editor.body().ops[0].clip.keyframes, { 'transform.x': [{ t: 0, v: 15 }] }); editor.requests[0].resolve(saved()); await saving;
  const speed = app('keyframes', 'speed'); speed.clips[0].time_remap = [{ t: 0, v: 1 }]; const editing = speed.openSession(); editing.model.remove(0); const clearing = editing.apply();
  assert.equal(speed.body().ops[0].clip.time_remap, null); speed.requests[0].resolve(saved()); await clearing;
});

test('color sessions preserve neighboring channels, wheels and grading fields', () => {
  for (const kind of ['rgb', 'wheel']) {
    const editor = app(kind, 'shadows'), session = editor.openSession(); session.model.set(0, kind === 'rgb' ? '1' : 'r', 0.1); session.apply();
    const color = editor.body().ops[0].clip.color; assert.equal(color.contrast, 0.2); assert.deepEqual(color.wheels.highlights, { r: 0.3 });
    assert.equal(color.wheels.shadows.custom, 1);
  }
});

test('Apply without changes makes no project write and does not materialize a default color curve', async () => {
  const editor = app('rgb'), before = plain(editor.project); assert.deepEqual(await editor.openSession().apply(), { saved: true, changed: false });
  assert.equal(editor.requests.length, 0); assert.deepEqual(plain(editor.project), before);
});

test('stale project, sequence, clip data, storage identity or duration prevents applying a draft', async () => {
  for (const change of [a => { a.scope.S.proj = plain(a.project); }, a => { a.scope.S.seq = a.project.sequences[1]; }, a => { a.scope.S.context = { ...a.scope.S.context, project: 'other' }; }, a => { a.clips[0].out += 1; }, a => { a.clips[0].keyframes['transform.scale'][0].v = 9; }, a => { a.tr.clips = []; }]) {
    const editor = app(), session = editor.openSession(); session.model.set(0, 'v', 3); change(editor);
    await assert.rejects(session.apply(), /changed/); assert.equal(editor.requests.length, 0);
  }
});

test('an earlier queued edit advances the revision before the precise-value edit is sent', async () => {
  const editor = app(), earlier = editor.scope.applyOps([{ op: 'set', path: '/name', value: 'earlier' }]);
  const session = editor.openSession(); session.model.set(0, 'v', 4); const saving = session.apply(); assert.equal(editor.requests.length, 1);
  editor.requests[0].resolve(saved('r1')); await earlier; await until(() => editor.requests.length === 2);
  assert.equal(editor.body(1)._context.revision, 'r1'); editor.requests[1].resolve(saved('r2')); await saving;
});

test('failed save retains the edited project in the browser draft and returns an uncertain result', async () => {
  const editor = app(), session = editor.openSession(); session.model.set(0, 'v', 4); const saving = session.apply();
  editor.requests[0].reject(new Error('Lost response')); assert.deepEqual(await saving, { saved: false, changed: true });
  const draft = JSON.parse([...editor.storage.values()][0]); assert.equal(draft.project.sequences[0].tracks[0].clips[0].keyframes['transform.scale'][0].v, 4);
});

test('the dialog uses labelled native fields, contains keyboard events and cancels without applying', () => {
  const editor = app(), session = editor.openSession(), before = plain(editor.project), ui = dialog(session);
  const field = ui.input('t'); assert.equal(field.parentElement.tagName, 'label'); assert.match(field.parentElement.children[0].textContent, /Time/);
  ui.set('v', 4); const press = key('ArrowRight'); ui.nodes.dlgCurveValues.events.keydown(press); assert.equal(press.stopped, true);
  assert.deepEqual(plain(editor.project), before); ui.cancel(); assert.equal(editor.requests.length, 0); assert.equal(ui.document.activeElement, ui.origin);
});

test('the dialog validates uncommitted input text before applying or changing points', async () => {
  const editor = app(), ui = dialog(editor.openSession()); ui.set('t', '', false); ui.nodes.curvePoint.value = '1'; ui.nodes.curvePoint.onchange();
  assert.equal(ui.nodes.curvePoint.value, '0'); assert.equal(ui.input('t').attributes['aria-invalid'], 'true');
  await ui.submit(); assert.equal(editor.requests.length, 0); assert.match(ui.nodes.curveMessage.textContent, /finite/);
});

test('add, select, remove and reset buttons work on local drafts through native actions', () => {
  const editor = app(), ui = dialog(editor.openSession()); ui.nodes.curveAdd.onclick(); assert.equal(ui.session.model.count, 3);
  ui.nodes.curveRemove.onclick(); assert.equal(ui.session.model.count, 2); assert.equal(editor.requests.length, 0);
  ui.nodes.curvePoint.value = '1'; ui.nodes.curvePoint.onchange(); assert.equal(ui.input('t').value, '2');
  const wheel = app('wheel', 'shadows'), colors = dialog(wheel.openSession()); colors.set('r', 0.7); colors.nodes.curveReset.onclick(); assert.equal(colors.input('r').value, '0');
  assert.equal(colors.nodes.curvePointTools.hidden, true); assert.equal(colors.nodes.curveHandleHelp.hidden, true); assert.equal(wheel.requests.length, 0);
});

test('double Apply is prevented and a confirmed save closes the dialog', async () => {
  const editor = app(), ui = dialog(editor.openSession()); ui.set('v', 2); const saving = ui.submit(); await ui.submit();
  assert.equal(editor.requests.length, 1); assert.equal(ui.nodes.curveFields.disabled, true);
  assert.equal(ui.nodes.curveCancel.textContent, 'Close'); assert.match(ui.nodes.curveMessage.textContent, /will not cancel the save/);
  editor.requests[0].resolve(saved()); await saving;
  assert.equal(ui.nodes.dlgCurveValues.open, false);
});

test('uncertain or thrown commit results disable Apply and direct the user to Recovery without retry', async () => {
  for (const throws of [false, true]) {
    const editor = app(); if (throws) editor.scope.CR.applyOps = async () => { throw new Error('Render failed after application'); };
    const ui = dialog(editor.openSession()); ui.set('v', 3); const saving = ui.submit();
    if (!throws) editor.requests[0].reject(new Error('Lost response')); await saving; await ui.submit();
    assert.equal(ui.nodes.curveApply.disabled, true); assert.equal(ui.nodes.curveCancel.textContent, 'Close'); assert.match(ui.nodes.curveMessage.textContent, /Recovery/);
    assert.equal(editor.requests.length, throws ? 0 : 1);
  }
});

test('a stale dialog preserves its draft for inspection and never enqueues its values', async () => {
  const editor = app(), ui = dialog(editor.openSession()); ui.set('v', 3); editor.scope.S.proj = plain(editor.project);
  await ui.submit(); assert.match(ui.nodes.curveMessage.textContent, /changed/); assert.equal(ui.nodes.dlgCurveValues.open, true); assert.equal(ui.input('v').value, '3'); assert.equal(editor.requests.length, 0);
});

test('closing while saving prevents a late response from changing a newer dialog', async () => {
  const pending = deferred(), first = { model: frames([{ t: 0, v: 1 }]), title: 'First', apply: () => pending.promise };
  const ui = dialog(first); ui.set('v', 2); const saving = ui.submit(); ui.cancel();
  assert.equal(ui.controller.open(first), false); pending.resolve({ saved: true, changed: true }); await saving;
  const next = { ...first, model: frames([{ t: 0, v: 4 }]), title: 'Second' }; assert.equal(ui.controller.open(next), true);
  ui.nodes.dlgCurveValues.events.close(); assert.equal(ui.nodes.dlgCurveValues.open, true); assert.equal(ui.input('v').value, '4');
});

test('literal project key names remain text and focus falls back when the invoking control was rebuilt', () => {
  let focused = false; const ui = dialog({ model: frames([{ t: 0, v: 1 }]), title: '<img src=x onerror=bad()>', apply: async () => ({ saved: true, changed: false }) });
  assert.equal(ui.nodes.curveTitle.textContent, '<img src=x onerror=bad()>'); assert.equal(ui.nodes.curveTitle.innerHTML, undefined);
  ui.cancel(); ui.origin.isConnected = false;
  ui.controller.open(ui.session, ui.origin, () => { focused = true; }); ui.cancel(); assert.equal(focused, true);
});

test('production keyframe and wheel buttons route the captured clip and property to the value editor', () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');
  for (const [attribute, field, value, kind] of [['data-kfedit', 'kfedit', 'transform.scale', 'keyframes'], ['data-wheel-values', 'wheelValues', 'midtones', 'wheel']]) {
    const button = { dataset: { [field]: value } }, c = { id: 'c' }, tr = { id: 't' }, calls = [], CR = {};
    const start = source.indexOf('  $$("[' + attribute + ']"'); assert.ok(start > 0);
    const code = source.slice(start, source.indexOf('\n', start));
    vm.runInNewContext(code, { c, tr, CR, pane: {}, $$: () => [button], window: { FilmocityCurveValues: { open: (...args) => calls.push(args) } } });
    button.onclick(); assert.equal(calls[0][0], CR); assert.equal(calls[0][1].c, c); assert.equal(calls[0][1].tr, tr);
    assert.equal(calls[0][1].kind, kind); assert.equal(calls[0][1].key, value); assert.equal(calls[0][1].origin, button);
  }
});

test('the production RGB button opens its captured curve and the shipped dialog has native form semantics', () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../frontend/extras.js'), 'utf8'), start = source.indexOf('  const valuesButton =');
  const button = {}, calls = [], c = {}, tr = {};
  vm.runInNewContext(source.slice(start, source.indexOf('\n', start)), { c, tr, CR: {}, wrap: { querySelector: () => button }, window: { FilmocityCurveValues: { open: (_, options) => calls.push(options) } } });
  button.onclick(); assert.equal(calls[0].kind, 'rgb'); assert.equal(calls[0].origin, button); assert.equal(calls[0].c, c);
  const index = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  assert.match(index, /<dialog id="dlgCurveValues"[^>]+aria-labelledby="curveTitle"[^>]+aria-describedby="curveDescription"/);
  assert.match(index, /<button id="curveApply" type="submit"/); assert.match(index, /<button id="curveCancel" type="button"/);
  assert.match(index, /id="curveMessage"[^>]+role="status"[^>]+aria-live="polite"/);
});
