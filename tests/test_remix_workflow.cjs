const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const base=require('./helpers/analysis-fixture.cjs');
const panels=fs.readFileSync(path.join(__dirname,'../frontend/panels.js'),'utf8');
const code=panels.slice(panels.indexOf('async function remixDialog()'),panels.indexOf('function timeTuner()'));
function fixture(){const f=base.fixture(),s=f.scope;f.tr.kind='audio';s.CR.selectedClips=s.selectedClips;s.CR.startClipAnalysis=s.startClipAnalysis;s.prompt=()=> '6';vm.runInContext(code,s);return f;}
test('actual Remix menu queues exact selected source and target for review without editing',async()=>{
 const f=fixture(),before=base.plain(f.project),pending=f.scope.remixDialog();await base.until(()=>f.requests.length===1);
 const body=f.body();assert.equal(body.target,6);assert.equal(body.in,5);assert.equal(body.out,8);assert.equal(body.clip_id,'a');assert.equal(body.sequence,'s1');assert.equal(body.bars_per_phrase,4);assert.equal(f.requests[0].url,'/api/audio/remix');
 assert.deepEqual(base.plain(f.project),before);f.reply(0,{ok:true,task:{id:'remix',kind:'analysis'},context:base.context()});await pending;
 assert.deepEqual(base.plain(f.project),before);assert.equal(f.opened(),1);assert.equal(f.requests.length,1);
});
test('actual Remix menu cancels and rejects bad targets, locks, Recovery, missing source and wrong tracks',async()=>{
 for(const mode of ['cancel','invalid','trailing','locked','recovery','missing','video','gesture']){
  const f=fixture();if(mode==='cancel')f.scope.prompt=()=>null;if(mode==='invalid')f.scope.prompt=()=>'NaN';if(mode==='trailing')f.scope.prompt=()=>'6seconds';
  if(mode==='locked')f.tr.locked=true;if(mode==='recovery')f.scope.projectSaveState().error='unknown';if(mode==='missing')delete f.project.media.m;
  if(mode==='video')f.tr.kind='video';if(mode==='gesture')f.scope.S.gesture={};
  const before=base.plain(f.project);await f.scope.remixDialog();assert.equal(f.requests.length,0,mode);assert.deepEqual(base.plain(f.project),before);
 }
});
test('actual Remix menu does not reinterpret a changed selection after the duration prompt',async()=>{
 const f=fixture();f.scope.prompt=()=>{f.scope.S.sel=new Set(['b']);return '6';};const before=base.plain(f.project);await f.scope.remixDialog();
 assert.equal(f.requests.length,0);assert.deepEqual(base.plain(f.project),before);assert.match(f.messages.at(-1),/target changed/);
});
test('actual Remix callback respects save flushing and stale project ownership',async()=>{
 const f=fixture();f.scope.applyOps([{op:'set_clip',sequence:'s1',track:'v1',clip:{id:'a',note:'pending'}}],'note','note');
 const pending=f.scope.remixDialog();assert.equal(f.requests.length,1);f.tr.clips[0].out=9;f.requests[0].resolve(base.saved('r1'));await pending;
 assert.equal(f.requests.length,1);assert.match(f.messages.at(-1),/source clip changed/);
 const g=fixture(),request=g.scope.remixDialog();await base.until(()=>g.requests.length===1);const other=base.plain(g.project);g.scope.S.proj=other;g.scope.S.seq=other.sequences[0];g.scope.S.context=base.context('r0','other');
 g.reply(0,{ok:true,task:{id:'remix'},context:base.context()});await request;assert.equal(g.opened(),0);assert.deepEqual(other,base.plain(g.project));
});
