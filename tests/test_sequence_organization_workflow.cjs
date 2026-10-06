const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {fixture,plain,until,context,saved,event,section}=require('./helpers/sequence-organization-workflow-fixture.cjs');

test('Nest reviews saved selection and applies exact fingerprint without optimistic changes',async()=>{
 const f=fixture(),before=plain(f.project),r=await f.review(),d=f.dialog();assert.deepEqual(f.body(),{sequence:'s1',clip_ids:['a'],name:'Nested 02',_context:context(),actor:'human',client:'test-client'});assert.equal(f.requests[0].url,'/api/sequence/nest/review');assert.deepEqual(plain(f.project),before);assert.equal(d.apply.disabled,false);
 for(const text of ['0.000000–3.000000','Picture [v1]','Nested sound','neutral','Full color','one undoable'])assert.ok(d.output.textContent.includes(text),text);
 const p=d.apply.click();await until(()=>f.requests.length===2);assert.equal(f.requests[1].url,'/api/sequence/nest');assert.deepEqual(f.body(1),{sequence:'s1',clip_ids:['a'],name:r.settings.name,fingerprint:r.fingerprint,_context:context(),actor:'human',client:'test-client'});assert.deepEqual(plain(f.project),before);await d.apply.click();assert.equal(f.requests.length,2);
 await f.ackNest(p,r);assert.deepEqual([...f.scope.S.sel],r.summary.wrapper_clip_ids);assert.equal(f.scope.S.seq.id,'s1');assert.equal(f.scope.S.proj.sequences.length,3);assert.equal(d.box.classList.contains('open'),false);assert.equal(Boolean(f.scope.projectSaveState().error),false);
});
test('Nest blocks missing selection, locked track and Recovery before controls open',()=>{
 for(const mutate of [f=>f.scope.S.sel.clear(),f=>f.tr.locked=true,f=>f.scope.S.recoveryRequired=true,f=>f.scope.projectSaveState().error='uncertain']){const f=fixture(),before=plain(f.project);mutate(f);assert.equal(f.open(),false);assert.equal(f.requests.length,0);if(!f.tr.locked)assert.deepEqual(plain(f.project),before);}
});
test('Nest captures intent before save wait and reviews the acknowledged revision',async()=>{
 const f=fixture();f.scope.applyOps([{op:'set',path:'/name',value:'Author edit'}],'name','name');f.open();const p=f.dialog().review.click();assert.equal(f.requests.length,1);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');f.reply(1,f.report());await p;assert.equal(f.dialog().apply.disabled,false);
});
test('Nest Cancel/Escape during save wait prevents review and restores focus',async()=>{
 for(const escape of [false,true]){const f=fixture(),origin=f.scope.$('#origin');origin.focus();f.scope.applyOps([{op:'set',path:'/name',value:'Pending'}],'name','name');f.open();const p=f.dialog().review.click();if(escape)f.dialog().box.onkeydown(event({key:'Escape',target:f.dialog().name}));else f.dialog().cancel.click();f.requests[0].resolve(saved('r1'));await p;assert.equal(f.requests.length,1);assert.equal(f.scope.document.activeElement,origin);}
});
test('Nest delayed review is retired by project/sequence/selection/source/dependency changes',async()=>{
 for(const mutate of [f=>f.scope.S.context=context('r0','folder-b'),f=>f.scope.S.context.workspace='elsewhere',f=>f.scope.S.seq=f.project.sequences[1],f=>f.scope.S.sel=new Set(['b']),f=>f.project.media.m.path='/different.mov',f=>f.project.sequences[1].tracks[0].clips[0].speed=2]){const f=fixture();f.open();const r=f.report(),p=f.dialog().review.click();await until(()=>f.requests.length===1);mutate(f);f.reply(0,r);await p;assert.equal(f.dialog().apply.disabled,true);assert.equal(Boolean(f.scope.projectSaveState().error),false);}
});
test('closing/reopening Nest controls ignores the previous review response',async()=>{
 const f=fixture();f.open();const r=f.report(),p=f.dialog().review.click();await until(()=>f.requests.length===1);f.dialog().cancel.click();f.open();const message=f.dialog().output.textContent;f.reply(0,r);await p;assert.equal(f.dialog().output.textContent,message);assert.equal(f.dialog().apply.disabled,true);
});
test('Nest unsupported-dependency review shows the reason and cannot Apply',async()=>{
 const f=fixture();const r=await f.review({ok:false,issues:[{code:'shared_bus',severity:'error',message:'Select all contributors on the shared nonlinear bus.'}]});assert.equal(r.ok,false);assert.match(f.dialog().output.textContent,/shared nonlinear bus/);assert.equal(f.dialog().apply.disabled,true);await f.dialog().apply.click();assert.equal(f.requests.length,1);
});
test('Nest name changes and reviewed-envelope mutations invalidate Apply',async()=>{
 for(const mutate of [f=>f.dialog().name.value='Another name',f=>{f.dialog().name.value='Changed';f.dialog().name.oninput();},(f,r)=>r.summary.processing.push('Changed meaning')]){const f=fixture(),r=await f.review();mutate(f,r);await f.dialog().apply.click();assert.equal(f.requests.length,1);}
 for(const bad of ['', 'x'.repeat(121),'bad\nname']){const f=fixture();f.open();f.dialog().name.value=bad;await f.dialog().review.click();assert.equal(f.requests.length,0);}
});
test('Nest rejects malformed or foreign reviews without Recovery',async()=>{
 for(const extra of [{context:context('r7')},{sequence:'s2'},{clip_ids:['b']},{fingerprint:'bad'},{kind:'other'},{settings:{name:'wrong'}}]){const f=fixture();await f.review(extra);assert.equal(f.dialog().apply.disabled,true);assert.equal(Boolean(f.scope.projectSaveState().error),false);}
});
test('Nest known Apply refusal is recoverable by a new explicit review',async()=>{
 const f=fixture();await f.review();const p=f.dialog().apply.click();await until(()=>f.requests.length===2);f.reply(1,{detail:'Locked target changed'},409);assert.equal(await p,false);assert.equal(Boolean(f.scope.projectSaveState().error),false);assert.equal(f.dialog().review.disabled,false);assert.equal(f.dialog().apply.disabled,true);
});
test('Nest uncertain or malformed mutation reply enters Recovery and never auto-replays',async()=>{
 for(const malformed of [false,true]){const f=fixture();await f.review();const p=f.dialog().apply.click();await until(()=>f.requests.length===2);if(malformed)f.reply(1,{ok:true});else f.requests[1].reject(Error('Lost response'));assert.equal(await p,false);assert.match(f.scope.projectSaveState().error,/not confirmed/);await f.dialog().apply.click();await f.dialog().review.click();assert.equal(f.requests.length,2);}
});
test('Nest foreign late Apply reply does not reload another project',async()=>{
 const f=fixture(),r=await f.review(),p=f.dialog().apply.click();await until(()=>f.requests.length===2);f.scope.S.proj=plain(f.project);f.scope.S.context=context('r0','folder-b');f.reply(1,f.nestReply(r));assert.equal(await p,false);assert.equal(f.requests.length,2);
});
test('Nest preserves newer selection, navigation and post-submit closure while adopting saved project',async()=>{
 for(const kind of ['selection','sequence','closed','status']){const f=fixture(),r=await f.review(),p=f.dialog().apply.click();await until(()=>f.requests.length===2);if(kind==='selection')f.scope.S.sel=new Set(['b']);if(kind==='sequence'){f.scope.S.seqId='s2';f.scope.S.seq=f.project.sequences[1];}if(kind==='closed')f.dialog().cancel.click();if(kind==='status')f.project.media.m.status='ingesting';await f.ackNest(p,r);assert.equal(Boolean(f.scope.projectSaveState().error),false);if(kind==='selection')assert.deepEqual([...f.scope.S.sel],['b']);if(kind==='sequence')assert.equal(f.scope.S.seqId,'s2');if(kind==='closed')assert.deepEqual([...f.scope.S.sel],['a']);}
});
test('Nest suppresses Trim Edit commit when pausing and checks cancellation at final dispatch',async()=>{
 const f=fixture(),r=await f.review(),calls=[];f.scope.S.playing=true;f.scope.togglePlay=(...args)=>{calls.push(plain(args));f.scope.S.playing=false;};const p=f.dialog().apply.click();await until(()=>f.requests.length===2);assert.deepEqual(calls,[[false,{commitTrim:false}]]);await f.ackNest(p,r);
 const g=fixture();await g.review();const original=g.scope.workflowRequest;g.scope.workflowRequest=(request,basis)=>original(context=>{g.dialog().cancel.click();return request(context);},basis);assert.equal(await g.dialog().apply.click(),false);assert.equal(g.requests.length,1);assert.equal(Boolean(g.scope.projectSaveState().error),false);
});
test('New Sequence uses one saved command and navigates only after canonical acknowledgment',async()=>{
 const f=fixture(),before=plain(f.project),p=f.scope.newSequence();await until(()=>f.requests.length===1);assert.equal(f.requests[0].url,'/api/sequence/create');assert.deepEqual(f.body(),{mode:'empty',sequence:'s1',_context:context(),actor:'human',client:'test-client'});assert.deepEqual(plain(f.project),before);assert.equal(f.scope.S.seqId,'s1');await f.scope.newSequence();assert.equal(f.requests.length,1);const result=await f.ackCreate(p,'empty');assert.equal(result.sequence,'created-sequence');assert.equal(f.scope.S.seqId,'created-sequence');assert.deepEqual([...f.scope.S.sel],[]);assert.equal(f.requests.length,2);assert.match(f.messages.at(-1),/1920×1080 · 30000\/1001 fps/);
});
test('From Source sends selected logical item once, preserving Source marks and library',async()=>{
 const f=fixture();f.project.media.m.subclip_of='n';f.project.media.m.sub_in=6;f.project.media.m.interpret_fps='30000/1001';const before=plain(f.project.media),p=f.scope.seqFromClip();await until(()=>f.requests.length===1);assert.deepEqual(f.body(),{mode:'source',sequence:'s1',media_id:'m',_context:context(),actor:'human',client:'test-client'});await f.ackCreate(p,'source');assert.deepEqual(plain(f.scope.S.proj.media),before);assert.equal(f.scope.S.src.id,'m');assert.equal(f.scope.S.srcIn,1);assert.equal(f.scope.S.srcOut,3);assert.deepEqual([...f.scope.S.sel],['created-clip']);
});
test('pending/invalid source duration refuses before any empty sequence or request is created',async()=>{
 for(const duration of [null,undefined,0,-1,NaN,Infinity]){const f=fixture();f.project.media.m.duration=duration;const before=f.project.sequences.length;assert.equal(await f.scope.seqFromClip(),false);assert.equal(f.requests.length,0);assert.equal(f.project.sequences.length,before);assert.equal(f.scope.S.seqId,'s1');}
});
test('still From Source captures Preferences duration; audio input uses canonical source command',async()=>{
 for(const kind of ['still','audio']){const f=fixture();f.project.media.m.has_video=false;if(kind==='still'){f.project.media.m.is_image=true;f.project.media.m.duration=null;f.scope.S.prefs.still=2.25;}const p=f.scope.seqFromClip();await until(()=>f.requests.length===1);assert.equal(f.body().media_id,'m');assert.equal(f.body().still_duration,kind==='still'?2.25:undefined);await f.ackCreate(p,'source');}
});
test('sequence creation capture precedes save wait and Escape or selection change prevents dispatch',async()=>{
 for(const kind of ['escape','source','selection','sequence']){const f=fixture();f.scope.applyOps([{op:'set',path:'/name',value:'Pending'}],'name','name');const p=f.scope.seqFromClip();if(kind==='escape')f.window.emit('keydown',event({key:'Escape'}));if(kind==='source')f.scope.S.src=f.project.media.n;if(kind==='selection')f.scope.S.binSel=new Set(['n']);if(kind==='sequence')f.scope.S.seq=f.project.sequences[1];f.requests[0].resolve(saved('r1'));assert.equal(await p,false);assert.equal(f.requests.length,1);}
 const f=fixture();f.scope.applyOps([{op:'set',path:'/name',value:'Pending'}],'name','name');const p=f.scope.newSequence('Named');f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');await f.ackCreate(p,'empty',{},1);
});
test('new sequence completion preserves any newer navigation or bin selection',async()=>{
 for(const kind of ['bin','sequence','marks']){const f=fixture(),p=f.scope.seqFromClip();await until(()=>f.requests.length===1);if(kind==='bin')f.scope.S.binSel=new Set(['n']);if(kind==='sequence'){f.scope.S.seq=f.project.sequences[1];f.scope.S.seqId='s2';}if(kind==='marks')f.scope.S.srcIn=7;await f.ackCreate(p,'source');assert.equal(f.scope.S.seqId,kind==='sequence'?'s2':'s1');if(kind==='bin')assert.deepEqual([...f.scope.S.binSel],['n']);if(kind==='marks')assert.equal(f.scope.S.srcIn,7);}
});
test('creation unknown replies enter Recovery; known refusal leaves no optimistic sequence',async()=>{
 for(const refusal of [false,true]){const f=fixture(),before=plain(f.project),p=f.scope.newSequence();await until(()=>f.requests.length===1);if(refusal)f.reply(0,{detail:'Source changed'},409);else f.requests[0].reject(Error('Reply lost'));assert.equal(await p,false);assert.deepEqual(plain(f.project),before);assert.equal(Boolean(f.scope.projectSaveState().error),!refusal);if(!refusal){await f.scope.newSequence();assert.equal(f.requests.length,1);}}
});
test('creation foreign receipt or reused identity cannot be adopted',async()=>{
 for(const extra of [{sequence:'s1'},{project:'folder-b'},{source_sequence:'s2'},{mode:'source'},{clip_id:'unexpected'}]){const f=fixture(),p=f.scope.newSequence();await until(()=>f.requests.length===1);f.reply(0,f.createReply('empty',extra));assert.equal(await p,false);assert.match(f.scope.projectSaveState().error,/not confirmed/);assert.equal(f.requests.length,1);}
});
test('Nest actual dialog traps keyboard focus and restores invoking control',()=>{
 const f=fixture(),origin=f.scope.$('#origin');origin.focus();f.open();f.flushTimers();const d=f.dialog();assert.equal(f.scope.document.activeElement,d.name);d.name.focus();d.box.onkeydown(event({key:'Tab',shiftKey:true,target:d.name}));assert.equal(f.scope.document.activeElement,d.review);d.box.onkeydown(event({key:'Escape',target:d.review}));assert.equal(f.scope.document.activeElement,origin);assert.equal(d.box.classList.contains('open'),false);
});
test('document IDs are unique, Nest labels are connected, and synchronous create callers are removed',()=>{
 const html=fs.readFileSync('frontend/index.html','utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);assert.match(html,/for="sequenceNestName"/);assert.match(html,/id="sequenceNestReview"[^>]+aria-live="polite"/);
 const app=fs.readFileSync('frontend/app.js','utf8'),panels=fs.readFileSync('frontend/panels.js','utf8');assert.ok(!app.includes('const sq = newSequence()'));assert.ok(!panels.includes('const sq = CR.newSequence()'));assert.ok(panels.includes('newSeq: () => CR.newSequence()'));assert.ok(app.includes('$("#binNewSeq").onclick = () => newSequence()'));
});

test('Nest review exposes the frame-aligned transparent lead-in separately from selected content',async()=>{const f=fixture();f.open();const r=f.report();r.summary.range.selected_start=.012;const p=f.dialog().review.click();await until(()=>f.requests.length===1);f.reply(0,r);await p;assert.match(f.dialog().output.textContent,/0.012000 s of transparent lead-in/);});

test('creation final dispatch cancellation and foreign late receipts cannot reload or replay',async()=>{
 const f=fixture(),original=f.scope.workflowRequest;f.scope.workflowRequest=(request,basis)=>original(context=>{f.window.emit('keydown',event({key:'Escape'}));return request(context);},basis);assert.equal(await f.scope.newSequence(),false);assert.equal(f.requests.length,0);assert.equal(Boolean(f.scope.projectSaveState().error),false);
 const g=fixture(),p=g.scope.newSequence();await until(()=>g.requests.length===1);g.scope.S.proj=plain(g.project);g.scope.S.context=context('r0','folder-b');g.reply(0,g.createReply('empty'));assert.equal(await p,false);assert.equal(g.requests.length,1);assert.equal(g.scope.S.seqId,'s1');
});

test('From Source falls back to the first captured bin item only when Source is empty',async()=>{
 const f=fixture();f.scope.S.src=null;f.scope.S.binSel=new Set(['n','m']);const p=f.scope.seqFromClip();await until(()=>f.requests.length===1);assert.equal(f.body().media_id,'n');await f.ackCreate(p,'source',{media_id:'n'});
 const g=fixture();g.scope.S.src=null;g.scope.S.binSel.clear();assert.equal(await g.scope.seqFromClip(),false);assert.equal(g.requests.length,0);const p2=g.scope.newSequence();await until(()=>g.requests.length===1);await g.ackCreate(p2,'empty');
});

function geometryFixture(media, width, height, decoded=null){
 const f=fixture(),s=f.scope;Object.assign(f.project.media.m,{width:64,height:48,has_video:true,...media});f.clips[0].media_id='m';delete f.clips[0].graphic;f.clips[0].start=0;f.clips[0].in_=0;f.clips[0].out=3;s.S.t=1;s.S.playing=false;s.S.seq.width=width;s.S.seq.height=height;
 s.FilmocityAudioPreview=require('../frontend/audio-preview.js');s.pool={};const key=s.FilmocityAudioPreview.voiceKey(s.S.context,s.S.seq.id,['program'],f.tr.id,f.clips[0].id);if(decoded)s.pool[key]=decoded;
 const calls=[],ctx=new Proxy({},{get:(o,k)=>o[k]||((...args)=>{calls.push([k,...args]);}),set:(o,k,v)=>{o[k]=v;return true;}}),cv=s.$('#prgCanvas');Object.assign(cv,{width,height,getContext:()=>ctx,getBoundingClientRect:()=>({left:0,top:0,width,height})});
 vm.runInContext(section('function pictureDisplaySize(', 'const AG =')+'\n'+section('function monitorHit(', 'function revealInProject(')+'\n'+section('function clipRect(', 'function frameStats('),s);
 const app=fs.readFileSync('frontend/app.js','utf8'),start=app.indexOf('  const sel = selectedClips().filter',app.indexOf('function renderProgram()')),end=app.indexOf('  for (const [mid, v]',start);
 vm.runInContext('globalThis.drawSelection = function(cv){'+app.slice(start,end)+'};',s);return{...f,calls,cv};
}
test('Program selection, actual corner hit and color-sampling bounds match non-square source display geometry',()=>{
 for(const [media,W,H] of [[{sample_aspect_ratio:'2:1'},128,48],[{width:48,height:64,rotation:90,sample_aspect_ratio:'2:1'},48,128],[{width:48,height:64,rotation:270,sample_aspect_ratio:'2/1'},48,128],[{sample_aspect_ratio:'1:1'},64,48]]){
  const f=geometryFixture(media,W,H),s=f.scope;s.drawSelection(f.cv);assert.deepEqual(f.calls.find(c=>c[0]==='strokeRect').slice(1),[-W/2,-H/2,W,H]);const hit=s.monitorHit({clientX:0,clientY:0});assert.equal(hit.kind,'scale');assert.equal(hit.dw,W);assert.equal(hit.dh,H);assert.deepEqual(plain(s.clipRect(f.clips[0])),[0,0,W,H]);
 }
});
test('Program handles prefer the same decoded proxy geometry as picture drawing without double SAR',()=>{
 const f=geometryFixture({sample_aspect_ratio:'2:1'},128,48,{readyState:2,videoWidth:64,videoHeight:24}),s=f.scope;s.drawSelection(f.cv);assert.deepEqual(f.calls.find(c=>c[0]==='strokeRect').slice(1),[-64,-24,128,48]);assert.deepEqual(plain(s.programPictureSize(f.clips[0],f.tr,128,48)),[64,24]);
 assert.deepEqual(plain(s.pictureDisplaySize({width:65,height:48,sample_aspect_ratio:'3:2'},null,1,1)),[98,48]);
 assert.deepEqual(plain(s.pictureDisplaySize({width:64,height:48,sample_aspect_ratio:'0:0'},null,1,1)),[64,48]);
});
test('production Program drawing uses the shared decoded display geometry for non-square media',()=>{
 const f=require('./audio_preview_fixture.cjs').fixture(),e=f.env;f.S.playing=false;f.S.prefs={gpu:false};f.S.t=.5;f.seq.width=128;Object.assign(f.S.proj.media.m,{width:64,height:48,has_audio:false,has_video:true,sample_aspect_ratio:'2:1'});e.FilmocityMediaColor={source:(p,m)=>m};e.cssFilter=()=>'none';e.stackCssNonColor=()=>'none';
 const draws=[],ctx=new Proxy({},{get:(o,k)=>o[k]||((...a)=>{if(k==='drawImage')draws.push(a);}),set:(o,k,v)=>{o[k]=v;return true;}}),cv={width:128,height:48,getContext:()=>ctx};
 e.drawSequence(cv,f.seq,.5,new Set(),0,['program']);assert.deepEqual(draws.at(-1).slice(-4),[-64,-24,128,48]);
 Object.assign(f.elements[0],{videoWidth:64,videoHeight:24});e.drawSequence(cv,f.seq,.5,new Set(),0,['program']);assert.deepEqual(draws.at(-1).slice(-4),[-64,-24,128,48]);
});

test('Nest review distinguishes disjoint picture spans and child-only tail padding',async()=>{const f=fixture();f.open();const r=f.report();Object.assign(r.summary,{picture_ranges:[[0,1],[2,3]],child_duration:3.033333333333333,tail_padding:1/30});const p=f.dialog().review.click();await until(()=>f.requests.length===1);f.reply(0,r);await p;assert.match(f.dialog().output.textContent,/0.000000–1.000000 s; 2.000000–3.000000 s/);assert.match(f.dialog().output.textContent,/Child render canvas: 3.033333 s/);assert.match(f.dialog().output.textContent,/does not extend the parent wrapper endpoints/);});
