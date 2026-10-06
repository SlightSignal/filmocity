// Controlled production-node lifetime checks, not native DSP/CPU measurements.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fx=require('../frontend/audio-time-fx.js');
const {fixture,nestedFixture}=require('./audio_preview_fixture.cjs');
const effects=()=>['delay','reverb','tremolo','chorus'].map(type=>({type}));
const graph=f=>Object.values(f.AG.nodes).find(n=>!n.element._mixOwner);
const oscillators=f=>f.nodes.filter(n=>n.kind==='oscillator');
const alive=f=>oscillators(f).filter(n=>n.started&&!n.stops);
const empty=ctx=>assert.deepEqual(fx.impulseStats(ctx),{buffers:0,leases:0,requestedBytes:0});
function direct(){const f=fixture(),ctx=f.env.audioCtx(),input=ctx.createGain(),output=ctx.createGain();
  return {...f,ctx,input,output,owner:fx.create(ctx,input,output)};}

test('ordinary, disabled and zero-wet clips allocate no time-effect resources',()=>{
  for(const stack of [undefined,[],effects().map(e=>({...e,enabled:false})),[{type:'delay',params:{mix:0}},{type:'reverb',params:{mix:0}},{type:'tremolo',params:{depth:0}}]]){
    const f=fixture();f.clip.afx_stack=stack;f.draw();const n=graph(f);
    assert.deepEqual(Object.keys(n.timeFx.units),[]);assert.equal(n.makeup.edges[0].to,n.pan);
    assert.equal(f.nodes.filter(n=>['oscillator','delay','convolver'].includes(n.kind)).length,0);assert.equal(f.buffers.length,0);empty(f.AG.ctx);
  }
});

test('each enabled effect owns only its required nodes',()=>{
  for(const [type,counts] of Object.entries({delay:[3,0,0],reverb:[2,0,1],tremolo:[3,1,0],chorus:[4,1,0],flanger:[4,1,0]})){
    const f=fixture();f.draw();const before=f.nodes.length;f.clip.afx_stack=[{type}];f.draw();
    assert.equal(f.nodes.length-before,counts[0],type);assert.equal(alive(f).length,counts[1]);assert.equal(f.buffers.length,counts[2]);
    assert.equal(Object.keys(graph(f).timeFx.units).length,1);f.env.resetPreviewVoices();empty(f.AG.ctx);assert.equal(alive(f).length,0);
  }
});

test('parameter redraws reuse nodes and do not duplicate connections',()=>{
  const f=fixture();f.clip.afx_stack=effects();f.draw();const n=graph(f),count=f.nodes.length;
  for(let i=0;i<200;i++){f.clip.afx_stack[0].params={delay_ms:100+i,feedback:.2,mix:.3};f.draw();}
  assert.equal(f.nodes.length,count);assert.equal(f.buffers.length,1);assert.equal(n.makeup.edges.length,1);
  const u=n.timeFx.units;assert.equal(n.makeup.edges[0].to,u.tremolo.gain);assert.equal(u.tremolo.gain.edges.length,4);
  assert.equal(u.delay.delay.delayTime.value,.299);assert.equal(u.delay.feedback.gain.value,.2);
  assert.equal(new Set(u.tremolo.gain.edges.map(e=>e.to)).size,4);
});

test('chorus and flanger reuse their shared slot and disable releases its oscillator',()=>{
  const f=fixture();f.clip.afx_stack=[{type:'chorus'}];f.draw();const unit=graph(f).timeFx.units.modulation,count=f.nodes.length;
  f.clip.afx_stack=[{type:'flanger'}];f.draw();assert.equal(graph(f).timeFx.units.modulation,unit);assert.equal(f.nodes.length,count);
  assert.equal(unit.delay.delayTime.value,.003);assert.equal(unit.oscillator.frequency.value,.3);
  f.clip.afx_stack=[];f.draw();assert.equal(unit.oscillator.stops,1);assert.equal(unit.wet.edges.length,0);
});

test('overlapping reverbs share a context buffer until the last owner leaves',()=>{
  const f=fixture();f.clip.afx_stack=[{type:'reverb'}];f.bus.clips=[{...f.clip,id:'second'}];f.draw();
  const a=Object.entries(f.AG.nodes).find(([key])=>JSON.parse(key).at(-1)==='c')[1];
  const b=Object.values(f.AG.nodes).find(n=>n!==a),ca=a.timeFx.units.reverb.convolver,cb=b.timeFx.units.reverb.convolver;
  assert.equal(ca.buffer,cb.buffer);assert.equal(f.buffers.length,1);
  assert.deepEqual(fx.impulseStats(f.AG.ctx),{buffers:1,leases:2,requestedBytes:614400});
  f.clip.afx_stack=[];f.draw();assert.equal(ca.buffer,null);assert.equal(fx.impulseStats(f.AG.ctx).leases,1);assert.ok(cb.buffer);
  f.bus.clips=[];f.draw();assert.equal(cb.buffer,null);empty(f.AG.ctx);
});

test('impulse ownership is isolated by context and new leases reproduce the same room',()=>{
  const a=direct(),b=direct();a.owner.update([{type:'reverb'}]);b.owner.update([{type:'reverb'}]);
  const first=a.owner.units.reverb.convolver.buffer,other=b.owner.units.reverb.convolver.buffer;
  assert.notEqual(first,other);assert.deepEqual(Array.from(first.getChannelData(0)),Array.from(other.getChannelData(0)));
  assert.notDeepEqual(Array.from(first.getChannelData(0)),Array.from(first.getChannelData(1)));
  a.owner.clear();empty(a.ctx);assert.equal(fx.impulseStats(b.ctx).leases,1);
  a.owner.update([{type:'reverb'}]);const next=a.owner.units.reverb.convolver.buffer;
  assert.notEqual(next,first);assert.deepEqual(Array.from(next.getChannelData(1)),Array.from(first.getChannelData(1)));
  a.owner.dispose();b.owner.dispose();empty(a.ctx);empty(b.ctx);
});

test('pause releases wet resources and resume retains the captured media source',()=>{
  const f=fixture();f.clip.afx_stack=effects();f.draw();const n=graph(f),units={...n.timeFx.units},src=n.src;
  f.S.playing=false;f.draw();assert.deepEqual(Object.keys(n.timeFx.units),[]);empty(f.AG.ctx);assert.equal(alive(f).length,0);
  assert.equal(units.reverb.convolver.buffer,null);assert.equal(n.element.paused,true);
  f.S.playing=true;f.draw();assert.equal(graph(f).src,src);assert.equal(f.AG.ctx.sources.size,1);assert.equal(alive(f).length,2);
  assert.notEqual(n.timeFx.units.tremolo.oscillator,units.tremolo.oscillator);assert.equal(units.tremolo.oscillator.stops,1);
});

test('mute, solo exclusion, unlink and hold release wet resources from retained voices',()=>{
  for(const change of [f=>f.bus.muted=true,f=>f.track.muted=true,f=>f.clip.audio.linked=false,f=>f.clip.hold=true,
    f=>f.seq.tracks.push({id:'A2',kind:'audio',index:2,solo:true,clips:[]})]){
    const f=fixture();f.clip.afx_stack=effects();f.draw();const n=graph(f);change(f);f.draw();
    assert.equal(n.out.gain.value,0);assert.equal(alive(f).length,0);empty(f.AG.ctx);assert.deepEqual(Object.keys(n.timeFx.units),[]);
  }
});

test('a hundred occurrence seeks retain only one wet graph and bounded idle voices',()=>{
  const f=fixture();f.clip.afx_stack=effects();let first;
  for(let i=0;i<100;i++){f.clip.id='seek-'+i;f.advance(1);f.draw();first ||= graph(f);
    assert.ok(Object.keys(f.pool).length<=17);assert.equal(alive(f).length,2);assert.equal(fx.impulseStats(f.AG.ctx).leases,1);
    assert.equal(Object.values(f.AG.nodes).filter(n=>Object.keys(n.timeFx.units).length).length,1);}
  assert.equal(first.element.removed,true);assert.equal(oscillators(f).filter(n=>n.stops===1).length,198);
  f.env.resetPreviewVoices();assert.equal(alive(f).length,0);empty(f.AG.ctx);assert.equal(Object.keys(f.pool).length,0);
});

test('returning to an idle voice makes fresh effects without recapturing its element',()=>{
  const f=fixture();f.clip.afx_stack=effects();f.draw();const n=graph(f),osc=n.timeFx.units.tremolo.oscillator;
  f.draw(2);assert.equal(Object.keys(f.pool).length,1);assert.equal(alive(f).length,0);empty(f.AG.ctx);
  f.draw(.2);assert.equal(graph(f),n);assert.equal(f.AG.ctx.sources.size,1);assert.notEqual(n.timeFx.units.tremolo.oscillator,osc);assert.equal(osc.stops,1);
});

test('silent reference rendering cannot allocate wet audio',()=>{
  const f=fixture();f.clip.afx_stack=effects();f.draw(.2,['compare']);assert.equal(f.buffers.length,0);assert.equal(alive(f).length,0);assert.equal(f.nodes.length,0);
});

test('nested clips share the impulse while retaining independently owned effects',()=>{
  const f=nestedFixture();f.clip.afx_stack=effects();f.nest.afx_stack=effects();f.draw();const {leaf,parent}=f.graphs();
  assert.notEqual(leaf.timeFx.units.reverb,parent.timeFx.units.reverb);assert.equal(f.buffers.length,1);assert.equal(fx.impulseStats(f.AG.ctx).leases,2);
  f.parentBus.muted=true;f.draw();assert.equal(alive(f).length,0);empty(f.AG.ctx);assert.equal(Object.keys(f.AG.groups).length,0);
  f.parentBus.muted=false;f.draw();assert.equal(f.AG.ctx.sources.size,1);assert.equal(fx.impulseStats(f.AG.ctx).leases,2);
  f.env.resetPreviewVoices();assert.equal(alive(f).length,0);empty(f.AG.ctx);
});

test('failed allocation after acquiring an impulse releases staged nodes and lease',()=>{
  const f=direct(),before=f.nodes.length;f.ctx.createGain=()=>{throw new Error('wet gain allocation failed');};
  assert.equal(f.owner.update([{type:'reverb'}]),false);assert.match(f.owner.error,/wet gain/);empty(f.ctx);
  assert.equal(f.nodes.length,before+1);const conv=f.nodes.at(-1);assert.equal(conv.kind,'convolver');assert.equal(conv.buffer,null);assert.equal(conv.disconnects,1);
  assert.equal(f.input.edges.length,1);assert.equal(f.input.edges[0].to,f.output);
});

test('adding a failed effect releases already running effects and latches identical retries',()=>{
  const f=fixture();f.clip.afx_stack=[{type:'tremolo'}];f.draw();const n=graph(f),osc=n.timeFx.units.tremolo.oscillator;
  let attempts=0;const original=f.AG.ctx.createBuffer;f.AG.ctx.createBuffer=()=>{attempts++;throw new Error('buffer allocation failed');};
  f.clip.afx_stack.push({type:'reverb'});f.draw();f.env.updateAudioPreviewStatus(false);
  assert.equal(osc.stops,1);assert.equal(alive(f).length,0);assert.equal(n.out.gain.value,0);assert.equal(n.element.muted,true);
  assert.match(f.fields['#audioPreviewStatus'].textContent,/unavailable/);assert.equal(attempts,1);
  const count=f.nodes.length;for(let i=0;i<200;i++)f.draw();assert.equal(f.nodes.length,count);assert.equal(attempts,1);
  f.AG.ctx.createBuffer=original;f.clip.afx_stack[1].params={mix:.4};f.draw();assert.equal(n.element.muted,false);assert.equal(n.element._audioError,false);
  assert.equal(alive(f).length,1);assert.equal(fx.impulseStats(f.AG.ctx).leases,1);
});

test('pause resets a failed allocation so resume can recover without changing saved settings',()=>{
  const f=fixture();f.draw();const create=f.AG.ctx.createConvolver;f.AG.ctx.createConvolver=()=>{throw new Error('temporary failure');};
  f.clip.afx_stack=[{type:'reverb'}];f.draw();assert.equal(graph(f).element.muted,true);
  f.AG.ctx.createConvolver=create;f.S.playing=false;f.draw();f.S.playing=true;f.draw();
  assert.equal(graph(f).element.muted,false);assert.equal(f.AG.ctx.sources.size,1);assert.equal(fx.impulseStats(f.AG.ctx).leases,1);
});

test('a failed bus allocation also retires time effects on the silenced clip',()=>{
  const f=fixture();f.env.audioCtx();f.AG.ctx.createAnalyser=()=>{throw new Error('bus failed');};f.clip.afx_stack=effects();f.draw();
  assert.equal(graph(f).out.gain.value,0);assert.equal(graph(f).element.muted,true);assert.equal(alive(f).length,0);empty(f.AG.ctx);
});

test('a failed nested master retires effects already configured on its parent',()=>{
  const f=nestedFixture();f.nest.afx_stack=effects();f.env.audioCtx();const original=f.AG.ctx.createDynamicsCompressor;let calls=0;
  f.AG.ctx.createDynamicsCompressor=function(){if(++calls===5)throw new Error('nested master failed');return original.call(this);};f.draw();
  const parent=Object.values(f.AG.nodes).find(n=>n.element._mixOwner);assert.ok(parent);assert.equal(parent.out.gain.value,0);
  assert.equal(alive(f).length,0);empty(f.AG.ctx);assert.equal(Object.values(f.pool)[0].muted,true);
});

test('repeated enable/remove edits keep at most four units and dispose exactly once',()=>{
  const f=direct();for(let i=0;i<100;i++){assert.equal(f.owner.update(effects()),true);assert.equal(Object.keys(f.owner.units).length,4);
    f.owner.clear();f.owner.clear();assert.equal(alive(f).length,0);empty(f.ctx);assert.equal(f.input.edges.length,1);}
  f.owner.update(effects());f.owner.dispose();f.owner.dispose();assert.equal(f.owner.update(effects()),false);
  assert.equal(f.input.edges.length,0);assert.ok(oscillators(f).every(n=>n.stops===1));empty(f.ctx);
});

test('catalog reverb and legacy alias select one slot with their compatible defaults',()=>{
  assert.deepEqual(fx.specifications([{type:'reverb'}]),{reverb:{mix:.3}});
  assert.deepEqual(fx.specifications([{type:'studio_reverb'}]),{reverb:{mix:.25}});
  const f=direct();f.owner.update([{type:'reverb'}]);const conv=f.owner.units.reverb.convolver;
  f.owner.update([{type:'studio_reverb',params:{mix:.7}}]);assert.equal(f.owner.units.reverb.convolver,conv);assert.equal(f.owner.units.reverb.wet.gain.value,.7);
});

test('invalid numeric parameters remain finite and disabled entries do not override enabled ones',()=>{
  assert.deepEqual(fx.specifications([null,{type:'delay',params:{delay_ms:Infinity,feedback:-4,mix:7}},{type:'delay',enabled:false},
    {type:'tremolo',params:{frequency:NaN,depth:null}},{type:'reverb',params:{mix:NaN}}]),
    {delay:{delay:.3,feedback:0,mix:1},tremolo:{frequency:5,depth:.5},reverb:{mix:.3}});
  assert.deepEqual(fx.specifications([{type:'delay'},{type:'delay',params:{mix:0}},{type:'unsupported'}]),{});
});
