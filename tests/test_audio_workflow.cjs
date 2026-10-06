const test = require('node:test'), assert = require('node:assert/strict');
const { fixture, plain, context, until, saved } = require('./helpers/audio-workflow-fixture.cjs');

test('audio analysis queues all captured clips with exact settings and no local edits', async()=>{
  const f=fixture();f.scope.S.sel=new Set(['a','b']);const before=plain(f.project),pending=f.scope.startAudioTask('loudness',{target:-18.125});await until(()=>f.requests.length===1);
  assert.match(f.requests[0].url,/\/audio\/measure$/);assert.deepEqual(f.body().clip_ids,['a','b']);assert.equal(f.body().target,-18.125);assert.equal(f.body().sequence,'s1');assert.match(f.body().request_id,/^[a-f0-9]{32}$/);assert.equal(f.body().media_id,undefined);
  f.reply(0,f.queued());await pending;assert.deepEqual(plain(f.project),before);assert.equal(f.opened(),1);
});
test('audio submission flushes edits and captures acknowledged revision',async()=>{
  const f=fixture();f.scope.applyOps([{op:'set_clip',sequence:'s1',track:'v1',clip:{id:'a',note:'pending'}}],'note','note');const pending=f.scope.startAudioTask('peak',{target:-3});assert.equal(f.requests.length,1);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.equal(f.body(1)._context.revision,'r1');f.reply(1,f.queued());await pending;
});
test('selection, locks, disabled source, stale owner and Recovery reject whole gain capture',()=>{
  for(const mode of ['locked','no-audio','missing','duplicate','Recovery']){const f=fixture();if(mode==='locked')f.tr.locked=true;if(mode==='disabled')f.tr.clips[0].enabled=false;if(mode==='no-audio')f.project.media.m.has_audio=false;if(mode==='missing')f.scope.S.sel.add('absent');if(mode==='duplicate')f.tr.clips.push({...f.tr.clips[0]});if(mode==='Recovery')f.scope.projectSaveState().error='unconfirmed';assert.throws(()=>f.scope.captureAudioTargets(),/audio|Recovery|selected|ambiguous|Wait/);assert.equal(f.requests.length,0);}
});
test('selection, source parent, target, project or sequence changes during flush prevent queue',async()=>{
  for(const mode of ['selection','source','parent','target','project','sequence']){const f=fixture();f.project.media.m.subclip_of='parent';f.project.media.parent={...plain(f.project.media.m),id:'parent',subclip_of:null};f.scope.applyOps([{op:'set_clip',sequence:'s1',track:'v1',clip:{id:'a',note:'pending'}}],'note','note');const pending=f.scope.startAudioTask('peak',{target:-3});if(mode==='selection')f.scope.S.sel.clear();if(mode==='source')f.project.media.m.path='/different';if(mode==='parent')f.project.media.parent.path='/different';if(mode==='target')f.tr.clips[0].audio.gain_db++;if(mode==='project')f.scope.S.proj=plain(f.project);if(mode==='sequence')f.scope.S.seq=f.project.sequences[1];f.requests[0].resolve(saved('r1'));await assert.rejects(pending,/changed/);assert.equal(f.requests.length,1,mode);}
});
test('reverse and ramped audio captures reach renderer without scalar source arithmetic',async()=>{
  for(const mode of ['reverse','ramp']){const f=fixture(),c=f.tr.clips[0];if(mode==='reverse')c.reverse=true;if(mode==='hold')c.hold=true;if(mode==='ramp')c.time_remap=[{t:0,v:.5},{t:3,v:2}];const queued=await f.queue();assert.equal(queued.task.kind,'audio_analysis');assert.deepEqual(f.body().clip_ids,['a']);assert.equal(f.body().in,undefined);assert.equal(f.body().out,undefined);}
});
test('invalid targets and beat intervals reject before network and prompt cancel produces no task',async()=>{
  for(const target of [NaN,Infinity,-40.1,.01]){const f=fixture();await assert.rejects(f.scope.startAudioTask('peak',{target}),/target/);assert.equal(f.requests.length,0);}
  for(const every of [0,1.2,129,NaN]){const f=fixture();await assert.rejects(f.scope.startAudioTask('beats',{every}),/integer/);assert.equal(f.requests.length,0);}
  for(const answer of [null,'','0','1.5']){const f=fixture();f.scope.prompt=()=>answer;assert.equal(await f.scope.beatMarkers(),false);assert.equal(f.requests.length,0);}
});
test('beats prompt precedes submission and estimates are reviewed before marker edits',async()=>{
  const f=fixture(),before=plain(f.project);let text;f.scope.prompt=message=>{text=message;return '3';};const pending=f.scope.beatMarkers();await until(()=>f.requests.length===1);assert.match(text,/not guaranteed beats or bars/);assert.equal(f.body().every,3);assert.match(f.requests[0].url,/\/beats$/);f.reply(0,f.queued());assert.equal(await pending,true);assert.deepEqual(plain(f.project),before);assert.equal(f.opened(),1);
});
test('late A to B analysis reply never edits B or opens foreign Tasks',async()=>{
  const f=fixture(),before=plain(f.project),pending=f.scope.startAudioTask('peak',{target:-3});await until(()=>f.requests.length===1);const result=f.queued(),other=plain(f.project);f.scope.S.proj=other;f.scope.S.seq=other.sequences[0];f.scope.S.context=context('r0','other');f.reply(0,result);await pending;assert.deepEqual(other,before);assert.equal(f.opened(),0);assert.equal(f.requests.length,1);
});
test('unknown queue outcome never retries and does not enter edit Recovery',async()=>{
  const f=fixture(),pending=f.scope.startAudioTask('peak',{target:-3});await until(()=>f.requests.length===1);f.requests[0].reject(Error('lost'));await assert.rejects(pending,/Check Tasks/);assert.equal(f.requests.length,1);assert.equal(!!f.scope.projectSaveState().error,false);
});
test('capture token resists caller edits and ignores unrelated proxy metadata',()=>{
  const f=fixture(),capture=f.scope.captureAudioTargets();f.project.media.unrelated={id:'unrelated',duration:10};f.project.media.m.proxy_status='ready';assert.equal(f.scope.audioTargetsCurrent(capture),true);capture.clip_ids.push('b');assert.equal(f.scope.audioTargetsCurrent(capture),false);
});
test('owned polling returns only ready matching measurements and stops on terminal error',async()=>{
  const f=fixture(),queued=await f.queue(),pending=f.scope.waitAudioTask(queued);await until(()=>f.requests.length===2);f.reply(1,f.catalog('running'));await until(()=>f.timers.length===1);f.tick();await until(()=>f.requests.length===3);f.reply(2,f.catalog());await until(()=>f.requests.length===4);f.reply(3,f.result());const review=await pending;assert.equal(review.result.measurements[0].peak_db,-6.25);
  const g=fixture(),q=await g.queue('beats'),p=g.scope.waitAudioTask(q);await until(()=>g.requests.length===2);g.reply(1,g.catalog('error'));await assert.rejects(p,/No reliable beats/);assert.equal(g.requests.length,2);
});
test('polling refuses late selection changes, cancel, foreign project and wrong task owner',async()=>{
  for(const mode of ['selection','cancel','project','task']){const f=fixture(),queued=await f.queue();let active=true;const pending=f.scope.waitAudioTask(queued,{isCurrent:()=>active});await until(()=>f.requests.length===2);const catalog=f.catalog();if(mode==='selection')f.scope.S.sel.clear();if(mode==='cancel')active=false;if(mode==='project')f.scope.S.context=context('r0','other');if(mode==='task')catalog.tasks[0].context=context('r0','other');f.reply(1,catalog);await assert.rejects(pending,/changed|unavailable/);assert.equal(f.requests.length,2);}
});
test('strict review rejects incomplete, foreign, malformed and differently clocked measurements',async()=>{
  for(const mode of ['version','clock','kind','sequence','context','measurement','nan','duration','beat','rawops']){const f=fixture();await assert.rejects(f.review('peak',r=>{if(mode==='version')r.result.version=2;if(mode==='clock')r.result.clock='native';if(mode==='kind')r.task.kind='analysis';if(mode==='sequence')r.result.sequence='other';if(mode==='context')r.context=context('r1');if(mode==='measurement')delete r.result.measurements[0].integrated_lufs;if(mode==='nan')r.result.measurements[0].peak_db='-6';if(mode==='duration')r.result.measurements[0].duration=601;if(mode==='beat')r.result.measurements[0].beats=[-1];if(mode==='rawops')r.plan.ops=[{}];},mode==='rawops'),/confirmed|invalid|incomplete/);assert.equal(f.requests.length,1,mode);}
});
test('read-only raw silent result retains null measurements and cannot Apply',async()=>{
  const f=fixture(),reviewed=await f.review('loudness',r=>{const m=r.result.measurements[0];Object.assign(m,{silent:true,peak_db:null,rms_db:null,integrated_lufs:null,true_peak_dbtp:null,loudness_range_lu:null});},true);assert.equal(reviewed.result.measurements[0].integrated_lufs,null);await assert.rejects(f.scope.applyAudioTask('audio-task',reviewed),/changed/);assert.equal(f.requests.length,1);
});
test('late review cannot attach to changed clip, source, project or revision',async()=>{
  for(const mode of ['clip','source','project','revision']){const f=fixture(),pending=f.scope.reviewAudioTask('audio-task');await until(()=>f.requests.length===1);const result=f.result();if(mode==='clip')f.tr.clips[0].in_++;if(mode==='source')f.project.media.m.path='/other';if(mode==='project')f.scope.S.proj=plain(f.project);if(mode==='revision')f.scope.projectSaveState().revision='r1';f.reply(0,result);await assert.rejects(pending,/changed/);}
});
test('Apply requires original complete review and unchanged source, clip and locks',async()=>{
  for(const mode of ['copy','measurement','fingerprint','clip','source','lock','noop']){const f=fixture(),reviewed=await f.review();let value=reviewed;if(mode==='copy')value=plain(reviewed);if(mode==='measurement')reviewed.result.measurements[0].peak_db++;if(mode==='fingerprint')reviewed.plan.fingerprint='forged';if(mode==='clip')f.tr.clips[0].out++;if(mode==='source')f.project.media.m.path='/other';if(mode==='lock')f.tr.locked=true;if(mode==='noop')reviewed.plan.ops=[];await assert.rejects(f.scope.applyAudioTask('audio-task',value),/changed|no changes/);assert.equal(f.requests.length,1);}
});
test('Tasks review need not retain selection and Apply sends canonical fingerprint then reloads',async()=>{
  const f=fixture(),reviewed=await f.review(),before=plain(f.project);f.scope.S.sel=new Set(['b']);f.scope.S.playing=true;const stops=[];f.scope.togglePlay=(on,options)=>{stops.push({on,options:plain(options)});f.scope.S.playing=on;};const pending=f.scope.applyAudioTask('audio-task',reviewed);await until(()=>f.requests.length===2);assert.deepEqual(stops,[{on:false,options:{commitTrim:false}}]);assert.deepEqual(plain(f.project),before);assert.equal(f.body(1).fingerprint,'audio-fingerprint');assert.equal(f.body(1).ops,undefined);const project=plain(f.project);project.sequences[0].tracks[0].clips[0].audio.gain_db=1.125;await f.complete(pending,1,project);assert.equal(f.scope.S.seq.tracks[0].clips[0].audio.gain_db,1.125);
});
test('manual gain preserves fractional values and uses one guarded canonical request',async()=>{
  const f=fixture(),capture=f.scope.captureAudioTargets(),before=plain(f.project),pending=f.scope.applyManualGain(capture,'adjust',.123456);await until(()=>f.requests.length===1);assert.match(f.requests[0].url,/\/audio\/gain$/);assert.equal(f.body().value,.123456);assert.equal(f.body().mode,'adjust');assert.deepEqual(f.body().clip_ids,['a']);assert.equal(f.body().ops,undefined);assert.deepEqual(plain(f.project),before);await f.complete(pending,0);
});
test('manual gain rejects empty/nonfinite mode inputs and stale captures before mutation',async()=>{
  for(const [mode,value] of [['set',25],['set',-41],['adjust',NaN],['bad',0]]){const f=fixture();await assert.rejects(f.scope.applyManualGain(f.scope.captureAudioTargets(),mode,value),/finite/);assert.equal(f.requests.length,0);}
  const f=fixture(),capture=f.scope.captureAudioTargets();f.scope.S.sel.clear();await assert.rejects(f.scope.applyManualGain(capture,'set',0),/changed/);assert.equal(f.requests.length,0);
});
test('uncertain gain mutation enters Recovery once; definite server rejection does not',async()=>{
  for(const failure of ['lost','conflict']){const f=fixture(),pending=f.scope.applyManualGain(f.scope.captureAudioTargets(),'set',0);await until(()=>f.requests.length===1);if(failure==='lost')f.requests[0].reject(Error('lost'));else f.reply(0,{detail:'Gain curve exceeds bounds'},409);await assert.rejects(pending);assert.equal(!!f.scope.projectSaveState().error,failure==='lost');assert.equal(f.requests.length,1);}
});
test('gain dialog opens without measuring and exposes explicit Analyze→Review→Apply',async()=>{
  const f=fixture();f.scope.gainDialog();assert.equal(f.requests.length,0);assert.match(f.output(),/every existing absolute gain point/);f.choose('peak',-3);assert.equal(f.scope.$('#gOk').textContent,'Analyze clip audio');const before=plain(f.project);await f.analyzeDialog();assert.equal(f.requests.length,3);assert.deepEqual(plain(f.project),before);assert.match(f.output(),/-6.25 dBFS sample peak/);assert.match(f.output(),/\+3.2500 dB/);assert.equal(f.scope.$('#gOk').textContent,'Apply reviewed gain');const pending=f.click();await until(()=>f.requests.length===4);assert.match(f.requests[3].url,/\/apply$/);await f.complete(pending,3);assert.equal(f.scope.$('#dlgGain').classList.contains('open'),false);
});
test('dialog Cancel, reopen, fields, selection and late project changes prevent review application',async()=>{
  for(const mode of ['cancel','reopen','field','selection','project']){const f=fixture();f.scope.gainDialog();f.choose('peak',-3);const pending=f.click();await until(()=>f.requests.length===1);if(mode==='cancel')f.close();if(mode==='reopen')f.scope.gainDialog();if(mode==='field')f.choose('lufs',-18);if(mode==='selection')f.scope.S.sel=new Set(['b']);if(mode==='project')f.scope.S.proj=plain(f.project);f.reply(0,f.queued());await pending;assert.equal(f.requests.length,1,mode);assert.notEqual(f.scope.$('#gOk').textContent,'Apply reviewed gain');}
});
test('changed review choices require a new Analyze and empty numeric input never becomes zero',async()=>{
  const f=fixture();f.scope.gainDialog();f.choose('peak',-3);await f.analyzeDialog();f.choose('peak',-4);assert.equal(f.scope.$('#gOk').textContent,'Analyze clip audio');f.choose('set','');await f.click();assert.equal(f.requests.length,3);assert.match(f.output(),/finite/);
});
test('inspector auto-match opens shared loudness dialog for its captured clip with no direct request',()=>{
  const f=fixture();f.scope.$('#amTarget').value=-22;f.scope.wireAudioMatch(f.tr.clips[0],f.tr);f.scope.$('#amGo').onclick();assert.equal(f.requests.length,0);assert.equal(f.radios.find(x=>x.checked).value,'lufs');assert.equal(f.scope.$('#gLufs').value,-22);assert.equal(f.scope.$('#gOk').textContent,'Analyze clip audio');
});
test('stale inspector callback refuses after project, clip identity or selection change',()=>{
  for(const mode of ['project','clip','selection']){const f=fixture();f.scope.wireAudioMatch(f.tr.clips[0],f.tr);if(mode==='project')f.scope.S.proj=plain(f.project);if(mode==='clip')f.tr.clips[0]={...f.tr.clips[0]};if(mode==='selection')f.scope.S.sel=new Set(['b']);f.scope.$('#amGo').onclick();assert.equal(f.requests.length,0);assert.equal(f.scope.$('#dlgGain').classList.contains('open'),false);assert.match(f.messages.at(-1),/changed/);}
});
test('disabled or held clip gain may be edited manually but cannot claim an audible measurement',async()=>{
  for(const mode of ['disabled','held','unlinked']){const f=fixture(),c=f.tr.clips[0];if(mode==='disabled')c.enabled=false;if(mode==='held')c.hold=true;if(mode==='unlinked')c.audio.linked=false;const capture=f.scope.captureAudioTargets();await assert.rejects(f.scope.startAudioTask('peak',{target:-3},capture),/Enable clip audio/);assert.equal(f.requests.length,0);const pending=f.scope.applyManualGain(capture,'adjust',.25);await until(()=>f.requests.length===1);await f.complete(pending,0);}
});
test('nested audio capture binds recursive source and child-sequence edits and supports null media measurement',async()=>{
  for(const mode of ['good','child','source','parent']){const f=fixture(),nested=f.project.sequences[1],c=f.tr.clips[0];nested.name='Nested sound';delete c.media_id;c.sequence_id=nested.id;c.in_=0;c.out=3;f.project.media.m.subclip_of='parent';f.project.media.parent={...plain(f.project.media.m),id:'parent',subclip_of:null};const capture=f.scope.captureAudioTargets();assert.equal(capture.names[0],'Nested sound');const reviewed=await f.review('peak',r=>{r.result.measurements[0].media_id=null;});if(mode==='child')nested.tracks[0].clips[1].audio={gain_db:6};if(mode==='source')f.project.media.m.path='/other';if(mode==='parent')f.project.media.parent.path='/other';if(mode!=='good'){assert.equal(f.scope.audioTargetsCurrent(capture),false);await assert.rejects(f.scope.applyAudioTask('audio-task',reviewed),/changed/);assert.equal(f.requests.length,1);}else{const pending=f.scope.applyAudioTask('audio-task',reviewed);await until(()=>f.requests.length===2);await f.complete(pending,1);}}
});
test('no-op reviewed normalization disables dialog Apply and leaves project unchanged',async()=>{
  const f=fixture();f.scope.gainDialog();f.choose('peak',-3);const pending=f.click();await until(()=>f.requests.length===1);f.reply(0,f.queued());await until(()=>f.requests.length===2);f.reply(1,f.catalog());await until(()=>f.requests.length===3);const result=f.result();result.plan.ops=[];f.reply(2,result);await pending;assert.equal(f.scope.$('#gOk').disabled,true);assert.match(f.output(),/No gain changes/);assert.equal(f.requests.length,3);
});
test('final dispatch rechecks dialog choices and selection without false Recovery',async()=>{
  for(const action of ['manual','reviewed']){const f=fixture(),reviewed=action==='reviewed'?await f.review():null,capture=f.scope.captureAudioTargets(),original=f.scope.workflowRequest;f.scope.workflowRequest=(send,basis)=>original(context=>{f.scope.S.sel.clear();return send(context);},basis);const pending=action==='manual'?f.scope.applyManualGain(capture,'set',0):f.scope.applyAudioTask('audio-task',reviewed,{isCurrent:()=>f.scope.S.sel.has('a')});await assert.rejects(pending,/changed/);assert.equal(f.requests.length,action==='manual'?0:1);assert.equal(!!f.scope.projectSaveState().error,false);}
});
test('closing controls after explicit gain dispatch does not retract it or report a false dialog success',async()=>{
  const f=fixture();f.scope.gainDialog();f.choose('adj',.125);const pending=f.click();await until(()=>f.requests.length===1);f.close();assert.match(f.messages.at(-1),/already submitted gain changes remain/);const project=plain(f.project);project.sequences[0].tracks[0].clips[0].audio.gain_db=-2;await f.complete(pending,0,project);assert.equal(f.scope.$('#dlgGain').classList.contains('open'),false);assert.equal(f.scope.S.seq.tracks[0].clips[0].audio.gain_db,-2);assert.ok(!f.messages.some(value=>value.startsWith('Clip gain applied.')));assert.equal(!!f.scope.projectSaveState().error,false);
});
