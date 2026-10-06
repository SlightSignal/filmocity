// Emit a real drag's planned request for Python store integration checks.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {fixture,event,node,plain}=require('./gesture-fixture.cjs');
function plan({group=false,legacy=false}={}){
  const f=fixture();f.scope.S.snap=false;
  if(legacy)vm.runInContext(fs.readFileSync(path.join(__dirname,'../../benchmarks/timeline-core/legacy-snap.txt'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'../../benchmarks/timeline-core/legacy-drag.txt'),'utf8'),f.scope);
  const before=plain(f.project);f.scope.S.sel=new Set(group?['b','a']:['a']);
  f.scope.startDrag(event(),f.clips[group?1:0],f.tr,null,node());f.move({clientX:group?180:240});f.up({clientX:group?180:240});
  if(f.requests.length!==1)throw Error('Drag must submit exactly one batch');
  return {before,body:f.body(),optimistic:plain(f.project)};
}
module.exports={plan};
if(require.main===module)process.stdout.write(JSON.stringify(plan(JSON.parse(fs.readFileSync(0,'utf8')||'{}'))));
