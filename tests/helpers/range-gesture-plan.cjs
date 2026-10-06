// Actual Ctrl-drag callbacks/save queue; pointers and HTTP replies are controlled.
const fs=require('node:fs');const base=require('./gesture-fixture.cjs');
function plan({mode='insert',cancel=false}={}){
 const f=base.fixture();let next=0;f.scope.uid=()=>`range-${++next}`;f.scope.S.snap=false;
 Object.assign(f.clips[1],{start:3,in_:2,out:8,reverse:true,audio:{fade_in:4,fade_out:3},keyframes:{'transform.x':[{t:0,v:0,e:'bezier',o:[.25,2]},{t:6,v:10,i:[.25,-1]}]},markers:[{t:.5,name:'left'},{t:2,name:'right'}]});f.clips[2].start=9;
 f.seq.captions=[{id:'cap',start:3.5,end:7,text:'retain words'}];f.seq.markers=[{id:'marker',time:5}];f.seq.in_point=3;f.seq.out_point=8;
 if(mode==='invalid')f.clips[1].transition_in={type:'dissolve',duration:2};
 const before=base.plain(f.project);f.start();f.move({clientX:240,ctrlKey:true});if(cancel)f.escape();else f.up({ctrlKey:true});
 return {before,body:f.requests.length?f.body():null,optimistic:base.plain(f.project),messages:f.messages};
}
module.exports={plan};if(require.main===module)process.stdout.write(JSON.stringify(plan(JSON.parse(fs.readFileSync(0,'utf8')||'{}'))));
