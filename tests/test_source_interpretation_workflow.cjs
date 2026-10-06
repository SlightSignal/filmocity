const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {fixture,plain,until,context,saved,event}=require('./helpers/source-interpretation-workflow-fixture.cjs');

test('real dialog reviews exact fractional rate before owned Apply and canonical reload',async()=>{
 const f=fixture(),before=plain(f.project),report=await f.review(),d=f.dialog();assert.equal(f.requests[0].url,'/api/media/interpret/review');assert.deepEqual(f.body(),{media_id:'m',fps:'30000/1001',include_fullmix:false,_context:context(),actor:'human',client:'test-client'});assert.deepEqual(plain(f.project),before);assert.equal(d.apply.disabled,false);
 for(const text of ['60000/1001','30000/1001','20.000000 → 40.000000','[locked track]','[held frame]','transcript, proxy','clears its In/Out'])assert.ok(d.output.textContent.includes(text),text);
 const pending=d.apply.click();await until(()=>f.requests.length===2);assert.deepEqual(f.body(1),{media_id:'m',...report.settings,fingerprint:report.fingerprint,_context:context(),actor:'human',client:'test-client'});assert.deepEqual(plain(f.project),before);await d.apply.click();assert.equal(f.requests.length,2);
 const result=await f.ack(pending,report);assert.equal(result.ok,true);assert.equal(f.scope.S.proj.media.m.interpret_fps,'30000/1001');assert.equal(f.scope.S.srcIn,null);assert.equal(f.scope.S.srcOut,null);assert.equal(f.scope.$('#srcVideo').paused,true);assert.equal(d.box.classList.contains('open'),false);assert.equal(Boolean(f.scope.projectSaveState().error),false);
});

test('strict decimal/rational input refuses malformed and empty assumed rates without any request',async()=>{
 for(const bad of ['','24.','not-a-rate','24garbage','0','-1','1/0','1001','Infinity','NaN','1/2/3','3e1',' '.repeat(2)]){const f=fixture();f.open();f.choose(bad);assert.equal(await f.dialog().review.click(),false);assert.equal(f.requests.length,0);assert.equal(f.dialog().apply.disabled,true);assert.equal(f.scope.document.activeElement,f.dialog().rate);}
 const f=fixture();f.open();f.choose('29.97');const r=await f.review();assert.equal(f.body().fps,'29.97');assert.equal(r.settings.fps,'30000/1001');
});

test('restore native is explicit and reviewed, and a confirmed no-op has no optimistic history',async()=>{
 const f=fixture();f.project.media.m.interpret_fps='30000/1001';f.open();f.choose(null);const r=f.report();r.summary.changed=false;r.summary.windows[0].new_duration=20;const before=plain(f.project),pending=f.dialog().review.click();await until(()=>f.requests.length===1);assert.equal(f.body().fps,null);f.reply(0,r);await pending;assert.match(f.dialog().output.textContent,/no Undo step/);const apply=f.dialog().apply.click();await until(()=>f.requests.length===2);assert.deepEqual(plain(f.project),before);const result=await f.ack(apply,r);assert.equal(result.changed,false);assert.equal(f.scope.S.srcIn,1);assert.equal(f.scope.S.srcOut,3);
});

test('initial Recovery and missing or multiple selection block opening without requests',()=>{
 for(const mutate of [f=>f.scope.projectSaveState().error='Unknown save',f=>f.scope.S.binSel.clear(),f=>f.scope.S.binSel=new Set(['m','n']),f=>f.scope.S.recoveryRequired=true]){const f=fixture();mutate(f);assert.equal(f.open(),null);assert.equal(f.requests.length,0);}
});

test('selection is captured before save wait; acknowledged revision is used for review',async()=>{
 const f=fixture();f.scope.applyOps([{op:'set',path:'/name',value:'Saved author edit'}],'name','name');f.open();f.choose();const pending=f.dialog().review.click();assert.equal(f.requests.length,1);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');f.reply(1,f.report());await pending;assert.equal(f.dialog().apply.disabled,false);
});

test('Escape or Cancel during pending saves prevents the review request and restores focus',async()=>{
 for(const escape of [false,true]){const f=fixture(),origin=f.scope.$('#origin');origin.focus();f.scope.applyOps([{op:'set',path:'/name',value:'Pending'}],'name','name');f.open();f.choose();const pending=f.dialog().review.click();if(escape)f.dialog().box.onkeydown(event({key:'Escape',target:f.dialog().rate}));else f.dialog().cancel.click();f.requests[0].resolve(saved('r1'));await pending;assert.equal(f.requests.length,1);assert.equal(f.scope.document.activeElement,origin);f.flushTimers();assert.equal(f.scope.document.activeElement,origin);}
});

test('owner, source, family and selected item changes retire review before and after dispatch',async()=>{
 const changes=[f=>{f.scope.S.proj=plain(f.project);f.scope.S.context=context('r0','folder-b');},f=>f.scope.S.context.workspace='other',f=>f.scope.S.seq=f.project.sequences[1],f=>f.scope.S.binSel=new Set(['n']),f=>f.project.media.m.path='/changed.mov',f=>f.project.media.child={id:'child',subclip_of:'m',sub_in:1,duration:2}];
 for(const pre of [false,true])for(const change of changes){const f=fixture();if(pre)f.scope.applyOps([{op:'set',path:'/name',value:'Pending'}],'name','name');f.open();f.choose();const report=f.report(),pending=f.dialog().review.click();if(!pre)await until(()=>f.requests.length===1);change(f);if(pre)f.requests[0].resolve(saved('r1'));else f.reply(0,report);await pending;assert.equal(f.requests.length,1);assert.equal(f.dialog().apply.disabled,true);}
});

test('closing or reopening controls discards a late read-only review',async()=>{
 const f=fixture();f.open();f.choose();const report=f.report(),pending=f.dialog().review.click();await until(()=>f.requests.length===1);f.dialog().cancel.click();f.open();const newText=f.dialog().output.textContent;f.reply(0,report);await pending;assert.equal(f.dialog().output.textContent,newText);assert.equal(f.dialog().apply.disabled,true);assert.equal(f.requests.length,1);
});

test('changing any review field invalidates consent even without an input event',async()=>{
 for(const change of [f=>f.dialog().rate.value='24',f=>f.dialog().mode.value='native',f=>f.dialog().fullmix.checked=true]){const f=fixture();await f.review();change(f);await f.dialog().apply.click();assert.equal(f.requests.length,1);assert.equal(f.dialog().apply.disabled,true);}
 const f=fixture();await f.review();f.dialog().rate.value='24';f.dialog().rate.oninput();assert.equal(f.dialog().apply.disabled,true);assert.match(f.dialog().output.textContent,/Settings changed/);
});

test('a physical original requires explicit listed full-mix consent and a new review',async()=>{
 const f=fixture();f.project.media.mix={...plain(f.project.media.m),id:'mix',name:'Dialogue full mix',subclip_of:'m',audio_alias:{version:1,physical_media_id:'m',source_media_id:'m'}};f.open();f.choose();const r=f.report({ok:false,issues:[{severity:'error',message:'Include the coupled full-mix audio group.'}]});r.summary.required_fullmix_ids=['mix'];const pending=f.dialog().review.click();await until(()=>f.requests.length===1);f.reply(0,r);await pending;assert.equal(f.dialog().apply.disabled,true);assert.match(f.dialog().output.textContent,/Dialogue full mix \[mix\]/);f.dialog().fullmix.checked=true;f.dialog().fullmix.onchange();const r2=await f.review();assert.equal(f.body(1).include_fullmix,true);assert.equal(r2.settings.include_fullmix,true);assert.equal(f.dialog().apply.disabled,false);
});

test('independent channel and ordinary child review keeps the exact selected media ID',async()=>{
 for(const channel of [false,true]){const f=fixture(),m=plain(f.project.media.m);f.project.media.child={...m,id:'child',name:'Selected range',subclip_of:'m',sub_in:6,duration:4,...(channel?{has_video:false,audio_alias:{version:1,physical_media_id:'m',source_media_id:'m',channel_index:1}}:{})};f.scope.S.binSel=new Set(['child']);const report=await f.review();assert.equal(f.body().media_id,'child');assert.equal(report.media_id,'child');assert.equal(f.dialog().apply.disabled,false);}
});

test('malformed review, unexpected owner/settings or modified reviewed envelope never permits Apply',async()=>{
 for(const extra of [{context:context('r9')},{requested_media_id:'n'},{kind:'wrong'},{fingerprint:'bad'},{settings:{fps:'24/1',include_fullmix:false}},{settings:{fps:null,include_fullmix:false}},{affected_media_ids:[]}]){const f=fixture();await f.review(extra);assert.equal(f.dialog().apply.disabled,true);assert.equal(Boolean(f.scope.projectSaveState().error),false);}
 const f=fixture(),r=await f.review();r.summary.message='mutated review';await f.dialog().apply.click();assert.equal(f.requests.length,1);
});

test('fresh timeline edit or saved revision invalidates reviewed application',async()=>{
 const f=fixture();await f.review();f.scope.applyOps([{op:'set_clip',sequence:'s1',track:'v1',clip:{id:'a',out:19}}],'trim','trim');await f.dialog().apply.click();assert.equal(f.requests.length,2);f.requests[1].resolve(saved('r1'));await until(()=>!f.scope.projectSaveState().pending);await f.dialog().apply.click();assert.equal(f.requests.length,2);
});

test('known Apply refusal creates no Recovery and requires a new review',async()=>{
 const f=fixture();await f.review();const pending=f.dialog().apply.click();await until(()=>f.requests.length===2);f.reply(1,{detail:'Source file changed; review again.'},409);assert.equal(await pending,false);assert.equal(Boolean(f.scope.projectSaveState().error),false);assert.equal(f.dialog().review.disabled,false);assert.equal(f.dialog().apply.disabled,true);assert.match(f.dialog().output.textContent,/Source file changed/);
});

test('unknown or malformed Apply acknowledgements enter Recovery with no reload or replay',async()=>{
 for(const malformed of [false,true]){const f=fixture();await f.review();const pending=f.dialog().apply.click();await until(()=>f.requests.length===2);if(malformed)f.reply(1,{ok:true});else f.requests[1].reject(Error('Response lost after possible commit'));assert.equal(await pending,false);assert.match(f.scope.projectSaveState().error,/not confirmed/);await f.dialog().apply.click();await f.dialog().review.click();assert.equal(f.requests.length,2);}
});

test('late foreign Apply result never reloads or reports success in the new owner',async()=>{
 const f=fixture(),r=await f.review(),pending=f.dialog().apply.click();await until(()=>f.requests.length===2);f.scope.S.proj=plain(f.project);f.scope.S.seq=f.scope.S.proj.sequences[0];f.scope.S.context=context('r0','folder-b');const messages=f.messages.length;f.reply(1,{ok:true,context:context('r1')});assert.equal(await pending,false);assert.equal(f.requests.length,2);assert.equal(f.messages.length,messages);
});

test('cancelling after dispatch does not withdraw the saved command or trigger a second request',async()=>{
 const f=fixture(),r=await f.review(),pending=f.dialog().apply.click();await until(()=>f.requests.length===2);f.dialog().cancel.click();assert.match(f.messages.at(-1),/already submitted/);await f.ack(pending,r);assert.equal(f.requests.length,3);assert.equal(f.scope.S.proj.media.m.interpret_fps,'30000/1001');assert.equal(Boolean(f.scope.projectSaveState().error),false);
});

test('last dispatch guard catches cancellation during transaction save flush',async()=>{
 const f=fixture();await f.review();const original=f.scope.CR.workflowRequest;f.scope.CR.workflowRequest=(request,basis)=>original(context=>{f.dialog().cancel.click();return request(context);},basis);assert.equal(await f.dialog().apply.click(),false);assert.equal(f.requests.length,1);assert.equal(Boolean(f.scope.projectSaveState().error),false);
});

test('own preparation updates after dispatch are accepted, and newer Source/marks are not stolen',async()=>{
 for(const newer of ['status','source','marks']){const f=fixture(),r=await f.review(),pending=f.dialog().apply.click();await until(()=>f.requests.length===2);if(newer==='status')f.project.media.m.status='ingesting';if(newer==='source')f.scope.S.src=f.project.media.n;if(newer==='marks')f.scope.S.srcIn=7;await f.ack(pending,r);assert.equal(Boolean(f.scope.projectSaveState().error),false);if(newer==='source'){assert.equal(f.scope.S.src.id,'n');assert.equal(f.scope.S.srcIn,1);}if(newer==='marks')assert.equal(f.scope.S.srcIn,7);}
});

test('Apply pauses Program without committing active Trim Edit',async()=>{
 const f=fixture(),r=await f.review(),calls=[];f.scope.S.playing=true;f.scope.CR.togglePlay=(...args)=>{calls.push(plain(args));f.scope.S.playing=false;};const pending=f.dialog().apply.click();await until(()=>f.requests.length===2);assert.deepEqual(calls,[[false,{commitTrim:false}]]);await f.ack(pending,r);
});

test('dialog keyboard containment, focus wrap and explicit Review activation use actual handlers',async()=>{
 const f=fixture(),origin=f.scope.$('#origin');origin.focus();f.open();f.flushTimers();const d=f.dialog();assert.equal(f.scope.document.activeElement,d.mode);d.mode.focus();const tab=event({key:'Tab',shiftKey:true,target:d.mode});d.box.onkeydown(tab);assert.equal(f.scope.document.activeElement,d.review);f.choose();const enter=event({key:'Enter',target:d.rate});d.box.onkeydown(enter);await until(()=>f.requests.length===1);f.reply(0,f.report());await until(()=>!d.review.disabled);assert.equal(f.requests.length,1);d.box.onkeydown(event({key:'Escape',target:d.output}));assert.equal(d.box.classList.contains('open'),false);assert.equal(f.scope.document.activeElement,origin);
});

test('actual document has unique IDs and labelled Interpret controls; production exports required transaction helpers',()=>{
 const html=fs.readFileSync('frontend/index.html','utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);for(const id of ['sourceInterpretMode','sourceInterpretRate'])assert.match(html,new RegExp('for="'+id+'"'));assert.match(html,/id="sourceInterpretReview" role="status" aria-live="polite"/);const app=fs.readFileSync('frontend/app.js','utf8');for(const name of ['projectSaveState','workflowRequest','CLIENT'])assert.ok(app.slice(app.indexOf('window.CR =')).includes(name));
});

test('in-place timeline preview changes invalidate both delayed review and Apply',async()=>{
 const f=fixture();f.open();f.choose();const report=f.report(),pending=f.dialog().review.click();await until(()=>f.requests.length===1);f.clips[0].out=19;f.reply(0,report);await pending;assert.equal(f.dialog().apply.disabled,true);
 const g=fixture();await g.review();g.clips[0].out=19;assert.equal(await g.dialog().apply.click(),false);assert.equal(g.requests.length,1);
});
