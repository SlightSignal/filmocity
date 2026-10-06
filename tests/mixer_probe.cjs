// Capture actual production node settings with a controlled context. This does
// not execute a browser; Python applies independent Web Audio EQ equations.
const fs=require('node:fs');
const {fixture,nestedFixture}=require('./audio_preview_fixture.cjs');
function capture(options={}){
  const f=(options.nested?nestedFixture:fixture)(options.legacy?{source:fs.readFileSync(options.legacy,'utf8')}:{});
  f.clip.audio_fx=options.clip||{};f.bus.audio_fx=options.track||{};
  if(options.nested){f.child.master={audio_fx:options.childMaster||{}};f.nest.audio_fx=options.parentClip||{};f.parentBus.audio_fx=options.parentTrack||{};}
  f.S.seq.master={gain_db:options.gain||0,audio_fx:options.master||{}};f.draw();
  const leaf=Object.values(f.AG.nodes).find(n=>!n.element._mixOwner);
  const strips=[leaf,leaf.bus];if(options.nested){const {master,parent}=f.graphs();strips.push(master,parent,parent.bus);}
  strips.push(f.AG.rootStrip||{gain:f.AG.master});
  return {layers:strips.map(strip=>({eq:['low','mid','high'].filter(k=>strip[k]).map(k=>({type:strip[k].type,frequency:strip[k].frequency.value,q:strip[k].Q.value,gain:strip[k].gain.value})),gain:strip.gain.gain.value})),
    nodes:f.nodes.length,oscillators:f.nodes.filter(n=>n.kind==='oscillator').length,convolvers:f.nodes.filter(n=>n.kind==='convolver').length,
    requested_buffer_bytes:f.buffers.reduce((s,b)=>s+b.channels*b.length*4,0),scope:'Controlled graph/parameter capture; no browser DSP or native resource measurements.'};
}
module.exports={capture};
if(require.main===module)console.log(JSON.stringify(capture(JSON.parse(process.argv[2]||'{}'))));
