const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {plan}=require('./helpers/range-gesture-plan.cjs');
const base=require('./helpers/gesture-fixture.cjs');
test('Ctrl-drag inserts into crossing advanced material and commits source, captions, IO and markers together',()=>{
 const result=plan(),track=result.optimistic.sequences[0].tracks[0],a=track.clips.find(c=>c.id==='a'),left=track.clips.find(c=>c.id==='b'),right=track.clips.find(c=>c.note===undefined&&c.id.startsWith('range-'));
 assert.ok(result.body);assert.equal(a.start,4);assert.equal(left.start,3);assert.equal(left.in_,7);assert.equal(left.out,8);
 assert.equal(right.start,7);assert.equal(right.in_,2);assert.equal(right.out,7);assert.equal(right.keyframes['transform.x'][0].t,-1);
 const seq=result.optimistic.sequences[0];assert.equal(seq.markers[0].time,8);assert.deepEqual(seq.captions.map(c=>[c.start,c.end]),[[3.5,4],[7,10]]);assert.equal(seq.out_point,11);
 assert.ok(result.body.ops.some(o=>o.path.endsWith('/clips')));assert.ok(result.body.ops.some(o=>o.path.endsWith('/captions')));assert.equal(result.body.tool,'insert_drag');
});
test('unsupported crossing transition and canceled Ctrl-drag restore full original state with no save',()=>{
 for(const options of [{mode:'invalid'},{cancel:true}]){const result=plan(options);assert.equal(result.body,null);assert.deepEqual(result.optimistic,result.before);}
});
test('native row video-only drop forwards marked range in one placement and stale rows are inert',()=>{
 const {fixture,dragEvent}=require('./helpers/placement-fixture.cjs'),f=fixture();
 f.scope.S.src=f.project.media.m;f.scope.S.srcIn=2;f.scope.S.srcOut=3;
 const ui=f.dragUI(),row=ui.row(),ev=dragEvent({clientX:10});ui.video.ondragstart(ev);row.ondrop(ev);
 assert.equal(f.requests.length,1);const placed=f.tr.clips.find(c=>f.scope.S.sel.has(c.id));
 assert.equal(placed.in_,2);assert.equal(placed.out,3);assert.equal(placed.audio.linked,false);
 const next=dragEvent({clientX:15});ui.video.ondragstart(next);f.scope.S.seq=f.project.sequences[1];row.ondrop(next);assert.equal(f.requests.length,1);
});
