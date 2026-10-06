const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const { fixture, plain, until, context, saved, section, part, event } = require('./helpers/source-relink-workflow-fixture.cjs');

test('actual Relink dialog always inspects and reviews before one exact owned Apply/reload', async () => {
  const f = fixture(), before = plain(f.project), { owner, report } = await f.inspect();
  assert.equal(f.requests.length, 1); assert.equal(f.requests[0].url, '/api/media/relink/inspect');
  assert.deepEqual(f.body(), { media_id: 'm', path: '/replacement.mov', _context: context(), actor: 'human', client: 'test-client' });
  assert.deepEqual(plain(f.project), before); const d = f.dialog(); assert.equal(d.apply.disabled, false);
  for (const text of ['Replacement: /replacement.mov', '30000/1001', '100.000000', '[locked track]', '[held source frame]', 'audio_alias <range>', 'native 3.000000–5.000000', 'proxy, transcript']) assert.ok(d.output.textContent.includes(text), text);
  const pending = d.apply.onclick(); await until(() => f.requests.length === 2);
  assert.deepEqual(f.body(1), { media_id: 'm', path: report.path, _context: context(), fingerprint: report.fingerprint, actor: 'human', client: 'test-client' });
  assert.equal(d.apply.disabled, true); await d.apply.onclick(); assert.equal(f.requests.length, 2); assert.deepEqual(plain(f.project), before);
  await f.complete(pending, 1); assert.equal(f.requests[2].url, '/api/project/state'); assert.equal(f.scope.S.proj.media.m.path, '/replacement.mov');
  assert.equal(f.scope.currentSourceRelink(), null); assert.equal(d.box.classList.contains('open'), false); assert.match(f.messages.at(-1), /one saved change/);
});

test('explicit alias intent preserves requested ID while reviewing/applying its physical parent', async () => {
  const f = fixture(); f.project.media.audio = { ...plain(f.project.media.m), id: 'audio', subclip_of: 'm', audio_alias: { version: 1, physical_media_id: 'm', source_media_id: 'm' }, has_video: false };
  const owner = f.begin('audio'), { report } = await f.inspect(owner); assert.equal(f.body().media_id, 'audio'); assert.equal(report.media_id, 'm');
  const pending = f.dialog().apply.onclick(); await until(() => f.requests.length === 2); assert.equal(f.body(1).media_id, 'audio'); await f.complete(pending, 1);
});

test('pending saves settle before Inspect captures the acknowledged revision', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'save this' } }], 'note', 'note');
  const owner = f.begin(), pending = f.scope.relinkFromBrowser(owner, '/replacement.mov'); assert.equal(f.requests.length, 1);
  f.requests[0].resolve(saved('r1')); await until(() => f.requests.length === 2); assert.equal(f.body(1)._context.revision, 'r1'); f.reply(1, f.report(owner)); await pending; assert.equal(f.dialog().apply.disabled, false);
});

test('Escape during save flush prevents inspection and restores dialog focus', async () => {
  const f = fixture(), s = f.scope, origin = s.$('#origin'); origin.focus();
  s.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note');
  const owner = f.begin(), pending = s.relinkFromBrowser(owner, '/replacement.mov'); f.dialog().box.onkeydown(event({ key: 'Escape', target: f.dialog().output }));
  f.requests[0].resolve(saved('r1')); await pending;
  assert.equal(f.requests.length, 1); assert.equal(s.currentSourceRelink(), null); assert.equal(f.dialog().box.classList.contains('open'), false); assert.equal(s.document.activeElement, origin); f.flushTimers(); assert.equal(s.document.activeElement, origin);
});

test('Cancel during Inspect discards the late report and never applies or reloads', async () => {
  const f = fixture(), owner = f.begin(), pending = f.scope.relinkFromBrowser(owner, '/replacement.mov'); await until(() => f.requests.length === 1);
  f.dialog().cancel.click(); const text = f.dialog().output.textContent; f.reply(0, f.report(owner)); await pending;
  assert.equal(f.requests.length, 1); assert.equal(f.dialog().output.textContent, text); assert.equal(f.scope.currentSourceRelink(), null);
});

test('project/workspace/sequence/selection/source/dependent/timeline changes retire delayed inspection', async () => {
  for (const change of [
    f => { f.scope.S.proj = plain(f.project); f.scope.S.context = context('r0', 'folder-b'); },
    f => { f.scope.S.context.workspace = 'other'; }, f => { f.scope.S.seq = f.project.sequences[1]; },
    f => { f.scope.S.binSel = new Set(['different']); }, f => { f.scope.S.sel.clear(); }, f => { f.scope.S.src = f.project.media.m; },
    f => { f.project.media.m.path = '/other.mov'; }, f => { f.project.media.sub = { id: 'sub', subclip_of: 'm', sub_in: 0, duration: 2 }; },
    f => { f.clips[0].out = 90; },
  ]) {
    const f = fixture(), owner = f.begin(), original = f.report(owner), pending = f.scope.relinkFromBrowser(owner, '/replacement.mov'); await until(() => f.requests.length === 1); change(f); f.reply(0, original); await pending;
    assert.equal(f.dialog().apply.disabled, true); assert.equal(f.requests.length, 1); assert.equal(!!f.scope.projectSaveState().error, false);
  }
});

test('changes during pending saves prevent even the read-only probe', async () => {
  const f = fixture(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', note: 'pending' } }], 'note', 'note');
  const owner = f.begin(), pending = f.scope.relinkFromBrowser(owner, '/replacement.mov'); f.project.media.m.path = '/changed.mov'; f.requests[0].resolve(saved('r1')); await pending; assert.equal(f.requests.length, 1);
});

test('compatibility errors show exact issues and keep Apply disabled without Recovery', async () => {
  const f = fixture(); await f.inspect(f.begin(), { ok: false, issues: [{ code: 'too_short', severity: 'error', message: 'Unused alias requires 6.000000 native seconds.' }] });
  assert.equal(f.dialog().apply.disabled, true); assert.match(f.dialog().output.textContent, /Unused alias requires/); await f.dialog().apply.click(); assert.equal(f.requests.length, 1); assert.equal(!!f.scope.projectSaveState().error, false);
});

test('malformed/foreign inspection envelope or changed review object never authorizes Apply', async () => {
  for (const extra of [{ context: context('r9') }, { requested_media_id: 'wrong' }, { media_id: 'wrong' }, { fingerprint: 'not-a-fingerprint' }]) {
    const f = fixture(); await f.inspect(f.begin(), extra); assert.equal(f.dialog().apply.disabled, true); assert.equal(!!f.scope.projectSaveState().error, false);
  }
  const f = fixture(), { report } = await f.inspect(); report.summary.message = 'changed'; await f.dialog().apply.click(); assert.equal(f.requests.length, 1); assert.match(f.dialog().output.textContent, /changed/);
});

test('saved revision or new edits after Review require a fresh Inspect', async () => {
  const f = fixture(); await f.inspect(); f.scope.applyOps([{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', out: 9 } }], 'trim', 'trim');
  await f.dialog().apply.click(); assert.equal(f.requests.length, 2); assert.equal(f.requests[1].url, '/api/project'); f.requests[1].resolve(saved('r1')); await until(() => !f.scope.projectSaveState().pending);
  await f.dialog().apply.click(); assert.equal(f.requests.length, 2);
});

test('known Apply refusal is visible and allows a different file without Recovery', async () => {
  const f = fixture(); await f.inspect(); const pending = f.dialog().apply.click(); await until(() => f.requests.length === 2); f.reply(1, { detail: 'Replacement file changed on disk.' }, 409); await pending;
  assert.match(f.dialog().output.textContent, /changed on disk/); assert.equal(f.dialog().choose.disabled, false); assert.equal(!!f.scope.projectSaveState().error, false); assert.equal(f.requests.length, 2);
});

test('unknown/malformed Apply reply retains Recovery and never retries', async () => {
  for (const kind of ['lost', 'malformed']) {
    const f = fixture(); await f.inspect(); const pending = f.dialog().apply.click(); await until(() => f.requests.length === 2);
    if (kind === 'lost') f.requests[1].reject(Error('connection lost')); else f.reply(1, { ok: true, context: context('r1') });
    await pending; assert.match(f.scope.projectSaveState().error, /not confirmed/); assert.match(f.dialog().output.textContent, /Recovery/);
    await f.dialog().apply.click(); assert.equal(f.requests.length, 2); assert.equal(f.begin(), null);
  }
});

test('Cancel after dispatched Apply closes controls but still reconciles its acknowledged saved result', async () => {
  const f = fixture(); await f.inspect(); const pending = f.dialog().apply.click(); await until(() => f.requests.length === 2); f.dialog().cancel.click();
  await f.complete(pending, 1); assert.equal(f.scope.S.proj.media.m.path, '/replacement.mov'); assert.equal(!!f.scope.projectSaveState().error, false); assert.equal(f.requests.length, 3);
});

test('foreign project after Apply dispatch gets no canonical reload or success message', async () => {
  const f = fixture(); await f.inspect(); const originalState = f.scope.projectSaveState(), pending = f.dialog().apply.click(); await until(() => f.requests.length === 2);
  f.scope.S.proj = plain(f.project); f.scope.S.context = context('r0', 'folder-b'); f.reply(1, { ok: true, context: context('r1'), changed: true, media_id: 'm', summary: { message: 'Saved in A' }, warnings: [] }); await pending;
  assert.equal(f.requests.length, 2); assert.match(originalState.error, /not confirmed/); assert.doesNotMatch(f.messages.join('\n'), /Saved in A/); assert.equal(f.scope.S.proj.media.m.path, '/fixtures/source.mov');
});

test('Apply pauses playback without committing an active Trim Edit', async () => {
  const f = fixture(); await f.inspect(); f.scope.S.playing = true; const calls = []; f.scope.togglePlay = (playing, options) => { calls.push([playing, options]); f.scope.S.playing = playing; };
  const pending = f.dialog().apply.click(); await until(() => f.requests.length === 2); assert.deepEqual(plain(calls), [[false, { commitTrim: false }]]); await f.complete(pending, 1);
});

test('dialog keyboard focus trap, explicit Enter and Escape use the same reviewed controls', async () => {
  const f = fixture(); await f.inspect(); const d = f.dialog(); assert.equal(d.box.attributes.role, 'dialog'); assert.equal(d.box.attributes['aria-modal'], 'true'); assert.equal(d.output.attributes['aria-busy'], 'false');
  d.apply.focus(); let prevented = false; d.box.onkeydown(event({ key: 'Tab', preventDefault() { prevented = true; } })); assert.equal(prevented, true); assert.equal(f.scope.document.activeElement, d.output);
  d.output.focus(); d.box.onkeydown(event({ key: 'Tab', shiftKey: true })); assert.equal(f.scope.document.activeElement, d.apply);
  d.box.onkeydown(event({ key: 'Escape', target: d.output })); assert.equal(f.scope.currentSourceRelink(), null);
});

test('browser pending ownership and keyboard row capture cannot reuse a foreign or canceled relink', async () => {
  const f = fixture(), s = f.scope, { file } = f.browserRows(), owner = f.begin(); const pending = s.renderBrowser('/files'); await until(() => f.requests.length === 1); f.reply(0, f.folder()); await pending;
  assert.match(s.$('#pane-browser').innerHTML, /Inspect replacement original/); assert.match(s.$('#pane-browser').innerHTML, /id="fsImportAll" disabled/); // generated DOM markup plus guarded handler
  const inspected = file.onkeydown(event({ key: 'Enter', target: file })); await until(() => f.requests.length === 2); assert.equal(f.body(1).path, '/files/replacement.mov'); f.reply(1, f.report(owner)); await inspected;
  s.cancelSourceRelink(owner); await file.ondblclick(); assert.equal(f.requests.length, 2);
  const g = fixture(), row = g.browserRows().file, other = g.begin(), listing = g.scope.renderBrowser('/files'); await until(() => g.requests.length === 1);
  g.scope.S.context = context('r0', 'folder-b'); g.scope.S.proj = plain(g.project); g.reply(0, g.folder()); await listing; assert.equal(row.ondblclick, undefined); assert.equal(g.requests.length, 1);
});

test('actual bin and export Locate callbacks capture intent and refuse retired controls', async () => {
  const f = fixture(), s = f.scope, link = s.$('#testRelink'); link.dataset = { relink: 'm' }; s.CR.panels.browseRelink = () => {};
  s.$$ = query => query === '[data-relink]' ? [link] : []; vm.runInContext(section('function wireBin(', 'function trackRows('), s); s.wireBin(s.$('#bin'));
  const owner = link.onclick(event()); assert.equal(owner.mediaId, 'm'); s.cancelSourceRelink(owner); link.isConnected = false; assert.equal(link.onclick(event()), null);
  vm.runInContext('let preflightRequest = 0, exportPending = false;\n' + part('function exportContextStamp()', 'function exportSettings()'), s);
  s.exportSettings = () => ({ format: 'h264' }); s.$('#dlgExport').classList.add('open'); const pending = s.refreshExportPreflight(); await until(() => f.requests.length === 1);
  f.reply(0, { ok: false, errors: 1, issues: [{ media_id: 'm', name: 'Source.mov', severity: 'error', message: 'Offline' }] }); await pending;
  const locate = s.$('#exPreflight').children.at(-1).children.find(n => n.tagName === 'BUTTON'); assert.ok(locate);
  f.scope.S.context.revision = 'changed'; assert.equal(locate.onclick(), undefined); assert.equal(s.currentSourceRelink(), null);
});

test('actual index keeps all IDs unique and labels the new accessible Relink controls', () => {
  const html = fs.readFileSync(require('node:path').join(__dirname, '../frontend/index.html'), 'utf8'), ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
  assert.equal(new Set(ids).size, ids.length); for (const id of ['dlgSourceRelink', 'sourceRelinkDescription', 'sourceRelinkReview', 'sourceRelinkCancel', 'sourceRelinkChoose', 'sourceRelinkApply']) assert.ok(ids.includes(id));
  assert.match(html, /id="dlgSourceRelink" aria-describedby="sourceRelinkDescription"/); assert.match(html, /id="sourceRelinkReview" role="status" aria-live="polite"/);
});


test('choosing a different file discards its prior consent and requires a fresh explicit review', async () => {
  const f = fixture(), { owner, report } = await f.inspect(), oldApply = f.dialog().apply.onclick;
  const browser = []; f.scope.browseRelink = async captured => browser.push(captured);
  f.dialog().choose.click(); assert.equal(owner.review, null); assert.equal(f.dialog().box.classList.contains('open'), false); assert.deepEqual(browser, [owner]);
  await oldApply(); assert.equal(f.requests.length, 1);
  const pending = f.scope.relinkFromBrowser(owner, '/different.mov'); await until(() => f.requests.length === 2);
  assert.equal(f.body(1).path, '/different.mov'); f.reply(1, f.report(owner, { path: '/different.mov', fingerprint: 'b'.repeat(64) })); await pending;
  const applying = f.dialog().apply.click(); await until(() => f.requests.length === 3); assert.equal(f.body(2).path, '/different.mov'); assert.equal(f.body(2).fingerprint, 'b'.repeat(64));
  f.reply(2, { detail: 'Controlled refusal' }, 409); await applying; assert.equal(report.path, '/replacement.mov');
});

test('Cancel at the final save await prevents Apply dispatch without leaving Recovery', async () => {
  const f = fixture(); await f.inspect(); const pending = f.dialog().apply.click(); f.dialog().cancel.click(); await pending;
  assert.equal(f.requests.length, 1); assert.equal(!!f.scope.projectSaveState().error, false); assert.equal(f.scope.currentSourceRelink(), null);
});

test('read-only transport failure stays outside Recovery and leaves different-file selection available', async () => {
  const f = fixture(), owner = f.begin(), pending = f.scope.relinkFromBrowser(owner, '/bad.mov'); await until(() => f.requests.length === 1);
  f.requests[0].reject(Error('probe unavailable')); await pending; assert.match(f.dialog().output.textContent, /probe unavailable/);
  assert.equal(f.dialog().apply.disabled, true); assert.equal(f.dialog().choose.disabled, false); assert.equal(!!f.scope.projectSaveState().error, false);
});

test('retained controls cannot apply after close/reopen and new inspection', async () => {
  const f = fixture(); await f.inspect(); const oldApply = f.dialog().apply.onclick; f.dialog().cancel.click();
  const next = await f.inspect(); await oldApply(); assert.equal(f.requests.length, 2); assert.equal(f.scope.currentSourceRelink(), next.owner); assert.equal(f.dialog().apply.disabled, false);
});

test('intent Escape and actual source/sequence switch hooks retire the original owner', () => {
  const f = fixture(), s = f.scope, owner = f.begin(); f.escape(); assert.equal(s.currentSourceRelink(), null);
  const next = f.begin(); vm.runInContext(section('function switchSeq(', 'function pushHist('), s); s.switchSeq('s2');
  assert.equal(s.currentSourceRelink(), null); assert.equal(s.S.seq.id, 's2'); assert.equal(owner.cancelled, true); assert.equal(next.cancelled, true);
});


test('actual optimistic edit queue decodes each JSON Pointer segment once without ID collisions', async () => {
  const f = fixture(), s = f.scope, escaped = 'a/b~c', literal = 'a~1b~0c';
  f.project.media[escaped] = { id: escaped, name: 'Real slash source', path: '/slash.mov' };
  f.project.media[literal] = { id: literal, name: 'Literal escape source', path: '/literal.mov' };
  const beforeLiteral = plain(f.project.media[literal]);
  const one = s.applyOps([{ op: 'set', path: '/media/a~1b~0c/input_transform', value: 'slog3' }], 'source_color', 'source color');
  assert.equal(f.project.media[escaped].input_transform, 'slog3'); assert.deepEqual(plain(f.project.media[literal]), beforeLiteral);
  assert.equal(f.body().ops[0].path, '/media/a~1b~0c/input_transform'); f.requests[0].resolve(saved('r1')); await one;
  const two = s.applyOps([{ op: 'set', path: '/media/a~01b~00c/input_transform', value: 'vlog' }], 'source_color', 'literal source color');
  assert.equal(f.project.media[literal].input_transform, 'vlog'); assert.equal(f.project.media[escaped].input_transform, 'slog3');
  assert.equal(f.body(1).ops[0].path, '/media/a~01b~00c/input_transform'); f.requests[1].resolve(saved('r2')); await two;
  assert.equal(s.projectSaveState().context.revision, 'r2'); assert.equal(!!s.projectSaveState().error, false);
});


test('own post-submit preparation updates and selection changes do not turn acknowledged Apply into Recovery', async () => {
  const f = fixture(); f.project.media.audio = { ...plain(f.project.media.m), id: 'audio', subclip_of: 'm', audio_alias: { version: 1, source_media_id: 'm', physical_media_id: 'm' }, status: 'ready' };
  await f.inspect(); const pending = f.dialog().apply.click(); await until(() => f.requests.length === 2);
  f.project.media.audio.status = 'ingesting'; f.project.media.audio.proxy_status = 'preparing'; f.project.media.m.proxy_status = 'preparing'; f.scope.S.sel.clear();
  await f.complete(pending, 1); assert.equal(!!f.scope.projectSaveState().error, false); assert.equal(f.scope.S.proj.media.m.path, '/replacement.mov'); assert.equal(f.requests.length, 3);
});


test('replacement path admission matches the server before any probe is requested', async () => {
  for (const path of ['', '/'+ 'x'.repeat(4096), '/bad\u0000.mov']) {
    const f = fixture(), owner = f.begin(); await assert.rejects(f.scope.inspectSourceRelink(owner, path), /4096 characters/); assert.equal(f.requests.length, 0); assert.equal(!!f.scope.projectSaveState().error, false);
  }
});
