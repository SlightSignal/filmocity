// Production routing/ownership checks with controlled nodes, not browser DSP.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const audio=require('../frontend/audio-preview.js');
const {fixture,nestedFixture,multicamFixture}=require('./audio_preview_fixture.cjs');
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-10,`${a} != ${b}`);

test('overlapping uses of one file own distinct playheads and gain/channel controls', () => {
  const f = fixture(); f.clip.audio = {gain_db: -6, channels: 'left', pan: .5};
  f.bus.clips.push({...f.clip, id: 'second', in_: 3, out: 4, audio: {gain_db: -12, channels: 'right'}});
  f.draw(.2);
  const videos = Object.values(f.pool), graphs = Object.values(f.AG.nodes);
  assert.equal(videos.length, 2); assert.notEqual(videos[0], videos[1]);
  assert.deepEqual(videos.map(v => v.currentTime).sort(), [.2, 3.2]);
  assert.equal(f.contexts[0].sources.size, 2);
  const first = graphs.find(n => n.element.currentTime === .2), second = graphs.find(n => n !== first);
  close(first.gain.gain.value, 10 ** (-6/20)); close(second.gain.gain.value, 10 ** (-12/20));
  assert.deepEqual(first.channels.gains.map(n => n.gain.value), [.5, 0, 1, 0]);
  assert.deepEqual(second.channels.gains.map(n => n.gain.value), [0, 1, 0, 1]);
  f.draw(.4); assert.equal(f.contexts[0].sources.size, 2);
});

test('linked video uses the visible A track and gain is after the bus compressor', () => {
  const f = fixture(); f.track.gain_db = 12; f.bus.gain_db = -6; f.bus.audio_fx = {comp:{enabled:true, makeup_db:3}}; f.draw();
  const graph = Object.values(f.AG.nodes)[0], bus = graph.bus;
  assert.equal(bus.trackId, 'A1'); assert.equal(bus.root, true);
  close(bus.gain.gain.value, 10 ** (-6/20)); assert.equal(graph.out.edges[0].to, bus.input);
  assert.equal(bus.input.edges[0].to, bus.low); assert.equal(bus.high.edges[0].to, bus.comp); assert.equal(bus.comp.edges[0].to, bus.makeup);
  close(bus.makeup.gain.value, 10 ** (3/20)); assert.equal(bus.makeup.edges[0].to, bus.gain); assert.equal(bus.gain.edges[0].to, f.AG.rootInput);
});

test('mute, solo, unlink and hold silence existing voices including wet tails', () => {
  for (const change of [f => f.bus.muted = true, f => f.seq.tracks.push({kind:'audio', id:'A2', index:2, solo:true, clips:[]}), f => f.clip.audio.linked = false, f => f.clip.hold = true]) {
    const f = fixture(); f.draw(); const graph = Object.values(f.AG.nodes)[0]; change(f); f.draw();
    assert.equal(graph.element.muted, true); assert.equal(graph.out.gain.value, 0);
  }
});

test('muting the source track retires its audible activity and soloing its bus admits it', () => {
  const f = fixture(); f.bus.solo = true; f.draw(); const graph = Object.values(f.AG.nodes)[0];
  assert.equal(graph.element.muted, false); f.track.muted = true; f.draw();
  assert.equal(graph.element.paused, true); assert.equal(graph.out.gain.value, 0);
});

test('each fade has its own curve and gain includes clip and enabled amplification', () => {
  const f = fixture(); f.clip.audio.gain_db = -6;
  f.clip.audio_transition_in = {type: 'constant_gain', duration: .4};
  f.clip.audio_transition_out = {type: 'constant_power', duration: .4};
  f.clip.afx_stack = [{type: 'amplify', params: {gain_db: 2}}, {type: 'amplify', params: {gain_db: 1}}, {type: 'amplify', enabled: false, params: {gain_db: 40}}];
  f.draw(.2); const graph = Object.values(f.AG.nodes)[0]; close(graph.gain.gain.value, 10 ** (-3/20) * .5);
  f.draw(.8); close(graph.gain.gain.value, 10 ** (-3/20) * Math.sqrt(.5));
});

test('removing highpass/lowpass restores shelf types and default frequencies', () => {
  const f = fixture(); f.clip.afx_stack = [{type: 'highpass', params: {frequency: 400}}, {type: 'lowpass', params: {frequency: 2000}}];
  f.draw(); const graph = Object.values(f.AG.nodes)[0]; assert.equal(graph.low.type, 'highpass'); assert.equal(graph.high.type, 'lowpass');
  f.clip.afx_stack = []; f.draw(); assert.equal(graph.low.type, 'lowshelf'); assert.equal(graph.low.frequency.value, 120);
  assert.equal(graph.mid.frequency.value, 1000); assert.equal(graph.high.type, 'highshelf'); assert.equal(graph.high.frequency.value, 6000);
});

test('matrix uses explicit stereo speaker upmix and balance without crossfeed', () => {
  const f = fixture(); f.clip.audio.pan = -.25; f.draw(); const graph = Object.values(f.AG.nodes)[0];
  assert.equal(graph.channels.input.channelCount, 2); assert.equal(graph.channels.input.channelCountMode, 'explicit');
  assert.equal(graph.channels.input.channelInterpretation, 'speakers');
  assert.deepEqual(graph.channels.gains.map(n => n.gain.value), [1, 0, 0, .75]);
  assert.deepEqual(audio.matrix('swap', 2), [[0, 0], [1, 0]]);
});

test('reference views get separate silent voices without disturbing program playback', () => {
  const f = fixture(); f.draw(.2); const program = Object.values(f.pool)[0];
  const used = new Set(); f.env.drawSequence(f.canvas(), f.seq, .2, used, 0, ['program']);
  f.env.drawSequence(f.canvas(), f.seq, .8, used, 0, ['compare']); f.env.sweepVoices(used);
  const reference = Object.values(f.pool).find(v => v !== program);
  assert.equal(program.currentTime, .2); assert.equal(program.muted, false); assert.equal(reference.currentTime, .8); assert.equal(reference.muted, true);
  assert.equal(Object.keys(f.AG.nodes).length, 1);
});

test('two occurrences of a nested sequence retain different child playheads', () => {
  const f = fixture(), child = f.seq;
  f.S.seq = {id:'parent', tracks:[{id:'V1', kind:'video', index:1, clips:[{id:'nest1', sequence_id:'s', start:0, in_:0, out:1}, {id:'nest2', sequence_id:'s', start:0, in_:.25, out:1}]}]};
  f.S.proj.sequences.push(f.S.seq); f.draw(.2);
  assert.deepEqual(Object.values(f.pool).map(v => v.currentTime).sort(), [.2, .45]);
  assert.equal(f.AG.ctx.sources.size, 2); assert.equal(Object.keys(f.AG.groups).length, 4); assert.equal(child.tracks[0].clips[0].in_, 0);
});

test('reset releases every decoder, oscillator, convolution and matrix node before replacement', () => {
  const f = fixture(); f.clip.afx_stack=[{type:'tremolo'},{type:'chorus'},{type:'reverb'}]; f.draw(); const graph = Object.values(f.AG.nodes)[0], old = graph.element, bus = graph.bus, units={...graph.timeFx.units};
  f.S.useProxy = true; f.env.resetPreviewVoices();
  assert.equal(old.removed, true); assert.equal(old.src, ''); assert.equal(old.loads, 1);
  assert.equal(units.tremolo.oscillator.stops,1); assert.equal(units.modulation.oscillator.stops,1); assert.equal(units.reverb.convolver.edges.length,0); assert.equal(units.reverb.convolver.buffer,null);
  for (const node of [...graph.channels.gains, graph.src, graph.out, bus.comp, bus.gain, bus.an]) assert.ok(node.disconnects >= 1);
  assert.equal(Object.keys(f.pool).length, 0); assert.equal(Object.keys(f.AG.nodes).length, 0); assert.equal(Object.keys(f.AG.buses).length, 0);
  f.draw(); const fresh = Object.values(f.pool)[0]; assert.notEqual(fresh, old); assert.match(fresh.src, /proxy=true/);
  assert.equal(f.contexts.length, 1); assert.equal(Object.values(f.AG.nodes)[0].element, fresh);
});

test('idle seek cache stays bounded and inactive oscillators stop immediately', () => {
  const f = fixture(); f.clip.afx_stack=[{type:'tremolo'},{type:'chorus'}];
  for (let i = 0; i < 30; i++) {f.clip.id = `clip${i}`; f.advance(1); f.draw();}
  assert.equal(Object.keys(f.pool).length, 17); assert.equal(Object.keys(f.AG.nodes).length, 17);
  assert.equal(f.nodes.filter(n => n.kind === 'oscillator' && n.stops).length, 58);
  assert.equal(f.elements.filter(v => !v.removed && !v.paused).length, 1);
  for (const n of Object.values(f.AG.nodes)) if (n.element.paused) assert.equal(n.out.gain.value, 0);
});

test('a reused voice key with a different element cannot reuse the old audio graph', () => {
  const f = fixture(); f.clip.afx_stack=[{type:'tremolo'}]; f.draw(); const [key, old] = Object.entries(f.AG.nodes)[0], oscillator=old.timeFx.units.tremolo.oscillator;
  const replacement = f.env.document.createElement('video'), graph = f.env.audioNodesFor(key, replacement);
  assert.notEqual(graph, old); assert.equal(graph.element, replacement); assert.equal(oscillator.stops, 1); assert.equal(old.src.edges.length, 0);
});

test('rendered audio bypasses baked-in mix processing and reuses one capture per element', () => {
  const f = fixture(); f.env.audioCtx(); const rendered = f.env.document.createElement('video');
  f.env.routeRenderedAudio(rendered); const source = f.AG.rendered.src;
  assert.equal(source.edges[0].to, f.AG.mix); assert.notEqual(source.edges[0].to, f.AG.master);
  assert.equal(f.AG.mix.edges[0].to, f.AG.ctx.destination); assert.equal(f.AG.mix.edges[1].to, f.AG.splitter);
  f.env.routeRenderedAudio(null); assert.equal(source.edges.length, 0);
  f.env.routeRenderedAudio(rendered); assert.equal(f.AG.rendered.src, source); assert.equal(f.AG.ctx.sources.size, 1); assert.equal(source.edges.length, 1);
});

test('meter reads the shared stereo mix without claiming another media source', () => {
  const f = fixture(); f.draw(); const count = f.AG.ctx.sources.size;
  f.env.meter(); f.env.meter(); assert.equal(f.contexts.length, 1); assert.equal(f.AG.ctx.sources.size, count);
  assert.equal(f.fields['#meterDb'].textContent, '-20.0 dB');
});

test('failed audio graph releases partial nodes and visibly suppresses unprocessed sound', () => {
  const f = fixture(); f.env.audioCtx(); f.fail(); f.draw(); f.env.updateAudioPreviewStatus(false);
  const video = Object.values(f.pool)[0], source = f.AG.ctx.sources.get(video);
  assert.equal(video.muted, true); assert.equal(video.volume, 0); assert.ok(source.disconnects >= 1);
  assert.match(f.fields['#audioPreviewStatus'].textContent, /Live audio unavailable/);
  f.draw(); assert.equal(f.AG.ctx.sources.size, 1);
});

test('preview limitation label is throttled during playback and updates after effects change', () => {
  const f = fixture(); f.env.updateAudioPreviewStatus(false);
  for (let i = 0; i < 60; i++) f.env.updateAudioPreviewStatus(false);
  assert.equal(f.scans(), 1); assert.match(f.fields['#audioPreviewStatus'].textContent, /Live stereo mix/);
  f.clip.audio_fx = {comp: {enabled: true}}; f.advance(500); f.env.updateAudioPreviewStatus(false);
  assert.match(f.fields['#audioPreviewStatus'].textContent, /audio effects/);
  f.env.updateAudioPreviewStatus(true); assert.equal(f.fields['#audioPreviewStatus'].textContent, 'Rendered picture and audio');
  assert.equal(f.scans(), 2);
});

test('unsupported nested, synthetic, retimed and surround processing is declared', () => {
  const f = fixture(); f.clip.speed = 2; f.S.proj.media.m.channels = 6; f.S.proj.media.m.synthetic = {kind: 'tone'};
  f.track.clips.push({sequence_id:'child'}); f.seq.master.audio_fx = {limiter: true};
  assert.deepEqual(new Set(audio.limitations(f.seq, f.S.proj)), new Set(['audio effects', 'retimed audio', 'unknown or surround channel layout', 'generated audio', 'nested or multicamera audio']));
});

test('a failed cached voice does not mislabel a different healthy active clip', () => {
  const f = fixture(); f.env.audioCtx(); f.fail(); f.draw(); f.env.updateAudioPreviewStatus(false);
  assert.match(f.fields['#audioPreviewStatus'].textContent, /unavailable/);
  f.recover(); f.clip.id = 'healthy'; f.draw(); f.env.updateAudioPreviewStatus(false);
  assert.match(f.fields['#audioPreviewStatus'].textContent, /Live stereo mix/);
  assert.equal(Object.values(f.pool).filter(v => v._audioError).length, 1);
});



test('child track sum passes through one sequence master and then parent clip and parent bus', () => {
  const f=nestedFixture();f.clip.audio.gain_db=-2;f.bus.gain_db=-3;f.child.master.gain_db=-4;
  f.nest.audio={gain_db:-5,channels:'swap',pan:.25};f.parentBus.gain_db=-6;f.S.seq.master.gain_db=-7;
  f.draw(.2);const {leaf,master,parent}=f.graphs();
  assert.equal(leaf.bus.gain.edges[0].to,master.src);assert.equal(master.out.edges[0].to,parent.src);
  assert.equal(parent.out.edges[0].to,parent.bus.input);assert.equal(parent.bus.gain.edges[0].to,f.AG.rootInput);
  assert.equal(parent.bus.root,true);assert.equal(leaf.bus.root,false);
  [-2,-3,-4,-5,-6,-7].forEach((db,i)=>close([leaf.gain,leaf.bus.gain,master.gain,parent.gain,parent.bus.gain,f.AG.master][i].gain.value,10**(db/20)));
  assert.deepEqual(parent.channels.gains.map(n=>n.gain.value),[0,.75,1,0]);
  assert.deepEqual(leaf.channels.gains.map(n=>n.gain.value),[1,0,0,1]);
});

test('child master compression operates on the shared child sum and parent fades on that result', () => {
  const f=nestedFixture();f.bus.clips.push({...f.clip,id:'second'});
  f.child.master.audio_fx={comp:{enabled:true,threshold_db:-22,ratio:4},limiter:true};
  f.nest.audio_transition_in={type:'constant_gain',duration:.4};f.nest.audio_fx={comp:{enabled:true,threshold_db:-12}};
  f.draw(.2);const {master,parent}=f.graphs(),leaves=Object.values(f.AG.nodes).filter(n=>!n.element._mixOwner);
  assert.equal(leaves.length,2);assert.equal(leaves[0].bus,leaves[1].bus);
  assert.equal(leaves[0].bus.gain.edges[0].to,master.src);assert.equal(master.high.edges[0].to,master.comp);
  assert.equal(master.comp.threshold.value,-22);assert.equal(master.makeup.edges[0].to,master.limit);
  close(parent.gain.gain.value,.5);assert.equal(parent.comp.threshold.value,-12);
  assert.equal(f.AG.ctx.sources.size,2);
});

test('disabled dynamics leave clip, track, master and nested paths without compressor lookahead nodes', () => {
  const f=nestedFixture();f.draw();
  for(const n of Object.values(f.AG.nodes)){assert.equal(n.high.edges[0].to,n.makeup);assert.equal(n.comp.edges.length,0);assert.equal((n.pan || n.makeup).edges[0].to,n.gain);assert.equal(n.limit.edges.length,0);}
  for(const b of Object.values(f.AG.buses)){assert.equal(b.high.edges[0].to,b.makeup);assert.equal(b.makeup.edges[0].to,b.gain);assert.equal(b.comp.edges.length,0);assert.equal(b.limit.edges.length,0);}
  assert.equal(f.AG.rootStrip.makeup.edges[0].to,f.AG.master);assert.equal(f.AG.limiter.edges.length,0);
  f.nest.audio_fx={comp:{enabled:true},limiter:true};f.parentBus.audio_fx={comp:{enabled:true},limiter:true};f.S.seq.master.audio_fx={limiter:true};f.draw();
  const {parent}=f.graphs();assert.equal(parent.pan.edges[0].to,parent.limit);assert.equal(parent.bus.makeup.edges[0].to,parent.bus.limit);
  assert.equal(f.AG.rootStrip.makeup.edges[0].to,f.AG.limiter);assert.equal(f.AG.limiter.edges[0].to,f.AG.master);
  f.nest.audio_fx={};f.parentBus.audio_fx={};f.S.seq.master.audio_fx={};f.draw();
  assert.equal(parent.high.edges[0].to,parent.makeup);assert.equal(parent.bus.comp.edges.length,0);assert.equal(f.AG.limiter.edges.length,0);
});

test('muted, unlinked, held or solo-excluded parent silences descendants and retires its mix owners', () => {
  for(const change of [f=>f.parentBus.muted=true,f=>f.nest.audio.linked=false,f=>f.nest.hold=true,f=>f.parentTrack.muted=true,f=>f.S.seq.tracks.push({id:'other',kind:'audio',index:2,clips:[],solo:true})]){
    const f=nestedFixture();f.nest.afx_stack=[{type:'tremolo'}];f.draw();const {leaf,parent,master}=f.graphs(),oscillator=parent.timeFx.units.tremolo.oscillator;change(f);f.draw();
    assert.equal(leaf.element.muted||leaf.element.paused,true);assert.equal(leaf.out.gain.value,0);
    assert.equal(Object.keys(f.AG.groups).length,0);assert.equal(oscillator.stops,1);assert.equal(master.lfo,undefined);assert.ok(master.src.disconnects>0);
    assert.equal(parent.out.edges.length,0);assert.equal(master.out.edges.length,0);
  }
});

test('returning to a retired nested occurrence connects cached leaf to newly owned master', () => {
  const f=nestedFixture();f.draw();const old=f.graphs();f.draw(2);f.draw(.4);const fresh=f.graphs();
  assert.equal(fresh.leaf,old.leaf);assert.notEqual(fresh.master,old.master);assert.equal(fresh.leaf.bus.gain.edges[0].to,fresh.master.src);
  assert.equal(old.master.src.edges.length,0);assert.equal(f.AG.ctx.sources.size,1);
  f.env.resetPreviewVoices();assert.equal(Object.keys(f.AG.nodes).length,0);assert.equal(Object.keys(f.AG.groups).length,0);
  assert.equal(fresh.master.lfo,undefined);assert.ok(fresh.master.src.disconnects>0);assert.equal(Object.keys(fresh.parent.timeFx.units).length,0);assert.ok(fresh.parent.src.disconnects>0);
});

test('two nested levels retain both parent processing stages and propagate constant playback rate', () => {
  const f=nestedFixture(),middle=f.S.seq;
  f.nest.speed=1.5;const outer={id:'outer',sequence_id:'parent',start:0,in_:0,out:1,speed:2};
  f.S.seq={id:'top',width:64,height:48,tracks:[{id:'topV',kind:'video',index:1,clips:[outer]}]};f.S.proj.sequences.push(f.S.seq);
  f.draw(.1);const leaf=Object.values(f.AG.nodes).find(n=>!n.element._mixOwner);
  close(leaf.element.currentTime,.3);close(leaf.element.playbackRate,3);assert.equal(Object.keys(f.AG.groups).length,4);
  assert.equal(Object.values(f.AG.nodes).filter(n=>n.element._mixOwner&&n.parent).length,2);
  outer.hold=true;outer.hold_at=.2;f.draw(.2);assert.equal(leaf.element.paused,true);assert.equal(leaf.element.muted,true);
  assert.equal(middle.tracks[0].clips[0].speed,1.5);
});

test('nested comparison view cannot allocate audible parent groups', () => {
  const f=nestedFixture();f.draw(.2,['compare']);assert.equal(Object.keys(f.AG.groups).length,0);
  assert.equal(Object.keys(f.AG.nodes).length,0);assert.equal(Object.values(f.pool)[0].muted,true);
});

test('failed nested processor allocation reports unavailable and never plays raw child sound', () => {
  const f=nestedFixture();f.env.audioCtx();f.fail();f.draw();f.env.updateAudioPreviewStatus(false);
  assert.match(f.fields['#audioPreviewStatus'].textContent,/Live audio unavailable/);
  assert.equal(Object.values(f.pool)[0].muted,true);assert.equal(Object.keys(f.AG.nodes).length,0);
  assert.equal(f.AG.ctx.sources.size,0);
});



test('multicamera follow switches the admitted audio source and keeps saved mute flags unchanged', () => {
  const f=multicamFixture(),before=JSON.stringify(f.child);f.nest.multicam_angle=1;f.draw();
  const audible=()=>Object.values(f.pool).filter(v=>!v.muted&&!v.paused);
  assert.equal(audible().length,1);assert.match(audible()[0].src,/m2\?/);
  f.nest.multicam_angle=0;f.draw();assert.equal(audible().length,1);assert.match(audible()[0].src,/\/m\?/);
  assert.equal(JSON.stringify(f.child),before);
});

test('multicamera fixed track stays audible on another picture angle and invalid selection retires the mix', () => {
  const f=multicamFixture();f.child.multicam_audio='fixed';f.child.multicam_audio_track='A1';f.nest.multicam_angle=1;f.draw();
  const audible=Object.values(f.pool).filter(v=>!v.muted&&!v.paused);assert.equal(audible.length,1);assert.match(audible[0].src,/\/m\?/);
  f.child.multicam_audio_track='missing';f.draw();assert.equal(Object.keys(f.AG.groups).length,0);
  assert.equal(audible[0].paused,true);assert.match(f.messages.at(-1),/unavailable/);
});

test('compressor makeup precedes the limiter and clip fader at each nested master', () => {
  const f=nestedFixture();f.child.master.audio_fx={comp:{enabled:true,makeup_db:6},limiter:true};f.draw();
  const {master}=f.graphs();assert.equal(master.comp.edges[0].to,master.makeup);
  assert.equal(master.limit.edges[0].to,master.gain);assert.equal(master.makeup.edges[0].to,master.limit);
  close(master.makeup.gain.value,10**(6/20));close(master.gain.gain.value,1);
});

test('track bus allocation failure releases partial nodes and keeps captured sound silent', () => {
  const f=fixture();f.env.audioCtx();const createAnalyser=f.AG.ctx.createAnalyser;f.AG.ctx.createAnalyser=()=>{throw new Error('meter allocation failed');};
  f.draw();f.env.updateAudioPreviewStatus(false);const graph=Object.values(f.AG.nodes)[0];
  assert.equal(graph.element.muted,true);assert.equal(graph.out.gain.value,0);assert.equal(Object.keys(f.AG.buses).length,0);
  assert.match(f.fields['#audioPreviewStatus'].textContent,/unavailable/);
  const partial=f.nodes.slice(-5);assert.ok(partial.every(n=>n.disconnects>0));
  f.AG.ctx.createAnalyser=createAnalyser;f.draw();assert.equal(graph.element.muted,false);assert.equal(graph.element._audioError,false);
});

test('a nested clip on an audio track receives its child sum and obeys audio track admission', () => {
  const f=nestedFixture();f.parentTrack.clips=[];f.parentBus.clips=[f.nest];f.nest.audio.linked=false;
  f.draw();const {leaf,parent}=f.graphs();assert.equal(leaf.element.muted,false);assert.equal(parent.bus.trackId,'PA');
  f.parentBus.muted=true;f.draw();assert.equal(leaf.element.paused,true);assert.equal(leaf.out.gain.value,0);
});

// Recover the parent owner as well as a media leaf after a temporary bus failure.
test('recovered nested bus clears its group error while keeping the child routing owned', () => {
  const f=nestedFixture();f.env.audioCtx();const original=f.AG.ctx.createAnalyser;
  f.AG.ctx.createAnalyser=()=>{throw new Error('temporary bus failure');};f.draw();
  assert.equal(Object.values(f.AG.groups)[0]._audioError,true);
  f.AG.ctx.createAnalyser=original;f.draw();f.env.updateAudioPreviewStatus(false);
  assert.equal(Object.values(f.AG.groups).some(v=>v._audioError),false);
  assert.equal(f.graphs().leaf.element.muted,false);assert.doesNotMatch(f.fields['#audioPreviewStatus'].textContent,/unavailable/);
});

test('track and root EQ plus root compression process the shared mix before their faders', () => {
  const f=fixture();f.bus.audio_fx={eq:{low_db:3,mid_db:-2,high_db:5}};
  f.seq.master={gain_db:-6,audio_fx:{eq:{low_db:-1,mid_db:4,high_db:2},comp:{enabled:true,threshold_db:-24,ratio:5,attack_ms:.5,release_ms:80,makeup_db:3},limiter:true}};
  f.draw();const leaf=Object.values(f.AG.nodes)[0],bus=leaf.bus,root=f.AG.rootStrip;
  assert.deepEqual([bus.low.gain.value,bus.mid.gain.value,bus.high.gain.value],[3,-2,5]);
  assert.deepEqual([root.low.gain.value,root.mid.gain.value,root.high.gain.value],[-1,4,2]);
  assert.equal(root.input.edges[0].to,root.low);assert.equal(root.low.edges[0].to,root.mid);assert.equal(root.mid.edges[0].to,root.high);
  assert.equal(root.high.edges[0].to,root.comp);assert.equal(root.comp.edges[0].to,root.makeup);assert.equal(root.makeup.edges[0].to,root.limit);assert.equal(root.limit.edges[0].to,root.gain);
  assert.equal(root.gain.edges[0].to,f.AG.mix);assert.equal(root.comp.threshold.value,-24);close(root.comp.attack.value,.0005);close(root.comp.release.value,.08);
  close(root.makeup.gain.value,10**(3/20));close(root.gain.gain.value,10**(-6/20));close(root.limit.threshold.value,20*Math.log10(.95));
});

test('removing track and root processing restores neutral EQ, makeup, fader and bypass', () => {
  const f=fixture();f.bus.audio_fx={eq:{low_db:6},comp:{enabled:true,makeup_db:9},limiter:true};f.seq.master={gain_db:-10,audio_fx:f.bus.audio_fx};f.draw();
  const leaf=Object.values(f.AG.nodes)[0],bus=leaf.bus,root=f.AG.rootStrip;
  f.bus.audio_fx={eq:null,comp:null};f.seq.master={audio_fx:{eq:null,comp:null}};f.draw();
  for(const strip of [bus,root]){assert.equal(strip.high.edges[0].to,strip.makeup);assert.equal(strip.makeup.edges[0].to,strip.gain);
    assert.equal(strip.comp.edges.length,0);assert.equal(strip.limit.edges.length,0);close(strip.makeup.gain.value,1);close(strip.gain.gain.value,1);
    assert.deepEqual([strip.low.gain.value,strip.mid.gain.value,strip.high.gain.value],[0,0,0]);}
});

test('sequence master strips do not allocate clip convolution, delays, matrices or oscillators', () => {
  const f=nestedFixture();f.clip.afx_stack=[{type:'reverb'},{type:'tremolo'}];f.nest.afx_stack=[{type:'reverb'},{type:'tremolo'}];f.draw();const {master,parent,leaf}=f.graphs();
  for(const key of ['channels','conv','delay','lfo','clfo','trem'])assert.equal(master[key],undefined);
  assert.equal(f.buffers.length,1);assert.equal(f.nodes.filter(n=>n.kind==='oscillator').length,2);
  assert.ok(parent.timeFx.units.reverb.convolver);assert.ok(leaf.timeFx.units.reverb.convolver);f.env.resetPreviewVoices();assert.ok(master.src.disconnects>0);
  assert.equal(f.AG.rootStrip.gain.edges[0].to,f.AG.mix);
});

test('all bus filter nodes are released when their owners retire', () => {
  const f=nestedFixture();f.draw();const buses=Object.values(f.AG.buses),master=f.graphs().master;
  f.env.resetPreviewVoices();
  for(const strip of [...buses,master])for(const key of ['input','low','mid','high','comp','makeup','limit','gain'])assert.ok(strip[key].disconnects>0);
  assert.equal(f.AG.rootStrip.input.disconnects,0);
});

test('partial root graph failure closes its context, releases every node, and can recover', () => {
  const f=fixture(),Original=f.env.window.AudioContext;
  class Failing extends Original {createAnalyser(){throw new Error('root meter failed');}}
  f.env.window.AudioContext=Failing;assert.equal(f.env.audioCtx(),null);
  assert.equal(f.contexts[0].state,'closed');assert.ok(f.nodes.filter(n=>n.kind!=='speaker').every(n=>n.disconnects>0));
  f.env.window.AudioContext=Original;f.draw();assert.equal(f.contexts.length,2);assert.equal(Object.values(f.pool)[0].muted,false);
});

test('unchanged mixer redraws reuse EQ/dynamics nodes and never allocate effect buffers for buses', () => {
  const f=fixture();f.seq.master={audio_fx:{eq:{low_db:4},comp:{enabled:true}}};f.draw();const count=f.nodes.length,bufferCount=f.buffers.length;
  for(let i=0;i<200;i++)f.draw(.2);
  assert.equal(f.nodes.length,count);assert.equal(f.buffers.length,bufferCount);assert.equal(f.AG.rootStrip.high.edges.length,1);
});
