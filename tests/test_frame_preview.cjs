const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../frontend/frame-preview.js');
const {deferred}=require('./dom_fixture.cjs');
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function setup(){
  const timers=new Map(),loads=[],draws=[],reports=[],saves=[],decoded=[],flush=deferred();let next=0;
  const view={context:{workspace:'w',project:'p',revision:'saved'},sequence:'s',revision:0,pending:0,error:'',gesture:false,switching:false,
    time:1001/30000,fps:30000/1001,duration:10,enabled:true,playing:false};
  let flushAction=async()=>{};
  const options={capture:()=>view,flush:()=>flushAction(),load:(url,signal)=>{const d=deferred();loads.push({url,signal,...d});return d.promise;},
    decode:async blob=>{const picture={blob,closed:false,close(){this.closed=true;}};decoded.push(picture);return picture;},
    draw:(...args)=>draws.push(args),report:(...args)=>reports.push(args),save:async(...args)=>saves.push(args),
    later:fn=>{const id=++next;timers.set(id,fn);return id;},cancel:id=>timers.delete(id)};
  const controller=create(options);
  return {view,loads,draws,reports,saves,decoded,options,controller,flush,setFlush:fn=>flushAction=fn,
    fire(){for(const [id,fn]of [...timers]){timers.delete(id);fn();}}};
}
test('frame requests carry unrounded canonical frame time and the saved project context',async()=>{
  const h=setup();h.controller.request();assert.equal(h.loads.length,0);h.fire();await tick();
  const url=new URL(h.loads[0].url,'http://filmocity');assert.equal(Number(url.searchParams.get('t')),1001/30000);
  assert.deepEqual(JSON.parse(url.searchParams.get('context')),h.view.context);
  h.loads[0].resolve('png');await tick();assert.equal(h.draws.length,1);assert.equal(h.draws[0][1].index,1);
});
test('save acknowledgement is awaited and the resulting saved revision owns the request',async()=>{
  const h=setup();h.view.pending=1;h.setFlush(()=>h.flush.promise);h.controller.request();h.fire();await tick();assert.equal(h.loads.length,0);
  h.view.context.revision='acknowledged';h.view.pending=0;h.flush.resolve();await tick();
  assert.equal(JSON.parse(new URL(h.loads[0].url,'http://filmocity').searchParams.get('context')).revision,'acknowledged');
});
test('save failures remain visible and never render the older saved edit',async()=>{
  const h=setup();h.setFlush(async()=>{h.view.error='Disk full';});h.controller.request();h.fire();await tick();
  assert.equal(h.loads.length,0);assert.equal(h.reports.length,1);assert.match(h.reports[0][0],/could not be saved/);
});
test('rapid seeks keep one worker and render only the most recent requested frame',async()=>{
  const h=setup();h.controller.request();h.fire();await tick();
  for(let i=2;i<=40;i++){h.view.time=i*1001/30000;h.controller.request();h.fire();}
  assert.equal(h.loads.length,1);h.loads[0].resolve('old');await tick();assert.equal(h.draws.length,0);assert.equal(h.loads.length,2);
  h.loads[1].resolve('latest');await tick();assert.equal(h.draws[0][1].index,40);
});
test('late frames never draw across project, sequence, local edit, playback or display changes',async()=>{
  for(const change of [h=>h.view.context.project='other',h=>h.view.sequence='other',h=>h.view.revision++,h=>h.view.context.revision='new',
    h=>h.view.playing=true,h=>h.view.enabled=false,h=>h.view.gesture=true,h=>h.view.switching=true]){
    const h=setup();h.controller.request();h.fire();await tick();change(h);h.loads[0].resolve('old');await tick();assert.equal(h.draws.length,0);
  }
});
test('ownership is checked again after image decoding and stale decoded pictures are released',async()=>{
  const h=setup(),decode=deferred(),picture={closed:false,close(){this.closed=true;}};h.options.decode=()=>decode.promise;
  h.controller.request();h.fire();await tick();h.loads[0].resolve('png');await tick();h.view.sequence='other';decode.resolve(picture);await tick();
  assert.equal(h.draws.length,0);assert.equal(picture.closed,true);
});
test('same-frame redraws reuse one decoded image and invalidation aborts/releases ownership',async()=>{
  const h=setup();h.controller.request();h.fire();await tick();h.loads[0].resolve('png');await tick();
  h.view.time+=.001;h.controller.request();assert.equal(h.loads.length,1);assert.equal(h.draws.length,2);
  h.controller.invalidate();assert.equal(h.decoded[0].closed,true);
  h.controller.request();h.fire();await tick();h.controller.invalidate();assert.equal(h.loads[1].signal.aborted,true);
  h.loads[1].resolve('late');await tick();assert.equal(h.draws.length,2);
});
test('failed requests report once until explicit invalidation or a new frame',async()=>{
  const h=setup();h.controller.request();h.fire();await tick();h.loads[0].reject(Error('Source changed'));await tick();
  h.controller.request();h.fire();await tick();assert.equal(h.loads.length,1);assert.match(h.reports[0][0],/Source changed/);
  h.controller.invalidate();h.controller.request();h.fire();await tick();assert.equal(h.loads.length,2);
});
test('end-of-sequence, invalid playhead and unsaved gesture do not enqueue frame work',async()=>{
  for(const change of [h=>h.view.time=10,h=>h.view.time=NaN,h=>h.view.time=-1,h=>h.view.gesture=true,h=>h.view.switching=true]){
    const h=setup();change(h);h.controller.request();h.fire();await tick();assert.equal(h.loads.length,0);
  }
});
test('export waits for save, avoids duplicate submissions and downloads the requested frame',async()=>{
  const h=setup();h.view.enabled=false;const first=h.controller.exportFrame();assert.equal(await h.controller.exportFrame(),false);await tick();
  h.view.time=2;h.loads[0].resolve('png');assert.equal(await first,true);assert.deepEqual(h.saves,[['png',1]]);
});
test('export refuses ownership changes and failed saves without a download',async()=>{
  const h=setup();const result=h.controller.exportFrame();await tick();h.view.context.project='other';h.loads[0].resolve('png');assert.equal(await result,false);assert.equal(h.saves.length,0);
  const failed=setup();failed.setFlush(async()=>{failed.view.error='Disk full';});assert.equal(await failed.controller.exportFrame(),false);assert.equal(failed.loads.length,0);
});
test('source export carries the selected media and rejects a changed source selection',async()=>{
  const h=setup();h.view.media='source/sub clip';h.view.sequence='source:'+h.view.media;h.view.enabled=false;
  const result=h.controller.exportFrame();await tick();const url=new URL(h.loads[0].url,'http://filmocity');assert.equal(url.searchParams.get('media'),'source/sub clip');
  h.view.sequence='source:other';h.loads[0].resolve('png');assert.equal(await result,false);assert.equal(h.saves.length,0);
});
