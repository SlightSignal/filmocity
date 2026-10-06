/* Core mixer contract. DSP/browser scheduling acceptance remains separate. */
(function(global){
  'use strict';
  const number=(v,d=0)=>Number.isFinite(Number(v))?Number(v):d;
  function duckDb(clip,time){
    const points=clip.keyframes?.['audio.duck_db'];if(points==null)return 0;
    if(!Array.isArray(points)||points.length>8192||!Number.isFinite(time))return NaN;
    if(!points.length)return 0;
    let last=-1;
    for(const p of points){if(!p||!Number.isFinite(p.t)||p.t<0||p.t>1e12||p.t<=last||!Number.isFinite(p.v)||p.v>0||p.v < -60||!['linear','hold'].includes(p.e===undefined?'linear':p.e))return NaN;last=p.t;}
    let lo=0,hi=points.length;
    while(lo<hi){const mid=(lo+hi)>>1;if(points[mid].t<=time)lo=mid+1;else hi=mid;}
    const index=Math.max(0,lo-1),a=points[index],b=points[index+1];
    if(time<a.t||!b||a.e==='hold')return a.v;
    return a.v+(b.v-a.v)*(time-a.t)/(b.t-a.t);
  }
  function destination(sequence,track){
    if(track.kind!=='video')return track;
    const audio=sequence.tracks.filter(t=>t.kind==='audio').sort((a,b)=>a.index-b.index);
    return audio.find(t=>t.index===track.index)||audio[0]||track;
  }
  function route(sequence,track,clip){
    const bus=destination(sequence,track);
    if(clip.enabled===false||clip.hold||track.muted||track._mc_audio_disabled||bus.muted||track.kind==='video'&&clip.audio?.linked===false)return null;
    if(sequence.tracks.some(t=>t.solo)&&!(track.solo||bus.solo))return null;
    return bus;
  }
  function multicam(sequence,angle=0){
    const videos=sequence.tracks.filter(t=>t.kind==='video').sort((a,b)=>a.index-b.index);
    const audios=sequence.tracks.filter(t=>t.kind==='audio').sort((a,b)=>a.index-b.index);
    if(!Number.isInteger(angle)||angle<0||angle>=videos.length)throw new Error('Choose an existing multicamera angle');
    const camera=videos[angle],follow=sequence.multicam_audio==='follow';let sound,soundCamera;
    if(follow){sound=destination(sequence,camera);soundCamera=camera;}
    else if(audios.length){
      sound=sequence.multicam_audio_track?audios.find(t=>t.id===sequence.multicam_audio_track):audios[0];
      if(!sound)throw new Error('The fixed multicamera audio track is unavailable');
      soundCamera=videos.find(t=>t.index===sound.index);
    }else sound=soundCamera=videos[0];
    const tracks=sequence.tracks.map(track=>{
      const value={...track};
      if(track.kind==='video'){
        value._mc_picture_hidden=track.id!==camera.id;
        value._mc_audio_disabled=!soundCamera||track.id!==soundCamera.id;
        value.muted=value._mc_picture_hidden&&value._mc_audio_disabled;
      }else if(track.kind==='audio')value.muted=track.id!==sound.id;
      if(value.muted)value.solo=false;
      return value;
    });
    return {...sequence,multicam:false,_multicam_angle:angle,tracks};
  }
  function matrix(mode,pan){
    const base={left:[[1,0],[1,0]],right:[[0,1],[0,1]],mono:[[.5,.5],[.5,.5]],swap:[[0,1],[1,0]]}[mode]||[[1,0],[0,1]];
    const p=Math.max(-1,Math.min(1,number(pan))),balance=[Math.min(1,1-p),Math.min(1,1+p)];
    return base.map((row,i)=>row.map(g=>g*balance[i]));
  }
  function fadeSettings(clip){
    const a=clip.audio||{};
    return [number(a.fade_in),number(a.fade_out),a.constant_power!==false,
      ...['in','out'].flatMap(side=>{const t=clip['audio_transition_'+side]||{};return [t.type||'',number(t.duration)];})];
  }
  function fadeWindow(clip,duration){
    const settings=fadeSettings(clip),w=clip.audio?.fade_window;
    if(w&&Number.isFinite(w.duration)&&w.duration>0&&Number.isFinite(w.offset)&&
      JSON.stringify(w.settings)===JSON.stringify(settings))return {...w,settings};
    return {duration,offset:0,settings};
  }
  function fadeSpec(clip,duration){
    const window=fadeWindow(clip,duration);duration=window.duration;
    const audio=clip.audio||{},fallback=audio.constant_power===false?'constant_gain':'constant_power';
    return ['in','out'].map(side=>{
      const transition=clip['audio_transition_'+side]||{};
      let curve=transition.type||fallback;if(!['constant_power','constant_gain','exponential'].includes(curve))curve='constant_power';
      const length=Math.min(Math.max(0,duration),Math.max(0,number(audio['fade_'+side]),number(transition.duration)));
      return {side,duration:length,start:(side==='in'?0:Math.max(0,duration-length))-window.offset,curve};
    });
  }
  function fadeGain(clip,duration,time){
    if(time<0||time>=duration)return 0;
    return fadeSpec(clip,duration).reduce((gain,f)=>{
      if(!f.duration)return gain;
      if(f.side==='in'&&time<f.start||f.side==='out'&&time>f.start+f.duration)return 0;
      const x=Math.max(0,Math.min(1,f.side==='in'?(time-f.start)/f.duration:(f.start+f.duration-time)/f.duration));
      return gain*(f.curve==='constant_gain'?x:f.curve==='exponential'?Math.exp(-11.512925464970227*(1-x)):Math.sin(x*Math.PI/2));
    },1);
  }
  function matrixNodes(ctx,make=(method,...args)=>ctx[method](...args)){
    const input=make("createGain"),split=make("createChannelSplitter",2),output=make("createChannelMerger",2),gains=[];
    input.channelCount=2;input.channelCountMode='explicit';input.channelInterpretation='speakers';input.connect(split);
    for(let out=0;out<2;out++)for(let source=0;source<2;source++){
      const node=make("createGain");node.gain.value=out===source?1:0;split.connect(node,source);node.connect(output,0,out);gains.push(node);
    }
    return {input,output,gains,update(mode,pan,time){matrix(mode,pan).flat().forEach((gain,i)=>gains[i].gain.setValueAtTime(gain,time));},
      dispose(){for(const node of [input,split,output,...gains])node.disconnect();}};
  }
  // Core EQ matches the existing FFmpeg bass/treble defaults (two poles,
  // Q=.5 and their bass/treble-specific shelf coefficients), and peaking Q=1.
  function eqBands(eq={}){
    eq ||= {};
    return [['low','lowshelf',120],['mid','peaking',1000],['high','highshelf',6000]].map(([name,type,frequency])=>
      ({name,type,frequency,q:1,gain:number(eq[name+'_db'])}));
  }
  function configureEQ(nodes,eq,time){
    for(const band of eqBands(eq)){
      const node=nodes[band.name];node.type=band.type;node.frequency.value=band.frequency;node.Q.value=band.q;
      node.gain.setTargetAtTime(band.gain,time,.02);
    }
  }
  function configureCompressor(node,comp={}){
    const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
    node.threshold.value=clamp(number(comp.threshold_db,-18),-100,0);
    node.ratio.value=clamp(number(comp.ratio,3),1,20);
    node.attack.value=clamp(number(comp.attack_ms,20)/1000,0,1);
    node.release.value=clamp(number(comp.release_ms,200)/1000,0,1);
    // FFmpeg's default multiplicative knee sqrt(8) spans about 9.03 dB.
    // Browser envelope/knee algorithms are still an estimate, not FFmpeg DSP.
    node.knee.value=20*Math.log10(Math.sqrt(8));
  }
  function configureLimiter(node){
    node.threshold.value=20*Math.log10(.95);node.knee.value=0;node.ratio.value=20;node.attack.value=.001;node.release.value=.05;
  }
  function routeStage(input,processor,output,enabled,owner,field){
    enabled=!!enabled;if(owner[field]===enabled)return;
    input.disconnect();processor.disconnect();input.connect(enabled?processor:output);
    if(enabled)processor.connect(output);owner[field]=enabled;
  }
  function busNodes(ctx){
    const owned=[];let disposed=false;
    const make=method=>{const node=ctx[method]();owned.push(node);return node;};
    try{
      const strip={input:make('createGain'),low:make('createBiquadFilter'),mid:make('createBiquadFilter'),high:make('createBiquadFilter'),
        comp:make('createDynamicsCompressor'),makeup:make('createGain'),limit:make('createDynamicsCompressor'),gain:make('createGain'),compressed:false,limited:false};
      strip.input.connect(strip.low);strip.low.connect(strip.mid);strip.mid.connect(strip.high);strip.high.connect(strip.makeup);strip.makeup.connect(strip.gain);
      configureLimiter(strip.limit);
      strip.update=(fx={},gain=0,time=ctx.currentTime)=>{
        if(disposed)throw new Error('Audio bus has been released');
        fx ||= {};const comp=fx.comp||{};configureEQ(strip,fx.eq,time);configureCompressor(strip.comp,comp);
        routeStage(strip.high,strip.comp,strip.makeup,comp.enabled,strip,'compressed');
        routeStage(strip.makeup,strip.limit,strip.gain,fx.limiter,strip,'limited');
        strip.makeup.gain.setValueAtTime(10**((comp.enabled?number(comp.makeup_db):0)/20),time);
        strip.gain.gain.setValueAtTime(10**(number(gain)/20),time);
      };
      strip.dispose=()=>{if(disposed)return;disposed=true;for(const node of owned)node.disconnect();};
      strip.update();return strip;
    }catch(error){for(const node of owned)node.disconnect();throw error;}
  }
  function voiceKey(context,sequence,path,track,clip){return JSON.stringify([context?.workspace,context?.project,sequence,path,track,clip]);}
  function disposeNodes(nodes){
    if(!nodes)return;
    if(typeof nodes.dispose==='function'){nodes.dispose();return;}
    nodes.channels?.dispose();
    for(const value of Object.values(nodes)){
      if(typeof value?.stop==='function'){try{value.stop();}catch{}}
      if(typeof value?.disconnect==='function'){try{value.disconnect();}catch{}}
    }
  }
  function limitations(sequence,project){
    const reasons=new Set();
    function fx(value){if(value&&Object.entries(value).some(([key,v])=>key==='limiter'?!!v:v&&typeof v==='object'&&(v.enabled||key==='eq'&&Object.values(v).some(x=>number(x)!==0))))reasons.add('audio effects');}
    fx(sequence.master?.audio_fx);
    for(const track of sequence.tracks){fx(track.audio_fx);
      for(const clip of track.clips||[]){
        if(clip.enabled===false)continue;
        if(clip.sequence_id)reasons.add('nested or multicamera audio');
        const media=project.media?.[clip.media_id];if(!media?.has_audio)continue;
        if(media.synthetic)reasons.add('generated audio');
        if(![1,2].includes(media.channels))reasons.add('unknown or surround channel layout');
        if(clip.reverse||clip.time_remap||media.interpret_fps||number(clip.speed,1)!==1)reasons.add('retimed audio');
        fx(clip.audio_fx);if((clip.afx_stack||[]).some(f=>f.enabled!==false))reasons.add('audio effects');
      }
    }
    return [...reasons];
  }
  const api={destination,route,multicam,duckDb,eqBands,configureEQ,configureCompressor,configureLimiter,routeStage,busNodes,matrix,fadeWindow,fadeSpec,fadeGain,matrixNodes,voiceKey,disposeNodes,limitations};
  global.FilmocityAudioPreview=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
