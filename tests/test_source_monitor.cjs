const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const base=require('./helpers/placement-fixture.cjs');
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);
function picture(f,native,logical){
 const time=require('../frontend/timeline-time.js'),rate=f.s.mediaRate(f.s.S.src),frame=time.displayFrame(native,rate);
 assert.equal(time.displayFrame(f.v.currentTime,rate),frame);
 assert.ok(f.v.currentTime>Math.max(f.s.sourceFrameBounds().begin,time.fromFrames(frame,rate))&&f.v.currentTime<Math.min(f.s.sourceFrameBounds().end,time.fromFrames(frame+1,rate)));
 close(f.s.sourcePlayheadTime(),logical);
}
function setup(){
 const f=base.fixture(),s=f.scope;Object.assign(f.project.media.m,{id:'m',fps:30,frame_rate:'30/1',interpret_fps:24,sub_in:6,duration:4});
 const v=s.$('#srcVideo');Object.assign(v,{currentTime:0,duration:20,paused:true,pause(){this.paused=true;}});
 s.FilmocityProxyPreview={replaceSource:()=>{v.currentTime=0;}};s.sourceMediaUrl=()=>'/source';s.sourcePreviewError=()=>{};s.updateSourcePreviewState=()=>{};s.renderBin=()=>{};s.setFocus=value=>s.S.focus=value;s.fmtTC=value=>String(value);s.seqDur=()=>20;s.seekTo=t=>{s.S.t=t;return true;};s.prompt=()=> '1';s.parseTC=(value,fps)=>require('../frontend/timeline-time.js').parseTimecode(value,fps);
 vm.runInContext(base.section('function stepSourceFrame(','function remapSegments(')+'\n'+base.section('function loadSource(','function timelineRangeHooks(')+'\n'+base.section('function navigateTo(','function rippleTrimToPlayhead('),s);
 s.loadSource('m');return {...f,s,v};
}
test('Source load and marked In/Out use subclip-local interpreted time in the actual placement',()=>{
 const f=setup();picture(f,4.8,0);f.v.currentTime=5.6;f.s.markIO('in');f.v.currentTime=6.4;f.s.markIO('out');close(f.s.S.srcIn,1);close(f.s.S.srcOut,2);
 f.tr.clips=[];f.s.S.t=3;f.s.insertFromSource('overwrite');assert.equal(f.requests.length,1);const c=f.tr.clips[0];close(c.in_,1);close(c.out,2);assert.equal(c.start,3);
});
test('Source stepping, Home/End and typed time stay inside the interpreted subclip window',()=>{
 const f=setup();f.s.stepSourceFrame(1);picture(f,4.8+1/30,1/24);
 f.s.navigateEdge(true);picture(f,8-1/30,4-1/24);f.s.stepSourceFrame(1);picture(f,8-1/30,4-1/24);
 f.s.navigateEdge(false);picture(f,4.8,0);f.s.stepSourceFrame(-1);picture(f,4.8,0);
 f.s.goToTimecode(true);picture(f,5.6,1);f.s.seekSourceTime(999);picture(f,8-1/30,4-1/24);
});
test('source time display and playback endpoint use logical duration, never parent duration',()=>{
 const f=setup();f.v.currentTime=6.4;f.s.updateSourceTime();close(Number(f.s.$('#srcTC').textContent),2);assert.equal(f.s.$('#srcDur').textContent,'4');
 f.v.paused=false;f.v.currentTime=12;f.s.updateSourceTime();assert.equal(f.v.paused,true);picture(f,8-1/30,4-1/24);
});
test('actual Source seek and marks keep fractional interpretation exact on long subclips',()=>{
 const f=setup();Object.assign(f.project.media.m,{fps:59.94,frame_rate:'60000/1001',interpret_fps:29.97,sub_in:6,duration:8000});f.v.duration=5000;
 f.s.loadSource('m');f.s.seekSourceTime(7194);picture(f,3600,7194);f.s.markIO('in');close(f.s.S.srcIn,7194);
});
test('Program compositor uses the same rational interpreted source clock as the Source monitor',()=>{
 const f=require('./audio_preview_fixture.cjs').fixture();Object.assign(f.S.proj.media.m,{fps:59.94,frame_rate:'60000/1001',interpret_fps:29.97,sub_in:6,duration:8000});
 Object.assign(f.clip,{in_:0,out:8000});f.draw(7194);const video=f.elements.find(x=>x.src.includes('/media/'));
 close(video.currentTime,3600);close(video.playbackRate,.5);
});
test('source scrub stops immediately on source change and retained moves cannot seek the new source',()=>{
 const f=setup(),bar=base.node();bar.getBoundingClientRect=()=>({left:0,width:100});f.s.bindMonitorScrub(bar,true);
 bar.onmousedown(base.event({clientX:10}));picture(f,5.12,.4);assert.ok(f.s.S.monitorScrub?.active);
 f.project.media.n={...f.project.media.m,id:'n',sub_in:0,duration:20};f.s.loadSource('n');assert.equal(f.s.S.monitorScrub,null);
 const before=f.v.currentTime;f.s.window.emit('mousemove',base.event({clientX:50,buttons:1}));assert.equal(f.v.currentTime,before);assert.equal(f.requests.length,0);
});
test('source and Program scrub cancel on lost button, Escape, blur, hidden document and foreign sequence',()=>{
 for(const kind of [true,false])for(const mode of ['buttons','escape','blur','hidden','sequence']){
  const f=setup(),bar=base.node();bar.getBoundingClientRect=()=>({left:0,width:100});f.s.bindMonitorScrub(bar,kind);bar.onmousedown(base.event({clientX:10}));
  if(mode==='buttons')f.s.window.emit('mousemove',base.event({clientX:50,buttons:0}));
  if(mode==='escape')f.s.window.emit('keydown',base.event({key:'Escape'}));
  if(mode==='blur')f.s.window.emit('blur',base.event());
  if(mode==='hidden'){f.s.document.hidden=true;f.s.document.emit('visibilitychange',base.event());}
  if(mode==='sequence'){f.s.S.seq={...f.seq};f.s.window.emit('mousemove',base.event({clientX:50,buttons:1}));}
  assert.equal(f.s.S.monitorScrub,null,`${kind}/${mode}`);assert.equal(f.requests.length,0);assert.equal(f.s.S.gesture,undefined);
 }
});
