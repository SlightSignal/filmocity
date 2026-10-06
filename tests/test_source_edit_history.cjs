const test=require('node:test'),assert=require('node:assert/strict');
const {fixture,plain}=require('./helpers/keyboard-trim-fixture.cjs');
const split=require('../frontend/clip-split.js');
function setup(){
 const f=fixture(),c={id:'a',media_id:'m',start:0,in_:10,out:40,time_remap:[{t:0,v:1},{t:2,v:3},{t:4,v:1}],keyframes:{'audio.duck_db':[{t:0,v:0},{t:1,v:-12},{t:3,v:0}]}};
 const trimmed=split.trim(c,5,20,{duration:f.scope.cutClock.duration(c)},{sourceLimit:100});f.edit(trimmed);return f;
}
const patch=(f,c)=>f.scope.applyLocal([{op:'set_clip',sequence:'s1',track:'v1',clip:{id:'a',...c}}]);
test('ordinary remove/recreate ramp patches cannot revive obsolete hidden knots',()=>{
 const f=setup(),c=f.tr.clips[0],visible=plain(c.time_remap),duck=plain(c.source_edit_window.duck);
 patch(f,{time_remap:[]});assert.equal(c.source_edit_window.ramp,undefined);assert.deepEqual(plain(c.source_edit_window.duck),duck);
 patch(f,{time_remap:visible});const restored=split.trim(c,-3,f.scope.cutClock.duration(c),{duration:f.scope.cutClock.duration(c)},{sourceLimit:100});
 assert.equal(restored.in_,16);assert.deepEqual(plain(restored.time_remap),[{t:0,v:1,e:'linear'},{t:3,v:1,e:'linear'}]);
});
test('duck removal invalidates only duck history even when recreated before the next trim',()=>{
 const f=setup(),c=f.tr.clips[0],visible=plain(c.keyframes),ramp=plain(c.source_edit_window.ramp);
 patch(f,{keyframes:{}});assert.equal(c.source_edit_window.duck,undefined);assert.deepEqual(plain(c.source_edit_window.ramp),ramp);
 patch(f,{keyframes:visible});const restored=split.trim(c,-4,f.scope.cutClock.duration(c),{duration:f.scope.cutClock.duration(c)},{sourceLimit:100});
 assert.ok(restored.keyframes['audio.duck_db'].every(p=>p.v===0));
});
test('unrelated/manual equal curve patches preserve history regardless of point key ordering',()=>{
 const f=setup(),c=f.tr.clips[0],before=plain(c.source_edit_window);
 patch(f,{keyframes:{...plain(c.keyframes),'transform.x':[{t:0,v:2}]}});
 patch(f,{time_remap:c.time_remap.map(p=>({e:p.e,v:p.v,t:p.t}))});assert.deepEqual(plain(c.source_edit_window),before);
 const restored=split.trim(c,-2,f.scope.cutClock.duration(c),{duration:f.scope.cutClock.duration(c)},{sourceLimit:100});assert.equal(restored.in_,16.5);
});
test('complete source plans retain explicit updated history while optimistic merge leaves request payload unchanged',()=>{
 const f=setup(),c=f.tr.clips[0],next=split.trim(c,1,10,{duration:f.scope.cutClock.duration(c)},{sourceLimit:100}),before=plain(next);
 patch(f,next);assert.deepEqual(plain(c.source_edit_window),before.source_edit_window);assert.deepEqual(next,before);
});
