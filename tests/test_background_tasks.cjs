const test=require('node:test');const assert=require('node:assert/strict');
const {create}=require('../frontend/background-tasks.js');const {fixture,deferred}=require('./dom_fixture.cjs');
const all=n=>[n,...n.children.flatMap(all)];
function setup(rows=[]){
  const f=fixture(['pane','btnTasks']);const calls=[],timers=[];
  const CR={S:{context:{workspace:'w',project:'p',revision:'r'},seq:{id:'s'}},
    api:{get:async()=>({context:{...CR.S.context},tasks:rows,unavailable:[]}),json:async(...args)=>{calls.push(args);return {ok:true};}},
    applyBackgroundTranscript:async(...args)=>{calls.push(['apply',...args]);return {message:'Applied'};},
    applyMediaCollection:async(...args)=>{calls.push(['collect',...args]);return {message:'Collected originals applied'};},
    retryBackgroundTask:async(...args)=>{calls.push(['retry',...args]);return {};},showTab:tab=>calls.push(['tab',tab]),switchSeq:id=>{CR.S.seq={id};}};
  const controller=create(CR,f.document,f.nodes.pane,{setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:()=>{}});
  const nodes=()=>all(f.nodes.pane),button=text=>nodes().find(n=>n.tagName==='button'&&n.textContent===text);
  return {...f,CR,controller,calls,timers,nodes,button};
}
const task=(status='ready',extra={})=>({id:'a'.repeat(32),kind:'transcribe',context:{workspace:'w',project:'p'},name:'<img onerror=bad>',status,sequence:'s',...extra});
test('sync review shows signed offsets, measured resolution, quality and exact planned moves before explicit Apply',async()=>{
  const row=task('ready',{kind:'sync'}),h=setup([row]);let reviewed;
  h.CR.reviewSyncTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{kind:'sync',mode:'timeline',reference:'ref',matches:[{id:'ref',offset:0},{id:'<clip>',offset:-.0125,method:'pcm',resolution:.000125,correlation:.98,overlap_seconds:3.2,confidence:.87,polarity:-1}]},plan:{fingerprint:'sync-f',ops:[{}],summary:{message:'Move one clip',tracks:['A2'],moves:[{clip_id:'<clip>',from:4,to:1.0125,residual:.005}],warnings:['Verify the match by listening']}}};
  h.CR.applySyncTask=async(id,value)=>{assert.equal(id,row.id);assert.equal(value,reviewed);h.calls.push(['sync-apply']);row.status='applied';return {ok:true};};
  await h.controller.refresh();assert.equal(h.calls.length,0);assert.equal(h.button('Apply transcript'),undefined);
  await h.button('Review result').events.click();const labels=h.nodes().map(n=>n.textContent);
  assert.ok(labels.includes('Review audio synchronization'));assert.ok(labels.some(s=>s.includes('<clip>: -0.012500 s')&&s.includes('resolution 0.125 ms')&&s.includes('correlation 0.980')&&s.includes('confidence score 0.870 / 1')&&s.includes('opposite waveform polarity')));
  assert.ok(labels.includes('<clip>: 4.000000 → 1.012500 s · rounding residual 5.000 ms'));
  assert.ok(labels.some(s=>s.includes('Confidence is a match-strength score, not a probability or guaranteed alignment accuracy.')));
  assert.ok(labels.includes('Download synchronization result'));assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));
  await h.button('Apply reviewed changes').events.click();assert.deepEqual(h.calls,[['sync-apply']]);h.controller.stop();
});
test('raw sync displays envelope uncertainty and disables timeline application',async()=>{
  const row=task('ready',{kind:'sync',sequence:null}),h=setup([row]);h.CR.reviewSyncTask=async()=>({task:row,context:{...h.CR.S.context},result:{kind:'sync',mode:'media',matches:[{id:'m',offset:2.02,method:'envelope',resolution:.02,correlation:.8}]},plan:{ops:[],summary:{message:'Source matches',warnings:['Waveform refinement unavailable; 20 ms estimate']}}});
  await h.controller.refresh();await h.button('Review result').events.click();assert.equal(h.button('Apply reviewed changes').disabled,true);
  assert.ok(h.nodes().some(n=>n.textContent==='Source matching only. No timeline edit can be applied from this result.'));
  assert.ok(h.nodes().some(n=>n.textContent.includes('loudness envelope')&&n.textContent.includes('resolution 20.000 ms')));
  assert.ok(h.nodes().some(n=>n.textContent==='Waveform refinement unavailable; 20 ms estimate'));h.controller.stop();
});
test('sync details stay bounded and stale review cannot dispatch application',async()=>{
  const row=task('ready',{kind:'sync'}),h=setup([row]);h.CR.reviewSyncTask=async()=>({task:row,context:{...h.CR.S.context},result:{kind:'sync',matches:Array.from({length:80},(_,i)=>({id:i,offset:i}))},plan:{ops:[{}],summary:{moves:Array.from({length:80},(_,i)=>({clip_id:i,from:0,to:i}))}}});
  await h.controller.refresh();await h.button('Review result').events.click();assert.equal(h.nodes().filter(n=>n.tagName==='li').length,100);
  h.CR.S.context.revision='new';await h.controller.refresh();assert.equal(h.button('Apply reviewed changes').disabled,true);await h.controller.action(row,'apply');assert.deepEqual(h.calls,[]);h.controller.stop();
});
test('analysis review is explicit and Apply sends the retained review once without applying a transcript',async()=>{
  const row=task('ready',{kind:'analysis'}),h=setup([row]);let reviewed;
  h.CR.reviewAnalysisTask=async id=>{h.calls.push(['review',id]);return reviewed={task:row,context:{...h.CR.S.context},result:{kind:'silences'},plan:{ops:[{}],fingerprint:'f',summary:{message:'Remove 1 second',tracks:['V1'],ranges:[[1,2]]}}};};
  h.CR.applyAnalysisTask=async(id,value)=>{assert.equal(value,reviewed);h.calls.push(['analysis-apply',id]);row.status='applied';return {ok:true};};
  await h.controller.refresh();assert.equal(h.calls.length,0);assert.equal(h.button('Apply transcript'),undefined);assert.equal(h.button('Apply reviewed changes'),undefined);
  await h.button('Review result').events.click();assert.ok(h.nodes().some(n=>n.textContent==='Remove 1 second'));assert.equal(h.button('Apply reviewed changes').disabled,false);
  await h.button('Apply reviewed changes').events.click();assert.deepEqual(h.calls.map(c=>c[0]),['review','analysis-apply']);h.controller.stop();
});
test('saved edits and project switches invalidate analysis review without automatic application',async()=>{
  const row=task('ready',{kind:'analysis'}),h=setup([row]);h.CR.reviewAnalysisTask=async()=>({task:row,context:{...h.CR.S.context},result:{kind:'scenes'},plan:{ops:[{}],summary:{cuts:[2],message:'One cut'}}});
  await h.controller.refresh();await h.button('Review result').events.click();h.CR.S.context.revision='new';await h.controller.refresh();assert.equal(h.button('Apply reviewed changes').disabled,true);
  await h.controller.action(row,'apply');assert.equal(h.calls.length,0);
  h.CR.S.context.project='different';await h.controller.refresh();assert.equal(h.button('Apply reviewed changes'),undefined);h.controller.stop();
});
test('analysis review renders only bounded literal positions and never enables no-op apply',async()=>{
  const row=task('ready',{kind:'analysis'}),h=setup([row]);h.CR.reviewAnalysisTask=async()=>({task:row,context:{...h.CR.S.context},result:{kind:'scenes'},plan:{ops:[],summary:{message:'<script>literal</script>',ranges:[],cuts:Array.from({length:80},(_,i)=>i)}}});
  await h.controller.refresh();await h.button('Review result').events.click();assert.equal(h.nodes().filter(n=>n.tagName==='li').length,50);assert.equal(h.button('Apply reviewed changes').disabled,true);
  assert.ok(h.nodes().some(n=>n.textContent==='<script>literal</script>'));assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));h.controller.stop();
});
test('a delayed analysis review cannot install a result after switching projects',async()=>{
  const row=task('ready',{kind:'analysis'}),h=setup([row]),pending=deferred();h.CR.reviewAnalysisTask=()=>pending.promise;
  await h.controller.refresh();const work=h.button('Review result').events.click();h.CR.S.context.project='new';pending.resolve({context:{workspace:'w',project:'p',revision:'r'},task:row,plan:{ops:[{}]}});await work;
  assert.equal(h.button('Apply reviewed changes'),undefined);assert.equal(h.calls.length,0);h.controller.stop();
});
test('render replacement uses its own review and Apply adapters with a bounded picture preview',async()=>{
  const row=task('ready',{kind:'render_replace'}),h=setup([row]);let reviewed;
  h.CR.reviewRenderReplaceTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{preview:{kind:'image',url:`/api/tasks/${row.id}/render-replace/preview`,description:'One representative picture'}},plan:{ops:[{}],fingerprint:'render-f',summary:{message:'Replace one clip',baked:['picture effects'],retained:['clip audio gain'],warnings:['<literal warning>']}}};
  h.CR.applyRenderReplaceTask=async(id,value)=>{assert.equal(id,row.id);assert.equal(value,reviewed);h.calls.push(['render-apply']);row.status='applied';return {ok:true};};
  await h.controller.refresh();assert.equal(h.button('Apply transcript'),undefined);await h.button('Review result').events.click();
  const picture=h.nodes().find(n=>n.tagName==='img');assert.equal(picture.src,reviewed.result.preview.url);assert.equal(picture.alt,'Representative rendered picture');
  assert.ok(h.nodes().some(n=>n.textContent==='Processing kept editable: clip audio gain'));assert.ok(h.nodes().some(n=>n.textContent==='<literal warning>'));assert.ok(h.nodes().some(n=>n.textContent==='Download render receipt'));
  await h.button('Apply reviewed changes').events.click();assert.deepEqual(h.calls,[['render-apply']]);assert.equal(h.nodes().some(n=>n.tagName==='img'),false);h.controller.stop();
});
test('audio render preview survives task polling and retires immediately when reviews invalidate',async()=>{
  const row=task('ready',{kind:'render_replace'}),h=setup([row]);h.CR.reviewRenderReplaceTask=async()=>({task:row,context:{...h.CR.S.context},result:{preview:{kind:'audio',url:`/api/tasks/${row.id}/render-replace/preview`}},plan:{ops:[{}],summary:{message:'Audio'}}});
  await h.controller.refresh();await h.button('Review result').events.click();const audio=h.nodes().find(n=>n.tagName==='audio');let pauses=0,loads=0;audio.pause=()=>pauses++;audio.load=()=>loads++;
  assert.equal(audio.controls,true);assert.equal(audio.preload,'none');await h.controller.refresh(true);assert.equal(h.nodes().find(n=>n.tagName==='audio'),audio);assert.equal(pauses,0);
  h.controller.invalidateReviews();assert.equal(pauses,1);assert.equal(loads,1);assert.equal(h.nodes().some(n=>n.tagName==='audio'),false);assert.equal(h.button('Apply reviewed changes'),undefined);h.controller.stop();
});
test('render preview cannot load arbitrary URLs and stale revisions cannot keep listening previews',async()=>{
  for(const foreign of [true,false]){
    const row=task('ready',{kind:'render_replace'}),h=setup([row]);h.CR.reviewRenderReplaceTask=async()=>({task:row,context:{...h.CR.S.context},result:{preview:{kind:'audio',url:foreign?'https://invalid.example/preview':`/api/tasks/${row.id}/render-replace/preview`}},plan:{ops:[{}],summary:{message:'Audio'}}});
    await h.controller.refresh();await h.button('Review result').events.click();const audio=h.nodes().find(n=>n.tagName==='audio');assert.equal(Boolean(audio),!foreign);
    h.CR.S.context.revision='changed';await h.controller.refresh();assert.equal(h.nodes().some(n=>n.tagName==='audio'),false);assert.equal(h.button('Apply reviewed changes').disabled,true);h.controller.stop();
  }
});
test('remix review discloses requested and achieved time, uncertain tempo and literal bounded source pieces',async()=>{
  const row=task('ready',{kind:'analysis'}),h=setup([row]);h.CR.reviewAnalysisTask=async()=>({task:row,context:{...h.CR.S.context},result:{kind:'remix',requested:2,achieved:2,bpm:null,tempo_confidence:.25,warnings:['Short source; exact head/tail cut'],segments:Array.from({length:80},()=>({in:1,out:2}))},plan:{ops:[{}],summary:{message:'Review remix',tracks:['A1']}}});
  await h.controller.refresh();await h.button('Review result').events.click();assert.ok(h.nodes().some(n=>n.textContent==='Requested 2.000000 s · Planned 2.000000 s'));
  assert.ok(h.nodes().some(n=>n.textContent==='No reliable tempo estimate; review the proposed source pieces.'));assert.ok(h.nodes().some(n=>n.textContent==='Short source; exact head/tail cut'));
  assert.ok(h.nodes().some(n=>n.textContent.startsWith('Rhythm similarity: 0.250 / 1.')));assert.equal(h.nodes().filter(n=>n.tagName==='li').length,50);assert.ok(h.nodes().some(n=>n.textContent==='30 more source pieces in the downloaded result.'));h.controller.stop();
});
test('ready bake metadata and failed-preview warnings remain visible without a fabricated preview',async()=>{
  const row=task('ready',{kind:'render_replace'}),h=setup([row]);h.CR.reviewRenderReplaceTask=async()=>({task:row,context:{...h.CR.S.context},result:{kind:'render_replace',preview_warning:'Verified bake; preview unavailable: <decode error>'},plan:{ops:[{}],summary:{message:'Ready',format:'FFV1 BGRA + float PCM',duration:3.125}}});
  await h.controller.refresh();await h.button('Review result').events.click();
  for(const text of ['Replacement format: FFV1 BGRA + float PCM','Replacement duration: 3.125000 s','Verified bake; preview unavailable: <decode error>'])assert.ok(h.nodes().some(n=>n.textContent===text));
  assert.equal(h.nodes().some(n=>n.tagName==='img'||n.tagName==='audio'),false);assert.equal(h.button('Apply reviewed changes').disabled,false);h.controller.stop();
});
test('literal task text, unknown progress, badge, and polling cadence',async()=>{
  const h=setup([task('running')]);await h.controller.refresh();assert.ok(h.nodes().some(n=>n.textContent==='<img onerror=bad>'));
  assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));assert.equal(h.document.getElementById('btnTasks').textContent,'Tasks (1)');
  assert.equal(h.nodes().find(n=>n.tagName==='progress').value,'');assert.equal(h.timers.at(-1).ms,1200);h.controller.stop();
});
test('waiting for a shared slot remains cancellable and does not display stale progress',async()=>{
  const rows=[task('running',{stage:'Video proxy',progress:.6,resource:{state:'waiting',operation:'encode'}})];
  const h=setup(rows);await h.controller.refresh();
  assert.ok(h.nodes().some(n=>n.textContent==='Waiting for processing slot'));assert.equal(h.nodes().find(n=>n.tagName==='progress').value,'');
  assert.equal(h.button('Cancel').disabled,false);
  rows[0].resource={state:'running',operation:'encode'};await h.controller.refresh();
  assert.ok(h.nodes().some(n=>n.textContent==='Video proxy'));assert.equal(h.nodes().find(n=>n.tagName==='progress').value,.6);
  assert.ok(!h.nodes().some(n=>n.textContent==='Waiting for processing slot'));h.controller.stop();
});
test('finished transcript is never automatically applied',async()=>{
  const h=setup([task()]);await h.controller.refresh();assert.equal(h.calls.length,0);assert.ok(h.button('Apply transcript'));
  assert.equal(h.timers.at(-1).ms,5000);await h.button('Apply transcript').events.click();assert.equal(h.calls[0][0],'apply');h.controller.stop();
});
test('duplicate apply is disabled during pending request',async()=>{
  const h=setup([task()]),pending=deferred();h.CR.applyBackgroundTranscript=async(...a)=>{h.calls.push(a);return pending.promise;};await h.controller.refresh();
  const first=h.controller.action(task(),'apply');await h.controller.action(task(),'apply');assert.equal(h.calls.length,1);assert.equal(h.button('Apply transcript').disabled,true);
  pending.resolve({ok:true});await first;h.controller.stop();
});
test('wrong project, wrong sequence, and pending corrections block apply',async()=>{
  const h=setup([task()]);await h.controller.refresh();h.CR.S.context.project='other';await h.controller.action(task(),'apply');
  h.CR.S.context.project='p';h.CR.S.seq.id='other';await h.controller.action(task(),'apply');
  h.CR.S.seq.id='s';global.FilmocityWorkflow={hasDrafts:()=>true};try{await h.controller.action(task(),'apply');}finally{delete global.FilmocityWorkflow;}
  assert.equal(h.calls.length,0);assert.ok(h.nodes().some(n=>n.textContent.includes('pending word corrections')));h.controller.stop();
});
test('stale polling response cannot display another project’s actions',async()=>{
  const h=setup(),pending=deferred();h.CR.api.get=()=>pending.promise;const request=h.controller.refresh();h.CR.S.context.project='other';
  pending.resolve({context:{workspace:'w',project:'p'},tasks:[task()]});await request;assert.equal(h.controller.rows.length,0);h.controller.stop();
});
test('polling failure recovers and clears stale offline warning',async()=>{
  const h=setup();h.CR.api.get=async()=>{throw Error('offline');};await h.controller.refresh();assert.ok(h.nodes().some(n=>n.textContent.includes('retrying')));
  h.CR.api.get=async()=>({context:h.CR.S.context,tasks:[]});await h.controller.refresh();assert.ok(!h.nodes().some(n=>n.textContent.includes('retrying')));h.controller.stop();
});
test('cancel and retry use their dedicated adapters and do not apply',async()=>{
  const h=setup([task('queued')]);await h.controller.refresh();await h.button('Cancel').events.click();assert.ok(h.calls[0][1].endsWith('/cancel'));
  await h.controller.action(task('interrupted'),'retry');assert.equal(h.calls[1][0],'retry');h.controller.stop();
});
test('long task lists mount at most 30 rows and page without dropping history',async()=>{
  const h=setup(Array.from({length:200},(_,i)=>task('ready',{id:String(i)})));await h.controller.refresh();
  assert.equal(h.nodes().filter(n=>n.className==='task-row').length,30);h.button('Next tasks').events.click();assert.ok(h.nodes().some(n=>n.dataset.task==='30'));
  assert.equal(h.controller.rows.length,200);h.controller.stop();
});

function submissionHarness(){
  const vm=require('node:vm'),fs=require('node:fs'),source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/app.js'),'utf8');
  const context={workspace:'w',project:'p',revision:'r'},calls=[];
  const env={S:{context,seq:{id:'s'}},crypto:{randomUUID:()=> 'a'.repeat(32)},window:{FilmocitySync:require('../frontend/project-sync.js'),FilmocityTasks:{open:()=>calls.push('open')}},
    withSavedProject:fn=>fn({context}),api:{json:async(...args)=>{calls.push(args);return {ok:true,context,task:{id:'task'}};}}};
  vm.runInNewContext(source.slice(source.indexOf('async function startTranscription('),source.indexOf('function applyBackgroundTranscript(')),env);
  return {env,calls,context};
}
test('production submission captures saved project and only queues once',async()=>{
  const h=submissionHarness();await h.env.startTranscription({model:'small'});
  assert.equal(h.calls[0][1],'/api/tasks/transcribe');assert.equal(h.calls[0][2].sequence,'s');assert.equal(h.calls[0][2]._context.revision,'r');assert.equal(h.calls[1],'open');
});
test('production submission rejects changed sequence before request',async()=>{
  const h=submissionHarness();await assert.rejects(h.env.startTranscription({}, {...h.context,sequence:'other'}),/edit changed/);assert.equal(h.calls.length,0);
});
test('lost submission reply directs inspection without replay or project mutation',async()=>{
  const h=submissionHarness();let calls=0;h.env.api.json=async()=>{calls++;throw Error('connection lost');};
  await assert.rejects(h.env.startTranscription(),/Open Tasks before starting again/);assert.equal(calls,1);
});

test('package result exposes a literal selectable folder and separate receipt',async()=>{
  const h=setup([task('done',{kind:'package',result:{folder:'C:\\<literal>',files:4,manifest_sha256:'abc',warnings:['<font fallback>'],omissions:['Undo history omitted']}})]);
  await h.controller.refresh();const path=h.nodes().find(n=>n.tagName==='input');assert.equal(path.value,'C:\\<literal>');assert.equal(path.readOnly,true);path.events.click();assert.equal(path.selectedText,true);
  assert.ok(h.nodes().some(n=>n.textContent==='Download package receipt'));assert.ok(h.nodes().some(n=>n.textContent==='<font fallback>'));assert.ok(!h.nodes().some(n=>n.textContent==='Apply transcript'));h.controller.stop();
});
test('package submission uses saved revision and reports lost replies without replay',async()=>{
  const h=submissionHarness();await h.env.startProjectPackage('package','C:\\transfer');assert.equal(h.calls[0][1],'/api/tasks/package');assert.equal(h.calls[0][2]._context.revision,'r');assert.equal(h.calls[1],'open');
  let calls=0;h.env.api.json=async()=>{calls++;throw Error('offline');};await assert.rejects(h.env.startProjectPackage('package_import','C:\\transfer\\manifest.json'),/Open Tasks before starting again/);assert.equal(calls,1);
});
test('changed project or unresolved saves reject package submission before requests',async()=>{
  const h=submissionHarness();await assert.rejects(h.env.startProjectPackage('package','C:\\transfer',{...h.context,project:'other'}),/project changed/);assert.equal(h.calls.length,0);
  h.env.withSavedProject=async()=>{throw Error('unsaved');};await assert.rejects(h.env.startProjectPackage('package','C:\\transfer'),/unsaved/);assert.equal(h.calls.length,0);
});

test('collection results have dedicated apply/discard actions and a separate receipt',async()=>{
  const h=setup([task('ready',{kind:'collect',sequence:null,result:{folder:'C:\\<literal>',verified:3,copied:2,reused:1}})]);
  await h.controller.refresh();assert.ok(h.button('Apply collected paths'));assert.ok(h.button('Keep current paths'));
  assert.equal(h.button('Open sequence'),undefined);assert.equal(h.button('Apply transcript'),undefined);
  assert.ok(h.nodes().some(n=>n.textContent==='Download collection receipt'));assert.equal(h.calls.length,0);
  await h.button('Apply collected paths').events.click();assert.equal(h.calls[0][0],'collect');assert.equal(h.calls[0][2].sequence,'s');h.controller.stop();
});
test('discarded collection retains folder and receipt without an apply action',async()=>{
  const h=setup([task('cancelled',{kind:'collect',result:{folder:'C:\\saved',verified:1,copied:1,reused:0}})]);
  await h.controller.refresh();assert.equal(h.button('Apply collected paths'),undefined);assert.ok(h.nodes().some(n=>n.textContent==='Download collection receipt'));
  assert.equal(h.nodes().find(n=>n.tagName==='input').value,'C:\\saved');h.controller.stop();
});
test('collection submission uses acknowledged saved context and uncertain replies never replay',async()=>{
  const h=submissionHarness();await h.env.startMediaCollection();assert.equal(h.calls[0][1],'/api/tasks/collect');assert.equal(h.calls[0][2]._context.revision,'r');assert.equal(h.calls[1],'open');
  await assert.rejects(h.env.startMediaCollection({...h.context,project:'other'}),/project changed/);
  let calls=0;h.env.api.json=async()=>{calls++;throw Error('offline');};await assert.rejects(h.env.startMediaCollection(),/Open Tasks before starting again/);assert.equal(calls,1);
  h.env.withSavedProject=async()=>{throw Error('unsaved');};await assert.rejects(h.env.startMediaCollection(),/unsaved/);assert.equal(calls,1);
});
test('clip audio Tasks shows measured and predicted levels, precise gain changes and explicit Apply',async()=>{
  const row=task('ready',{kind:'audio_analysis'}),h=setup([row]);let reviewed;
  h.CR.reviewAudioTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{kind:'audio_analysis',mode:'loudness',scope:'timeline',measurements:[{id:'<clip>',media_id:'m',peak_db:-3.14,rms_db:-15.22,integrated_lufs:-18.12,true_peak_dbtp:-2.89,loudness_range_lu:4.1,silent:false,warnings:['<ramp approximation>']}]},plan:{fingerprint:'audio-f',ops:[{}],summary:{message:'Review normalization',gains:[{clip_id:'<clip>',target:-14,delta_db:4.1234,from_db:-2,to_db:2.1234,automation_points:3,predicted_peak_db:.98,predicted_true_peak_dbtp:1.23}],warnings:['No limiter is added.']}}};
  h.CR.applyAudioTask=async(id,value)=>{assert.equal(id,row.id);assert.equal(value,reviewed);h.calls.push(['audio-apply']);row.status='applied';return {ok:true};};
  await h.controller.refresh();assert.equal(h.calls.length,0);assert.equal(h.button('Apply transcript'),undefined);await h.button('Review result').events.click();
  const labels=h.nodes().map(n=>n.textContent);assert.ok(labels.includes('Review clip audio gain'));assert.ok(labels.some(s=>s.includes('Integrated loudness -18.12 LUFS')&&s.includes('True peak -2.89 dBTP')));assert.ok(labels.some(s=>s.includes('gain shift +4.1234 dB')&&s.includes('3 gain points shifted')&&s.includes('predicted true peak 1.23 dBTP')));assert.ok(labels.includes('<ramp approximation>'));assert.ok(labels.includes('No limiter is added.'));assert.ok(labels.includes('Download clip audio result'));assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));
  await h.button('Apply reviewed changes').events.click();assert.deepEqual(h.calls,[['audio-apply']]);h.controller.stop();
});
test('estimated beat review displays numeric confidence and bounded measured marker positions without bar claims',async()=>{
  const row=task('ready',{kind:'audio_analysis'}),h=setup([row]);h.CR.fmtTC=()=> '00:00:00:04';h.CR.reviewAudioTask=async()=>({task:row,context:{...h.CR.S.context},result:{mode:'beats',scope:'timeline',measurements:[{id:'c',bpm:120.25,tempo_confidence:.853,resolution:.005,beats:Array(75).fill(1),warnings:[]}]},plan:{ops:[{}],summary:{markers:Array.from({length:75},(_,i)=>({time:i+.123,name:'Transient '+i})),gains:[],warnings:[]}}});
  await h.controller.refresh();await h.button('Review result').events.click();const labels=h.nodes().map(n=>n.textContent);assert.ok(labels.includes('Review estimated beat markers'));assert.ok(labels.includes('Transient 0: 0.123000 s · 00:00:00:04'));assert.ok(labels.some(s=>s.includes('tempo confidence 0.853 / 1')&&s.includes('analysis grid 5.000 ms (not guaranteed timing accuracy)')));assert.ok(labels.some(s=>s.includes('not guaranteed beats, bars or downbeats')));assert.ok(labels.includes('25 additional marker positions in the downloaded result.'));assert.equal(h.nodes().filter(n=>n.tagName==='li').length,51);h.controller.stop();
});
test('raw silent audio review remains null-valued and read-only with no-op Apply disabled',async()=>{
  const row=task('ready',{kind:'audio_analysis',sequence:null}),h=setup([row]);h.CR.reviewAudioTask=async()=>({task:row,context:{...h.CR.S.context},result:{mode:'loudness',scope:'media',measurements:[{id:'m',peak_db:null,integrated_lufs:null,silent:true,warnings:[]}]},plan:{ops:[],summary:{gains:[],markers:[],warnings:[]}}});
  await h.controller.refresh();await h.button('Review result').events.click();assert.equal(h.button('Apply reviewed changes').disabled,true);assert.ok(h.nodes().some(n=>n.textContent.includes('silent / below the measurement gate')));assert.ok(h.nodes().some(n=>n.textContent.includes('Source measurements only')));assert.ok(!h.nodes().some(n=>n.textContent.includes('Sample peak 0.00')));h.controller.stop();
});
test('audio review cannot survive foreign late response, revision edits or explicit discard',async()=>{
  for(const mode of ['project','revision','discard']){const row=task('ready',{kind:'audio_analysis'}),h=setup([row]),pending=deferred();h.CR.reviewAudioTask=()=>pending.promise;h.CR.applyAudioTask=async()=>h.calls.push(['unexpected']);await h.controller.refresh();const work=h.button('Review result').events.click();const review={task:row,context:{...h.CR.S.context},result:{mode:'peak'},plan:{ops:[{}],summary:{}}};if(mode==='project')h.CR.S.context.project='other';pending.resolve(review);await work;if(mode==='revision'){h.CR.S.context.revision='new';await h.controller.refresh();await h.controller.action(row,'apply');}if(mode==='discard'){await h.button('Discard result').events.click();assert.equal(h.button('Apply reviewed changes'),undefined);}assert.ok(!h.calls.some(call=>call[0]==='unexpected'));h.controller.stop();}
});
test('recipe Tasks uses dedicated adapters, exact production review and fingerprinted Apply',async()=>{
 const helper=require('./helpers/recipe-workflow-fixture.cjs').fixture(),row=task('ready',{kind:'recipe'}),h=setup([row]);let reviewed;
 h.CR.recipeReviewLines=helper.scope.recipeReviewLines;h.CR.reviewRecipeTask=async()=>{reviewed=helper.result('reel');reviewed.task=row;reviewed.context={...h.CR.S.context};return reviewed;};
 h.CR.applyRecipeTask=async(id,value)=>{assert.equal(id,row.id);assert.equal(value,reviewed);h.calls.push(['recipe-apply']);row.status='applied';return {ok:true};};
 await h.controller.refresh();assert.equal(h.button('Apply transcript'),undefined);assert.equal(h.calls.length,0);await h.button('Review result').events.click();const labels=h.nodes().map(n=>n.textContent);assert.ok(labels.includes('Review New Reel'));assert.ok(labels.includes('New sequence: Reviewed reel'));assert.ok(labels.includes('Cut: 1.123456 s'));assert.ok(labels.includes('Range: 2.123456 – 3.654321 s'));assert.ok(labels.includes('Changed fields: transform.scale, afx_stack'));assert.ok(labels.includes('Music coverage 0.000000 s'));assert.ok(labels.includes('Download recipe result'));assert.ok(!h.nodes().some(n=>n.tagName==='video'||n.tagName==='audio'));await h.button('Apply reviewed changes').events.click();assert.deepEqual(h.calls,[['recipe-apply']]);h.controller.stop();
});
test('Talking Head recipe review stays read-only, literal, bounded and invalidates after revisions',async()=>{
 const helper=require('./helpers/recipe-workflow-fixture.cjs').fixture(),row=task('ready',{kind:'recipe'}),h=setup([row]);h.CR.recipeReviewLines=helper.scope.recipeReviewLines;h.CR.reviewRecipeTask=async()=>{const reviewed=helper.result('talking_head');reviewed.task=row;reviewed.context={...h.CR.S.context};reviewed.plan.summary.cuts=Array.from({length:80},(_,i)=>i+.001);reviewed.plan.summary.warnings=['<literal warning>'];return reviewed;};h.CR.applyRecipeTask=async()=>h.calls.push(['unexpected']);await h.controller.refresh();await h.button('Review result').events.click();const labels=h.nodes().map(n=>n.textContent);assert.ok(labels.includes('Review Talking Head'));assert.ok(labels.includes('<literal warning>'));assert.equal(labels.filter(text=>text.startsWith('Cut:')).length,50);assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));h.CR.S.context.revision='new';await h.controller.refresh();assert.equal(h.button('Apply reviewed changes').disabled,true);await h.controller.action(row,'apply');assert.equal(h.calls.length,0);h.controller.stop();
});
test('recipe no-op Apply is disabled and a foreign late review cannot install actions',async()=>{
 const helper=require('./helpers/recipe-workflow-fixture.cjs').fixture();
 for(const mode of ['noop','foreign']){const row=task('ready',{kind:'recipe'}),h=setup([row]),pending=deferred();h.CR.recipeReviewLines=helper.scope.recipeReviewLines;h.CR.reviewRecipeTask=()=>pending.promise;await h.controller.refresh();const work=h.button('Review result').events.click(),reviewed=helper.result();reviewed.task=row;reviewed.context={...h.CR.S.context};if(mode==='noop')reviewed.plan.ops=[];else h.CR.S.context.project='other';pending.resolve(reviewed);await work;if(mode==='noop')assert.equal(h.button('Apply reviewed changes').disabled,true);else assert.equal(h.button('Apply reviewed changes'),undefined);assert.equal(h.calls.length,0);h.controller.stop();}
});
test('Cover Tasks review exposes owned PNGs and explicit downloads without a timeline Apply',async()=>{
 const row=task('ready',{kind:'cover'}),h=setup([row]);let reviewed;h.CR.coverArtifactUrl=(id,index,c)=>'/api/tasks/'+id+'/cover/'+index+'?workspace='+c.workspace+'&project='+c.project;h.CR.coverReviewCurrent=r=>r===reviewed;
 h.CR.reviewCoverTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{kind:'cover',time:1.234567,covers:[{index:0,width:64,height:48,url:h.CR.coverArtifactUrl(row.id,0,h.CR.S.context),filename:'cover.png',sha256:'a'.repeat(64),size:1234}]},plan:{ops:[],summary:{message:'Complete composed frame',warnings:['Inspect layout']}}};
 await h.controller.refresh();await h.button('Review result').events.click();assert.ok(h.nodes().some(n=>n.textContent==='Review rendered covers'));assert.ok(h.nodes().some(n=>n.textContent==='Captured sequence frame: 1.234567 s'));assert.equal(h.button('Apply reviewed changes'),undefined);assert.equal(h.button('Apply transcript'),undefined);const picture=h.nodes().find(n=>n.tagName==='img'),link=h.nodes().find(n=>n.tagName==='a'&&n.textContent==='Download 64 × 48 PNG');assert.equal(picture.src,reviewed.result.covers[0].url);assert.equal(link.href,reviewed.result.covers[0].url+'&download=1&revision=r');assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));await h.controller.action(row,'apply');assert.deepEqual(h.calls,[]);assert.ok(h.nodes().some(n=>n.textContent.includes('Cover output is read-only')));h.controller.stop();
});
test('Cover review drops image links after owner/sequence changes and old download handlers prevent navigation',async()=>{
 const row=task('ready',{kind:'cover'}),h=setup([row]);let reviewed,current=true;h.CR.coverArtifactUrl=(id,index,c)=>'/api/tasks/'+id+'/cover/'+index+'?workspace='+c.workspace+'&project='+c.project;h.CR.coverReviewCurrent=r=>current&&r===reviewed;
 h.CR.reviewCoverTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{kind:'cover',time:0,covers:[{index:0,width:80,height:80,url:h.CR.coverArtifactUrl(row.id,0,h.CR.S.context),filename:'x.png',sha256:'b'.repeat(64),size:100}]},plan:{ops:[],summary:{warnings:[]}}};await h.controller.refresh();await h.button('Review result').events.click();const link=h.nodes().find(n=>n.tagName==='a'&&n.textContent==='Download 80 × 80 PNG');current=false;let prevented=false;link.events.click({preventDefault(){prevented=true;}});assert.equal(prevented,true);await h.controller.refresh(true);assert.ok(!h.nodes().some(n=>n.tagName==='img'));current=true;h.CR.S.seq.id='other';await h.controller.refresh(true);assert.ok(!h.nodes().some(n=>n.tagName==='img'));assert.ok(h.button('Open sequence'));h.controller.stop();
});
test('Cover Tasks refuse unexpected preview URLs and expose load failures without retrying mutations',async()=>{
 const row=task('ready',{kind:'cover'}),h=setup([row]);let reviewed;h.CR.coverArtifactUrl=(id,index,c)=>'/api/tasks/'+id+'/cover/'+index+'?workspace='+c.workspace+'&project='+c.project;h.CR.coverReviewCurrent=()=>true;h.CR.reviewCoverTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{kind:'cover',covers:[{index:0,url:'https://foreign.invalid/image.png'},{index:1,url:h.CR.coverArtifactUrl(row.id,1,h.CR.S.context),width:64,height:48}]},plan:{ops:[],summary:{warnings:[]}}};await h.controller.refresh();await h.button('Review result').events.click();const images=h.nodes().filter(n=>n.tagName==='img');assert.equal(images.length,1);images[0].events.error();assert.ok(h.nodes().some(n=>n.textContent.includes('cover preview could not load')));assert.equal(h.calls.length,0);h.controller.stop();
});
test('Tasks label Explainer and Hook Variants reviews and use retained recipe Apply adapter',async()=>{
 for(const mode of ['explainer','variants']){const row=task('ready',{kind:'recipe'}),h=setup([row]);let reviewed;h.CR.recipeReviewLines=()=>['Explicit text targets and exact card positions'];h.CR.reviewRecipeTask=async()=>reviewed={task:row,context:{...h.CR.S.context},result:{mode,kind:'recipe'},plan:{ops:[{}],summary:{}}};h.CR.applyRecipeTask=async(id,value)=>{assert.equal(id,row.id);assert.equal(value,reviewed);h.calls.push(['recipe-apply',mode]);return {ok:true};};await h.controller.refresh();await h.button('Review result').events.click();assert.ok(h.nodes().some(n=>n.textContent===(mode==='explainer'?'Review Explainer cards':'Review Hook Variants')));await h.button('Apply reviewed changes').events.click();assert.deepEqual(h.calls,[['recipe-apply',mode]]);h.controller.stop();}
});
