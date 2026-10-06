const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {fixture,plain,until,saved,context,read,section}=require('./helpers/source-creation-workflow-fixture.cjs');
const proxy=require('../frontend/proxy-preview.js'),info=require('../frontend/media-info.js');

test('actual Subclip sends selected-relative interpreted marks and reloads one confirmed owner',async()=>{
 const f=fixture(),m=f.project.media.m;f.project.media.parent={...plain(m),id:'parent',duration:20};Object.assign(m,{subclip_of:'parent',sub_in:6,duration:4,frame_rate:'60000/1001',interpret_fps:'30000/1001'});
 f.scope.S.srcIn=.75;f.scope.S.srcOut=1.875;const before=plain(f.project),pending=f.run('subclip');await until(()=>f.requests.length===1);
 assert.deepEqual(f.body(),{media_id:'m',in:.75,out:1.875,name:'Selected take',_context:context(),actor:'human',client:'test-client'});assert.deepEqual(plain(f.project),before);
 await f.complete(pending,'subclip');assert.equal(f.requests[1].url,'/api/project/state');assert.match(f.messages.at(-1),/Canonical source items saved/);assert.equal(f.scope.S.srcIn,.75);assert.equal(f.scope.S.srcOut,1.875);
});
test('Subclip defaults to entire selected source duration without native or parent conversion',async()=>{
 const f=fixture();f.scope.S.srcIn=f.scope.S.srcOut=null;f.project.media.m.duration=2.75;const pending=f.run('subclip');await until(()=>f.requests.length===1);assert.equal(f.body().in,0);assert.equal(f.body().out,2.75);await f.complete(pending,'subclip');
});
test('Subclip prompt cancellation invalid names and invalid ranges do not dispatch',async()=>{
 for(const answer of [null,'','   ','x'.repeat(257),'line\nbreak']){const f=fixture({answer});assert.equal(await f.run('subclip'),false);assert.equal(f.requests.length,0);}
 for(const [start,end] of [[NaN,3],[0,Infinity],[-1,3],[4,4],[4,3],[0,21]]){const f=fixture();f.scope.S.srcIn=start;f.scope.S.srcOut=end;assert.equal(await f.run('subclip'),false);assert.equal(f.requests.length,0);assert.equal(f.prompts.length,0);}
});
test('Subclip captures marks/source before the naming prompt and rejects changed intent',async()=>{
 for(const change of [f=>f.scope.S.src=f.project.media.n,f=>f.scope.S.srcIn=2,f=>f.project.media.m.path='/relinked',f=>f.scope.S.seq=f.project.sequences[1]]){
 const f=fixture({prompt:f=>{change(f);return 'Named';}});assert.equal(await f.run('subclip'),false);assert.equal(f.requests.length,0);}
});
test('Duplicate submits the entire ordered bin selection without copied media fields and selects acknowledged copies',async()=>{
 const f=fixture();f.scope.S.binSel=new Set(['n','m']);const originalSource=f.scope.S.src.id,pending=f.run('duplicate');await until(()=>f.requests.length===1);
 assert.deepEqual(f.body().media_ids,['n','m']);assert.deepEqual(Object.keys(f.body()).sort(),['_context','actor','client','media_ids']);assert.deepEqual([...f.scope.S.binSel],['n','m']);
 await f.complete(pending,'duplicate');assert.deepEqual([...f.scope.S.binSel],['created-0','created-1']);assert.equal(f.scope.S.src.id,originalSource);assert.equal(f.scope.S.srcIn,1);assert.equal(f.scope.S.srcOut,3);
});
test('Duplicate acknowledgement preserves a newer user bin selection',async()=>{
 const f=fixture(),pending=f.run('duplicate');await until(()=>f.requests.length===1);const records=f.records(),reply=f.result('duplicate',records);f.scope.S.binSel=new Set(['n']);await f.complete(pending,'duplicate',records,{reply});assert.deepEqual([...f.scope.S.binSel],['n']);
});
test('Breakout dispatches one selected source and renders truthful returned warnings after canonical reload',async()=>{
 const f=fixture(),pending=f.run('breakout');await until(()=>f.requests.length===1);assert.deepEqual(f.body(),{media_id:'m',_context:context(),actor:'human',client:'test-client'});
 const records=f.records(2).map((m,i)=>({...m,has_video:false,audio_alias:{version:1,source_media_id:'m',physical_media_id:'m',channel_index:i}}));
 await f.complete(pending,'breakout',records);assert.match(f.messages.at(-1),/preview preparation/);assert.deepEqual([...f.scope.S.binSel],['m']);
});
test('source command admission rejects unavailable media, unsupported channel counts, Recovery and oversized duplicate selection',async()=>{
 for(const channels of [undefined,0,1.5,33,NaN]){const f=fixture();f.project.media.m.channels=channels;assert.equal(await f.run('breakout'),false);assert.equal(f.requests.length,0);}
 for(const mode of ['subclip','duplicate','breakout'])for(const change of [f=>f.scope.S.recoveryRequired=true,f=>f.scope.S.gesture={},f=>f.scope.projectSaveState().error='uncertain',f=>f.project.media.m.subclip_of='missing']){const f=fixture();change(f);assert.equal(await f.run(mode),false);assert.equal(f.requests.length,0);}
 const f=fixture();f.scope.S.binSel=new Set(Array.from({length:51},(_,i)=>'m'+i));assert.equal(await f.run('duplicate'),false);assert.equal(f.requests.length,0);
});
test('save queue flush uses acknowledged revision and preserves locked unrelated timeline tracks',async()=>{
 for(const mode of ['subclip','duplicate','breakout']){const f=fixture();f.tr.locked=true;f.scope.applyOps([{op:'set',path:'/name',value:'Saved name'}],'name','name');const pending=f.run(mode);assert.equal(f.requests.length,1);f.requests[0].resolve(saved('r1'));await until(()=>f.requests.length===2);assert.deepEqual(f.body(1)._context,context('r1'));await f.complete(pending,mode,undefined,{index:1});assert.equal(f.scope.S.seq.tracks[0].locked,true);}
});
test('changed selection source parent marks or owner while saving retire the command without submitting',async()=>{
 for(const mode of ['subclip','duplicate','breakout'])for(const change of [f=>f.scope.S.binSel.clear(),f=>f.project.media.m.path='/new',f=>f.project.media.parent.path='/new',f=>f.scope.S.context=context('r0','foreign'),f=>f.scope.S.seq=f.project.sequences[1]]){
 const f=fixture();f.project.media.parent={...plain(f.project.media.m),id:'parent'};f.project.media.m.subclip_of='parent';f.scope.applyOps([{op:'set',path:'/name',value:'Saved'}],'name','name');const pending=f.run(mode);change(f);f.requests[0].resolve(saved('r1'));assert.equal(await pending,false);assert.equal(f.requests.length,1);}
});
test('Escape cancels save-waiting creation and repeated command invocation cannot duplicate it',async()=>{
 for(const mode of ['subclip','duplicate','breakout']){const f=fixture();f.scope.applyOps([{op:'set',path:'/name',value:'Saved'}],'name','name');const pending=f.run(mode);assert.equal(await f.run(mode),false);f.escape();f.requests[0].resolve(saved('r1'));assert.equal(await pending,false);assert.equal(f.requests.length,1);}
});
test('strict acknowledgement rejects wrong owner kind mode source list created IDs and malformed preparation into Recovery',async()=>{
 const mutations=[r=>r.project='foreign',r=>r.kind='other',r=>r.mode='subclip',r=>r.summary.source_media_ids=['n'],r=>r.summary.created_media_ids=['wrong'],r=>r.media[0].id='wrong',r=>r.media_ids[0]='m',r=>r.preparation=null,r=>r.context=context('r1','foreign')];
 for(const mutate of mutations){const f=fixture(),pending=f.run('duplicate');await until(()=>f.requests.length===1);const r=f.result('duplicate',f.records());mutate(r);f.reply(0,r);assert.equal(await pending,false);assert.match(f.scope.projectSaveState().error,/not confirmed/);assert.equal(f.requests.length,1);assert.equal(await f.run('duplicate'),false);assert.equal(f.requests.length,1);}
});
test('lost submitted mutation enters Recovery without retry; known refusal stays retryable',async()=>{
 for(const known of [false,true]){const f=fixture(),pending=f.run('breakout');await until(()=>f.requests.length===1);if(known)f.reply(0,{detail:'Source changed'},409);else f.requests[0].reject(Error('lost reply'));assert.equal(await pending,false);assert.equal(Boolean(f.scope.projectSaveState().error),!known);assert.equal(f.requests.length,1);}
});
test('postsubmit foreign owner never reloads or selects there; own preparation changes do not falsely invalidate success',async()=>{
 const f=fixture(),pending=f.run('duplicate');await until(()=>f.requests.length===1);const reply=f.result('duplicate',f.records()),foreign=plain(f.project);f.scope.S.proj=foreign;f.scope.S.seq=foreign.sequences[0];f.scope.S.context=context('r0','foreign');const count=f.messages.length;f.reply(0,reply);assert.equal(await pending,false);assert.equal(f.requests.length,1);assert.equal(f.messages.length,count);
 const good=fixture(),run=good.run('duplicate');await until(()=>good.requests.length===1);good.project.media.m.proxy_status='preparing';await good.complete(run,'duplicate');assert.equal(Boolean(good.scope.projectSaveState().error),false);
});
test('creation pauses Trim playback without submitting an implicit trim edit',async()=>{
 const f=fixture(),calls=[];f.scope.S.playing=true;f.scope.togglePlay=(...args)=>{calls.push(plain(args));f.scope.S.playing=false;};const pending=f.run('duplicate');await until(()=>f.requests.length===1);assert.deepEqual(calls,[[false,{commitTrim:false}]]);await f.complete(pending,'duplicate');
});
test('advertised Duplicate shortcut invokes the guarded callback, suppresses repeat/pending and respects form focus or custom binding',async()=>{
 const f=fixture();assert.equal(f.key().defaultPrevented,true);await until(()=>f.requests.length===1);f.key({repeat:true});f.key();assert.equal(f.requests.length,1);await f.complete(undefined,'duplicate');
 for(const target of [{matches:()=>true},{matches:()=>false,isContentEditable:true}]){const f=fixture();assert.equal(f.key({target}).defaultPrevented,undefined);assert.equal(f.requests.length,0);}
 const g=fixture();g.scope.S.keymap.duplicate_media='';g.key();assert.equal(g.requests.length,0);
 const keyboard=require('../frontend/keyboard.js');assert.equal(keyboard.readKeymap({duplicate_media:['Duplicate','ctrl+shift+/'],other:['Other','x']},{other:'ctrl+shift+/'}).duplicate_media,'');
});
const owner={workspace:'w',project:'p'},channel={id:'channel',has_audio:true,has_video:false,channels:6,sample_rate:48000,audio_alias:{version:1,channel_index:4},proxy_info:{codec:'aac',channels:2,channel_index:4,validation:'audio_alias_common_clock_metadata'}},ready={source_id:'channel',generation:'g',original_online:true,original_lease:'original',proxy_available:true,proxy_state:'ready',proxy_lease:'own-channel'};
test('typed channel aliases always use their own verified channel proxy even under Prefer original',()=>{
 for(const prefer of [true,false]){const url=new URL(proxy.playbackUrl(channel,ready,owner,prefer,true),'http://fixture');assert.equal(url.searchParams.get('proxy'),'1');assert.equal(url.searchParams.get('lease'),'own-channel');assert.match(proxy.availabilityMessage(channel,ready,true,prefer),/Channel 5 preview.*dual mono.*first audio stream/);}
 const ordinary={...channel,audio_alias:{version:1}};assert.match(proxy.playbackUrl(ordinary,ready,owner,false,true),/proxy=0/);
});
test('pending stale missing foreign unverified and mismapped channel previews cannot fall back to the original mix',()=>{
 for(const state of [{...ready,proxy_state:'none',proxy_available:false},{...ready,proxy_state:'stale_source'},{...ready,proxy_state:'unverified'},{...ready,proxy_state:'missing'},{...ready,source_id:'parent'},{...ready,proxy_lease:null}]){assert.equal(proxy.playbackUrl(channel,state,owner,false,true),'');assert.match(proxy.availabilityMessage(channel,state),/unavailable/);}
 assert.equal(proxy.playbackUrl({...channel,proxy_info:{...channel.proxy_info,channel_index:3}},ready,owner,true,true),'');assert.match(proxy.availabilityMessage({...channel,status:'ingesting'}, {...ready,proxy_available:false}),/Preparing selected-channel/);
});
test('channel information retains physical measurements and identifies one selected first-stream channel',()=>{
 assert.equal(info.audioLabel(channel),'48 kHz · 6 ch · channel 5 selected from first stream');assert.doesNotMatch(info.audioLabel(channel),/\.wav|mono/);
 assert.equal(info.audioLabel({...channel,audio_alias:{version:1},channel_mode:'left'}),'48 kHz · 6 ch · left selected');
});
test('actual Source URL and Program voice clear stale channel media instead of playing original audio',()=>{
 const f=fixture(),s=f.scope;s.FilmocityProxyPreview=proxy;s.S.mediaAvailability={channel:{...ready}};s.S.context=owner;s.S.useProxy=false;s.S.proj.media.channel=plain(channel);s.S.proj.media.channel.path='/source.wav';
 s.performance={now:()=>1};s.document.body={appendChild(){}};s.document.createElement=()=>({style:{},preload:'',attributes:{},addEventListener(){},getAttribute(k){return this.attributes[k];},set src(v){this.attributes.src=v;},pause(){this.paused=true;},removeAttribute(k){delete this.attributes[k];},load(){this.unloaded=true;}});
 vm.runInContext('const pool={};\n'+section('function sourceMediaUrl(', 'function updateSourcePresentation(')+'\n'+section('function vidFor(', 'function activeClipsOf('),s);
 assert.match(s.sourceMediaUrl(s.S.proj.media.channel),/proxy=1/);const video=s.vidFor('channel');assert.match(video.getAttribute('src'),/own-channel/);s.S.mediaAvailability.channel.proxy_state='stale_source';s.vidFor('channel');assert.equal(video.getAttribute('src'),undefined);assert.equal(video._channelPreviewUnavailable,true);assert.equal(video.paused,true);
});
test('final dispatch guard retires Escape or disconnected retained controls after the first save flush',async()=>{
 for(const cancel of [true,false]){const f=fixture(),original=f.scope.workflowRequest;let connected=true;
 f.scope.workflowRequest=(request,basis)=>{if(cancel)f.escape();else connected=false;return original(request,basis);};
 assert.equal(await f.scope.createSourceItems('duplicate',{current:()=>connected}),false);assert.equal(f.requests.length,0);assert.equal(Boolean(f.scope.projectSaveState().error),false);}
});
test('Escape after submission does not withdraw a confirmed creation or replay it',async()=>{
 const f=fixture(),pending=f.run('duplicate');await until(()=>f.requests.length===1);f.escape();await f.complete(pending,'duplicate');assert.equal(f.requests.length,2);assert.deepEqual([...f.scope.S.binSel],['created-0']);
});
test('actual Program audio status reports unavailable selected-channel audition until its verified proxy is ready',()=>{
 const f=require('./audio_preview_fixture.cjs').fixture();f.env.FilmocityProxyPreview=proxy;
 Object.assign(f.S.proj.media.m,plain(channel),{id:'m',path:'/source.wav'});f.S.mediaAvailability={m:{...ready,source_id:'m'}};f.draw();f.env.updateAudioPreviewStatus(false);assert.doesNotMatch(f.fields['#audioPreviewStatus'].textContent,/Selected-channel live audio unavailable/);
 f.S.mediaAvailability.m.proxy_state='stale_source';f.draw();f.env.updateAudioPreviewStatus(false);assert.match(f.fields['#audioPreviewStatus'].textContent,/Selected-channel live audio unavailable.*Prepare media/);assert.equal(f.elements.find(v=>v._activeVoice).getAttribute('src'),'');
});
test('actual HTML controls retain unique identifiers and the Duplicate hint matches its registered default',()=>{
 const html=fs.readFileSync(require('node:path').join(__dirname,'../frontend/index.html'),'utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);
 assert.match(html,/<button data-act="dupMedia">Duplicate selected bin items <span class="k">Ctrl\+Shift\+\//);assert.match(html,/id="srcSubclip"[^>]*title="Make Subclip \(Ctrl\+U\)"/);
});
test('Breakout validates its physical first-stream channel count and refuses to break out an already-selected channel',async()=>{
 const f=fixture();f.project.media.parent={...plain(f.project.media.m),id:'parent',channels:6};Object.assign(f.project.media.m,{subclip_of:'parent',channels:2});const pending=f.run('breakout');await until(()=>f.requests.length===1);await f.complete(pending,'breakout',f.records(6));
 const g=fixture();g.project.media.m.audio_alias={version:1,channel_index:0};assert.equal(await g.run('breakout'),false);assert.equal(g.requests.length,0);assert.match(g.messages.at(-1),/already selects one channel/);
});
test('Subclip name limit counts Unicode characters consistently with the server',async()=>{
 const name='🎬'.repeat(256),f=fixture({answer:name}),pending=f.run('subclip');await until(()=>f.requests.length===1);assert.equal(f.body().name,name);await f.complete(pending,'subclip');
 const invalid=fixture({answer:name+'x'});assert.equal(await invalid.run('subclip'),false);assert.equal(invalid.requests.length,0);
});
test('restored unprepared physical audio and still copies expose owned Prepare without bypassing stale-source refusal',async()=>{
 for(const picture of [false,true]){const f=fixture(),s=f.scope;Object.assign(f.project.media.m,{status:'unprepared',has_audio:!picture,has_video:picture,is_image:picture});
 s.S.mediaStatus={m:true};s.S.mediaAvailability={m:{source_id:'m',generation:'g',original_online:true,original_lease:'current',proxy_available:false,proxy_state:'none'}};
 s.FilmocityMediaColor=require('../frontend/media-color.js');s.window.FilmocityMediaInfo=info;s.window.FilmocityWelcome={renderWelcome(){}};s.fmtTC=String;s.wireBin=()=>{};
 vm.runInContext(section('const seqDurOf =','const frame =')+section('function renderBin()','function wireBin('),s);s.renderBin();assert.match(s.$('#bin').innerHTML,/data-prepare="m"/);
 s.S.mediaAvailability.m.proxy_state='stale_source';s.renderBin();assert.doesNotMatch(s.$('#bin').innerHTML,/data-prepare="m"/);s.S.mediaAvailability.m.proxy_state='none';
 const button=s.$('#preparePhysical'),bin=s.$('#bin');Object.assign(button,{dataset:{prepare:'m'},disabled:false});s.$$=(selector,owner)=>selector==='[data-prepare]'&&owner===bin?[button]:[];
 vm.runInContext(section('async function prepareMedia(','function importXml(')+section('function wireBin(','function trackRows('),s);s.wireBin(bin);const pending=button.onclick({preventDefault(){},stopPropagation(){}});await until(()=>f.requests.length===1);assert.equal(f.requests[0].url,'/api/tasks/media/m/prepare');assert.deepEqual(f.body(),{_context:context()});f.reply(0,{detail:'Accepted file changed; relink first.'},409);await pending;assert.match(f.messages.at(-1),/Preparation could not start/);assert.equal(Boolean(s.projectSaveState().error),false);
 }
});
