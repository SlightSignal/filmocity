const test=require('node:test'),assert=require('node:assert/strict');
const clock=require('../frontend/source-clock.js');
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);
test('forward and reverse map source boundaries through the same integrated ramp clock',()=>{
 const c={in_:2,out:14},model={duration:3,sourceOffset:t=>t*t+t};
 for(const reverse of [false,true])for(const t of [0,.001,.2,.8,1,2,2.999,3]){
  const clip={...c,reverse},s=clock.sourceTime(clip,t,model);close(s,reverse?14-t*t-t:2+t*t+t);close(clock.timelineTime(clip,s,model),t);
 }
 assert.equal(clock.timelineTime(c,1,model),null);assert.equal(clock.sourceTime(c,4,model),null);
});
test('held source position is constant and inverse chooses the first occurrence only',()=>{
 const c={in_:2,out:12,hold:true},model={duration:10};
 assert.equal(clock.sourceTime(c,9,model),2);assert.equal(clock.timelineTime(c,2,model),0);assert.equal(clock.timelineTime(c,7,model),null);
 assert.throws(()=>clock.sourceRanges(c,[[2,3]],model),/held/);
});
test('source removal windows clamp, inverse-map and merge in timeline order for reverse ramps',()=>{
 const c={in_:2,out:14,reverse:true},model={duration:3,sourceOffset:t=>t*t+t};
 const ranges=clock.sourceRanges(c,[[12,20],[0,3],[3,4]],model);
 close(ranges[0][0],0);close(ranges[0][1],1);close(ranges[1][0],(-1+Math.sqrt(41))/2);close(ranges[1][1],3);
});
test('subclip and interpreted source coordinates round trip without changing caller metadata',()=>{
 const media={sub_in:6,interpret_fps:24,fps:30},before=structuredClone(media);
 for(const value of [0,.1,2,10]){const native=clock.nativeTime(media,value);close(native,(value+6)/1.25);close(clock.logicalTime(media,native),value);}
 assert.deepEqual(media,before);close(clock.nativeTime({sub_in:1,interpret_fps:24},2,{nativeRate:30000/1001}),2.4024);
 assert.equal(clock.logicalTime({sub_in:6},2),-4);
});
test('malformed source clocks and overflow fail before seeking or planning',()=>{
 assert.throws(()=>clock.sourceTime({in_:2,out:8},1,{duration:2,sourceOffset:t=>t}),/does not match/);
 assert.throws(()=>clock.nativeTime({sub_in:-2},1));assert.throws(()=>clock.nativeTime({fps:30,interpret_fps:0},1));
 assert.throws(()=>clock.nativeTime({sub_in:1e308},1e308));assert.throws(()=>clock.timelineTime({in_:0,out:1},NaN,{duration:1,sourceOffset:t=>t}));
});
test('native and interpreted fractional aliases share one rational clock over long sources',()=>{
 for(const native of [59.94,'60000/1001'])for(const interpreted of [29.97,'30000/1001']){
  const media={fps:59.94,frame_rate:native,interpret_fps:interpreted,sub_in:6};
  close(clock.interpretationFactor(media),2);close(clock.nativeTime(media,7194),3600);close(clock.logicalTime(media,3600),7194);
 }
});
