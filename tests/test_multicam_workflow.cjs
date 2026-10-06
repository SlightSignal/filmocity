const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {fixture,plain,until,context,saved,event,section}=require('./helpers/multicam-workflow-fixture.cjs');
function upper(f,id='upper',index=9){const tr={id,kind:'video',index,clips:[{...plain(f.outer),id:id+'-clip'}]};f.scope.S.seq.tracks.push(tr);return tr;}
test('angle switching prioritizes explicit selection, then target track, then topmost visible camera',()=>{
 for(const mode of ['selected','target','top']){const f=fixture(),s=f.scope,t=upper(f);s.S.sel=new Set(mode==='selected'?[t.clips[0].id]:[]);s.S.target.video=mode==='target'?'v1':'missing';s.switchAngle(1);assert.equal(f.body().ops[0].clip.id,mode==='target'?'outer':'upper-clip');}
});
test('ambiguous selected cameras, selected disabled picture and locks refuse without falling through',()=>{
 for(const mode of ['ambiguous','selected-muted','selected-disabled','locked-head','locked-cut','bus-lock']){const f=fixture(),s=f.scope,t=upper(f);if(mode==='ambiguous')s.S.sel.add(t.clips[0].id);if(mode==='selected-muted')f.track.muted=true;if(mode==='selected-disabled')f.outer.enabled=false;if(mode.startsWith('locked'))f.track.locked=true;if(mode==='locked-cut')s.S.t=1;if(mode==='bus-lock')s.S.seq.tracks.push({id:'a1',kind:'audio',index:1,locked:true,clips:[]});const before=plain(s.S.proj);assert.equal(s.switchAngle(1),false);assert.equal(f.requests.length,0);assert.deepEqual(plain(s.S.proj),before);assert.ok(f.messages.length);}
});
test('fallback ignores muted and disabled candidates while explicit targeted locks refuse',()=>{
 for(const hidden of ['muted','disabled']){const f=fixture(),s=f.scope,t=upper(f);s.S.sel.clear();if(hidden==='muted')f.track.muted=true;else f.outer.enabled=false;s.switchAngle(1);assert.equal(f.body().ops[0].clip.id,t.clips[0].id);}
 const f=fixture();upper(f);f.scope.S.sel.clear();f.track.locked=true;assert.equal(f.scope.switchAngle(1),false);assert.equal(f.requests.length,0);
});
test('same angle, invalid index and frame-rounded end do not create edits',()=>{
 for(const n of [0,-1,2,NaN,.5]){const f=fixture();f.scope.S.t=1;f.scope.switchAngle(n);assert.equal(f.requests.length,0);assert.equal(f.track.clips.length,1);}
 const f=fixture();f.scope.S.t=3.999;f.scope.switchAngle(1);assert.equal(f.requests.length,0);
});
test('rapid queued cuts preserve reverse source clocks and advance saved revision without replay',async()=>{
 const f=fixture(),s=f.scope;f.outer.reverse=true;s.S.t=1;s.switchAngle(1);s.S.t=2;s.switchAngle(0);assert.equal(f.requests.length,1);assert.deepEqual(f.track.clips.map(c=>[c.start,c.in_,c.out,c.multicam_angle]),[[0,3,4,0],[1,2,3,1],[2,0,2,0]]);assert.equal(s.S.sel.size,1);assert.equal([...s.S.sel][0],f.track.clips[2].id);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');f.requests[1].resolve(saved('r2'));await s.flushSaves();assert.equal(Boolean(s.projectSaveState().error),false);
});
test('pending author save precedes an angle command and submitted failure blocks later cuts',async()=>{
 const f=fixture(),s=f.scope;s.applyOps([{op:'set',path:'/name',value:'Author'}],'name','name');s.switchAngle(1);assert.equal(f.requests.length,1);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');f.requests[1].reject(Error('Lost reply'));await s.flushSaves();s.S.t=1;s.switchAngle(0);assert.equal(f.requests.length,2);assert.ok(s.projectSaveState().error);
});
test('Recovery, loading owner and gesture guards prevent angle mutations',()=>{
 for(const key of ['recoveryRequired','switching','commandPending','gesture']){const f=fixture(),s=f.scope;s.S[key]=true;const before=plain(s.S.proj);s.switchAngle(1);assert.equal(f.requests.length,0);assert.deepEqual(plain(s.S.proj),before);}
});
test('angle grid uses the same directional CFR source clock and parent transport scope as Program',()=>{
 for(const [patch,t,expected] of [[{},.5,.5],[{reverse:true},.5,104/30],[{hold:true,in_:1,out:5},.5,1],[{time_remap:[{t:0,v:1},{t:2,v:3}]},.5,.625]]){const f=fixture(),s=f.scope;Object.assign(f.outer,patch);s.S.t=t;s.S.playing=true;f.drawGrid();assert.equal(f.draws.length,2);for(const draw of f.draws){assert.equal(draw.local,expected);assert.equal(draw.audioScope.audible,false);assert.equal(draw.audioScope.held,!!patch.hold);assert.equal(draw.audioScope.reversed,!!patch.reverse);assert.equal(draw.audioScope.rate,s.speedAt(f.outer,t));}assert.equal(s.S.mvGrid.n,2);}
});
test('stale grid clicks reject changed owner, selection, source timing and camera ordering',()=>{
 for(const mutate of [f=>f.scope.S.context=context('r0','folder-b'),f=>f.scope.S.seq=f.project.sequences[1],f=>f.scope.S.sel.clear(),f=>f.outer.in_=.5,f=>f.child.tracks[0].index=4,f=>f.scope.S.multiView=false]){const f=fixture();f.drawGrid();const callback=f.buttons[1].onclick;mutate(f);callback();assert.equal(f.requests.length,0);}
});
test('angle controls stay stable and pressed state updates without moving focus',()=>{
 const f=fixture();f.drawGrid();const button=f.buttons[1];button.focus();f.drawGrid();assert.equal(f.buttons[1],button);assert.equal(f.scope.document.activeElement,button);button.click();assert.equal(f.requests.length,1);f.drawGrid();assert.equal(f.buttons[1],button);assert.equal(button.attributes['aria-pressed'],'true');f.scope.S.multiView=false;f.scope.updateMulticamControls(null);assert.equal(f.scope.$('#multicamAngles').hidden,true);assert.equal(button.disabled,true);
});
test('angle keyboard shortcuts respect form/source/panel focus and suppress repeats',()=>{
 for(const [focus,form,repeat,expected] of [['program',false,false,1],['timeline',false,false,1],['source',false,false,0],['panel',false,false,0],['program',true,false,0],['program',false,true,0]]){const f=fixture();f.scope.S.focus=focus;f.keys(event({key:'2',repeat,target:{matches:()=>form,isContentEditable:false,closest:()=>null}}));assert.equal(f.requests.length,expected);}
});
test('Flatten reviews complete ranges and applies one exact saved command with no optimistic change',async()=>{
 const f=fixture(),before=plain(f.project),r=await f.review(),d=f.dialog();assert.deepEqual(plain(f.project),before);assert.equal(f.requests[0].url,'/api/multicam/flatten/review');assert.equal(d.apply.disabled,false);assert.match(d.output.textContent,/source 2.000000–10.000000 s · 2×/);const p=d.apply.click();await until(()=>f.requests.length===2);assert.equal(f.requests[1].url,'/api/multicam/flatten');assert.deepEqual(f.body(1),{sequence:'s1',clip_ids:['outer'],fingerprint:r.fingerprint,_context:context(),actor:'human',client:'test-client'});assert.deepEqual(plain(f.project),before);await f.ack(p,r);assert.deepEqual([...f.scope.S.sel],['outer','new-sound']);assert.equal(f.scope.S.seqId,'s1');assert.ok(f.scope.S.proj.sequences.find(q=>q.id==='cameras'));assert.equal(d.box.classList.contains('open'),false);
});
test('Flatten invalid selection, locked track and Recovery refuse before opening',()=>{
 for(const change of [f=>f.scope.S.sel.clear(),f=>f.track.locked=true,f=>f.scope.S.recoveryRequired=true,f=>delete f.child.multicam]){const f=fixture();change(f);assert.equal(f.open(),false);assert.equal(f.requests.length,0);}
});
test('Flatten pending save is acknowledged first and Cancel/Escape prevents readonly dispatch',async()=>{
 for(const cancel of [false,true]){const f=fixture();f.scope.applyOps([{op:'set',path:'/name',value:'Pending'}],'name','name');f.open();const p=f.dialog().review.click();if(cancel)f.window.emit('keydown',event({key:'Escape'}));f.requests[0].resolve(saved('r1'));if(cancel){await p;assert.equal(f.requests.length,1);}else{await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');f.reply(1,f.report());await p;assert.equal(f.dialog().apply.disabled,false);}}
});
test('late Flatten reviews retire across source/selection/project/sequence changes and reopened controls',async()=>{
 for(const change of [f=>f.scope.S.sel.clear(),f=>f.scope.S.context=context('r0','folder-b'),f=>f.scope.S.seq=f.project.sequences[1],f=>f.child.tracks[0].clips[0].speed=3,f=>{f.dialog().cancel.click();f.open();}]){const f=fixture();f.open();const r=f.report(),p=f.dialog().review.click();await until(()=>f.requests.length===1);change(f);f.reply(0,r);await p;assert.equal(f.dialog().apply.disabled,true);assert.equal(Boolean(f.scope.projectSaveState().error),false);}
});
test('refused, foreign and modified Flatten reviews cannot Apply',async()=>{
 for(const extra of [{ok:false,issues:[{severity:'error',message:'Shared processing is unsupported.'}]},{fingerprint:'bad'},{context:context('different')},{clip_ids:['other']}]){const f=fixture();await f.review(extra);await f.dialog().apply.click();assert.equal(f.requests.length,1);assert.equal(f.dialog().apply.disabled,true);}
 const f=fixture(),r=await f.review();r.summary.resolved[0].picture_ranges[0].speed=4;await f.dialog().apply.click();assert.equal(f.requests.length,1);
});
test('Flatten refusal keeps actionable source-sampling guidance after a retained disabled Apply callback',async()=>{
 const f=fixture(),message='The child and parent frame rates differ; removing the intermediate CFR stage changes frame selection. Use Render & Replace.';await f.review({ok:false,issues:[{code:'source_sampling',severity:'error',message}]});const text=f.dialog().output.textContent;assert.ok(text.includes(message));assert.equal(f.dialog().apply.disabled,true);await f.dialog().apply.click();assert.equal(f.dialog().output.textContent,text);assert.equal(f.requests.length,1);assert.equal(Boolean(f.scope.projectSaveState().error),false);
});
test('Flatten known refusal permits fresh review; uncertain receipt requires Recovery with no replay',async()=>{
 for(const known of [false,true]){const f=fixture();await f.review();const p=f.dialog().apply.click();await until(()=>f.requests.length===2);if(known)f.reply(1,{detail:'Target locked'},409);else f.reply(1,{ok:true});assert.equal(await p,false);assert.equal(Boolean(f.scope.projectSaveState().error),!known);await f.dialog().apply.click();assert.equal(f.requests.length,2);}
});
test('Flatten canonical acknowledgment preserves newer selection/navigation and foreign owner never reloads',async()=>{
 for(const kind of ['selection','sequence','closed','foreign']){const f=fixture(),r=await f.review(),p=f.dialog().apply.click();await until(()=>f.requests.length===2);if(kind==='selection')f.scope.S.sel=new Set(['newer']);if(kind==='sequence'){f.scope.S.seq=f.project.sequences[1];f.scope.S.seqId='s2';}if(kind==='closed')f.dialog().cancel.click();if(kind==='foreign'){f.scope.S.proj=plain(f.project);f.scope.S.context=context('r0','folder-b');f.reply(1,f.replyFlatten(r));await p;assert.equal(f.requests.length,2);continue;}await f.ack(p,r);if(kind==='selection')assert.deepEqual([...f.scope.S.sel],['newer']);if(kind==='sequence')assert.equal(f.scope.S.seqId,'s2');if(kind==='closed')assert.deepEqual([...f.scope.S.sel],['outer']);}
});
test('Flatten final dispatch guard and pause avoid accidental Trim Edit commits',async()=>{
 const f=fixture(),r=await f.review(),calls=[];f.scope.S.playing=true;f.scope.togglePlay=(...args)=>{calls.push(plain(args));f.scope.S.playing=false;};const p=f.dialog().apply.click();await until(()=>f.requests.length===2);assert.deepEqual(calls,[[false,{commitTrim:false}]]);await f.ack(p,r);
 const g=fixture();await g.review();const original=g.scope.workflowRequest;g.scope.workflowRequest=(request,basis)=>original(c=>{g.dialog().cancel.click();return request(c);},basis);assert.equal(await g.dialog().apply.click(),false);assert.equal(g.requests.length,1);
});
test('actual document has unique multicam dialog IDs and labelled controls',()=>{
 const html=fs.readFileSync('frontend/index.html','utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);assert.match(html,/id="multicamAngleButtons" role="group" aria-label="Camera angles"/);assert.match(html,/id="dlgMulticamFlatten"[^>]+aria-labelledby="multicamFlattenTitle"/);
});

test('queued multicamera edits remain bound to the original owner after a project change',async()=>{
 const f=fixture(),s=f.scope;s.S.t=1;s.switchAngle(1);s.S.t=2;s.switchAngle(0);const other=plain(f.project);s.S.proj=other;s.S.seq=other.sequences[1];s.S.seqId=s.S.seq.id;s.S.context=context('r0','folder-b');const before=plain(other);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.project,'folder-a');assert.equal(f.body(1)._context.revision,'r1');f.reply(1,{detail:'The active project changed'},409);await Promise.resolve();assert.deepEqual(other,before);assert.equal(s.S.context.project,'folder-b');assert.equal(s.S.context.revision,'r0');assert.equal(f.requests.length,2);
});
test('actual camera decoder remains paused for inherited reverse/hold and receives ramp rate without camera sound',()=>{
 for(const [patch,time] of [[{reverse:true},.5],[{hold:true,in_:1,out:5},.5],[{time_remap:[{t:0,v:1},{t:2,v:3}]},.5]]){
  const f=fixture();Object.assign(f.outer,patch);f.scope.S.t=time;f.drawGrid();
  const actual=require('./audio_preview_fixture.cjs').fixture(),e=actual.env;e.window.FilmocityTime=require('../frontend/timeline-time.js');e.FilmocityMediaColor=require('../frontend/media-color.js');e.cssFilter=()=> 'none';actual.S.prefs={};
  Object.assign(actual.S.proj.media.m,{has_video:true,has_audio:true,frame_rate:'30/1',width:64,height:48,duration:30});Object.assign(actual.clip,{in_:0,out:30,speed:1});
  const draw=f.draws[0];e.drawSequence(actual.canvas(),actual.seq,draw.local,new Set(),1,draw.voicePath,draw.audioScope);const v=actual.elements[0];assert.equal(v.muted,true);
  if(patch.reverse||patch.hold){const frame=Math.floor(draw.local*30+1e-7);assert.ok(v.currentTime>frame/30&&v.currentTime<(frame+1)/30);assert.equal(v.plays,0);assert.equal(v.paused,true);}else{assert.equal(v.currentTime,draw.local);assert.equal(v.playbackRate,1.5);assert.equal(v.plays,1);}
 }
});
test('Flatten keyboard focus returns after Escape and obsolete Apply callback cannot act after reopening',async()=>{
 const f=fixture(),origin=f.scope.$('#origin');origin.focus();f.open();f.flushTimers();assert.equal(f.scope.document.activeElement,f.dialog().review);const r=await f.review(),old=f.dialog().apply.onclick;f.dialog().box.onkeydown(event({key:'Escape',target:f.dialog().apply}));assert.equal(f.scope.document.activeElement,origin);f.open();await old();assert.equal(f.requests.length,1);assert.equal(f.dialog().apply.disabled,true);assert.ok(r.ok);
});

test('angle cuts refuse detached partner duplication while head picture switches keep the association',()=>{
 for(const kind of ['receipt','reverse-reference']){const f=fixture();f.outer.audio.linked=false;if(kind==='receipt')f.outer.audio_detached_id='audio-child';else f.scope.S.seq.tracks.push({id:'sound',kind:'audio',index:2,clips:[{id:'audio-child',unlinked_from:'outer',media_id:'m',start:0,in_:0,out:4}]});f.scope.S.t=1;const before=plain(f.project);assert.equal(f.scope.switchAngle(1),false);assert.deepEqual(plain(f.project),before);assert.equal(f.requests.length,0);f.scope.S.t=0;f.scope.switchAngle(1);assert.equal(f.requests.length,1);assert.equal(f.outer.audio.linked,false);}
});
test('grid mouse activation precedes transform handles and rejects outside cells',()=>{
 const f=fixture();f.drawGrid();f.scope.monitorHit=()=>{throw Error('The hidden Program transform handle must not intercept a camera cell');};f.scope.canvasDrag(event({clientX:500,clientY:50}));assert.equal(f.requests.length,1);assert.equal(f.body().ops[0].clip.multicam_angle,1);
 const g=fixture();g.drawGrid();g.scope.canvasDrag(event({clientX:-10,clientY:50}));assert.equal(g.requests.length,0);
});

test('angle cuts preserve retimed source travel and complete surviving effect/marker payloads',()=>{
 const f=fixture(),s=f.scope;Object.assign(f.outer,{time_remap:[{t:0,v:1},{t:2,v:3}],note:'Authored note',fx_stack:[{type:'gaussian_blur',enabled:true,params:{sigma:2}}],markers:[{t:.2,name:'Earlier'},{t:1,name:'Later'}],keyframes:{'audio.gain_db':[{t:0,v:-6},{t:2,v:0}]}});const old=plain(f.outer);s.S.t=.5;s.switchAngle(1);assert.equal(f.requests.length,1);const [left,right]=f.track.clips,clock=require('../frontend/source-clock.js'),model=c=>({duration:s.clipDur(c),sourceOffset:t=>s.sourceOffset(c,t)});assert.ok(Math.abs(clock.sourceTime(right,.1,model(right))-clock.sourceTime(old,.6,model(old)))<1e-9);assert.equal(right.note,old.note);assert.deepEqual(plain(right.fx_stack),old.fx_stack);assert.equal(right.markers[0].name,'Later');assert.equal(right.markers[0].t,.5);assert.equal(left.markers[0].name,'Earlier');assert.ok(right.keyframes['audio.gain_db'].length);assert.equal(right.multicam_angle,1);
});

test('Flatten rejects altered saved processing but accepts reordered equivalent summary keys',async()=>{
 const f=fixture(),r=await f.review(),p=f.dialog().apply.click();await until(()=>f.requests.length===2);f.reply(1,f.replyFlatten(r,{summary:{...r.summary,processing:['Dropped processing']}}));assert.equal(await p,false);assert.match(f.scope.projectSaveState().error,/not confirmed/);assert.equal(f.requests.length,2);
 const g=fixture(),rr=await g.review(),pp=g.dialog().apply.click();await until(()=>g.requests.length===2);await g.ack(pp,rr,{summary:Object.fromEntries(Object.entries(rr.summary).reverse())});assert.equal(Boolean(g.scope.projectSaveState().error),false);
});
