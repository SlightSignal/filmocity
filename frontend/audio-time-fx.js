/* On-demand ownership for the live time-effect estimates. The render engine
 * remains authoritative for effect order, algorithms, phase and tail output.
 */
(function(global){
  'use strict';
  const impulses=new WeakMap();
  const number=(v,d)=>Number.isFinite(Number(v))&&v!=null?Number(v):d;
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  function impulse(ctx){
    let record=impulses.get(ctx);
    if(!record){
      const frames=Math.round(ctx.sampleRate*1.6),buffer=ctx.createBuffer(2,frames,ctx.sampleRate);
      // Repeatable per-channel noise: seeking does not invent another room.
      for(let ch=0;ch<2;ch++){const data=buffer.getChannelData(ch);let seed=(0x6d2b79f5+ch)>>>0;
        for(let i=0;i<data.length;i++){seed=(Math.imul(seed,1664525)+1013904223)>>>0;data[i]=(seed/2147483648-1)*Math.pow(1-i/data.length,2.8);}}
      record={buffer,refs:0,bytes:2*frames*4};impulses.set(ctx,record);
    }
    record.refs++;let released=false;
    return {buffer:record.buffer,release(){if(released)return;released=true;this.buffer=null;if(--record.refs===0){record.buffer=null;impulses.delete(ctx);}}};
  }
  function impulseStats(ctx){const record=impulses.get(ctx);return {buffers:record?1:0,leases:record?.refs||0,requestedBytes:record?.bytes||0};}
  function specifications(stack){
    const result={};
    for(const effect of stack||[]){if(!effect||effect.enabled===false)continue;const p=effect.params||{};
      if(effect.type==='delay')result.delay={delay:clamp(number(p.delay_ms,300)/1000,0,2),feedback:clamp(number(p.feedback,.4),0,.9),mix:clamp(number(p.mix,.5),0,1)};
      else if(effect.type==='reverb'||effect.type==='studio_reverb')result.reverb={mix:clamp(number(p.mix,effect.type==='reverb'?.3:.25),0,1)};
      else if(effect.type==='tremolo')result.tremolo={frequency:clamp(number(p.frequency,5),.1,20),depth:clamp(number(p.depth,.5),0,1)};
      else if(effect.type==='chorus'||effect.type==='flanger')result.modulation={type:effect.type,delay:effect.type==='flanger'?.003:.015,frequency:effect.type==='flanger'?.3:.8,depth:effect.type==='flanger'?.002:.004,mix:.5};
    }
    // These estimates leave the dry path at unity when their wet depth is zero.
    if(result.delay?.mix===0)delete result.delay;
    if(result.reverb?.mix===0)delete result.reverb;
    if(result.tremolo?.depth===0)delete result.tremolo;
    return result;
  }
  function unit(ctx,kind,output){
    const owned=[];let lease,disposed=false;
    const make=(method,...args)=>{const node=ctx[method](...args);owned.push(node);return node;};
    const dispose=()=>{if(disposed)return;disposed=true;
      for(const node of owned){if(node.buffer)node.buffer=null;if(typeof node.stop==='function'){try{node.stop();}catch{}}node.disconnect();}
      lease?.release();};
    try{
      let value;
      if(kind==='delay'){
        const delay=make('createDelay',2),feedback=make('createGain'),wet=make('createGain');wet.gain.value=0;feedback.gain.value=0;
        delay.connect(feedback);feedback.connect(delay);delay.connect(wet);wet.connect(output);
        value={input:delay,delay,feedback,wet,update(p,t){delay.delayTime.setTargetAtTime(p.delay,t,.02);feedback.gain.setTargetAtTime(p.feedback,t,.02);wet.gain.setTargetAtTime(p.mix,t,.02);}};
      }else if(kind==='reverb'){
        const convolver=make('createConvolver');lease=impulse(ctx);convolver.buffer=lease.buffer;
        const wet=make('createGain');wet.gain.value=0;convolver.connect(wet);wet.connect(output);
        value={input:convolver,convolver,wet,update(p,t){wet.gain.setTargetAtTime(p.mix,t,.02);}};
      }else if(kind==='tremolo'){
        const gain=make('createGain'),oscillator=make('createOscillator'),depth=make('createGain');depth.gain.value=0;
        oscillator.connect(depth);depth.connect(gain.gain);oscillator.start();
        value={input:gain,gain,oscillator,depth,update(p,t){oscillator.frequency.value=p.frequency;depth.gain.setTargetAtTime(p.depth*.5,t,.02);}};
      }else{
        const delay=make('createDelay',.05),wet=make('createGain'),oscillator=make('createOscillator'),depth=make('createGain');wet.gain.value=0;depth.gain.value=0;
        oscillator.connect(depth);depth.connect(delay.delayTime);delay.connect(wet);wet.connect(output);oscillator.start();
        value={input:delay,delay,wet,oscillator,depth,update(p,t){delay.delayTime.value=p.delay;oscillator.frequency.value=p.frequency;depth.gain.setTargetAtTime(p.depth,t,.02);wet.gain.setTargetAtTime(p.mix,t,.02);}};
      }
      return {...value,dispose};
    }catch(error){dispose();throw error;}
  }
  function create(ctx,input,output){
    const units={};let disposed=false,failedKey=null,layout='',lastError=null;
    input.connect(output);
    function detach(){input.disconnect();units.tremolo?.gain.disconnect();}
    function connect(){const tap=units.tremolo?.gain||input;if(tap!==input)input.connect(tap);tap.connect(output);
      for(const [kind,value] of Object.entries(units))if(kind!=='tremolo')tap.connect(value.input);}
    function clear(){if(disposed)return;failedKey=null;lastError=null;if(!Object.keys(units).length)return;
      detach();for(const kind of Object.keys(units)){units[kind].dispose();delete units[kind];}layout='';connect();}
    function update(stack){
      if(disposed)return false;const specs=specifications(stack),key=JSON.stringify(specs);if(key===failedKey)return false;
      const added={};try{
        for(const [kind,p] of Object.entries(specs)){const value=units[kind]||(added[kind]=unit(ctx,kind,output));value.update(p,ctx.currentTime);}
      }catch(error){for(const value of Object.values(added))value.dispose();clear();failedKey=key;lastError=error.message||String(error);return false;}
      const next=Object.keys(specs).sort().join('|');
      if(next!==layout){detach();for(const kind of Object.keys(units))if(!specs[kind]){units[kind].dispose();delete units[kind];}
        Object.assign(units,added);layout=next;connect();}
      failedKey=null;lastError=null;return true;
    }
    return {units,update,clear,get error(){return lastError;},dispose(){if(disposed)return;detach();for(const kind of Object.keys(units)){units[kind].dispose();delete units[kind];}layout='';failedKey=null;disposed=true;}};
  }
  const api={create,specifications,impulseStats};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.FilmocityAudioTimeFX=api;
})(typeof window==='undefined'?globalThis:window);
