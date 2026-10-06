const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const base=require('./gesture-fixture.cjs');
const source=fs.readFileSync(path.join(__dirname,'../../frontend/app.js'),'utf8');
function section(a,b){const start=source.indexOf(a),end=source.indexOf(b,start);if(start<0||end<0)throw Error('Missing '+a);return source.slice(start,end);}
function fixture(){
  const f=base.fixture(),s=f.scope;
  s.window.FilmocityClipSplit=require('../../frontend/clip-split.js');s.timing=require('../../frontend/timeline-time.js');
  s.window.FilmocityAudioPreview=require('../../frontend/audio-preview.js');
  let id=0;s.uid=()=>`cut-${++id}`;
  s.S.target={video:'v1',audio:'a1'};s.S.tlopt={};s.S.agentHot={};s.S.tool='razor';
  s.mediaRate=()=>s.S.seq.fps;s.xToT=x=>x/s.S.pps;
  s.document.createElement=()=>Object.assign(base.node(),{innerHTML:''});
  vm.runInContext(section('function remapSegments(','const seqDurOf')+
    section('function razorAt(t,','function selectedClips(')+
    section('function clipEl(','function refreshSel(')+
    section('function addEditAllTracks(','function defaultTransitionsToSelection(')+

    '\nglobalThis.cutClock={duration:clipDur,sourceOffset,speedAt}; globalThis.evalCurve=kfVal;' ,s);
  f.install=(clip,fps=30)=>{s.S.seq.fps=fps;f.tr.clips=[base.plain(clip)];return f.tr.clips[0];};
  f.plan=(clip,at)=>s.window.FilmocityClipSplit.split(clip,at,clip.id+'-'+s.uid(),{duration:s.cutClock.duration(clip),sourceOffset:t=>s.cutClock.sourceOffset(clip,t),speedAt:t=>s.cutClock.speedAt(clip,t)});
  return f;
}
module.exports={fixture,...Object.fromEntries(Object.entries(base).filter(([k])=>k!=='fixture'))};
if(require.main===module){const input=JSON.parse(fs.readFileSync(0,'utf8')),f=fixture();if(input.clip){f.install(input.clip,input.fps||30);process.stdout.write(JSON.stringify(f.scope.razorAt(input.time,'v1')));}else process.stdout.write(JSON.stringify(input.map(c=>f.plan(c.clip,c.at))));}
