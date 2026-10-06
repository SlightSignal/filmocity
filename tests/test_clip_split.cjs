const test=require('node:test'),assert=require('node:assert/strict');
const {fixture,event,plain,until,saved,response}=require('./helpers/cut-fixture.cjs');
const audio=require('../frontend/audio-preview.js'),timing=require('../frontend/timeline-time.js');
const clip=patch=>({id:'c',media_id:'m',start:0,in_:3,out:7,speed:1,...patch});
function near(a,b,eps=1e-9){assert.ok(Math.abs(a-b)<eps,`${a} != ${b}`);}
function source(f,c,t){return c.hold?c.in_:c.reverse?c.out-f.scope.cutClock.sourceOffset(c,t):c.in_+f.scope.cutClock.sourceOffset(c,t);}
test('Razor mouse click commits one saved cut, without needing another command',async()=>{
  const f=fixture(),c=f.install(clip());f.scope.clipEl(c,f.tr,false).onmousedown(event({clientX:90.4}));
  await until(()=>f.requests.length===1);const body=f.body();assert.equal(body.ops.length,2);assert.equal(body.tool,'razor');near(body.ops[1].clip.start,1.5);
  assert.equal(f.tr.clips.length,2);f.requests[0].resolve(saved());await until(()=>!f.scope.projectSaveState().pending);
});
test('fractional rates and hour-long positions use exact frame boundaries with snapping off',()=>{
  for(const fps of [23.976,29.97,59.94,'24000/1001','30000/1001','60000/1001',24,25,60]){
    const f=fixture(),start=timing.fromFrames(100000,fps);f.install(clip({start}),fps);f.scope.S.snap=false;
    const ops=f.scope.razorAt(start+timing.fromFrames(17.3,fps),'v1');near(ops[1].clip.start,timing.fromFrames(100017,fps));
    near(f.scope.cutClock.duration(ops[0].clip)+f.scope.cutClock.duration(ops[1].clip),4);
  }
});
test('cuts at clip edges, outside clips, on locked or missing tracks do not edit',()=>{
  const f=fixture();f.install(clip());for(const t of [0,4,5,NaN])assert.equal(f.scope.razorAt(t,'v1'),null);
  assert.equal(f.scope.razorAt(1,'missing'),null);f.tr.locked=true;assert.equal(f.scope.razorAt(1,'v1'),null);assert.equal(f.requests.length,0);
});
test('source positions and durations survive forward, reverse, constant speed and speed ramps',()=>{
  const f=fixture();for(const patch of [{},{speed:2},{speed:2,time_remap:[]},{reverse:true},{reverse:true,speed:1.5},{time_remap:[{t:0,v:.5},{t:2,v:2},{t:3,v:1,e:'hold'}]},{reverse:true,time_remap:[{t:0,v:1,e:'hold'},{t:1,v:2}]}]){
    const c=clip(patch),d=f.scope.cutClock.duration(c),at=d*.37,[a,b]=f.plan(c,at);
    near(f.scope.cutClock.duration(a),at);near(f.scope.cutClock.duration(b),d-at);
    for(let i=0;i<21;i++){const t=d*i/21,part=t<at?a:b,local=t<at?t:t-at;near(source(f,part,local),source(f,c,t));}
    assert.deepEqual(c,clip(patch));
  }
});
test('held picture keeps the same source frame and permits one frame at 59.94',()=>{
  const f=fixture(),c=clip({hold:true}),at=timing.fromFrames(1,59.94),[a,b]=f.plan(c,at);
  assert.equal(a.in_,3);assert.equal(b.in_,3);near(f.scope.cutClock.duration(a),at);near(f.scope.cutClock.duration(b),4-at);
});
test('linear, hold, ease and Bezier curves preserve values and handles through a cut',()=>{
  const f=fixture();for(const e of ['linear','hold','ease','ease_in','ease_out','bezier']){
    const curve=[{t:0,v:-8,e,o:[.2,3],vendor:'keep'},{t:4,v:5,i:[.4,-2]}],c=clip({keyframes:{'audio.gain_db':curve}}),[a,b]=f.plan(c,1.3);
    // kfVal is the production evaluator; all curve controls remain intact.
    for(let i=0;i<40;i++){const t=i/10,part=t<1.3?a:b;const actual=f.scope.evalCurve(part.keyframes['audio.gain_db'],t<1.3?t:t-1.3);near(actual,f.scope.evalCurve(curve,t),1e-7);}
    assert.equal(b.keyframes['audio.gain_db'][0].vendor,'keep');assert.equal(b.keyframes['audio.gain_db'][0].t,-1.3);
  }
});
test('ducking boundary is interpolated without negative automation times',()=>{
  const f=fixture();for(const e of ['linear','hold']){
    const c=clip({keyframes:{'audio.duck_db':[{t:0,v:0,e},{t:2,v:-18},{t:4,v:0}]}}),[a,b]=f.plan(c,1);
    assert.equal(b.keyframes['audio.duck_db'][0].t,0);
    for(let t=0;t<4;t+=.01)near(audio.duckDb(t<1?a:b,t<1?t:t-1),audio.duckDb(c,t));
  }
});
test('markers belong to one piece and boundary markers start the right piece',()=>{
  const f=fixture(),c=clip({markers:[{t:.5,name:'left'},{t:1,name:'cut'},{t:3,name:'right'}]}),[a,b]=f.plan(c,1);
  assert.deepEqual(a.markers,[{t:.5,name:'left'}]);assert.deepEqual(b.markers,[{t:0,name:'cut'},{t:2,name:'right'}]);
});
test('outer picture transitions stay on outer edges and internal transitions clear',()=>{
  const f=fixture(),c=clip({transition_in:{type:'wipe_left',duration:.5},transition_out:{type:'dip_black',duration:.5}}),[a,b]=f.plan(c,2);
  assert.deepEqual(a.transition_in,c.transition_in);assert.equal(a.transition_out,null);assert.equal(b.transition_in,null);assert.deepEqual(b.transition_out,c.transition_out);
  assert.throws(()=>f.plan(c,.25),/inside a picture transition/);assert.throws(()=>f.plan(c,3.75),/inside a picture transition/);
});
test('audio fade gain survives repeated cuts including overlapping and exponential fades',()=>{
  const f=fixture();for(const curve of ['constant_gain','constant_power','exponential']){
    const c=clip({audio:{fade_in:8,fade_out:9,gain_db:-6},audio_transition_out:{type:curve,duration:3}}),[a,b]=f.plan(c,1),[b1,b2]=f.plan(b,1.25),parts=[a,b1,b2];
    for(let t=0;t<4;t+=.01){const part=parts.find(x=>t>=x.start&&t<x.start+f.scope.cutClock.duration(x));near(audio.fadeGain(part,f.scope.cutClock.duration(part),t-part.start),audio.fadeGain(c,4,t));}
    assert.equal(b2.audio.gain_db,-6);assert.equal(b2.audio.fade_window.offset,2.25);
  }
});
test('explicit fade edits invalidate inherited anchors while other audio edits preserve them',()=>{
  const f=fixture(),[,b]=f.plan(clip({audio:{fade_in:2,fade_out:2}}),1);b.audio.gain_db=5;assert.equal(audio.fadeWindow(b,3).offset,1);
  b.audio.fade_in=.5;assert.equal(audio.fadeWindow(b,3).offset,0);assert.equal(audio.fadeGain(b,3,0),0);
});
test('Add Edit cuts target tracks together and respects locks',async()=>{
  const f=fixture();f.install(clip());f.seq.tracks.push({id:'a1',kind:'audio',index:0,clips:[clip({id:'a'})]});f.scope.S.t=1.01;f.scope.addEditAtPlayhead();await until(()=>f.requests.length===1);assert.equal(f.body().ops.length,4);
  f.requests[0].resolve(saved());await until(()=>!f.scope.projectSaveState().pending);
  f.seq.tracks[1].locked=true;f.scope.S.t=2;f.scope.addEditAtPlayhead();await until(()=>f.requests.length===2);assert.equal(f.body(1).ops.length,2);
});
test('rejected cut remains a recoverable local draft without sending another edit',async()=>{
  const f=fixture();f.install(clip());f.scope.razorAtCmd(1,'v1');await until(()=>f.requests.length===1);f.requests[0].resolve(response(409,{detail:'Project changed'}));
  await until(()=>!!f.scope.projectSaveState().error);assert.equal(f.tr.clips.length,2);assert.ok(f.storage.size>0);f.scope.razorAtCmd(2,'v1');assert.equal(f.requests.length,1);
});
test('malformed ramp and keyframes fail before the original changes',()=>{
  const f=fixture();for(const c of [clip({speed:1e-7}),clip({time_remap:[{t:0,v:1e-7}]}),clip({time_remap:[{t:0,v:0}]}),clip({keyframes:{x:[{t:0,v:'bad'}]}})]){const before=plain(c);assert.throws(()=>f.plan(c,1));assert.deepEqual(c,before);}
});

test('all-track edits include untargeted tracks and commit one batch',async()=>{
  const f=fixture();f.install(clip());f.seq.tracks.push({id:'v2',kind:'video',clips:[clip({id:'b'})]},{id:'v3',kind:'video',locked:true,clips:[clip({id:'locked'})]});
  f.scope.S.t=1;f.scope.addEditAllTracks();await until(()=>f.requests.length===1);assert.equal(f.body().ops.length,4);assert.deepEqual(f.body().ops.map(o=>o.track),['v1','v1','v2','v2']);
});
test('an unsupported transition cut aborts the entire target/all-track edit',()=>{
  for(const command of ['addEditAtPlayhead','addEditAllTracks']){
    const f=fixture();f.install(clip());f.seq.tracks.push({id:'a1',kind:'audio',clips:[clip({id:'b',transition_in:{type:'dissolve',duration:2}})]});
    const before=plain(f.project);f.scope.S.t=1;f.scope[command]();assert.equal(f.requests.length,0);assert.deepEqual(plain(f.project),before);assert.match(f.messages.at(-1),/inside a picture transition/);
  }
});
