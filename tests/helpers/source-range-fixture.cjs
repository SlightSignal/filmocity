// Actual source-edit planner using production source clocks; no browser DSP emulation.
const fs=require('node:fs'),{fixture}=require('./cut-fixture.cjs'),edit=require('../../frontend/clip-split.js');
function create(){const f=fixture(),c=f.scope.cutClock;return {f,edit,clock:clip=>({duration:c.duration(clip),sourceOffset:t=>c.sourceOffset(clip,t),speedAt:t=>c.speedAt(clip,t)})};}
function plan(value){const h=create();let clip=value.clip;for(const step of value.steps||[value]){
  const method=step.method||'trim';
  if(method==='trim')clip=h.edit.trim(clip,step.begin,step.end,h.clock(clip),step.options||{});
  else if(method==='slip')clip=h.edit.slip(clip,step.delta,h.clock(clip),step.options||{});
  else if(method==='split')clip=h.edit.split(clip,step.at,clip.id+'-split',h.clock(clip))[step.part??1];
  else throw Error('Unknown source edit method');
}return clip;}
module.exports={create,plan};
if(require.main===module){const input=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(Array.isArray(input)?input.map(plan):plan(input)));}
