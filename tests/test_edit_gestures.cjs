const assert = require('node:assert/strict');
const test = require('node:test');
const { fixture, event, node, plain, until, saved, read, context, section } = require('./helpers/gesture-fixture.cjs');

test('ripple trim uses original neighbor timing on every move and commits every changed clip', async () => {
  const app = fixture(); app.scope.S.tool = 'ripple'; app.start('r');
  app.move({ clientX: 60 }); assert.equal(app.clips[1].start, 4);
  app.move({ clientX: 90 }); assert.equal(app.clips[1].start, 4.5); assert.equal(app.clips[2].start, 7.5);
  app.up(); assert.deepEqual(app.body().ops.map(x => [x.clip.id, x.clip.start, x.clip.out]), [['a', 0, 9.5], ['b', 4.5, 8], ['c', 7.5, 8]]);
  app.requests[0].resolve(saved()); await app.scope.flushSaves(); assert.equal(app.storage.size, 0);
});

test('ripple head, roll head/tail and slide save their adjacent clip changes', () => {
  for (const [tool, handle, index, expected] of [
    ['ripple', 'l', 1, [['b', 3, 6, 8], ['c', 5, 5, 8]]],
    ['roll', 'l', 1, [['b', 4, 6, 8], ['a', 0, 5, 9]]],
    ['roll', 'r', 0, [['a', 0, 5, 9], ['b', 4, 6, 8]]],
    ['slide', null, 1, [['b', 4, 5, 8], ['a', 0, 5, 9], ['c', 7, 6, 8]]],
  ]) {
    const app = fixture(); app.scope.S.tool = tool; app.start(handle, index); app.move({ clientX: 30 }); app.move({ clientX: 60 }); app.up();
    assert.deepEqual(app.body().ops.map(x => [x.clip.id, x.clip.start, x.clip.in_, x.clip.out]), expected, tool + handle);
  }
});

test('Ctrl-drag inserts clips and shifted markers together in the guarded transaction', () => {
  const app = fixture(); app.start(); app.move({ clientX: 180, ctrlKey: true }); app.up({ ctrlKey: true });
  const body = app.body(); assert.equal(body.tool, 'insert_drag'); assert.equal(body._context.project, 'folder-a');
  assert.deepEqual(body.ops.find(x => x.path?.endsWith('/markers'))?.value, [{ time: 1 }, { time: 7 }]);
  assert.equal(app.seq.markers[1].time, 7); assert.equal(app.requests.length, 1);
});

test('Ctrl-drag of a group keeps ordinary group-move behavior and saves every selected preview', () => {
  const app = fixture(); app.scope.S.sel = new Set(['a', 'b']);
  app.scope.startDrag(event(), app.clips[0], app.tr, null, node()); app.move({ clientX: 60, ctrlKey: true }); app.up({ ctrlKey: true });
  assert.deepEqual(app.body().ops.filter(x => x.op === 'set_clip').map(x => [x.clip.id, x.clip.start]), [['a', 1], ['b', 4]]);
  assert.deepEqual(app.seq.markers, [{ time: 1 }, { time: 4 }]);
});

test('trim monitor draws the new endpoint on the same movement event', () => {
  const app = fixture(), frames = []; app.scope.renderProgram = () => frames.push([app.scope.S.previewT, app.clips[0].out]);
  app.clips[1].start = 5; app.start('r'); app.move({ clientX: 60 }); assert.deepEqual(frames.at(-1), [4, 9]); app.escape();
});

test('Escape cancels all live ripple changes, clears listeners and ignores late movement and release', () => {
  const app = fixture(), before = plain(app.project); app.scope.S.tool = 'ripple'; app.start('r');
  const late = app.window.callbacks('mousemove')[0]; app.move({ clientX: 60 }); app.escape();
  late(event({ clientX: 120 })); app.up(); assert.deepEqual(plain(app.project), before);
  assert.equal(app.requests.length, 0); assert.equal(app.scope.S.gesture, null);
  assert.equal(app.window.listenerCount + app.document.listenerCount, 0);
});

test('blur, hidden document, pointer cancellation and outside release each roll back without saving', () => {
  for (const cancel of [a => a.window.emit('blur'), a => { a.document.hidden = true; a.document.emit('visibilitychange'); }, a => a.window.emit('pointercancel'), a => a.move({ buttons: 0 })]) {
    const app = fixture(), before = plain(app.project); app.start(); app.move({ clientX: 60 }); cancel(app); app.up();
    assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0);
    assert.equal(app.window.listenerCount + app.document.listenerCount, 0);
  }
});

test('project and sequence switches, Undo and competing edits are refused throughout a drag', async () => {
  const app = fixture(); app.start(); app.move({ clientX: 60 });
  await assert.rejects(app.scope.testApi.json('POST', '/api/projects/open', { id: 'b' }), /Wait/);
  app.scope.switchSeq('s2'); await app.scope.historyAction('undo');
  assert.equal(await app.scope.applyOps([{ op: 'set', path: '/name', value: 'blocked' }]), false);
  assert.equal(app.scope.S.seq, app.seq); assert.equal(app.requests.length, 0); app.escape();
  app.scope.switchSeq('s2'); assert.equal(app.scope.S.seq.id, 's2');
});

test('changed project or sequence ownership cannot redirect a pending drag into reused clip IDs', () => {
  for (const change of [a => { a.scope.S.seq = a.project.sequences[1]; a.scope.S.seqId = 's2'; }, a => { a.scope.S.proj = plain(a.project); }, a => { a.scope.S.context = context('r0', 'other-folder'); }]) {
    const app = fixture(), before = plain(app.project); app.start(); app.move({ clientX: 60 }); change(app); app.up();
    assert.deepEqual(plain(app.project), before); assert.equal(app.requests.length, 0); assert.equal(app.scope.S.gesture, null);
  }
});

test('refresh requested during drag waits until its committed save and then reads once', async () => {
  const app = fixture(); app.start(); app.move({ clientX: 60 });
  assert.equal(await app.scope.loadProject(true), false); assert.equal(await app.scope.loadProject(true), false); assert.equal(app.requests.length, 0);
  app.up(); await Promise.resolve(); assert.equal(app.requests.length, 1);
  app.requests[0].resolve(saved()); await until(() => app.requests.length === 2);
  app.requests[1].resolve(read(app.project, 'r1')); await until(() => app.scope.S.proj !== app.project);
  assert.equal(app.scope.S.seq.tracks[0].clips.find(x => x.id === 'a').start, 1); assert.equal(app.storage.size, 0);
});

test('refresh begun before drag cannot overwrite its preview and is replaced after cancellation', async () => {
  const app = fixture(), before = plain(app.project), loading = app.scope.loadProject(true);
  app.start(); app.move({ clientX: 60 }); app.requests[0].resolve(read(before));
  assert.equal(await loading, false); assert.equal(app.clips[0].start, 1);
  app.escape(); await until(() => app.requests.length === 2); app.requests[1].resolve(read(before));
  await until(() => app.scope.S.proj !== app.project); assert.deepEqual(plain(app.scope.S.proj), before);
});

test('known earlier save may advance the revision during drag without invalidating its captured objects', async () => {
  const app = fixture(), saving = app.scope.applyOps([{ op: 'set', path: '/name', value: 'earlier' }]);
  app.start(); app.move({ clientX: 60 }); app.requests[0].resolve(saved()); await saving;
  app.up(); assert.equal(app.requests.length, 2); assert.equal(app.body(1)._context.revision, 'r1');
  assert.equal(app.body(1).ops.find(x => x.op === 'set_clip').clip.start, 1);
});

test('normalization of an earlier save defers refresh and retains its draft through gesture cancellation', async () => {
  const app = fixture(), saving = app.scope.applyOps([{ op: 'set', path: '/name', value: 'earlier' }]);
  app.start(); app.move({ clientX: 60 }); app.requests[0].resolve(saved('r1', { warnings: ['Normalized'] })); await saving;
  assert.equal(app.requests.length, 1); assert.equal(app.storage.size, 1);
  app.escape(); await until(() => app.requests.length === 2); app.requests[1].resolve(read(app.project, 'r1'));
  await until(() => app.storage.size === 0); assert.equal(app.clips[0].start, 0);
});

test('an uncertain earlier save cancels preview and retains the earlier draft without retry or deferred refresh', async () => {
  const app = fixture(), saving = app.scope.applyOps([{ op: 'set', path: '/name', value: 'earlier' }]);
  app.start(); app.move({ clientX: 60 }); await app.scope.loadProject(true);
  app.requests[0].reject(new Error('Disconnected')); await saving; app.up();
  await Promise.resolve(); assert.equal(app.requests.length, 1); assert.equal(app.clips[0].start, 0);
  const draft = JSON.parse([...app.storage.values()][0]); assert.equal(draft.project.name, 'earlier'); assert.equal(draft.project.sequences[0].tracks[0].clips[0].start, 0);
  app.start(); assert.equal(app.scope.S.gesture, null); assert.match(app.messages.at(-1), /Recovery/);
});

test('failed gesture commit retains its editor copy for Recovery and never automatically refreshes it away', async () => {
  const app = fixture(); app.start(); app.move({ clientX: 60 }); await app.scope.loadProject(true); app.up();
  app.requests[0].reject(new Error('Lost reply')); await app.scope.flushSaves(); await Promise.resolve();
  assert.equal(app.requests.length, 1); assert.equal(app.clips[0].start, 1); assert.equal(app.storage.size, 1);
});

test('Alt duplicate is removed on Escape or click without movement and a moved duplicate does not slip', () => {
  for (const action of ['escape', 'click', 'commit']) {
    const app = fixture(), duplicate = { ...app.clips[0], id: 'duplicate' }; app.tr.clips.push(duplicate);
    app.scope.S.sel = new Set([duplicate.id]); app.scope.S.dupPending = { clip: duplicate, selection: ['a'] };
    app.scope.startDrag(event({ altKey: true }), duplicate, app.tr, null, node());
    if (action !== 'click') app.move({ clientX: 60, altKey: true });
    if (action === 'escape') app.escape(); else app.up({ altKey: true });
    if (action === 'commit') { assert.equal(app.body().ops[0].clip.start, 1); assert.equal(app.body().ops[0].clip.in_, 5); }
    else { assert.equal(app.tr.clips.length, 3); assert.deepEqual([...app.scope.S.sel], ['a']); assert.equal(app.requests.length, 0); }
    assert.equal(app.scope.S.dupPending, null);
  }
});

test('cross-track drag commits a removal and insert, and cancelled drag leaves both tracks intact', () => {
  for (const cancel of [false, true]) {
    const app = fixture(); app.seq.tracks.push({ id: 'v2', kind: 'video', clips: [] });
    app.document.elementsFromPoint = () => [{ classList: { contains: () => true }, dataset: { track: 'v2' } }];
    app.start(); app.move({ clientX: 60 });
    if (cancel) { app.escape(); assert.equal(app.tr.clips.length, 3); assert.equal(app.seq.tracks[1].clips.length, 0); }
    else { app.up(); assert.deepEqual(app.body().ops.map(x => [x.op, x.track]), [['remove_clip', 'v1'], ['set_clip', 'v2']]); }
  }
});

test('opacity, gain and rate previews restore exact field presence on cancel and ignore clicks without movement', () => {
  for (const name of ['opDrag', 'gainDrag', 'rateStretch']) for (const move of [false, true]) {
    const app = fixture(), before = plain(app.clips[0]);
    app.scope[name](event(), app.clips[0], app.tr, node(), 'r');
    if (move) { app.move({ clientX: 60, clientY: 22 }); app.escape(); } else app.up();
    assert.deepEqual(plain(app.clips[0]), before, name); assert.equal(app.requests.length, 0);
  }
});

test('caption timing and transition/keyframe previews cancel without committing or leaking data', () => {
  for (const name of ['captionDrag', 'transitionDrag', 'opKfDrag', 'gainKfDrag']) {
    const app = fixture(), c = app.clips[0], target = node();
    c.transition_in = { type: 'dissolve', duration: 0.5 }; c.keyframes = { 'transform.opacity': [{ t: 0, v: 0.7 }], 'audio.gain_db': [{ t: 0, v: -3 }] };
    app.seq.captions = [{ id: 'caption', start: 1, end: 2 }]; target.dataset.ot = target.dataset.gt = '0';
    const before = plain(app.project);
    if (name === 'captionDrag') app.scope[name](event({ target }), app.seq.captions[0], node());
    else app.scope[name](event({ target }), c, app.tr, name === 'transitionDrag' ? 'in' : node());
    app.move({ clientX: 60, clientY: 10 }); app.escape(); app.up();
    assert.deepEqual(plain(app.project), before, name); assert.equal(app.requests.length, 0);
  }
});

test('monitor scale, rotation and position cancel exactly and simple clicks do not write', () => {
  for (const kind of ['scale', 'rotate', 'position']) for (const move of [false, true]) {
    const app = fixture(), c = app.clips[0], before = plain(c);
    app.scope.hit = kind === 'position' ? null : { kind, c, tr: app.tr, sc: 1, rot0: 0, cx: 0, cy: 0 };
    app.scope.canvasDrag(event({ clientX: 10 }));
    if (move) { app.move({ clientX: 20, clientY: 20 }); app.escape(); } else app.up();
    assert.deepEqual(plain(c), before, kind); assert.equal(app.requests.length, 0);
  }
});

test('keyframed scale and position preview and commit keyframes without silently changing static transform values', () => {
  for (const [kind, key, value] of [['scale', 'transform.scale', 2], ['position', 'transform.x', 15]]) {
    const app = fixture(), c = app.clips[0]; c.transform = { opacity: 0.8 }; c.keyframes = { [key]: [{ t: 0, v: kind === 'scale' ? 1 : 5 }] };
    app.scope.hit = kind === 'position' ? null : { kind, c, tr: app.tr, sc: 1, rot0: 0, cx: 0, cy: 0 };
    app.scope.canvasDrag(event({ clientX: 10 })); app.move({ clientX: 20 });
    assert.equal(c.keyframes[key].find(x => x.t === 1).v, value);
    app.up();
    assert.deepEqual(plain(c.transform), { opacity: 0.8 });
    const clip = app.body().ops[0].clip; assert.equal(clip.transform, undefined); assert.equal(clip.keyframes[key].find(x => x.t === 1).v, value);
  }
});

test('monitor rotation retains its static transform contract and Shift snaps to fifteen degrees', () => {
  const app = fixture(), c = app.clips[0]; c.transform = { opacity: 0.8, rotation: 10 };
  app.scope.hit = { kind: 'rotate', c, tr: app.tr, rot0: 10, cx: 0, cy: 0 };
  app.scope.canvasDrag(event({ clientX: 10 })); app.move({ clientX: 0, clientY: 10, shiftKey: true }); app.up();
  assert.deepEqual(app.body().ops[0].clip.transform, { opacity: 0.8, rotation: 105 });
});

test('cancelling animated monitor previews restores all keyframe values and easing metadata', () => {
  for (const kind of ['scale', 'position']) {
    const app = fixture(), c = app.clips[0]; c.keyframes = { 'transform.scale': [{ t: 0, v: 1 }], 'transform.x': [{ t: 1, v: 10, e: 'ease_out', o: [0.4, 0] }] };
    const before = plain(c); app.scope.hit = kind === 'scale' ? { kind, c, tr: app.tr, sc: 1, cx: 0, cy: 0 } : null;
    app.scope.canvasDrag(event({ clientX: 10 })); app.move({ clientX: 20 }); app.escape();
    assert.deepEqual(plain(c), before); assert.equal(app.requests.length, 0);
  }
});

test('moving an existing animated position preserves its easing and tangent metadata', () => {
  const app = fixture(), c = app.clips[0]; c.keyframes = { 'transform.x': [{ t: 1, v: 10, e: 'ease_out', o: [0.4, 0] }] };
  app.scope.canvasDrag(event()); app.move({ clientX: 20 }); app.up();
  assert.deepEqual(app.body().ops[0].clip.keyframes['transform.x'], [{ t: 1, v: 30, e: 'ease_out', o: [0.4, 0] }]);
  assert.equal(Object.hasOwn(c, 'transform'), false);
});

test('rectangle and ellipse previews are removed on cancellation and line draft never leaks into a save', () => {
  for (const tool of ['rect', 'ellipse', 'line']) {
    const app = fixture(), before = plain(app.project); app.scope.S.tool = tool;
    app.scope.canvasDrag(event()); app.move({ clientX: 100, clientY: 80 }); app.escape(); app.up();
    assert.deepEqual(plain(app.project), before); assert.deepEqual([...app.scope.S.sel], ['a']); assert.equal(app.requests.length, 0); assert.equal(app.scope.S.lineDraft, null);
  }
});

test('drag pauses playback and cancellation after a move exception removes every listener', () => {
  const app = fixture(); app.scope.S.playing = true;
  let rolledBack = false;
  app.scope.watchEditGesture(event(), () => { throw new Error('draw failed'); }, () => assert.fail('must not commit'), () => { rolledBack = true; });
  assert.equal(app.scope.S.playing, false); app.move(); assert.equal(rolledBack, true);
  assert.equal(app.window.listenerCount + app.document.listenerCount, 0); assert.match(app.messages.at(-1), /draw failed/);
});

test('commit exceptions pause saves, retain a draft and detach all input listeners', () => {
  const app = fixture(); app.scope.watchEditGesture(event(), () => {}, () => { app.project.name = 'unconfirmed'; throw new Error('callback failed'); });
  app.up(); assert.equal(app.window.listenerCount + app.document.listenerCount, 0); assert.equal(app.scope.S.gesture, null);
  assert.equal(app.scope.projectSaveState().error.includes('callback failed'), true); assert.equal(app.storage.size, 1);
});

test('acknowledgement of an earlier save cannot erase a draft retained after a gesture callback failure', async () => {
  const app = fixture(), saving = app.scope.applyOps([{ op: 'set', path: '/name', value: 'earlier' }]);
  app.scope.watchEditGesture(event(), () => {}, () => { app.project.name = 'unconfirmed'; throw new Error('callback failed'); }); app.up();
  app.requests[0].resolve(saved()); await saving;
  assert.equal(JSON.parse([...app.storage.values()][0]).project.name, 'unconfirmed');
  assert.match(app.scope.projectSaveState().error, /callback failed/);
});


test('starting a gesture during Trim Edit playback cannot queue a late trim after release or cancellation', () => {
  const vm = require('node:vm');
  for (const finish of ['release', 'cancel']) {
    const app = fixture(), timers = []; let trims = 0;
    Object.assign(app.scope, { setTimeout: callback => timers.push(callback), trimToPlayhead: () => { trims++; }, cancelAnimationFrame() {} });
    vm.runInContext('let playbackFrame = null;\n' + section('function togglePlay(', '// ---------- source monitor'), app.scope);
    app.scope.S.trimMode = true; app.scope.S.playing = true;
    app.start(); app.move({ clientX: 60 });
    if (finish === 'release') app.up(); else app.escape();
    assert.equal(app.scope.S.playing, false); assert.equal(app.scope.S.trimMode, true);
    for (const callback of timers.splice(0)) callback();
    assert.equal(trims, 0); assert.equal(app.requests.length, finish === 'release' ? 1 : 0);
    app.scope.S.playing = true; app.scope.togglePlay(false);
    assert.equal(timers.length, 1); timers.shift()(); assert.equal(trims, 1);
  }
});

test('the production clip mouse handler creates an Alt duplicate and gesture cancellation removes it', () => {
  const vm = require('node:vm');
  for (const cancel of [false, true]) {
    const app = fixture(); app.scope.S.agentHot = {}; app.scope.S.tlopt = {};
    app.scope.mediaRate = () => 30; app.scope.xToT = x => x / app.scope.S.pps; app.document.createElement = () => node();
    vm.runInContext(section('function clipEl(', 'function refreshSel('), app.scope);
    const element = app.scope.clipEl(app.clips[0], app.tr, false);
    element.onmousedown(event({ altKey: true }));
    assert.equal(app.tr.clips.length, 4); assert.equal(app.scope.S.dupPending.clip.id, 'new-shape');
    app.move({ clientX: 60, altKey: true });
    if (cancel) { app.escape(); assert.equal(app.tr.clips.length, 3); assert.equal(app.requests.length, 0); }
    else { app.up({ altKey: true }); assert.equal(app.body().ops.length, 1); assert.equal(app.body().ops[0].clip.id, 'new-shape'); assert.equal(app.body().ops[0].clip.in_, 5); assert.equal(app.body().ops[0].clip.start, 1); }
    assert.equal(app.scope.S.dupPending, null);
  }
});

test('retained clip mouse callbacks cannot select, duplicate or cut reused IDs after ownership changes', () => {
  const vm = require('node:vm');
  const replacements = [
    app => { app.scope.S.proj = plain(app.project); app.scope.S.seq = app.scope.S.proj.sequences[0]; },
    app => { app.project.sequences[1] = { ...plain(app.seq), id: 's2' }; app.scope.S.seq = app.project.sequences[1]; app.scope.S.seqId = 's2'; },
    app => { app.tr.clips[0] = plain(app.tr.clips[0]); },
    app => { app.scope.S.context = context('r0', 'different-folder'); },
    app => { app.scope.S.proj = { ...app.project, name: 'new document retaining the same sequence objects' }; },
  ];
  for (const change of replacements) for (const mode of ['select', 'duplicate', 'razor']) {
    const app = fixture(); app.scope.S.agentHot = {}; app.scope.S.tlopt = {};
    app.scope.xToT = x => x / app.scope.S.pps; app.document.createElement = () => node();
    vm.runInContext(section('function clipEl(', 'function refreshSel(') + section('function razorAt(t,', 'function selectedClips('), app.scope);
    const element = app.scope.clipEl(app.clips[0], app.tr, false);
    change(app); app.scope.S.sel = new Set(['b']); app.scope.S.editPoint = { clipId: 'b', side: 'l' }; app.scope.S.tool = mode === 'razor' ? 'razor' : 'select';
    const original = plain(app.project), active = plain(app.scope.S.proj);
    element.onmousedown(event({ clientX: 60, altKey: mode === 'duplicate' })); app.move({ clientX: 120 }); app.up();
    assert.deepEqual(plain(app.project), original); assert.deepEqual(plain(app.scope.S.proj), active);
    assert.deepEqual([...app.scope.S.sel], ['b']); assert.deepEqual(plain(app.scope.S.editPoint), { clipId: 'b', side: 'l' });
    assert.equal(app.requests.length, 0); assert.equal(app.storage.size, 0); assert.equal(app.scope.S.gesture == null, true); assert.equal(app.scope.S.dupPending == null, true);
  }
});

test('startDrag itself rejects detached clip and track objects with matching current IDs', () => {
  for (const detached of ['clip', 'track', 'project']) {
    const app = fixture(), oldClip = app.clips[0], oldTrack = app.tr;
    if (detached === 'clip') app.tr.clips[0] = plain(oldClip);
    else if (detached === 'track') app.seq.tracks[0] = plain(oldTrack);
    else { app.scope.S.proj = plain(app.project); app.scope.S.seq = app.scope.S.proj.sequences[0]; }
    const original = plain(app.project), active = plain(app.scope.S.proj);
    app.scope.startDrag(event(), oldClip, oldTrack, null, node()); app.move({ clientX: 60 }); app.up();
    assert.deepEqual(plain(app.project), original); assert.deepEqual(plain(app.scope.S.proj), active);
    assert.equal(app.requests.length, 0); assert.equal(app.storage.size, 0); assert.equal(app.scope.S.gesture == null, true);
  }
});
