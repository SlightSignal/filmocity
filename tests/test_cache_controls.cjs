// Production controller with controlled DOM/API; native dialogs remain separate.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../frontend/cache-controls.js');
const {fixture,deferred}=require('./dom_fixture.cjs');
function setup(){
  const f=fixture(['info','output','segments','proxies','all','refresh']);const buttons=['segments','proxies','all'].map(n=>{f.nodes[n].dataset.cache=n;return f.nodes[n];});
  const calls=[],messages=[],sizes={proxies_mb:1,thumbs_mb:2,segments_mb:3,renders_mb:4};
  let response={...sizes,reports:[{category:'segments',removed:['one'],skipped:[],errors:[],status:'complete'}]};
  const CR={S:{context:{workspace:'w',project:'p'}},status:(...a)=>messages.push(a),api:{get:async()=>sizes,json:async(...a)=>{calls.push(a);return response;}},loadProject:async()=>{calls.push('refresh');return true;}};
  const dialog={open:true,classList:{contains:()=>dialog.open}};f.document.body={};
  const controller=create(CR,{buttons,info:f.nodes.info,output:f.nodes.output,dialog,refreshButton:f.nodes.refresh,document:f.document});return {...f,buttons,CR,calls,messages,controller,dialog,setResponse:value=>response=value};
}
test('deferred and partial cleanup remains explicit and text is literal',async()=>{
  const f=setup();f.setResponse({reports:[{status:'deferred',reason:'Cache in use <img>',removed:[],skipped:[],errors:[]},{removed:['a'],skipped:['partial'],errors:[{message:'sharing denied'}]}]});
  await f.controller.clear('all');assert.match(f.nodes.output.textContent,/Removed 1 cache files. 1 skipped; 1 failed; 1 cache\(s\) busy/);
  assert.match(f.nodes.output.textContent,/<img>/);assert.equal(f.nodes.output.innerHTML,undefined);assert.equal(f.messages.at(-1)[1],'err');
});
test('one pending cleanup disables all categories and suppresses duplicates even on refresh',async()=>{
  const f=setup(),pending=deferred();f.CR.api.json=async(...a)=>{f.calls.push(a);return pending.promise;};
  const request=f.controller.clear('segments');await f.controller.clear('proxies');await f.controller.refresh();
  assert.equal(f.calls.length,1);assert.ok(f.buttons.every(b=>b.disabled));
  pending.resolve({reports:[]});await request;assert.ok(f.buttons.every(b=>!b.disabled));assert.equal(f.controller.pending,false);
});
test('successful deletion is not reported as failed when media refresh fails',async()=>{
  const f=setup();f.CR.loadProject=async()=>{throw Error('offline');};await f.controller.clear('segments');
  assert.match(f.nodes.output.textContent,/Removed 1 cache files/);assert.match(f.nodes.output.textContent,/Cleanup finished; media display refresh failed: offline/);
});
test('unsaved edits blocking refresh do not erase the cleanup receipt',async()=>{
  const f=setup();f.CR.loadProject=async()=>false;await f.controller.clear('segments');
  assert.match(f.nodes.output.textContent,/Removed 1 cache files/);assert.match(f.nodes.output.textContent,/finish saving and refresh media/);
});
test('project change during cleanup does not refresh another project',async()=>{
  const f=setup(),pending=deferred();f.CR.api.json=()=>pending.promise;const request=f.controller.clear('proxies');f.CR.S.context.project='other';
  pending.resolve({reports:[]});await request;assert.ok(!f.calls.includes('refresh'));assert.match(f.nodes.output.textContent,/Removed 0/);
});
test('old size responses cannot replace the post-cleanup sizes',async()=>{
  const f=setup(),old=deferred();f.CR.api.get=()=>old.promise;const request=f.controller.refresh();
  await f.controller.clear('segments');old.resolve({proxies_mb:999,segments_mb:999});await request;
  assert.match(f.nodes.info.textContent,/segment cache 3 MB/);assert.doesNotMatch(f.nodes.info.textContent,/999/);
});
test('cleanup failure restores controls and never announces a cleared cache',async()=>{
  const f=setup();f.CR.api.json=async()=>{throw Error('preparation active');};await f.controller.clear('all');
  assert.match(f.nodes.output.textContent,/not confirmed: preparation active/);assert.ok(f.buttons.every(b=>!b.disabled));assert.equal(f.calls.length,0);
});
test('cache size failure is visible and active segment leases are labelled',async()=>{
  const f=setup();f.CR.api.get=async()=>{throw Error('offline');};await f.controller.refresh();assert.match(f.nodes.info.textContent,/unavailable: offline/);
  f.CR.api.get=async()=>({segment_activity:{read_leases:2}});await f.controller.refresh();assert.match(f.nodes.info.textContent,/segment cache in use/);assert.match(f.nodes.info.textContent,/proxies unavailable/);
});
test('keyboard focus returns after disabled-button focus loss without reopening a closed dialog',async()=>{
  for(const closed of [false,true]){
    const f=setup();f.nodes.segments.focus();f.CR.api.json=async()=>{f.document.activeElement=f.document.body;f.dialog.open=!closed;return {reports:[]};};
    await f.nodes.segments.onclick();assert.equal(f.document.activeElement,closed?f.document.body:f.nodes.segments);
  }
});
test('cleanup never steals focus from a control selected while work was pending',async()=>{
  const f=setup();f.nodes.segments.focus();f.CR.api.json=async()=>{f.nodes.info.focus();return {reports:[]};};
  await f.nodes.segments.onclick();assert.equal(f.document.activeElement,f.nodes.info);
});
test('size-scan failure keeps the successful deletion receipt visible',async()=>{
  const f=setup();f.setResponse({reports:[{removed:['x'],skipped:[],errors:[]}],inventory_error:'scan already running <img>'});
  await f.controller.clear('proxies');assert.match(f.nodes.output.textContent,/Removed 1 cache files/);
  assert.match(f.nodes.output.textContent,/cache sizes unavailable: scan already running <img>/);
  assert.doesNotMatch(f.nodes.output.textContent,/not confirmed/);assert.match(f.nodes.info.textContent,/proxies unavailable/);
});
test('refresh recovers the last receipt after a lost cleanup response',async()=>{
  const f=setup();f.CR.api.json=async()=>{throw Error('connection lost');};await f.controller.clear('proxies');
  f.CR.api.get=async()=>({proxies_mb:0,last_cleanup:{id:'r',reports:[{removed:['one'],skipped:['scratch'],errors:[]}]}});
  await f.nodes.refresh.onclick();assert.match(f.nodes.output.textContent,/Last cleanup: Removed 1 cache files. 1 skipped/);
  assert.doesNotMatch(f.nodes.output.textContent,/connection lost/);assert.equal(f.messages.at(-1)[1],'err');
});
test('another client cleanup disables actions until refreshed but keeps status refresh usable',async()=>{
  const f=setup();f.CR.api.get=async()=>({cleanup_active:{id:'running'}});await f.nodes.refresh.onclick();
  assert.ok(f.buttons.every(b=>b.disabled));assert.ok(!f.nodes.refresh.disabled);assert.match(f.nodes.output.textContent,/Closing this dialog does not cancel/);
  assert.equal(await f.controller.clear('proxies'),false);assert.equal(f.calls.length,0);
  f.CR.api.get=async()=>({cleanup_active:null,last_cleanup:{reports:[]}});await f.nodes.refresh.onclick();assert.ok(f.buttons.every(b=>!b.disabled));
});
test('incomplete sizes stay unavailable and name the unmeasured categories',async()=>{
  const f=setup();f.CR.api.get=async()=>({proxies_mb:null,thumbs_mb:2,size_reports:{proxies:{complete:false},thumbs:{complete:true}}});
  await f.controller.refresh();assert.match(f.nodes.info.textContent,/proxies unavailable/);assert.match(f.nodes.info.textContent,/thumbnails 2 MB/);
  assert.match(f.nodes.info.textContent,/Unmeasured entries in proxies/);
});
test('failed or blocked cleanup receipts never imply that every removal is known',async()=>{
  const f=setup();f.setResponse({status:'incomplete',reports:[],operation_error:'later stage failed'});
  await f.controller.clear('all');assert.match(f.nodes.output.textContent,/some removals may not be listed/);assert.doesNotMatch(f.nodes.output.textContent,/Removed 0/);
  f.CR.api.get=async()=>({last_cleanup:{status:'not_started',reports:[],operation_error:'preparation busy'}});
  await f.controller.refresh();assert.match(f.nodes.output.textContent,/Cleanup did not start: preparation busy/);
});
