const test=require('node:test'),assert=require('node:assert/strict');
const {range,clock,hooks}=require('./helpers/range-engine-fixture.cjs'),audio=require('../frontend/audio-preview.js');
const clone=x=>JSON.parse(JSON.stringify(x)),near=(a,b)=>assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);
const clip=(id='base',extra={})=>({id,start:0,in_:5,out:15,speed:1,...extra});
const seq=(clips=[clip()],extra={})=>({id:'s',fps:30000/1001,tracks:[{id:'V1',kind:'video',index:0,clips}],...extra});
const clips=p=>p.sequence.tracks[0].clips;
const source=(c,t)=>c.hold?c.in_:c.reverse?c.out-clock(c).sourceOffset(t):c.in_+clock(c).sourceOffset(t);
function freeze(value){if(value&&typeof value==='object'){Object.freeze(value);for(const child of Object.values(value))freeze(child);}return value;}
function limited(key,value,fn){const before=range.LIMITS[key];range.LIMITS[key]=value;try{return fn();}finally{range.LIMITS[key]=before;}}

test('range union merges overlap and adjacency once and cumulative time mapping respects half-open boundaries',()=>{
  const ranges=range.normalizeRanges([[4,6],[1,3],[2,4],[10,12]]);assert.deepEqual(ranges,[[1,6],[10,12]]);assert.ok(Object.isFrozen(ranges));
  for(const [a,b] of [[0,0],[1,1],[3,1],[6,1],[7,2],[10,5],[11,5],[12,5],[15,8]])near(range.mapTime(a,ranges),b);
});
test('insertion splits crossing media and moves downstream clips while preserving identity and source windows',()=>{
  const original=seq([clip(),clip('later',{start:12,in_:0,out:2})]),before=clone(original);freeze(original);
  const result=range.planInsert(original,['V1'],4,2,hooks());assert.deepEqual(original,before);assert.equal(result.tracks.length,1);
  const [a,b,c]=clips(result);assert.equal(a.id,'base');assert.equal(b.id,'fragment-1');assert.equal(c.id,'later');
  assert.deepEqual([a.start,clock(a).duration,b.start,clock(b).duration,c.start],[0,4,6,6,14]);near(source(b,0),9);
});
test('insertion at existing half-open boundaries moves only clips beginning there',()=>{
  const original=seq([clip('left',{out:9}),clip('right',{start:4,in_:1,out:3})]);
  const result=range.planInsert(original,['V1'],4,1,hooks({id:()=>{throw Error('No split needed');}}));
  assert.deepEqual(clips(result).map(c=>[c.id,c.start]),[['left',0],['right',5]]);
});
test('lift removes the normalized union without closing time or retaining removed marker ownership',()=>{
  const c=clip('base',{markers:[{t:1,name:'a'},{t:2,name:'removed'},{t:3,name:'removed-too'},{t:4,name:'right-edge'},{t:9,name:'tail'}]});
  const result=range.planRemove(seq([c]),['V1'],[[2,3],[3,4],[6,8]],{},hooks());
  assert.deepEqual(clips(result).map(c=>[c.start,clock(c).duration]),[[0,2],[4,2],[8,2]]);
  assert.deepEqual(clips(result).flatMap(c=>c.markers.map(m=>[m.name,c.start+m.t])),[['a',1],['right-edge',4],['tail',9]]);
  assert.equal(result.removedDuration,4);
});
test('close maps disjoint overlapping selections once across each explicit sync track',()=>{
  const original=seq();original.tracks.push({id:'A1',kind:'audio',clips:[clip('sound',{out:17})]});
  const result=range.planRemove(original,['V1','A1'],[[2,4],[3,5],[8,9]],{close:true},hooks());
  assert.deepEqual(result.sequence.tracks[0].clips.map(c=>[c.start,clock(c).duration]),[[0,2],[2,3],[5,1]]);
  assert.deepEqual(result.sequence.tracks[1].clips.map(c=>[c.start,clock(c).duration]),[[0,2],[2,3],[5,3]]);assert.equal(result.removedDuration,4);
});
test('close moves clips after the union while leaving unsupplied tracks and sequence annotations intact',()=>{
  const original=seq([clip('after',{start:10,out:7})],{markers:[{time:8}],captions:[{start:1,end:4,text:'unchanged'}],in_point:1,out_point:12});
  original.tracks.push({id:'locked',locked:true,clips:[clip('locked-clip')]});const before=clone(original);
  const result=range.planRemove(original,['V1'],[[1,3],[5,6]],{close:true},hooks());near(clips(result)[0].start,7);
  assert.deepEqual(result.sequence.tracks[1],before.tracks[1]);for(const key of ['markers','captions','in_point','out_point'])assert.deepEqual(result.sequence[key],before[key]);
});
test('source windows survive inserts and multiple removals for ramps, reverse, holds and nonunit speed',()=>{
  for(const extra of [{speed:.5},{speed:2},{reverse:true},{hold:true},{time_remap:[{t:0,v:.5},{t:3,v:2}]},{reverse:true,time_remap:[{t:0,v:1,e:'hold'},{t:2,v:2}]}]){
    const c=clip('base',extra),d=clock(c).duration,at=d/3,insert=range.planInsert(seq([c]),['V1'],at,.5,hooks());
    for(const p of clips(insert))for(const fraction of [.1,.5,.9]){const t=clock(p).duration*fraction,old=p.id==='base'?t:at+t;near(source(p,t),source(c,old));}
    const begin=d*.25,end=d*.6,result=range.planRemove(seq([c]),['V1'],[[begin,end]],{close:true},hooks());
    const right=clips(result)[1];near(clock(right).duration,d-end);near(source(right,.1*clock(right).duration),source(c,end+.1*clock(right).duration));
  }
});
test('curves and inherited fades retain original clocks through range edits',()=>{
  const c=clip('base',{audio:{fade_in:8,fade_out:8},keyframes:{'transform.x':[{t:0,v:0,e:'bezier',o:[.2,4]},{t:10,v:10,i:[.4,-2]}],'audio.duck_db':[{t:0,v:0},{t:2,v:-12,e:'hold'},{t:8,v:-12},{t:10,v:0}]}});
  const result=range.planRemove(seq([c]),['V1'],[[2,4],[6,7]],{close:true},hooks()),offsets=[0,4,7];
  clips(result).forEach((p,index)=>{const d=clock(p).duration;for(const fraction of [.1,.5,.9]){const t=d*fraction;near(audio.fadeGain(p,d,t),audio.fadeGain(c,10,offsets[index]+t));near(audio.duckDb(p,t),audio.duckDb(c,offsets[index]+t));}
    near(p.keyframes['transform.x'][0].t,-offsets[index]);assert.deepEqual(p.keyframes['transform.x'][0].o,[.2,4]);});
});
test('sequential overwrite clips trim prior planned placements deterministically',()=>{
  const original=seq(),result=range.planOverwrite(original,[{trackId:'V1',clip:clip('one',{start:2,in_:0,out:2})},{trackId:'V1',clip:clip('two',{start:3,in_:1,out:4})}],hooks());
  const values=clips(result).map(c=>[c.id,c.start,clock(c).duration]).sort((a,b)=>a[1]-b[1]);
  assert.deepEqual(values,[['base',0,2],['one',2,1],['two',3,3],['fragment-1',6,4]]);assert.deepEqual(original,seq());
});
test('all incoming IDs are reserved before earlier overwrite splits allocate fragments',()=>{
  let n=0;const generated=['future','fresh-fragment'];
  const result=range.planOverwrite(seq(),[{trackId:'V1',clip:clip('first',{start:2,in_:0,out:1})},{trackId:'V1',clip:clip('future',{start:15,in_:0,out:1})}],hooks({id:()=>generated[n++]}));
  assert.equal(new Set(clips(result).map(c=>c.id)).size,clips(result).length);assert.ok(clips(result).some(c=>c.id==='fresh-fragment'));assert.equal(n,2);
});
test('reserved IDs from other sequences or annotations are skipped and detached IDs require explicit opt-in',()=>{
  let n=0;const result=range.planInsert(seq(),['V1'],2,1,hooks({reservedIds:['fragment-1','fragment-2'],id:()=>`fragment-${++n}`}));assert.equal(clips(result)[1].id,'fragment-3');
  const empty=seq([]),incoming={trackId:'V1',clip:clip('detached')};assert.throws(()=>range.planOverwrite(empty,[incoming],hooks({reservedIds:['detached']})),/already in use/);
  assert.equal(clips(range.planOverwrite(empty,[incoming],hooks({reservedIds:['detached'],reuseIds:['detached']})))[0].id,'detached');
  assert.throws(()=>range.planOverwrite(seq(),[{trackId:'V1',clip:clip()}],hooks({reuseIds:['base']})),/already in use/);
});
test('duplicate placements, unavailable or locked scopes reject before any input changes',()=>{
  const original=seq();original.tracks.push({id:'L',locked:true,clips:[]});const before=clone(original);
  for(const call of [()=>range.planOverwrite(original,[{trackId:'V1',clip:clip('same')},{trackId:'V1',clip:clip('same')}],hooks()),()=>range.planInsert(original,['L'],1,1,hooks()),()=>range.planRemove(original,['missing'],[[1,2]],{},hooks())])assert.throws(call);
  assert.deepEqual(original,before);
});
test('a late transition failure discards an entire multi-track or sequential placement plan',()=>{
  const original=seq();original.tracks.push({id:'V2',clips:[clip('fade',{transition_in:{type:'wipe_left',duration:2}})]});const before=clone(original);
  assert.throws(()=>range.planRemove(original,['V1','V2'],[[1,3]],{},hooks()),/picture transition/);assert.deepEqual(original,before);
  assert.throws(()=>range.planOverwrite(original,[{trackId:'V1',clip:clip('ok',{start:2,in_:0,out:1})},{trackId:'V2',clip:clip('bad',{start:1,in_:0,out:1})}],hooks()),/picture transition/);assert.deepEqual(original,before);
});
test('fully removed clips do not need to slice their picture transitions',()=>{
  const result=range.planRemove(seq([clip('fade',{transition_in:{type:'wipe',duration:2}})]),['V1'],[[0,10]],{},hooks());assert.deepEqual(clips(result),[]);
});
test('frame fractions and genuine subframe gaps retain exact stored timing',()=>{
  const f=1001/30000,c=clip('base',{start:30*f,in_:0,out:90*f}),result=range.planRemove(seq([c]),['V1'],[[40*f,50*f]],{close:true},hooks());
  near(clips(result)[1].start,40*f);near(clock(clips(result)[0]).duration,10*f);
  assert.equal(range.normalizeRanges([[1,2],[2+1e-7,3]]).length,2);
  const tiny=clip('tiny',{start:20,in_:0,out:1e-10});assert.deepEqual(clips(range.planRemove(seq([tiny]),['V1'],[[0,1]],{},hooks())),[tiny]);
});
test('normal ranges and no-op scope never rewrite unrelated clip metadata',()=>{
  const c=clip('base',{vendor:{x:7},audio:{fade_in:2}}),original=seq([c]);
  const result=range.planRemove(original,['V1'],[[20,21]],{},hooks());assert.deepEqual(result.sequence,original);assert.deepEqual(result.tracks,[]);
  result.sequence.tracks[0].clips[0].vendor.x=99;assert.equal(original.tracks[0].clips[0].vendor.x,7);
});
test('invalid ranges and nonfinite JSON are refused without silent coercion',()=>{
  for(const intervals of [[[1,1]],[[-1,2]],[[2,1]],[[0,Infinity]],[[0,1,2]],null])assert.throws(()=>range.normalizeRanges(intervals));
  assert.throws(()=>range.planInsert(seq([clip('bad',{vendor:NaN})]),['V1'],1,1,hooks()),/finite/);
});
test('output, work, payload and placement bounds fail atomically',()=>{
  const original=seq(),before=clone(original);
  limited('clips',2,()=>assert.throws(()=>range.planRemove(original,['V1'],[[1,2],[3,4]],{},hooks()),/too many clips/));
  limited('visits',1,()=>assert.throws(()=>range.planInsert(original,['V1'],2,1,hooks()),/too large/));
  limited('bytes',200,()=>assert.throws(()=>range.planRemove(seq([clip('large',{vendor:'x'.repeat(300)})]),['V1'],[[1,2]],{},hooks()),/too large/));
  limited('placements',1,()=>assert.throws(()=>range.planOverwrite(original,[{trackId:'V1',clip:clip('a')},{trackId:'V1',clip:clip('b')}],hooks()),/at most/));
  assert.deepEqual(original,before);
});
test('ID factory collisions are bounded and never overwrite an existing clip',()=>{
  let count=0;assert.throws(()=>range.planInsert(seq(),['V1'],1,1,hooks({id:()=>{count++;return 'base';}})),/unique clip ID/);assert.equal(count,256);
});
test('large disjoint range closures do bounded work and preserve the final cumulative position',()=>{
  const intervals=Array.from({length:1000},(_,i)=>[i*2+1,i*2+1.5]),original=seq([clip('long',{in_:0,out:2001})]);
  const result=range.planRemove(original,['V1'],intervals,{close:true},hooks());assert.equal(clips(result).length,1001);near(result.removedDuration,500);near(clips(result).at(-1).start+clock(clips(result).at(-1)).duration,1501);
});
