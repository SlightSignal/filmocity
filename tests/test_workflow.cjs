const test = require('node:test');
const assert = require('node:assert/strict');
const { create, passages } = require('../frontend/workflow.js');
const { run } = require('../frontend/workflow-transaction.js');
const sync = require('../frontend/project-sync.js');
const { fixture, deferred } = require('./dom_fixture.cjs');
function nodes(root) { return [root, ...root.children.flatMap(nodes)]; }
function setup(options = {}) {
  const f = fixture(['pane']); f.nodes.pane.className = '';
  const calls = [];
  const seq = { id: 's', name: '<img src=x>', width: 1920, height: 1080, fps: 30,
    tracks: [{ id: 'V1', clips: [{ id: 'c', media_id: 'a', start: 0 }] }],
    transcript: [{ w: 'First.', s: .1, e: .5 }, { w: '<img onerror=bad>.', s: 1, e: 2 }], captions: [] };
  const CR = { S: { context: { workspace: 'w', project: 'p', revision: 'r' }, seq, seqId: 's', sel: new Set(['c']),
      proj: { media: { a: { id: 'a', name: '<script>bad</script>', duration: 3, has_audio: true }, b: { id: 'b', name: 'B', duration: 2 } }, sequences: [seq] } },
    workflowAction: async (...args) => { calls.push(args); return { ok: true, message: 'Applied' }; },
    workflowUpload: async (...args) => { calls.push(['import', ...args]); return { ok: true, message: 'Imported' }; },
    panels: { menuAction: action => calls.push(['menu', action]) }, showTab: tab => calls.push(['tab', tab]),
    seekTo: time => calls.push(['seek', time]), togglePlay: on => calls.push(['play', on]),
    getRenderedPreview: () => ({ start: options => calls.push(['preview', options]) }),
    loadProject: async () => { calls.push(['reload']); return true; }, hasUnsavedEdits: () => false, seqDur: () => 3, switchSeq: id => calls.push(['switch', id]) };
  Object.assign(CR, options);
  const controller = create(CR, f.document, f.nodes.pane);
  const all = () => nodes(f.nodes.pane);
  const button = text => all().find(n => n.tagName === 'button' && n.textContent === text);
  const check = text => all().find(n => n.tagName === 'label' && n.children.some(c => c.textContent.includes(text)))?.children.find(c => c.type === 'checkbox');
  const choose = (box, value) => { box.checked = value; box.events.change(); };
  return { ...f, CR, controller, calls, all, button, check, choose };
}

test('passages split by pauses, punctuation and size with stable word indices', () => {
  assert.deepEqual(passages([{ w: 'One', s: 0, e: .1 }, { w: 'two.', s: .2, e: .3 }, { w: 'Three', s: 1, e: 2 }]), [[0, 1], [2]]);
  const words = Array.from({ length: 25 }, (_, i) => ({ w: 'word', s: i*.1, e: i*.1+.09 }));
  assert.deepEqual(passages(words).map(a => a.length), [12,12,1]);
});
test('literal media and transcript labels never become HTML', () => {
  const h = setup(); assert.ok(h.all().some(n => n.textContent === '<script>bad</script> · 0:03.0'));
  h.controller.go(1); assert.ok(h.all().some(n => n.textContent === '<img onerror=bad>.'));
  assert.ok(h.all().every(n => !Object.hasOwn(n, 'innerHTML')));
});
test('footage order and named format are sent with the captured context', async () => {
  const h = setup(); h.choose(h.check('B ·'), true); h.choose(h.check('<script>'), true);
  await h.button('Build rough cut').events.click();
  assert.equal(h.calls[0][0], 'assemble'); assert.deepEqual(h.calls[0][1].media_ids, ['b','a']);
  assert.deepEqual(h.calls[0][2], { workspace: 'w', project: 'p', revision: 'r', sequence: 's' });
  assert.equal(h.controller.state.step, 1);
});
test('story selection survives search and excludes unchecked passages', async () => {
  const h = setup(); h.controller.go(1); h.choose(h.check('First.'), false);
  const search = h.all().find(n => n.type === 'search'); search.value = 'First'; search.events.input();
  assert.equal(h.controller.state.keep.size, 1);
  assert.equal(h.all().filter(n => n.className === 'wf-passage').length, 1);
  assert.ok(!h.all().some(n => n.textContent === '<img onerror=bad>.'));
  await h.button('Create cut from kept passages').events.click();
  assert.deepEqual(h.calls[0][1].words, [1]); assert.equal(h.controller.state.step, 2);
});
test('analysis cannot be double submitted or advanced while pending', async () => {
  const h = setup(), pending = deferred(); h.CR.workflowAction = async (...args) => { h.calls.push(args); return pending.promise; };
  h.controller.go(1); const job = h.button('Transcribe again').events.click();
  await h.controller.run('transcribe', {}); h.controller.go(5);
  assert.equal(h.calls.length, 1); assert.equal(h.controller.state.step, 1);
  assert.equal(h.nodes.pane.attributes['aria-busy'], 'true');
  pending.resolve({ ok: true, message: '2 words' }); await job;
  assert.equal(h.nodes.pane.attributes['aria-busy'], 'false'); assert.equal(h.controller.state.busy, false);
});
test('an edit invalidates draft actions until explicitly refreshed', async () => {
  const h = setup(); h.controller.go(1); h.CR.S.context.revision = 'r2'; h.CR.S.seq.name = 'Changed'; h.controller.refresh();
  await h.controller.run('story', { words: [0] }); assert.equal(h.calls.length, 0);
  assert.equal(h.controller.state.changed, true);
  await h.button('Refresh step').events.click(); assert.deepEqual(h.calls, [['reload']]); assert.equal(h.controller.state.changed, false);
  assert.equal(h.controller.state.basis.revision, 'r2');
});
test('thumbnail completion keeps form nodes, focus and choices', () => {
  const h = setup(); h.controller.go(1); h.choose(h.check('First.'), false);
  const search = h.all().find(n => n.type === 'search'); search.focus();
  h.CR.S.proj.media.a.thumb = '/thumb.jpg'; h.CR.S.proj.updated = 3; h.CR.S.context.revision = 'r2'; h.controller.refresh();
  assert.equal(h.controller.state.changed, false); assert.equal(h.controller.state.keep.size, 1);
  assert.equal(h.document.activeElement, search); assert.equal(h.controller.state.basis.revision, 'r2');
});
test('project switch clears footage and transcript choices even with shared sequence ids', () => {
  const h = setup(); h.choose(h.check('B ·'), true); h.controller.go(1); h.choose(h.check('First.'), false);
  h.CR.S.context.project = 'other'; h.controller.refresh();
  assert.deepEqual(h.controller.state.selected, []); assert.equal(h.controller.state.keep.size, 2);
  assert.equal(h.controller.state.basis.project, 'other');
});
test('missing transcriber stays on Story with actionable error and permits retry', async () => {
  const h = setup(); h.CR.workflowAction = async () => { throw new Error('Open System Check to install speech tools'); };
  h.controller.go(1); await h.button('Transcribe again').events.click();
  assert.equal(h.controller.state.error, true); assert.equal(h.controller.state.busy, false); assert.equal(h.controller.state.step, 1);
  assert.match(h.controller.state.message, /System Check/);
});
test('sound applies only selected clips and preview uses the renderer', async () => {
  const h = setup(); h.controller.go(2); await h.button('Apply dialogue cleanup').events.click();
  assert.equal(h.calls[0][0], 'cleanup'); assert.deepEqual(h.calls[0][1].clip_ids, ['c']);
  h.button('Render preview with sound').events.click(); assert.deepEqual(h.calls[1], ['preview', { range: false }]);
});
test('delivery invokes real export and queue, and never invents completion', () => {
  const h = setup(); h.controller.go(5); h.button('Export this sequence…').events.click(); h.button('Exports, progress and review files').events.click();
  assert.deepEqual(h.calls, [['menu','export'],['tab','queue']]);
  assert.match(h.all().find(n => n.textContent.includes('An export is finished')).textContent, /Exports panel/);
});
function transaction() {
  const state = { context: { workspace: 'w', project: 'p', revision: 'r' } }, calls = [];
  const deps = { sync, sequence: () => 's', withSavedProject: async fn => fn(state), preserve: () => calls.push('preserve'),
    clear: () => calls.push('clear'), busy: value => calls.push(['busy',value]), changed: () => {},
    reload: async context => { calls.push(['reload',context]); return true; } };
  const basis = { ...state.context, sequence: 's' };
  return { state, deps, calls, basis, response: { ok: true, context: { ...state.context, revision: 'next' } } };
}
test('transaction rejects a stale sequence or revision before making a request', async () => {
  for (const field of ['sequence', 'revision', 'project']) {
    const h = transaction(); h.basis[field] = 'old'; let submitted = false;
    await assert.rejects(run(h.deps, async () => { submitted = true; }, h.basis), /edit changed/);
    assert.equal(submitted, false); assert.equal(h.calls.length, 0);
  }
});
test('confirmed workflow reloads the captured project before clearing its draft', async () => {
  const h = transaction(); const result = await run(h.deps, async expected => { assert.deepEqual(expected,h.state.context); return h.response; }, h.basis);
  assert.equal(result, h.response); assert.deepEqual(h.calls, ['preserve',['busy',true],['reload',h.response.context],'clear',['busy',false]]);
});
test('definitive validation and missing dependency errors release draft without pausing saves', async () => {
  for (const status of [400,409,501]) {
    const h = transaction(); await assert.rejects(run(h.deps, async () => { throw Object.assign(new Error('Rejected'),{status}); }, h.basis), /Rejected/);
    assert.equal(h.state.error, undefined); assert.ok(h.calls.includes('clear'));
  }
});
test('network loss and an unexpected project response preserve the draft and pause saves', async () => {
  for (const response of [null,{ ok:true, context:{workspace:'w', project:'other', revision:'x'} }]) {
    const h = transaction(); await assert.rejects(run(h.deps, async () => { if (response) return response; throw new Error('Connection lost'); }, h.basis), /Open Recovery/);
    assert.match(h.state.error, /not confirmed/); assert.ok(!h.calls.includes('clear'));
  }
});
test('successful commit with a failed refresh forbids repeating the action', async () => {
  const h = transaction(); h.deps.reload = async () => false;
  await assert.rejects(run(h.deps, async () => h.response, h.basis), /do not repeat/);
  assert.match(h.state.error, /Saved, but/); assert.ok(!h.calls.includes('clear'));
});

const Transcript = require('../frontend/transcript-editor.js');
function drafts(writer = 'test') {
  const data = new Map(), storage = { get length() { return data.size; }, key: i => [...data.keys()][i],
    getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v), removeItem: k => data.delete(k) };
  return { storage, store: Transcript.createDraftStore(() => storage, writer) };
}
function correct(h, text = 'Émile.') {
  h.button('Words…').events.click();
  const input = h.all().find(n => n.attributes['aria-label'] === 'Correct word 1: First.');
  input.value = text; input.events.input(); return input;
}
test('word corrections persist separately, block other actions, and preserve selection after confirmed save', async () => {
  const d = drafts(), h = setup({ transcriptDrafts: d.store }); h.controller.go(1);
  h.choose(h.check('First.'), false); correct(h);
  assert.equal(h.CR.S.seq.transcript[0].w, 'First.'); assert.equal(h.controller.hasDrafts(), true);
  assert.equal(d.store.list(h.CR.S.context,'s').drafts[0].edits[0].text, 'Émile.');
  await h.controller.run('story', { words:[1] }); assert.equal(h.calls.length,0); assert.match(h.controller.state.message,/pending word corrections/);
  h.CR.workflowAction = async (action,body) => {
    h.calls.push([action,body]); h.CR.S.seq.transcript = h.CR.S.seq.transcript.map((w,i)=>i ? w : {...w,w:body.edits[0].text});
    h.CR.S.context.revision = 'saved'; return {ok:true,message:'Saved corrections'};
  };
  await h.button('Save word corrections').events.click();
  assert.equal(h.calls[0][0],'transcript_edit'); assert.deepEqual(h.calls[0][1].edits,[{index:0,expected:'First.',text:'Émile.'}]);
  assert.equal(h.controller.state.keep.size,1); assert.equal(h.controller.state.keep.has(1),true);
  assert.equal(h.controller.state.basis.revision,'saved'); assert.equal(h.controller.hasDrafts(),false);
  assert.equal(d.store.list(h.CR.S.context,'s').drafts.length,0);
});
test('a browser reload offers explicit draft recovery only against the same project revision', () => {
  const d = drafts(), a = setup({transcriptDrafts:d.store}); a.controller.go(1); correct(a);
  const b = setup({transcriptDrafts:d.store}); b.controller.go(1);
  assert.ok(b.button('Restore corrections')); assert.deepEqual(b.controller.state.draft.transcript.edits,{});
  b.button('Restore corrections').events.click(); assert.equal(b.controller.state.draft.transcript.edits[0],'Émile.');
  const c = setup({transcriptDrafts:d.store}); c.CR.S.context.revision = 'newer'; c.controller.refresh(); c.controller.go(1);
  assert.equal(c.button('Restore corrections'),undefined); assert.ok(c.button('Download draft'));
});
test('a displayed recovery button revalidates after the saved revision changes', () => {
  const d=drafts(),a=setup({transcriptDrafts:d.store});a.controller.go(1);correct(a);
  const b=setup({transcriptDrafts:d.store});b.controller.go(1);const restore=b.button('Restore corrections');
  b.CR.S.context.revision='later';b.controller.refresh();restore.events.click();
  assert.deepEqual(b.controller.state.draft.transcript.edits,{});assert.match(b.controller.state.message,/no longer matches/);
});
test('failed correction save preserves both typed text and browser draft for retry', async () => {
  const d = drafts(), h = setup({transcriptDrafts:d.store}); h.controller.go(1); correct(h);
  h.CR.workflowAction = async () => { throw new Error('Open Recovery; response lost'); };
  await h.button('Save word corrections').events.click();
  assert.equal(h.controller.state.draft.transcript.edits[0],'Émile.'); assert.equal(h.controller.hasDrafts(),true);
  assert.equal(d.store.list(h.CR.S.context,'s').drafts.length,1); assert.match(h.controller.state.message,/Recovery/);
});
test('storage failure keeps a downloadable memory draft across project switches and deleting another record', () => {
  const d=drafts(), downloads=[], h=setup({transcriptDrafts:d.store,downloadTranscriptDraft:value=>downloads.push(value)});
  d.store.save({...h.CR.S.context,revision:'old'},'s',[{index:0,expected:'First.',text:'Earlier.',s:.1,e:.5}]);
  d.storage.setItem=()=>{throw new Error('quota');}; h.controller.go(1); correct(h,'Newer.');
  assert.match(h.controller.state.message,/Download the draft/);
  h.button('Delete saved draft').events.click();
  h.CR.S.context.project='other'; h.controller.refresh(); h.CR.S.context.project='p'; h.controller.refresh();
  assert.ok(h.button('Restore corrections')); h.button('Download draft').events.click();
  assert.equal(downloads[0].edits[0].text,'Newer.');
  h.button('Restore corrections').events.click(); assert.equal(h.controller.state.draft.transcript.edits[0],'Newer.');
});
test('background ingest metadata advances the pending draft revision without losing word input focus', () => {
  const d=drafts(),h=setup({transcriptDrafts:d.store});h.controller.go(1);const input=correct(h);input.focus();
  h.CR.S.proj.media.a.thumb='/thumb.jpg';h.CR.S.context.revision='metadata';h.controller.refresh();
  assert.equal(h.document.activeElement,input);
  assert.equal(d.store.list(h.CR.S.context,'s').drafts[0].context.revision,'metadata');
});
test('passage preview sets a stopping point and caption review is an explicit guarded action', async () => {
  const h=setup();h.controller.go(1);h.button('Play passage at 0:00.1').events.click();
  assert.equal(h.CR.S.stopAt,.5);assert.deepEqual(h.calls,[['seek',.1],['play',true]]);
  h.CR.S.seq.captions=[{id:'cap',text:'Manual',start:0,end:1}];h.CR.S.seq.workflow={caption_review_ids:['cap']};
  h.controller.go(5);await h.button('Mark captions reviewed').events.click();
  assert.equal(h.calls.at(-1)[0],'caption_review');assert.deepEqual(h.calls.at(-1)[1],{caption_ids:['cap']});
});

test('queued transcription preserves story selection and unlocks after admission', async () => {
  const h=setup(),pending=deferred();h.CR.startTranscription=async (...args)=>{h.calls.push(['queued',...args]);return pending.promise;};
  h.controller.go(1);h.choose(h.check('First.'),false);const selected=[...h.controller.state.keep];
  const request=h.button('Transcribe again').events.click();assert.equal(h.controller.state.busy,true);
  pending.resolve({ok:true,task:{id:'t'},message:'Queued'});await request;
  assert.deepEqual([...h.controller.state.keep],selected);assert.equal(h.controller.state.busy,false);assert.equal(h.controller.state.step,1);assert.equal(h.calls[0][0],'queued');
});
