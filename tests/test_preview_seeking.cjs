// Production seeking/compositor functions with a controlled microsecond clock.
// This models boundary truncation; final native decoded-picture QA is separate.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const timing = require('../frontend/timeline-time.js');
const clock = require('../frontend/source-clock.js');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a); return source.slice(a, b);
}
function environment() {
  const env = {S:{playing:false,seq:{fps:120}},window:{FilmocityTime:timing,FilmocitySourceClock:clock}};
  vm.createContext(env);
  vm.runInContext(section('const timing =', 'function bezierY(') +
    section('function pictureSourcePosition(', 'function activeClipsOf('), env);
  return env;
}
function decoder(rate, existing = {}) {
  let current = 0; const assigned = [], listeners = {};
  const video = Object.assign(existing, {duration:4,paused:true,readyState:2,style:{},src:'',
    addEventListener(name, callback){listeners[name] = callback;},
    getAttribute(key){return this[key];},removeAttribute(key){this[key]='';},
    pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();},load(){},remove(){}});
  Object.defineProperty(video,'currentTime',{configurable:true,get:()=>current,
    set:value=>{assigned.push(value);current=Math.floor(value*1e6)/1e6;}});
  return {video,assigned,listeners,frame:()=>Math.floor(current*rate)};
}

test('real compositor seeks every paused24/60/120fps step despite microsecond boundary truncation',()=>{
  const {fixture} = require('./audio_preview_fixture.cjs');
  for (const fps of [24,60,120]) {
    const f=fixture(), d=decoder(fps); f.S.playing=false; f.seq.fps=25;
    f.env.window.FilmocityTime=timing; f.clip.out=4; f.track.kind='audio';
    Object.assign(f.S.proj.media.m,{has_video:true,has_audio:false,frame_rate:String(fps),fps:25});
    f.env.document.createElement=kind=>{assert.equal(kind,'video');return d.video;};
    for(const index of [0,1,2,1,0]) {
      f.draw(timing.fromFrames(index,fps)); assert.equal(d.frame(),index);
      const seekCount=d.assigned.length; f.draw(timing.fromFrames(index,fps));
      assert.equal(d.assigned.length,seekCount,'Same paused frame must not start another seek');
    }
    assert.equal(d.assigned.length,5);
  }
});

test('same-frame boundary is corrected once and seeked redraws cannot form a seek loop',()=>{
  const env=environment(), d=decoder(24), media={has_video:true,frame_rate:'24/1'};
  d.video.currentTime=1/24; assert.equal(d.frame(),0,'Control reproduces leading-boundary truncation');
  assert.equal(env.seekPreviewPicture(d.video,1/24,media),true); assert.equal(d.frame(),1);
  const count=d.assigned.length;
  for(let i=0;i<100;i++)assert.equal(env.seekPreviewPicture(d.video,1/24,media),false);
  assert.equal(d.assigned.length,count);
});

test('native rate and existing reverse/hold/ramp/subclip/interpretation mappings choose the retained picture',()=>{
  const env=environment(), media={has_video:true,frame_rate:'60/1',fps:30,interpret_fps:30,sub_in:2};
  const cases=[
    [{in_:0,out:2,speed:2},1/30,62],
    [{in_:0,out:2,speed:1,reverse:true},0,119],
    [{in_:0,out:2,hold:true},.5,60],
    [{in_:0,out:2,time_remap:[{t:0,v:1},{t:1,v:3}]},.5,82],
  ];
  for(const [clip,local,index] of cases){
    const before=JSON.stringify({clip,media}), d=decoder(60);
    const position=env.pictureSourcePosition(clip,local,media);
    env.seekPreviewPicture(d.video,position,media); assert.equal(d.frame(),index);
    assert.equal(JSON.stringify({clip,media}),before,'Seeking must not mutate stored clocks/metadata');
  }
});

test('held and reverse pictures use paused frame ownership during global playback',()=>{
  const {fixture}=require('./audio_preview_fixture.cjs');
  for(const mode of ['hold','reverse','nestedHold']){
    const f=fixture(), d=decoder(24);f.S.playing=true;f.clip.out=2;f.track.kind='audio';
    Object.assign(f.S.proj.media.m,{has_video:true,has_audio:false,frame_rate:24});
    f.env.window.FilmocityTime=timing;f.env.document.createElement=()=>d.video;
    if(mode==='hold'){f.clip.hold=true;f.clip.in_=1/24;}
    if(mode==='reverse')f.clip.reverse=true;
    if(mode==='nestedHold')f.clip.in_=1/24;
    const draw=()=>f.env.drawSequence(f.canvas(),f.seq,0,new Set(),0,['program'],mode==='nestedHold'?{held:true}:null);
    draw();assert.equal(d.frame(),mode==='reverse'?47:1);assert.equal(d.video.paused,true);
    const count=d.assigned.length;draw();assert.equal(d.assigned.length,count);
    assert.equal(f.S.playing,true,'Paused picture voices must not stop Program playback');
  }
});

test('source monitor steps native frames while program playing and retains fractional subclip bounds',()=>{
  const {create}=require('./helpers/navigation-fixture.cjs');
  for(const fps of [24,60,120]){
    const h=create({fps:25}), d=decoder(fps,h.video);
    h.S.src={has_video:true,frame_rate:String(fps),duration:4}; h.S.playing=true;
    for(const n of [1,1,-1,-1]){assert.equal(h.env.stepSourceFrame(n),true);}
    assert.equal(d.frame(),0);assert.equal(h.S.playing,true);
    assert.deepEqual(d.assigned.map(t=>Math.floor(t*fps)),[1,2,1,0]);
  }
  const h=create(), d=decoder(24,h.video);h.S.src={has_video:true,frame_rate:24,sub_in:.01,duration:.02};
  assert.equal(h.env.seekSourceTime(0),true);assert.ok(d.video.currentTime>=.01 && d.video.currentTime<.03);
});

test('last partial native frame stays inside its real decoder duration',()=>{
  const env=environment(), d=decoder(60);d.video.duration=2.005;
  env.seekPreviewPicture(d.video,2.004,{has_video:true,frame_rate:60});
  assert.equal(d.frame(),120);assert.ok(d.video.currentTime<2.005);
  const count=d.assigned.length;env.seekPreviewPicture(d.video,2.004,{has_video:true,frame_rate:60});
  assert.equal(d.assigned.length,count);
});

test('paused Source logical clocks and marks retain exact chosen time and expire after playback or owner changes',()=>{
  const {create}=require('./helpers/navigation-fixture.cjs');
  for(const fps of [24,60,120]){
    const h=create(), d=decoder(fps,h.video);
    h.S.src={id:'m',path:'original.mp4',has_video:true,frame_rate:fps,duration:4};h.S.focus='source';h.S.proj.media={m:h.S.src};
    const values={};h.env.$=id=>id==='#srcVideo'?h.video:(values[id]||={textContent:''});
    vm.runInContext(section('function updateSrcIO(', 'function bindMonitorScrub('),h.env);
    assert.equal(h.env.stepSourceFrame(1),true);assert.equal(d.frame(),1);
    assert.equal(h.env.sourcePlayheadTime(),1/fps);h.env.updateSourceTime();assert.equal(values['#srcTC'].textContent,'00:00:00:01');
    h.env.markIO('in');assert.equal(h.S.srcIn,1/fps);
    h.env.seekSourceTime(.123456);h.env.markIO('out');assert.equal(h.S.srcOut,.123456);
    d.video.paused=false;assert.equal(h.env.sourcePlayheadTime(),d.video.currentTime);
    d.video.paused=true;h.S.src={...h.S.src};assert.equal(h.env.sourcePlayheadTime(),d.video.currentTime);
  }
});

test('playing resynchronization and audio-only clocks retain their existing tolerance',()=>{
  const env=environment(), d=decoder(60);env.S.playing=true;
  assert.equal(env.seekPreviewPicture(d.video,1/60,{has_video:true,frame_rate:60}),false);
  assert.equal(env.seekPreviewPicture(d.video,.2,{has_video:true,frame_rate:60}),true);
  assert.equal(d.video.currentTime,.2,'Playing position must not be moved to a frame center');
  const a=decoder(60);env.S.playing=false;
  assert.equal(env.seekPreviewPicture(a.video,.02,{has_video:false}),false);
  env.seekPreviewPicture(a.video,.2,{has_video:false});assert.equal(a.video.currentTime,.2);
  const vfr=decoder(60);env.seekPreviewPicture(vfr.video,.012345,{has_video:true,vfr:true,frame_rate:60});
  assert.equal(vfr.video.currentTime,.012345,'VFR must not be snapped using an average CFR rate');
});

test('refused Source decoder seek cannot claim a new logical frame or move its marks',()=>{
  const {create}=require('./helpers/navigation-fixture.cjs'), h=create(), d=decoder(24,h.video);
  h.S.src={id:'m',has_video:true,frame_rate:24,duration:4};
  assert.equal(h.env.stepSourceFrame(1),true);assert.equal(h.env.sourcePlayheadTime(),1/24);
  const current=d.video.currentTime;
  Object.defineProperty(d.video,'currentTime',{get:()=>current,set(){throw Error('controlled decoder refusal');}});
  assert.equal(h.env.stepSourceFrame(1),false);assert.equal(h.env.seekSourceTime(.5),false);
  assert.equal(h.env.sourcePlayheadTime(),1/24);assert.equal(d.frame(),1);
});

test('Source Home/End and Match Frame retain exact logical addresses with interior pictures',()=>{
  const {create}=require('./helpers/navigation-fixture.cjs'),h=create(),d=decoder(24,h.video);
  const media={id:'m',has_video:true,frame_rate:24,duration:4};h.S.src=media;h.S.focus='source';
  assert.equal(h.env.navigateEdge(true),true);assert.equal(d.frame(),95);assert.equal(h.env.sourcePlayheadTime(),95/24);
  assert.equal(h.env.navigateEdge(false),true);assert.equal(d.frame(),0);assert.equal(h.env.sourcePlayheadTime(),0);
  h.S.proj.media={m:media};h.S.proj.sequences=[h.S.seq];h.S.t=1/24;
  h.S.seq.tracks=[{id:'V1',clips:[{id:'c',media_id:'m',start:0,in_:0,out:2,speed:1}]}];
  Object.assign(h.env,{canEdit:()=>true,loadSource:id=>{h.S.src=h.S.proj.media[id];},updateSrcIO:()=>{}});
  vm.runInContext(section('function matchFrame(', 'function nudge('),h.env);
  assert.equal(h.env.matchFrame(),true);assert.equal(d.frame(),1);assert.equal(h.env.sourcePlayheadTime(),1/24);
  assert.equal(h.S.srcIn,0);assert.equal(h.S.srcOut,2);
  const current=d.video.currentTime;
  Object.defineProperty(d.video,'currentTime',{get:()=>current,set(){throw Error('controlled decoder refusal');}});
  assert.equal(h.env.navigateEdge(true),false);assert.equal(h.env.sourcePlayheadTime(),1/24);
});

test('initial Source picture remains inside a short fractional subclip Out while its logical clock stays zero',()=>{
  const {create}=require('./helpers/navigation-fixture.cjs'),h=create(),d=decoder(24,h.video);
  const media={id:'m',name:'short alias',has_video:true,frame_rate:24,sub_in:.01,duration:.005};
  h.S.proj.media={m:media};const name={textContent:''};h.env.$=id=>id==='#srcVideo'?h.video:name;
  Object.assign(h.env,{FilmocityProxyPreview:{replaceSource(){}},sourceMediaUrl:()=> 'source.mp4',
    sourcePreviewError(){},updateSourcePreviewState(){},updateSrcIO(){},renderBin(){},setFocus(){}});
  vm.runInContext(section('function loadSource(', 'function updateSrcIO('),h.env);
  assert.equal(h.env.loadSource('m'),true);assert.equal(d.frame(),0);
  assert.ok(d.video.currentTime>=.01&&d.video.currentTime<.015);
  assert.equal(h.env.sourcePlayheadTime(),0);
});
