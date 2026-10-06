const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { fixture, event, node, plain, saved, read, until } = require('./helpers/gesture-fixture.cjs');
const controls = require('../frontend/property-controls.js');
const panels = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');
const extras = fs.readFileSync(path.join(__dirname, '../frontend/extras.js'), 'utf8');
function section(source, start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start); return source.slice(a, b);
}
function setup(type = 'range', initial = '1') {
  const app = fixture(), { scope } = app;
  Object.assign(scope.CR, { S: scope.S, ...Object.fromEntries(['captureGestureFields', 'watchEditGesture', 'canEdit', 'renderAll', 'renderProgram', 'status', 'keyframesWithValue'].map(k => [k, scope[k]])) });
  scope.CR.bindPropertyControl = (input, options) => controls.bind(scope.CR, input, options);
  scope.CR.propertyTarget = (c, tr, el) => controls.target(scope.CR, c, tr, el);
  const pane = { id: 'pane-ec', querySelectorAll: () => [app.input] };
  app.document.activeElement = null; app.document.getElementById = id => id === pane.id ? pane : null;
  const input = Object.assign(node(), { type, tagName: 'INPUT', value: initial, min: '', max: '', step: '0.1', isConnected: true, ownerDocument: app.document,
    attributes: [{ name: 'data-k', value: 'transform.scale' }], dataset: { k: 'transform.scale' }, closest: () => pane,
    nextElementSibling: { textContent: initial }, checkValidity: () => true,
    getAttribute(name) { return this.attributes.find(a => a.name === name)?.value ?? null; },
    focus() { app.document.activeElement = this; },
  });
  app.input = input; app.pane = pane;
  app.inputEvent = (type, extra) => input.emit(type, event({ target: input, ...extra }));
  app.set = (value, type = 'input') => { input.value = String(value); app.inputEvent(type); };
  app.bind = (options = {}) => controls.bind(scope.CR, input, { c: app.clips[0], tr: app.tr, fields: ['transform'],
    preview: () => { app.clips[0].transform = { ...(app.clips[0].transform || {}), scale: +input.value }; },
    commit: () => scope.applyOps([{ op: 'set_clip', sequence: scope.S.seq.id, track: app.tr.id, clip: { id: 'a', transform: { ...(app.clips[0].transform || {}), scale: +input.value } } }], 'property'), ...options });
  return app;
}

test('native range previews cancel exactly and listeners detach after Escape, blur and input cancellation', () => {
  for (const end of [a => a.escape(), a => a.window.emit('blur'), a => a.inputEvent('blur'), a => a.window.emit('pointercancel')]) {
    const app = setup(), before = plain(app.project); app.bind(); app.inputEvent('mousedown'); app.set(2); assert.equal(app.clips[0].transform.scale, 2);
    end(app); app.up(); app.inputEvent('change'); assert.deepEqual(plain(app.project), before); assert.equal(app.input.value, '1');
    assert.equal(app.requests.length, 0); assert.equal(app.window.listenerCount + app.document.listenerCount, 0);
  }
});

test('native mouseup and change commit once in either event order with one browser draft', async () => {
  for (const changeFirst of [false, true]) {
    const app = setup(); app.bind(); app.inputEvent('mousedown'); app.set(2);
    if (changeFirst) app.inputEvent('change'); app.up(); app.inputEvent('change');
    assert.equal(app.requests.length, 1); assert.equal(app.body().ops[0].clip.transform.scale, 2); assert.equal(app.storage.size, 1);
    app.requests[0].resolve(saved()); await app.scope.flushSaves(); assert.equal(app.storage.size, 0);
  }
});

test('native keyboard and accessibility input can preview and commit without mouse movement', () => {
  for (const key of ['ArrowRight', 'PageUp', 'Home', null]) {
    const app = setup(); app.bind(); if (key) app.inputEvent('keydown', { key }); app.set(1.1); app.inputEvent('change');
    if (key) app.inputEvent('keyup', { key }); assert.equal(app.requests.length, 1); assert.equal(app.body().ops[0].clip.transform.scale, 1.1);
  }
});

test('keyboard Escape restores preview and keyup does not commit the cancelled value', () => {
  const app = setup(); app.bind(); app.inputEvent('keydown', { key: 'ArrowRight' }); app.set(2); app.escape(); app.inputEvent('keyup', { key: 'ArrowRight' });
  assert.equal(app.requests.length, 0); assert.equal(app.input.value, '1'); assert.equal(Object.hasOwn(app.clips[0], 'transform'), false);
});

test('focus returns to the matching rebuilt property input after a keyboard commit', () => {
  const app = setup(); app.input.focus(); const replacement = { getAttribute: k => k === 'data-k' ? 'transform.scale' : null, focus: () => { app.document.activeElement = replacement; } };
  app.bind({ commit: () => { app.pane.querySelectorAll = () => [replacement]; app.scope.applyOps([{ op: 'set', path: '/name', value: 'change' }]); } });
  app.inputEvent('keydown', { key: 'ArrowRight' }); app.set(2); app.inputEvent('change'); assert.equal(app.document.activeElement, replacement);
});

test('stale or detached property nodes cannot write into a new project or sequence with matching clip IDs', () => {
  for (const change of [a => { a.scope.S.seq = a.project.sequences[1]; }, a => { a.scope.S.proj = plain(a.project); }, a => { a.input.isConnected = false; }, a => { a.tr.clips = a.tr.clips.filter(c => c.id !== 'a'); }]) {
    const app = setup(); app.bind(); change(app); app.set(2, 'change'); assert.equal(app.requests.length, 0);
  }
});

test('a removed control cancels an in-flight preview rather than saving into a recreated clip', () => {
  const app = setup(), before = plain(app.clips[0]); app.bind(); app.inputEvent('mousedown'); app.set(2); app.input.isConnected = false; app.up();
  assert.deepEqual(plain(app.clips[0]), before); assert.equal(app.requests.length, 0);
});

test('label scrubbing applies step, Shift multiplier and bounds and saves once', () => {
  const app = setup('number', '1'); app.input.min = '0'; app.input.max = '4'; app.input.step = '0.1'; app.bind();
  app.input.filmocityScrub(event()); app.move({ clientX: 2, shiftKey: true }); assert.equal(+app.input.value, 3);
  app.move({ clientX: 10, shiftKey: true }); assert.equal(+app.input.value, 4); app.up(); assert.equal(app.requests.length, 1);
  assert.equal(app.body().ops[0].clip.transform.scale, 4);
});

test('label scrubbing cancels model fields and empty gestures do not save', () => {
  for (const moved of [false, true]) {
    const app = setup('number'); app.bind(); app.input.filmocityScrub(event());
    if (moved) { app.move({ clientX: 8 }); app.escape(); } else app.up();
    assert.equal(app.requests.length, 0); assert.equal(app.input.value, '1'); assert.equal(Object.hasOwn(app.clips[0], 'transform'), false);
  }
});

test('invalid numeric values and refused edits never mutate the project', () => {
  for (const invalid of ['', 'NaN', 'Infinity', '99']) {
    const app = setup('number'); app.bind(); if (invalid === '99') app.input.checkValidity = () => false;
    app.set(invalid, 'change'); assert.equal(app.requests.length, 0); assert.equal(Object.hasOwn(app.clips[0], 'transform'), false);
  }
  const app = setup(); app.bind(); app.start(); app.set(2); app.inputEvent('change'); assert.equal(app.requests.length, 0); app.escape();
});

test('property gestures block switching, defer refresh, and keep uncertain saved copies for Recovery', async () => {
  const app = setup(); app.bind(); app.inputEvent('mousedown'); app.set(2);
  await assert.rejects(app.scope.testApi.json('POST', '/api/projects/open', {}), /Wait/);
  assert.equal(await app.scope.loadProject(true), false); app.inputEvent('change'); app.requests[0].reject(new Error('Lost reply'));
  await app.scope.flushSaves(); await Promise.resolve(); assert.equal(app.requests.length, 1);
  assert.equal(JSON.parse([...app.storage.values()][0]).project.sequences[0].tracks[0].clips[0].transform.scale, 2);
});

function wireEffect(app, key) {
  const { scope, input } = app; input.dataset.k = key;
  Object.assign(scope, { c: app.clips[0], tr: app.tr, kf: app.clips[0].keyframes || {}, rel: 1, uploadFontDialog() {}, parseTC: Number,
    $$: selector => selector === '[data-k]' ? [input] : [], pane: app.pane });
  vm.runInContext(section(panels, 'function setKeyframe(', '// ---------- Color ----------'), scope);
  vm.runInContext('{' + section(panels, '  const patchFor =', '  $$("[data-gmv]"') +
    section(panels, '  $$("[data-fontadd]", pane).forEach(b => b.onclick = uploadFontDialog); $$("[data-k]"', '  const relNow =') + '}', scope);
}

test('production Effect Controls animate a preview, preserve static values and existing easing when committed', () => {
  const app = setup('range', '1'), c = app.clips[0]; c.transform = { opacity: 0.7 }; c.keyframes = { 'transform.scale': [{ t: 1, v: 1, e: 'ease', o: [0.3, 0] }] };
  wireEffect(app, 'transform.scale'); app.inputEvent('mousedown'); app.set(2); assert.equal(c.keyframes['transform.scale'][0].v, 2);
  app.inputEvent('change'); assert.deepEqual(plain(c.transform), { opacity: 0.7 });
  assert.deepEqual(app.body().ops[0].clip.keyframes['transform.scale'], [{ t: 1, v: 2, e: 'ease', o: [0.3, 0] }]);
});

test('production Effect Controls range and label previews cancel without creating missing nested objects', () => {
  for (const scrub of [false, true]) {
    const app = setup(); wireEffect(app, 'transform.scale'); const before = plain(app.clips[0]);
    if (scrub) { app.input.filmocityScrub(event()); app.move({ clientX: 10 }); } else { app.inputEvent('mousedown'); app.set(2); }
    app.escape(); assert.deepEqual(plain(app.clips[0]), before); assert.equal(app.requests.length, 0);
  }
});

test('production color range preview rolls back original grading on cancellation', () => {
  const app = setup('range', '0'); app.input.dataset.c = 'exposure'; app.clips[0].color = { contrast: 0.25 };
  Object.assign(app.scope, { c: app.clips[0], tr: app.tr, pane: app.pane, $$: () => [app.input], commit: () => assert.fail('cancel must not save') });
  vm.runInContext(section(panels, '  $$("[data-c]", pane)', '  $("#colReset")'), app.scope);
  app.inputEvent('mousedown'); app.set(0.4); assert.equal(app.clips[0].color.exposure, 0.4); app.escape();
  assert.deepEqual(plain(app.clips[0].color), { contrast: 0.25 });
});

test('production effect-stack preview does not mutate the saved parameter objects and commits after rollback', () => {
  const app = setup('range', '0'); app.input.dataset.sp = 'fx1|amount'; const stack = [{ id: 'fx1', type: 'vignette', params: { amount: 0, other: 1 } }]; app.clips[0].fx_stack = stack;
  Object.assign(app.scope, { c: app.clips[0], tr: app.tr, key: 'fx_stack', wrap: {}, $$: () => [app.input], save: list => app.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', fx_stack: list } }]) });
  vm.runInContext(section(extras, '  $$("[data-sp]", wrap)', '  $$("[data-fxen]"'), app.scope);
  app.inputEvent('mousedown'); app.set(0.7); assert.equal(stack[0].params.amount, 0); app.up();
  assert.deepEqual(app.body().ops[0].clip.fx_stack[0].params, { amount: 0.7, other: 1 });
});

test('production keyframe lane commits captured timing and blocks project-switch redirection', async () => {
  const app = setup(), dot = Object.assign(node(), { isConnected: true, dataset: { kfi: 'transform.scale|0' }, parentElement: node() });
  const keyframes = [{ t: 0, v: 1 }, { t: 2, v: 2 }];
  Object.assign(app.scope, { c: app.clips[0], tr: app.tr, pane: {}, $$: () => [dot], kfList: () => keyframes,
    writeKf: (key, list) => app.scope.applyOps([{ op: 'set_clip', sequence: app.scope.S.seq.id, track: 'v1', clip: { id: 'a', keyframes: { [key]: list } } }]) });
  vm.runInContext(section(panels, '  $$("[data-kfi]", pane)', '  for (const key of Object.keys(kf))'), app.scope);
  dot.onmousedown(event()); app.move({ clientX: 300 }); assert.equal(keyframes[0].t, 0);
  await assert.rejects(app.scope.testApi.json('POST', '/api/projects/open', {}), /Wait/); app.up(); assert.equal(app.body().ops[0].clip.keyframes['transform.scale'][0].t, 1.5);
});

function curve(app, rgb = false) {
  const ctx = new Proxy({}, { get: (object, key) => object[key] || (() => {}) });
  const canvases = [];
  app.document.createElement = tag => { const el = Object.assign(node(), { isConnected: true, getContext: () => ctx, appendChild() {}, querySelector: () => ({}) });
    if (tag === 'canvas') { el.getBoundingClientRect = () => ({ left: 0, top: 0, width: el.width, height: el.height }); canvases.push(el); } return el; };
  app.scope.CR.kfVal = (list, t) => list[0].v;
  const pane = { querySelector: () => ({ after() {} }), appendChild() {} };
  if (rgb) { vm.runInContext(section(extras, 'CR.curvesEditor =', 'function trackMenu('), app.scope); app.scope.CR.curvesEditor(pane, app.clips[0], app.tr); }
  else { vm.runInContext(section(extras, 'function curveEditor(', '// ---- clip context menu'), app.scope); app.scope.curveEditor(pane, app.clips[0], app.tr, 'transform.scale'); }
  return canvases[0];
}

test('production value graph edits cloned points and cancellation preserves values, easing and tangents', () => {
  for (const modifiers of [{}, { altKey: true }, { shiftKey: true }]) {
    const app = setup(); app.clips[0].keyframes = { 'transform.scale': [{ t: 0, v: 1 }, { t: 2, v: 2 }] }; const before = plain(app.clips[0]); const cv = curve(app);
    const at = modifiers.shiftKey ? { clientX: 12 + 2 / 3 * 536, clientY: 12 + 0.15 / 1.3 * 168 } : { clientX: 12, clientY: 180 - 0.15 / 1.3 * 168 };
    cv.onmousedown(event({ ...at, ...modifiers })); assert.ok(app.scope.S.gesture); app.move({ clientX: 100, clientY: 100 });
    assert.deepEqual(plain(app.clips[0]), before); app.escape(); app.up(); assert.deepEqual(plain(app.clips[0]), before); assert.equal(app.requests.length, 0);
  }
});

test('production value graph saves a moved point once and rejects later detached or stale callbacks', () => {
  const app = setup(); app.clips[0].keyframes = { 'transform.scale': [{ t: 0, v: 1 }, { t: 2, v: 2 }] }; const cv = curve(app);
  cv.onmousedown(event({ clientX: 12, clientY: 180 - 0.15 / 1.3 * 168 })); app.move({ clientY: 96 }); app.up();
  assert.equal(app.body().ops[0].clip.keyframes['transform.scale'][0].v, 1.5);
  cv.isConnected = false; cv.onmousedown(event({ clientX: 12, clientY: 96 })); assert.equal(app.scope.S.gesture, null); assert.equal(app.requests.length, 1);
});

test('RGB curve insert and drag cancellation leave project colors unchanged', () => {
  const app = setup(); app.clips[0].color = { curves: [[0, 0], [1, 1]], contrast: 0.2 }; const before = plain(app.clips[0]); const cv = curve(app, true);
  cv.onmousedown(event({ clientX: 150, clientY: 100 })); app.move({ clientX: 180, clientY: 180 }); app.escape(); app.up();
  assert.deepEqual(plain(app.clips[0]), before); assert.equal(app.requests.length, 0);
});

test('production color wheels cancel without altering grading and commit only to their original clip', () => {
  for (const cancel of [true, false]) {
    const app = setup(), cv = Object.assign(node(), { isConnected: true, dataset: { wheel: 'shadows' } });
    const ctx = new Proxy({}, { get: (o, k) => o[k] || (k.startsWith('create') ? () => ({ addColorStop() {} }) : () => {}) }); cv.getContext = () => ctx;
    app.clips[0].color = { contrast: 0.2 }; Object.assign(app.scope, { c: app.clips[0], tr: app.tr, pane: {}, $$: () => [cv] });
    vm.runInContext(section(panels, '  $$("[data-wheel]", pane)', '\n}'), app.scope);
    cv.onmousedown(event({ clientX: 100, clientY: 100 })); app.move({ clientX: 200, clientY: 100 });
    assert.deepEqual(plain(app.clips[0].color), { contrast: 0.2 });
    if (cancel) { app.escape(); assert.equal(app.requests.length, 0); }
    else { app.up(); assert.equal(app.body().ops[0].clip.color.contrast, 0.2); assert.ok(app.body().ops[0].clip.color.wheels.shadows); }
  }
});

test('RGB curve insertion saves its ordered points without replacing other color controls', () => {
  const app = setup(); app.clips[0].color = { curves: [[0, 0], [1, 1]], contrast: 0.2 }; const cv = curve(app, true);
  cv.onmousedown(event({ clientX: 150, clientY: 150 })); app.up();
  const col = app.body().ops[0].clip.color; assert.equal(col.contrast, 0.2); assert.deepEqual(col.curves, [[0, 0], [0.5, 0.5], [1, 1]]);
});

test('production video effects, masks and audio effects remain editable through the shared controls', () => {
  for (const [dataset, key, prefix, end, field] of [
    ['fxv', 'blur', '  $$("[data-fxv]", wrap)', '\n}', 'effects'],
    ['mk', 'feather', '  $$("[data-mk]", wrap)', '\n  audioFx(pane', 'mask'],
    ['fx', 'eq.low_db', '  $$("[data-fx]", wrap)', '\n}', 'audio_fx'],
  ]) {
    const app = setup('range', '0'); app.input.dataset[dataset] = key; app.clips[0][field] = { retained: 1 };
    Object.assign(app.scope, { c: app.clips[0], tr: app.tr, wrap: {}, $$: () => [app.input], mk: app.clips[0].mask });
    vm.runInContext(section(extras, prefix, end), app.scope);
    app.inputEvent('mousedown'); app.set(2); app.up();
    const patch = app.body().ops[0].clip[field]; assert.equal(patch.retained, 1); assert.equal(key.includes('.') ? patch.eq.low_db : patch[key], 2);
  }
});

test('asynchronous Effect Controls and Color redraws defer while an input owns the gesture', () => {
  const app = setup(); app.bind(); app.inputEvent('mousedown'); app.set(2); let renders = 0;
  app.scope.CR.panels.render = () => { renders++; };
  vm.runInContext(section(panels, 'function renderEC()', 'function textEditor('), app.scope);
  vm.runInContext(section(panels, 'function renderColor()', '\nfunction '), app.scope);
  app.scope.renderEC(); app.scope.renderColor(); assert.equal(renders, 0); assert.ok(app.scope.S.gesture.panelsNeeded);
  app.escape(); assert.equal(renders, 1); assert.equal(app.requests.length, 0);
});

test('a range control commits successive keyboard changes in save order without duplicates on late change', async () => {
  const app = setup(); app.bind();
  app.inputEvent('keydown', { key: 'ArrowRight' }); app.set(1.1); app.inputEvent('keyup', { key: 'ArrowRight' }); app.inputEvent('change');
  app.inputEvent('keydown', { key: 'ArrowRight' }); app.set(1.2); app.inputEvent('change'); app.inputEvent('keyup', { key: 'ArrowRight' });
  assert.equal(app.requests.length, 1); app.requests[0].resolve(saved('r1')); await until(() => app.requests.length === 2);
  assert.equal(app.body(1)._context.revision, 'r1'); assert.equal(app.body(1).ops[0].clip.transform.scale, 1.2);
  app.requests[1].resolve(saved('r2')); await app.scope.flushSaves(); assert.equal(app.storage.size, 0);
});

test('a deferred refresh resumes after a confirmed property commit', async () => {
  const app = setup(); app.bind(); app.inputEvent('mousedown'); app.set(2); await app.scope.loadProject(true); app.up();
  app.requests[0].resolve(saved()); await until(() => app.requests.length === 2); app.requests[1].resolve(read(app.project, 'r1'));
  await until(() => app.scope.S.proj !== app.project); assert.equal(app.scope.S.seq.tracks[0].clips[0].transform.scale, 2);
});


test('production source time fields use the source rate and reject invalid labels without writes', () => {
  const timing = require('../frontend/timeline-time.js');
  for (const value of ['bad', '00:00:00:24', '00:01:00;00']) {
    const app = setup('text', '00:00:00:00'); app.input.classList.contains = name => name === 'tcin'; wireEffect(app, 'in_');
    app.scope.m = { fps: 24 }; app.scope.CR.mediaRate = m => m.fps;
    app.scope.parseTC = (v, fps) => { try { return timing.parseTimecode(v, fps); } catch { return NaN; } };
    const before = plain(app.project); app.inputEvent('focus'); app.set(value); app.inputEvent('change');
    assert.equal(app.requests.length, 0); assert.deepEqual(plain(app.project), before);
  }
  const app = setup('text', '00:00:00:00'); app.input.classList.contains = name => name === 'tcin'; wireEffect(app, 'in_');
  app.scope.m = { fps: 24000/1001 }; app.scope.CR.mediaRate = m => m.fps;
  app.scope.parseTC = timing.parseTimecode; app.inputEvent('focus'); app.set('00:00:00:01'); app.inputEvent('change');
  assert.equal(app.body().ops[0].clip.in_, 1001/24000);
});
