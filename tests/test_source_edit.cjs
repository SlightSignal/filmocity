const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('./helpers/source-range-fixture.cjs'),audio=require('../frontend/audio-preview.js');
const h=create(),{edit,clock}=h,clone=x=>JSON.parse(JSON.stringify(x));
const base=extra=>({id:'c',media_id:'m',start:10,in_:5,out:13,speed:1,...extra});
const near=(a,b,message='value')=>assert.ok(Math.abs(a-b)<=1e-8*Math.max(1,Math.abs(a),Math.abs(b)),`${message}: ${a} != ${b}`);
const source=(c,t)=>{const offset=t<0?t*(c.time_remap?.[0]?.v??c.speed??1):clock(c).sourceOffset(t);return c.hold?c.in_:c.reverse?c.out-offset:c.in_+offset;};
const trim=(c,a,b,options={})=>edit.trim(c,a,b,clock(c),{sourceLimit:100,...options});
const ramp=[{t:0,v:1},{t:2,v:3,e:'hold'},{t:5,v:2},{t:8,v:.5}];
const duck=[{t:0,v:0},{t:1,v:-12,e:'hold'},{t:3,v:-12},{t:6,v:0}];

test('constant-speed trims and extensions preserve source mapping in both directions',()=>{
  for(const speed of [.25,1,2])for(const reverse of [false,true]){
    const c=base({speed,reverse}),d=clock(c).duration,part=trim(c,-.5,d+.5);
    near(clock(part).duration,d+1);near(part.start,9.5);
    for(const t of [0,.5,d/2,d+.9])near(source(part,t),reverse?c.out-(t-.5)*speed:c.in_+(t-.5)*speed);
  }
});
test('ramp extension extrapolates initial speed before zero and final speed after the last knot',()=>{
  for(const reverse of [false,true]){
    const c=base({out:25,time_remap:ramp,reverse}),d=clock(c).duration,part=trim(c,-1,d+1);
    near(clock(part).duration,d+2);near(part.time_remap[0].v,1);near(part.time_remap[1].t,1);
    for(const t of [0,.5,1,2,3,6,d+1.5]){
      const old=t-1,offset=old<0?old:clock(c).sourceOffset(old);near(source(part,t),reverse?c.out-offset:c.in_+offset);
    }
  }
});
test('trim then re-extend restores ramp knots, ducking, source windows and all markers',()=>{
  for(const reverse of [false,true]){
    const c=base({out:25,time_remap:ramp,reverse,keyframes:{'audio.duck_db':duck},markers:[{t:.5,name:'head'},{t:3,name:'middle'},{t:10,name:'tail'}]}),d=clock(c).duration;
    const cropped=trim(c,2,6),restored=trim(cropped,-2,d-2);
    near(restored.in_,c.in_);near(restored.out,c.out);near(clock(restored).duration,d);
    assert.deepEqual(restored.time_remap,c.time_remap);assert.deepEqual(restored.keyframes,c.keyframes);assert.deepEqual(restored.markers,c.markers);
    assert.ok(cropped.markers[0].t<0);assert.ok(cropped.markers.at(-1).t>clock(cropped).duration);
  }
});
test('crop then split then extend retains inherited ramp and duck history',()=>{
  const c=base({out:25,time_remap:ramp,keyframes:{'audio.duck_db':duck}}),d=clock(c).duration,cropped=trim(c,1,7);
  const [,right]=edit.split(cropped,2,'right',clock(cropped)),restored=trim(right,-3,d-3,{start:c.start});
  near(restored.in_,c.in_);near(restored.out,c.out);assert.deepEqual(restored.time_remap,c.time_remap);assert.deepEqual(restored.keyframes,c.keyframes);
});
test('manual ramp or duck edits invalidate obsolete restoration history',()=>{
  const cropped=trim(base({out:25,time_remap:ramp,keyframes:{'audio.duck_db':duck}}),2,6);
  cropped.time_remap=[{t:0,v:2}];cropped.keyframes['audio.duck_db']=[{t:0,v:-3}];
  const result=trim(cropped,-1,3);assert.ok(result.time_remap.every(p=>p.v===2));assert.ok(result.keyframes['audio.duck_db'].every(p=>p.v===-3));
});
test('head trims retain every easing control and evaluate exactly at the old clip clock',()=>{
  for(const e of ['linear','hold','ease','ease_in','ease_out','bezier']){
    const points=[{t:0,v:0,e,o:[.2,4],vendor:7},{t:8,v:20,i:[.4,-2]}],c=base({keyframes:{'transform.x':points}}),part=trim(c,2,5);
    assert.equal(part.keyframes['transform.x'][0].t,-2);assert.equal(part.keyframes['transform.x'][0].vendor,7);
    for(const t of [0,.5,1,2.9])near(h.f.scope.evalCurve(part.keyframes['transform.x'],t),h.f.scope.evalCurve(points,t+2));
  }
});
test('duck rebasing stays nonnegative and preserves hold/linear values including negative extensions',()=>{
  const c=base({keyframes:{'audio.duck_db':duck}});
  for(const begin of [-2,.5,1.5,4]){const part=trim(c,begin,8);assert.equal(part.keyframes['audio.duck_db'][0].t,0);
    for(let t=0;t<8-begin;t+=.2)near(audio.duckDb(part,t),audio.duckDb(c,t+begin));}
});
test('signed and past-duration fade windows keep their original fade clock',()=>{
  for(const curve of ['constant_gain','constant_power','exponential']){
    const c=base({audio:{fade_in:3,fade_out:4},audio_transition_out:{type:curve,duration:4}});
    for(const [begin,end] of [[-2,10],[9,12],[2,6]]){
      const part=trim(c,begin,end);assert.equal(audio.fadeWindow(part,end-begin).offset,begin);assert.equal(audio.fadeWindow(part,end-begin).duration,8);
      for(const t of [0,.25,1,Math.min(end-begin-.1,3)]){
        const spec=audio.fadeSpec(part,end-begin);assert.equal(spec[0].start,-begin);assert.equal(spec[1].start,4-begin);
        if(t+begin>=0&&t+begin<8)near(audio.fadeGain(part,end-begin,t),audio.fadeGain(c,8,t+begin));
      }
    }
  }
});
test('explicit fade setting changes reset inherited clocks while gain changes do not',()=>{
  const c=trim(base({audio:{fade_in:3,fade_out:4}}),-2,10);c.audio.gain_db=-12;assert.equal(audio.fadeWindow(c,12).offset,-2);
  c.audio.fade_out=1;assert.equal(audio.fadeWindow(c,12).offset,0);assert.equal(audio.fadeWindow(c,12).duration,12);
});
test('source bounds map handles through forward and reverse ramps',()=>{
  for(const reverse of [false,true]){const c=base({out:25,time_remap:ramp,reverse}),range=edit.bounds(c,clock(c),{sourceLimit:30});
    const part=trim(c,range.begin,range.end,{start:0,sourceLimit:30});near(part.in_,0);near(part.out,30);
    assert.throws(()=>trim(c,range.begin-.01,range.end,{start:0,sourceLimit:30}),/source handles/);
  }
});
test('slip preserves timeline, retime profile, manual curves and fades while moving source content',()=>{
  for(const reverse of [false,true])for(const time_remap of [[],ramp]){
    const c=base({out:25,reverse,time_remap,keyframes:{'audio.gain_db':[{t:0,v:-3},{t:8,v:0}],'audio.duck_db':duck},audio:{fade_in:2}}),d=clock(c).duration,shift=clock(c).sourceOffset(.5);
    const part=edit.slip(c,.5,clock(c),{sourceLimit:100});near(part.start,c.start);near(clock(part).duration,d);
    assert.deepEqual(part.time_remap,c.time_remap);assert.deepEqual(part.keyframes,c.keyframes);assert.deepEqual(part.audio,c.audio);
    for(const t of [0,1,3])near(source(part,t),source(c,t)+(reverse?-shift:shift));
  }
});
test('slip source markers follow source content under a ramp and can return from offscreen',()=>{
  const c=base({out:25,time_remap:ramp,markers:[{t:.25,name:'early'},{t:3,name:'later'}]}),part=edit.slip(c,1,clock(c),{sourceLimit:100});
  assert.ok(part.markers[0].t<0);
  for(let i=0;i<c.markers.length;i++)near(source(part,part.markers[i].t),source(c,c.markers[i].t));
  // Inverse timeline delta is determined by the ramp integral, not simply -1.
  const shift=clock(c).sourceOffset(1),back=-shift;const restored=edit.slip(part,back,clock(part),{sourceLimit:100});
  for(let i=0;i<c.markers.length;i++)near(restored.markers[i].t,c.markers[i].t);
});
test('slip bounds retain source-span width and reject overshoot without mutating input',()=>{
  for(const reverse of [false,true]){const c=base({out:25,time_remap:ramp,reverse}),before=clone(c),range=edit.slipBounds(c,clock(c),{sourceLimit:30});
    for(const delta of [range.begin,range.end]){const part=edit.slip(c,delta,clock(c),{sourceLimit:30});near(part.out-part.in_,20);assert.ok(part.in_>=-1e-9&&part.out<=30+1e-9);}
    assert.throws(()=>edit.slip(c,range.end+.01,clock(c),{sourceLimit:30}),/source handles/);assert.deepEqual(c,before);
  }
});
test('holds preserve frozen frame through duration extension and slip only through existing frames',()=>{
  const c=base({in_:1,out:3,hold:true,time_remap:ramp}),part=trim(c,-5,9,{start:0,sourceLimit:2});
  assert.equal(part.in_,1);near(clock(part).duration,14);assert.deepEqual(edit.bounds(c,clock(c),{sourceLimit:2}),{begin:-Infinity,end:Infinity});
  const range=edit.slipBounds(c,clock(c),{sourceLimit:2,sourceFrame:1/25});near(range.end,.96);
  const slipped=edit.slip(c,.96,clock(c),{sourceLimit:2,sourceFrame:1/25});near(slipped.in_,1.96);near(clock(slipped).duration,2);
  assert.throws(()=>edit.slip(c,1,clock(c),{sourceLimit:2,sourceFrame:1/25}),/source handles/);
});
test('stills have unbounded virtual duration and slipping is a no-op',()=>{
  const c=base({in_:0,out:3});assert.deepEqual(edit.bounds(c,clock(c),{still:true,sourceLimit:0}),{begin:-Infinity,end:Infinity});
  const part=trim(c,-5,9,{start:0,still:true,sourceLimit:0});assert.ok(part.in_>=0);near(clock(part).duration,14);
  assert.deepEqual(edit.slip(c,100,clock(c),{still:true,sourceLimit:0}),c);
});
test('picture transition trim policy keeps unchanged full edges and rejects relocated/partial transitions',()=>{
  const c=base({transition_in:{type:'wipe_left',duration:1},transition_out:{type:'dip_black',duration:2}});
  assert.deepEqual(trim(c,0,8),c);assert.deepEqual(trim(c,0,5).transition_in,c.transition_in);assert.equal(trim(c,0,5).transition_out,null);
  assert.deepEqual(trim(c,2,8).transition_out,c.transition_out);assert.equal(trim(c,2,8).transition_in,null);
  for(const [a,b] of [[.5,8],[0,7],[-1,8],[0,9]])assert.throws(()=>trim(c,a,b),/picture transition/);
  const noTransitions=trim(c,8,10);assert.equal(noTransitions.transition_in,null);assert.equal(noTransitions.transition_out,null);
});
test('no-op trim/slip preserves metadata exactly and malformed input rejects without mutation',()=>{
  const c=base({vendor:{value:7},markers:[]});assert.deepEqual(trim(c,0,8),c);assert.deepEqual(edit.slip(c,0,clock(c)),c);
  for(const change of [{time_remap:[{t:0,v:0}]},{keyframes:{x:[{t:0,v:NaN}]}},{audio:[]},{markers:[{t:Infinity}]}]){
    const value=base(change);assert.throws(()=>trim(value,1,4));
  }
});

test('actual clip markup hides offscreen markers while keeping their stored edit history',()=>{
  const c=base({markers:[{t:.5,name:'hidden-before-trim'},{t:3,name:'visible-marker'},{t:7,name:'hidden-after-trim'}]}),part=trim(c,2,5),before=clone(part.markers);
  const installed=h.f.install(part),html=h.f.scope.clipEl(installed,h.f.tr,false).innerHTML;
  assert.ok(html.includes('visible-marker'));assert.ok(!html.includes('hidden-before-trim'));assert.ok(!html.includes('hidden-after-trim'));
  assert.deepEqual(installed.markers,before);assert.deepEqual(trim(part,-2,6).markers,c.markers);
});


test('cleared ramp and ducking records cannot revive after trim and later recreation',()=>{
  const c=base({out:35,time_remap:[{t:0,v:1},{t:2,v:3},{t:4,v:1}],keyframes:{'audio.duck_db':duck}});
  const cropped=trim(c,5,20);cropped.time_remap=[];delete cropped.keyframes['audio.duck_db'];
  const again=trim(cropped,2,12);assert.equal(again.source_edit_window.ramp,undefined);assert.equal(again.source_edit_window.duck,undefined);
  again.time_remap=[{t:0,v:1,e:'linear'}];again.keyframes['audio.duck_db']=[{t:0,v:0,e:'linear'}];
  const restored=trim(again,-3,10);assert.ok(restored.time_remap.every(p=>p.v===1));assert.ok(restored.keyframes['audio.duck_db'].every(p=>p.v===0));near(restored.in_,again.in_-3);
});


test('extended exponential fades are silent outside their original fade window',()=>{
  const c=base({audio_transition_in:{type:'exponential',duration:1},audio_transition_out:{type:'exponential',duration:1}}),part=trim(c,-2,10);
  assert.equal(audio.fadeGain(part,12,1),0);assert.equal(audio.fadeGain(part,12,11),0);
  assert.ok(audio.fadeGain(part,12,2)>0);assert.ok(audio.fadeGain(part,12,9)>0);
});


test('equivalent point property order preserves inherited source history across extension',()=>{
  const c=base({in_:10,out:40,time_remap:[{t:0,v:1},{t:2,v:3},{t:4,v:1}],keyframes:{'audio.duck_db':[{t:0,v:0},{t:2,v:-12},{t:4,v:0}]}});
  const cropped=trim(c,5,20);
  cropped.time_remap=cropped.time_remap.map(p=>Object.fromEntries(Object.entries(p).reverse()));
  cropped.keyframes['audio.duck_db']=cropped.keyframes['audio.duck_db'].map(p=>Object.fromEntries(Object.entries(p).reverse()));
  const restored=trim(cropped,-2,10),expected=trim(c,3,15);
  near(restored.in_,16.5);assert.deepEqual(restored.time_remap,expected.time_remap);assert.deepEqual(restored.keyframes,expected.keyframes);
});
