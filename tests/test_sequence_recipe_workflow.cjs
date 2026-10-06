const test=require('node:test'),assert=require('node:assert/strict');
const {fixture,plain,until,saved,context}=require('./helpers/sequence-recipe-workflow-fixture.cjs');
test('Explainer and Variants queue captured finite choices without optimistic edits',async()=>{
 for(const mode of ['explainer','variants']){const f=fixture(),before=plain(f.project),choices=f.choices(mode),pending=f.scope.startRecipeTask(mode,choices);await until(()=>f.requests.length===1);assert.equal(f.requests[0].url,mode==='variants'?'/api/sequences/variants':'/api/recipes/explainer');assert.equal(f.body().sequence,'s1');assert.match(f.body().request_id,/^[a-f0-9]{32}$/);if(mode==='variants'){choices.hooks.reverse();choices.targets[0].layer=1;assert.deepEqual(f.body().targets,[{clip_id:'b',layer:2}]);assert.deepEqual(f.body().hooks,['One','Two']);}f.reply(0,f.queued(mode));await pending;assert.deepEqual(plain(f.project),before);}
});
test('sequence recipes work with no bin selection and preserve explicitly locked original tracks',async()=>{
 const f=fixture();f.scope.S.binSel.clear();f.tr.locked=true;const queued=await f.queue('explainer');assert.equal(queued.task.kind,'recipe');assert.equal(f.body().lower_third.name,'A name');
 const g=fixture();g.scope.S.binSel.clear();const queued2=await g.queue('variants');assert.equal(queued2.task.kind,'recipe');
});
test('Variants requires exact explicit text targets and rejects locked, missing, unrelated or excessive choices',async()=>{
 for(const patch of [{targets:[]},{targets:[{clip_id:'b',layer:0}]},{targets:[{clip_id:'a',title:false}]},{targets:[{clip_id:'other',title:true}]},{targets:[{clip_id:'b',layer:2},{clip_id:'b',layer:2}]},{hooks:Array(21).fill('x')},{hooks:['']},{name_prefix:''}]){const f=fixture();await assert.rejects(f.scope.startRecipeTask('variants',{...f.choices('variants'),...patch}));assert.equal(f.requests.length,0);}
 const f=fixture();f.tr.locked=true;await assert.rejects(f.scope.startRecipeTask('variants',f.choices('variants')),/unlocked/);assert.equal(f.requests.length,0);
});
test('Explainer rejects malformed text, times and nonboolean choices before queue',async()=>{
 for(const patch of [{lower_third:{name:'',role:'',at:1,duration:2}},{lower_third:{name:'N',role:'R',at:Infinity,duration:2}},{chapter_duration:0},{end_duration:61},{end_card:null},{chapters:'yes'}]){const f=fixture();await assert.rejects(f.scope.startRecipeTask('explainer',{...f.choices('explainer'),...patch}));assert.equal(f.requests.length,0);}
});
test('capture protects nested sequence sources, owned sequence, brand and active selection',()=>{
 for(const mode of ['nested','parent','brand','project','selection']){const f=fixture();f.seq.tracks.push({id:'nested',kind:'video',index:3,clips:[{id:'nest',sequence_id:'s2',start:0,in_:0,out:3}]});const capture=f.scope.captureRecipeTargets('variants');if(mode==='nested')f.project.sequences[1].tracks[0].clips[0].in_++;if(mode==='parent')f.project.media.m.path='/relinked';if(mode==='brand')f.project.brand={text:'changed'};if(mode==='project')f.scope.S.proj=plain(f.project);if(mode==='selection')f.scope.S.sel.clear();assert.equal(f.scope.recipeTargetsCurrent(capture),false,mode);}
});
test('pending saves flush before recipe submission; close during flush prevents undispatched work',async()=>{
 const f=fixture();await f.open('explainer');f.scope.applyOps([{op:'set_clip',sequence:'s1',track:'v1',clip:{id:'a',note:'draft'}}],'note','draft');
 // Reopen against the authored draft so only modal lifetime changes after capture.
 await f.open('explainer');const pending=f.dialog('explainer').button.onclick();f.dialog('explainer').cancel.onclick();f.requests[0].resolve(saved('r1'));await pending;assert.equal(f.requests.length,1);assert.equal(f.dialog('explainer').box.classList.contains('open'),false);
});
test('Explainer dialog exposes real timing options and Cancel makes no request',async()=>{
 const f=fixture();await f.open('explainer');assert.equal(f.scope.$('#xpLower').checked,false);assert.equal(f.scope.$('#xpAt').value,1);assert.equal(f.scope.$('#xpLowerDuration').value,4.5);assert.equal(f.scope.$('#xpChapters').checked,true);assert.equal(f.scope.$('#xpEnd').value,'');f.dialog('explainer').cancel.onclick();assert.equal(f.requests.length,0);
});
test('Variants dialog never preselects unrelated text and sends literal chosen layer only',async()=>{
 const f=fixture();await f.open('variants');const choices=f.scope.$('#hvTargets').children;assert.equal(choices.length,3);assert.ok(choices.every(row=>row.children[0].checked===false));assert.ok(choices[0].children[1].textContent.includes('<existing title>'));f.scope.$('#hvHooks').value='First hook\nSecond hook';await f.dialog('variants').button.onclick();assert.equal(f.requests.length,0);assert.match(f.dialog('variants').output.textContent,/Explicitly select/);choices[2].children[0].checked=true;const pending=f.dialog('variants').button.onclick();await until(()=>f.requests.length===1);assert.deepEqual(f.body().targets,[{clip_id:'b',layer:2}]);f.dialog('variants').cancel.onclick();f.reply(0,f.queued('variants'));await pending;
});
test('recipe review displays card positions and explicit replacement targets; malformed review never applies',async()=>{
 const f=fixture(),review=await f.review('explainer'),lines=f.scope.recipeReviewLines(review);assert.ok(lines.includes('Card: lower_third · 1.000000 – 3.000000 s · new-video · <name> · Role'));
 const g=fixture(),variants=await g.review('variants'),v=g.scope.recipeReviewLines(variants);assert.ok(v.includes('Text target: b · Layer 3'));assert.ok(v.includes('Variant: First variant · One · 1 text target(s)'));
 for(const patch of [r=>r.plan.summary.placements=[{kind:'cta',start:2,end:1,track:'x'}],r=>delete r.plan.summary.variants]){const h=fixture();await assert.rejects(h.review('explainer',patch),/incomplete/);assert.equal(h.requests.length,1);}
});
test('both sequence dialogs require separate review and fingerprinted Apply then select confirmed destination',async()=>{
 for(const mode of ['explainer','variants']){const f=fixture();await f.open(mode);if(mode==='variants'){f.scope.$('#hvHooks').value='One';f.scope.$('#hvTargets').children[2].children[0].checked=true;}const before=plain(f.project);await f.prepare(mode);assert.equal(f.requests.length,3);assert.deepEqual(plain(f.project),before);const pending=f.dialog(mode).button.onclick();await until(()=>f.requests.length===4);assert.equal(f.body(3).fingerprint,'recipe-fingerprint');assert.equal(f.body(3).ops,undefined);assert.deepEqual(plain(f.project),before);await f.complete(pending,3,mode);assert.equal(f.scope.S.seq.id,mode==='variants'?'new-variant':'s1');}
});
test('sequence dialog cancellation, settings, selection, source or project changes discard late callback ownership',async()=>{
 for(const change of ['cancel','settings','selection','source','project']){const f=fixture();await f.open('variants');f.scope.$('#hvHooks').value='One';f.scope.$('#hvTargets').children[2].children[0].checked=true;const pending=f.dialog('variants').button.onclick();await until(()=>f.requests.length===1);if(change==='cancel')f.dialog('variants').cancel.onclick();if(change==='settings'){f.scope.$('#hvHooks').value='Changed';f.dialog('variants').box.oninput();}if(change==='selection')f.scope.S.sel.clear();if(change==='source')f.project.media.m.path='/changed';if(change==='project')f.scope.S.proj=plain(f.project);f.reply(0,f.queued('variants'));await pending;assert.equal(f.requests.length,1,change);}
});
test('Cover queues immutable full-composition choices without timeline mutation or popup',async()=>{
 const f=fixture(),before=plain(f.project),choices=f.choices('cover'),pending=f.scope.startCoverTask(choices);await until(()=>f.requests.length===1);assert.equal(f.requests[0].url,'/api/recipes/cover');assert.deepEqual(f.body().sizes,[[64,48],[80,80]]);choices.sizes[0][0]=999;f.reply(0,f.queued('cover'));await pending;assert.deepEqual(plain(f.project),before);assert.equal(f.requests.length,1);
});
test('Cover rejects nonfinite/outside time, output budgets and malformed choices before queue',async()=>{
 for(const patch of [{time:Infinity},{time:-1},{time:6},{sizes:[[15,48]]},{sizes:[[64,48],[64,48]]},{sizes:[[4096,4096],[4096,4095],[100,100]]},{sizes:[]},{framing:'stretch'},{headline:'x'.repeat(501)},{template:''}]){const f=fixture();await assert.rejects(f.scope.startCoverTask({...f.choices('cover'),...patch}));assert.equal(f.requests.length,0);}
});
test('Cover settings allow current 64x48 and empty text without stripping the captured composition',async()=>{
 const f=fixture();f.seq.width=64;f.seq.height=48;await f.openCover();assert.equal(f.scope.$('#cvSizes').value,'64x48');assert.equal(f.scope.$('#cvHeadline').value,'');assert.equal(f.scope.$('#cvSub').value,'');assert.equal(f.scope.$('#cvFraming').value,'blur_fill');assert.ok(f.scope.$('#cvTemplate').children.some(node=>node.textContent==='<literal template>'));assert.equal(f.requests.length,1);
});
test('Cover template load cannot resurrect canceled or replaced dialogs or foreign projects',async()=>{
 for(const change of ['cancel','reopen','project','source']){const f=fixture(),pending=f.open('cover');await until(()=>f.requests.length===1);if(change==='cancel')f.dialog('cover').cancel.onclick();if(change==='reopen'){const next=f.open('cover');await until(()=>f.requests.length===2);f.reply(1,{'New template':{}});await next;}if(change==='project')f.scope.S.proj=plain(f.project);if(change==='source')f.project.media.m.path='/changed';f.reply(0,{'Late template':{}});await pending;assert.ok(!f.scope.$('#cvTemplate').children.some(item=>item.value==='Late template'));assert.equal(f.requests.length,change==='reopen'?2:1);}
});
test('Cover review validates owned endpoint, artifact metadata and read-only envelope',async()=>{
 for(const change of ['url','owner','size','hash','ops','frame','context','count']){const f=fixture();await assert.rejects(f.review('cover',r=>{if(change==='url')r.result.covers[0].url='https://example.com';if(change==='owner')r.result.covers[0].url=f.scope.coverArtifactUrl('cover-task',0,context('r0','other'));if(change==='size')r.result.covers[0].width=0;if(change==='hash')r.result.covers[0].sha256='bad';if(change==='ops')r.plan.ops=[{}];if(change==='frame')r.result.frame=-1;if(change==='context')r.result.context.project='other';if(change==='count')r.result.covers=[];}),/confirmed|invalid/);assert.equal(!!f.scope.projectSaveState().error,false);}
});
test('Cover owner URL encodes workspace/project; exact review object guards subsequent downloads',async()=>{
 const f=fixture();assert.equal(f.scope.coverArtifactUrl('task',0,{workspace:'a b/x',project:"(p)!"}),'/api/tasks/task/cover/0?workspace=a+b%2Fx&project=%28p%29%21');const review=await f.review('cover');assert.equal(f.scope.coverReviewCurrent(review),true);assert.equal(f.scope.coverReviewCurrent(plain(review)),false);f.project.media.m.path='/changed';assert.equal(f.scope.coverReviewCurrent(review),false);
});
test('Cover dialog displays real reviewed images and explicit owned downloads without Apply/reload',async()=>{
 const f=fixture();await f.openCover();const before=plain(f.project);await f.prepare('cover');assert.equal(f.requests.length,4);assert.deepEqual(plain(f.project),before);assert.equal(f.dialog('cover').button.disabled,true);const figure=f.scope.$('#cvResults').children[0],image=figure.children[0],link=figure.children[2];assert.equal(image.src,f.scope.coverArtifactUrl('cover-task',0,f.scope.S.context));assert.ok(link.href.endsWith('&download=1&revision=r0'));assert.match(link.textContent,/64 × 48 PNG/);let prevented=false;f.scope.S.context=context('r1');link.onclick({preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(f.requests.length,4);
});
test('Cover changes while waiting cannot install images, downloads or mutate current project',async()=>{
 for(const change of ['cancel','settings','project','sequence','source']){const f=fixture();await f.openCover();const pending=f.dialog('cover').button.onclick();await until(()=>f.requests.length===2);if(change==='cancel')f.dialog('cover').cancel.onclick();if(change==='settings'){f.scope.$('#cvHeadline').value='Changed';f.dialog('cover').box.oninput();}if(change==='project')f.scope.S.proj=plain(f.project);if(change==='sequence')f.scope.S.seq=f.project.sequences[1];if(change==='source')f.project.media.m.path='/changed';f.reply(1,f.queued('cover'));await pending;assert.equal(f.requests.length,2,change);assert.equal(f.scope.$('#cvResults').children.length,0);}
});
test('uncertain Cover queue/review stay read-only; uncertain sequence Apply enters Recovery',async()=>{
 const f=fixture(),pending=f.scope.startCoverTask(f.choices('cover'));await until(()=>f.requests.length===1);f.requests[0].reject(Error('lost'));await assert.rejects(pending,/Check Tasks/);assert.equal(!!f.scope.projectSaveState().error,false);assert.equal(f.requests.length,1);
 const g=fixture(),r=await g.review('variants'),apply=g.scope.applyRecipeTask('recipe-task',r);await until(()=>g.requests.length===2);g.requests[1].reject(Error('lost'));await assert.rejects(apply);assert.equal(!!g.scope.projectSaveState().error,true);
});

test('explicit title/layer targets accept reordered JSON keys and reject duplicate equivalent targets',async()=>{
 const f=fixture();await f.queue('variants',{targets:[{layer:2,clip_id:'b'},{title:true,clip_id:'a'}]});assert.deepEqual(f.body().targets,[{clip_id:'b',layer:2},{clip_id:'a',title:true}]);const g=fixture();await assert.rejects(g.scope.startRecipeTask('variants',{...g.choices('variants'),targets:[{layer:2,clip_id:'b'},{clip_id:'b',layer:2}]}),/distinct/);assert.equal(g.requests.length,0);
});

test('large variant catalogs require explicit selected text clips instead of silently truncating targets',()=>{
 const f=fixture();for(let i=0;i<510;i++)f.tr.clips.push({id:'text'+i,start:0,in_:0,out:1,title:{text:'Headline '+i}});f.scope.S.sel.clear();assert.throws(()=>f.scope.captureRecipeTargets('variants'),/Select the text clips.*500/);f.scope.S.sel.add('text509');const capture=f.scope.captureRecipeTargets('variants');assert.deepEqual(plain(capture.text_targets.map(x=>x.target)),[{clip_id:'text509',title:true}]);assert.equal(f.scope.recipeTargetsCurrent(capture),true);f.scope.S.sel.add('text508');assert.equal(f.scope.recipeTargetsCurrent(capture),false);assert.equal(f.requests.length,0);
});

test('large card reviews retain final global overlap warnings and disclose omitted notices',async()=>{
 const f=fixture(),r=await f.review('explainer',value=>{value.plan.summary.warnings=[...Array.from({length:70},(_,i)=>'Short chapter '+i),'Cards on independent tracks overlap; review layout.'];});const lines=f.scope.recipeReviewLines(r);assert.ok(lines.includes('Cards on independent tracks overlap; review layout.'));assert.ok(lines.includes('21 more warnings in the recipe review API response. Review the full plan before applying.'));assert.equal(lines.filter(x=>x.startsWith('Short chapter')).length,49);
});

test('actual document gives recipe controls unique IDs and Explainer leaves Export controls untouched',async()=>{
 const fs=require('node:fs'),path=require('node:path'),html=fs.readFileSync(path.join(__dirname,'../frontend/index.html'),'utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);assert.equal(new Set(ids).size,ids.length,'Duplicate IDs let global selectors bind another dialog');for(const id of ['exName','exChapters','exOut','exCancel','xpName','xpChapters','xpOut','xpCancel'])assert.equal(ids.filter(value=>value===id).length,1,id);const f=fixture();f.scope.$('#exName').value='Export filename';f.scope.$('#exChapters').checked=false;f.scope.$('#exOut').textContent='Export status';let exportCancel=0;f.scope.$('#exCancel').onclick=()=>exportCancel++;await f.open('explainer');f.scope.$('#xpChapters').checked=false;f.scope.$('#xpLower').checked=true;f.scope.$('#xpName').value='Visible recipe name';const pending=f.dialog('explainer').button.onclick();await until(()=>f.requests.length===1);assert.equal(f.body().lower_third.name,'Visible recipe name');assert.equal(f.body().chapters,false);f.dialog('explainer').cancel.onclick();f.reply(0,f.queued('explainer'));await pending;assert.equal(f.scope.$('#exName').value,'Export filename');assert.equal(f.scope.$('#exChapters').checked,false);assert.equal(f.scope.$('#exOut').textContent,'Export status');assert.equal(exportCancel,0);
});
