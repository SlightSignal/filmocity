// Production source clock and pure range engine; no DOM or network substitutes.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const range=require('../../frontend/timeline-range.js');
const app=fs.readFileSync(path.join(__dirname,'../../frontend/app.js'),'utf8');
const begin=app.indexOf('function remapSegments('),end=app.indexOf('function bezierY(',begin);
if(begin<0||end<0)throw Error('Missing production source-clock section');
const env={};vm.createContext(env);vm.runInContext(app.slice(begin,end)+'\nthis.clockFunctions={clipDur,sourceOffset,speedAt};',env);
const clock=clip=>({duration:env.clockFunctions.clipDur(clip),sourceOffset:t=>env.clockFunctions.sourceOffset(clip,t),speedAt:t=>env.clockFunctions.speedAt(clip,t)});
function hooks(options={}){let id=0;return {clock,id:()=>`fragment-${++id}`,reservedIds:options.reservedIds||[],reuseIds:options.reuseIds||[],...options};}
function plan(value){const h=hooks(value);if(value.method==='insert')return range.planInsert(value.sequence,value.trackIds,value.at,value.duration,h);
  if(value.method==='overwrite')return range.planOverwrite(value.sequence,value.placements,h);
  return range.planRemove(value.sequence,value.trackIds,value.intervals,{close:!!value.close},h);}
module.exports={range,clock,hooks,plan};
if(require.main===module){const value=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(Array.isArray(value)?value.map(plan):plan(value)));}
