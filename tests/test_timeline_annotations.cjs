const test=require('node:test'),assert=require('node:assert/strict');
const annotations=require('../frontend/timeline-annotations.js');
const clone=v=>JSON.parse(JSON.stringify(v));
function hooks(){let n=0;return {id:()=>`new-${++n}`,reservedIds:['new-1']};}
test('insert partitions crossing captions and shifts markers and marked ranges atomically',()=>{
 const seq={markers:[{id:'m0',time:1,duration:1},{id:'m1',time:1,duration:4},{id:'m2',time:2},{id:'m3',time:5}],captions:[{id:'c',start:1,end:4,text:'retain text',style:{keep:true}},{id:'d',start:4,end:6,text:'next'}],in_point:1,out_point:4},before=clone(seq);
 const value=annotations.edit(seq,{type:'insert',at:2,duration:3},hooks());
 assert.deepEqual(value.markers.map(m=>[m.time,m.duration]),[[1,1],[1,7],[5,undefined],[8,undefined]]);
 assert.deepEqual(value.captions.map(c=>[c.id,c.start,c.end,c.text]),[['c',1,2,'retain text'],['new-2',5,7,'retain text'],['d',7,9,'next']]);
 assert.equal(value.out_point,7);assert.equal(value.in_point,undefined);assert.deepEqual(seq,before);
});
test('removal union maps annotations once, keeps notes at the join and retains surviving caption parts',()=>{
 const seq={markers:[{time:2},{time:3},{time:5},{time:7},{time:9},{time:1,duration:10}],captions:[{id:'long',start:0,end:10,text:'same'},{id:'gone',start:2,end:3,text:'gone'}],in_point:2.5,out_point:8};
 const v=annotations.edit(seq,{type:'remove',ranges:[[2,4],[3,5],[7,9]],close:true},hooks());
 assert.deepEqual(v.markers.map(m=>m.time),[2,2,2,4,4,1]);assert.equal(v.markers[5].duration,5);
 assert.deepEqual(v.captions.map(c=>[c.start,c.end,c.text]),[[0,2,'same'],[2,4,'same'],[4,5,'same']]);
 assert.equal(v.in_point,2);assert.equal(v.out_point,4);
});
test('collapsed marked range clears both ends, untouched boundaries and absent fields remain absent',()=>{
 assert.deepEqual(annotations.edit({in_point:2,out_point:4},{type:'remove',ranges:[[2,4]],close:true}),{in_point:null,out_point:null});
 assert.deepEqual(annotations.edit({in_point:0,out_point:2},{type:'insert',at:2,duration:1}),{});
 assert.deepEqual(annotations.edit({},{type:'insert',at:2,duration:1}),{});
 assert.deepEqual(annotations.edit({captions:[{start:0,end:2,text:'keep'}]},{type:'remove',ranges:[[0,1]],close:false}),{});
});
test('invalid annotations and exhausted IDs reject without touching caller data',()=>{
 for(const seq of [{markers:[{time:NaN}]},{captions:[{start:2,end:1}]},{markers:{}},{captions:[{id:'dup',start:0,end:3}]}]){
  const before=structuredClone(seq);assert.throws(()=>annotations.edit(seq,{type:'insert',at:1,duration:1},{id:()=> 'dup'}));assert.deepEqual(seq,before);
 }
});
test('annotation count limits reject before iterating or copying large payloads',()=>{
 const seq={markers:Array(100001).fill({time:0})};assert.throws(()=>annotations.edit(seq,{type:'insert',at:1,duration:1}),/Too many/);
});
test('caption-only edits preserve arbitrary fields and first surviving identity after head removal',()=>{
 const seq={captions:[{id:'x',start:1,end:6,text:'same',vendor:{a:2}}]};const v=annotations.edit(seq,{type:'remove',ranges:[[0,3]],close:true},hooks());
 assert.deepEqual(v,{captions:[{id:'x',start:0,end:3,text:'same',vendor:{a:2}}]});assert.equal(seq.captions[0].start,1);
});
test('overflowing moved annotation endpoints reject before they can become JSON null',()=>{
 const seq={markers:[{time:0,duration:1e308}]},before=clone(seq);
 assert.throws(()=>annotations.edit(seq,{type:'insert',at:1,duration:1e308}),/Moved marker end/);
 assert.deepEqual(seq,before);
});
